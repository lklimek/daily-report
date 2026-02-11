# Research: Local Git vs GitHub API for Daily Report

## 1. Available Local Repos

Three dashpay repos are cloned locally:

| Repo | Path | Remote | Branches |
|------|------|--------|----------|
| tenderdash | `/home/ubuntu/git/tenderdash` | `dashpay/tenderdash` | 49 |
| dash-evo-tool | `/home/ubuntu/git/dash-evo-tool` | `dashpay/dash-evo-tool` | 47 |
| platform | `/home/ubuntu/platform` (symlinked from `~/git/platform`) | `dashevo/platform` (resolves to `dashpay/platform`) | 424 |

For the test case (user=lklimek, 2026-02-03..2026-02-09), `gh search` returned PRs across
6 repos. Three are cloned locally, three are not:

| Repo | Cloned | PRs found by gh search |
|------|--------|------------------------|
| platform | Yes | 6 |
| dash-evo-tool | Yes | 4 |
| tenderdash | Yes | 1 |
| grovestark | No (489 KB) | 1 |
| rust-dashcore | No (89 MB) | 1 |
| bls-signatures | No (27 MB) | 1 |

**78% of PRs (11/14) are in locally cloned repos.**

---

## 2. Benchmark: Local Git Log vs GitHub API

### Test case: user=lklimek, date range 2026-02-03..2026-02-09

#### Local git log (all 3 repos combined)

```
real  0m0.010s   (10 milliseconds)
```

- tenderdash: 8 commits found in 0.016s
- platform: 21 commits found in 0.035s
- dash-evo-tool: 19 commits found in 0.007s

#### gh search prs (API)

```
real  ~1.0s per search call
```

- `gh search prs --author=lklimek --updated=... --owner=dashpay`: ~1.05s

#### Full daily_report.py run (current approach)

```
real  0m50.435s   (50 seconds)
```

Makes approximately 104 individual REST API calls.

#### Git fetch (required for freshness)

```
tenderdash:    0.743s
platform:      0.564s
dash-evo-tool: 0.540s
Total:         ~1.85s
```

**Result: Local git log is ~5000x faster than a single gh search call.
Even including git fetch, the local approach starts ~25x faster than
the current tool's total runtime.**

---

## 3. Commit-to-PR Mapping Methods

### Methods tested

| Option | Method | Works? | Speed | Notes |
|--------|--------|--------|-------|-------|
| A | `gh api /search/issues?q=sha:HASH+repo:ORG/REPO+type:pr` | Yes | ~585ms | Returns correct PRs. Subject to search rate limits. |
| B | `gh pr list --search HASH` | **No** | ~540ms | Returns empty results. Does not work for commit SHA search. |
| C | Parse `#NNN` from commit messages | Yes | 0ms | Works for squash-merged PRs. 38% of commits had PR refs in this test. |
| D | `git log --merges --ancestry-path COMMIT..HEAD` | **Unreliable** | ~5ms | Fails when branch is deleted or commit is not an ancestor of HEAD. |
| E | `gh api repos/ORG/REPO/commits/SHA/pulls` | Yes | ~600ms | Most reliable REST option. Works for commits on any branch. |
| **GraphQL batch** | `associatedPullRequests` on Commit objects | **Yes** | **~1.5s for 13 commits** | Best option. Maps many commits in one call. |

### GraphQL Batch (recommended)

A single GraphQL query can map up to ~20+ commits to PRs simultaneously:

```graphql
{
  repository(owner: "dashpay", name: "platform") {
    c0: object(expression: "SHA1") {
      ... on Commit {
        associatedPullRequests(first: 3) {
          nodes { number title }
        }
      }
    }
    c1: object(expression: "SHA2") { ... }
    # ... up to ~20+ commits per query
  }
}
```

**Timing comparison for 13 commits:**
- Individual REST calls (Option E): 13 x 600ms = **~8 seconds**
- Single GraphQL batch: **~1.5 seconds** (5.3x faster)

### Optimization: filter merge commits

Using `--no-merges` reduces commits from 21 to 12 (platform) by excluding
local merge commits (e.g., "Merge branch 'v3.1-dev' into feature-branch").
These merge commits map to the same PRs as the non-merge commits, so
filtering them out reduces API calls without losing information.

### Optimization: extract PR numbers from commit messages

Squash-merged commits contain `(#NNN)` in the message. For this test case,
8 of 21 platform commits (38%) had PR refs, requiring zero API calls to map.
Only the remaining commits need GraphQL lookup.

---

## 4. Coverage Analysis

### What local git catches that the current approach misses

**Confirmed gap: Bot-authored PRs with user commits.**

PR #3075 (`platform`): "chore: merge v3.1-dev into feat/drive-event-bus"
- Author: **Copilot** (bot)
- lklimek has **11 commits** in this PR
- lklimek has **0 reviews** and **0 comments**
- `gh search --author=lklimek`: misses (wrong author)
- `gh search --reviewed-by=lklimek`: misses (no reviews)
- `gh search --commenter=lklimek`: misses (no comments)
- **Local git: FINDS it** via commit authorship

The current `daily_report.py` has no way to discover this PR at all.

### What local git cannot detect

| Activity | Local git? | API needed? |
|----------|-----------|-------------|
| User authored commits | Yes | No |
| PR the commit belongs to | Partially (from commit message) | Yes (for unmapped commits) |
| PR state (open/merged/closed) | No | Yes |
| PR additions/deletions | No | Yes |
| Review activity (approvals, comments) | No | Yes |
| Pending review requests | No | Yes |
| User comments on PRs | No | Yes |

### Commits in open PRs (feature branches)

Local git finds these **only if branches are fetched**. With the default
`+refs/heads/*:refs/remotes/origin/*` fetch refspec plus `git fetch --all`,
all remote branches are available. This was confirmed to work.

### Commits pushed but not yet in a PR

Local git would find these commits, but they would fail the commit-to-PR
mapping step (GraphQL `associatedPullRequests` returns empty). These could
be reported separately as "unpublished work" or silently ignored.

---

## 5. Hybrid Approach Feasibility

### Recommended architecture

```
Phase 1: Local git (commit discovery)        ~2 seconds
  - git fetch --all for each configured repo
  - git log --remotes --author=USER --no-merges --after/--before
  - Extract PR #s from commit messages (free)
  - GraphQL batch for remaining commit -> PR mapping

Phase 2: GraphQL API (review discovery)      ~2 seconds
  - Single GraphQL search for reviewed-by + commenter
  - Dedup against Phase 1 results

Phase 3: GraphQL API (details + enrichment)  ~2 seconds
  - Batch fetch PR details (state, merged_at, +/-, isDraft)
  - Batch fetch open authored PRs with pending reviewers
  - All in 1-2 GraphQL calls

Total estimated time: ~6-7 seconds
```

### API call comparison

| Metric | Current approach | Hybrid approach |
|--------|-----------------|-----------------|
| REST API calls | ~104 | 0 |
| GraphQL API calls | 0 | ~5-7 |
| Git protocol calls | 0 | 3 (fetch) |
| Wall clock time | ~50 seconds | ~6-7 seconds |
| **Speedup** | baseline | **~7-8x faster** |

### Handling non-cloned repos

Three options (not mutually exclusive):

1. **Shallow clone on demand**: `git clone --depth=1 --no-single-branch` for
   small repos (grovestark is 489 KB). Fast for first run.
2. **Fallback to API**: For repos not cloned locally, use GraphQL search
   as fallback. This is the simplest approach.
3. **Configurable repo list**: Let users specify which repos to clone in a
   config file. The tool auto-clones on first run.

Recommended: **Option 2** (fallback to API) with an optional `--repos-dir`
flag to specify where local clones live.

### GraphQL advantages beyond speed

The research revealed that switching from REST to GraphQL alone (without
local git) would provide significant speedups:

- **Batch PR details**: One GraphQL call replaces 3+ REST calls per PR
  (`get_merged_info` + `get_additions_deletions` + `check_review_activity`)
- **Combined search**: One GraphQL call for reviewed-by + commenter replaces
  2 `gh search` calls + N verification calls
- **Review data inline**: GraphQL returns reviews with PR details, eliminating
  separate review-checking calls

Even without local git, a pure GraphQL rewrite could reduce API calls from
~104 to ~10-15 and cut runtime from ~50s to ~15s.

---

## 6. Risks and Considerations

### Risks

1. **Stale data**: Local repos must be fetched before each run. If fetch
   fails (network issues), results will be incomplete. Mitigation: fall back
   to API on fetch failure.

2. **Missing repos**: Not all repos are cloned locally (78% coverage in
   this test). Mitigation: API fallback for missing repos.

3. **Author identity mismatch**: Git uses email-based author identification,
   while GitHub uses login-based. A user's git email must match their GitHub
   account. Most users configure this correctly, but it can cause false
   negatives. Mitigation: Could also search by email patterns.

4. **Repo configuration burden**: Users must configure which repos to search.
   Currently, the tool just searches by `--owner=ORG` with no repo setup.

### Mitigations

- Use `--author=USERNAME` in git log, which matches against the git author
  name field (typically the GitHub username for most devs).
- For email-based matching, git log supports `--author=email@example.com`.
- Keep the API fallback path for robustness.

---

## 7. Conclusions and Recommendations

### Primary recommendation: Hybrid approach with GraphQL

1. **Use local git for authored/contributed PR discovery** (Phase 1).
   This catches bot-authored PRs with user commits -- a real gap in the
   current approach.

2. **Use GraphQL for reviews, details, and enrichment** (Phases 2-3).
   This replaces ~100 REST calls with ~5-7 GraphQL calls.

3. **Keep API fallback for non-cloned repos.**

### Alternative: Pure GraphQL rewrite (no local git)

If the local git complexity is not worth it, a pure GraphQL rewrite would
still provide:
- ~3-4x speedup (50s -> ~15s)
- ~7-10x fewer API calls (104 -> ~10-15)
- No repo cloning/configuration needed

However, this would NOT fix the bot-authored PR gap.

### Implementation priority

1. **Quick win**: Replace REST calls with GraphQL batching (biggest
   speed improvement, lowest complexity).
2. **Medium effort**: Add local git commit discovery for authored PRs.
3. **Optional**: Auto-clone repos, configurable repo paths, email matching.
