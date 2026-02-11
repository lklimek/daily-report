# Detailed Design: daily_report.py Rewrite

## 1. Overview

### Problem Statement

The current `daily_report.py` tool generates a daily GitHub PR activity report for a
given user and organization. It works correctly in most cases but suffers from two
critical issues:

1. **Performance**: A typical run takes approximately 50 seconds and makes ~104
   individual REST API calls via `gh`. Each PR requires multiple sequential calls
   (`get_merged_info`, `get_additions_deletions`, `check_review_activity`,
   `check_commits_for_user`, `get_requested_reviewers`), creating a long serial
   chain of HTTP round-trips.

2. **Coverage gap**: The tool discovers PRs exclusively through `gh search prs` with
   `--author`, `--reviewed-by`, and `--commenter` filters. This means it completely
   misses PRs where the user has commits but is not the PR author, reviewer, or
   commenter. A confirmed real-world example: PR #3075 on `platform` is authored by
   Copilot (a bot) but contains 11 commits by the target user -- the current tool
   cannot discover this PR at all.

### Proposed Solution

A hybrid local-git + GraphQL approach that:

- Uses **local git repositories** to discover commits authored by the user, then maps
  those commits to PRs. This catches bot-authored PRs with user commits.
- Uses **GitHub GraphQL API** instead of REST for all GitHub data fetching, enabling
  batch queries that replace dozens of individual REST calls.
- Falls back to **API-based discovery** for repositories that are not cloned locally.

### Goals

| Metric               | Current    | Target      |
|----------------------|------------|-------------|
| Wall clock time      | ~50s       | ~6-7s       |
| API calls            | ~104 REST  | ~5-7 GraphQL|
| PR coverage          | Misses bot-authored PRs with user commits | Complete coverage |
| Backward compat      | N/A        | Same CLI interface, same output format |

---

## 2. Architecture

The rewrite follows a three-phase pipeline architecture:

### Phase 1: Local Git Commit Discovery (~2 seconds)

Scan locally cloned repositories for commits authored by the target user within the
date range. Extract PR numbers from commit messages and use GraphQL batch queries to
map remaining commits to PRs.

### Phase 2: GraphQL Review Discovery (~2 seconds)

Use GitHub GraphQL search to find PRs where the user has review or comment activity.
This replaces the current `gh search prs --reviewed-by` and `--commenter` calls.

### Phase 3: GraphQL Batch Enrichment (~2 seconds)

Fetch detailed information (state, merged_at, additions/deletions, isDraft, author)
for all discovered PRs in a single batch GraphQL call. Separately fetch open authored
PRs with pending review requests.

### Data Flow Diagram

```
                    +------------------+
                    |   Configuration  |
                    |  (repos.yaml /   |
                    |   CLI flags)     |
                    +--------+---------+
                             |
              +--------------+--------------+
              |                             |
              v                             v
   +----------+-----------+      +----------+-----------+
   | Phase 1: Local Git   |      | Phase 2: GraphQL     |
   | Commit Discovery     |      | Review Discovery     |
   |                      |      |                      |
   | For each cloned repo:|      | GraphQL search:      |
   |  1. git fetch --all  |      |  reviewed-by:USER    |
   |  2. git log --remotes|      |  commenter:USER      |
   |  3. Extract PR #s    |      |  updated:FROM..TO    |
   |  4. GraphQL batch    |      |  org:ORG             |
   |     for unmapped     |      |                      |
   |                      |      | Verify review dates  |
   | For non-cloned repos:|      | are within range     |
   |  API fallback search |      |                      |
   +----------+-----------+      +----------+-----------+
              |                             |
              |  Set of (org, repo, pr#)    |  Set of (org, repo, pr#)
              |  with role: authored /      |  with role: reviewed
              |         contributed         |
              |                             |
              +-------------+---------------+
                            |
                            v
                 +----------+-----------+
                 | Deduplication &      |
                 | Role Assignment      |
                 |                      |
                 | Union of all PR keys |
                 | with classification: |
                 |  - authored          |
                 |  - contributed       |
                 |  - reviewed          |
                 +----------+-----------+
                            |
                            v
                 +----------+-----------+
                 | Phase 3: GraphQL     |
                 | Batch Enrichment     |
                 |                      |
                 | Single batch query:  |
                 |  - state, merged_at  |
                 |  - isDraft           |
                 |  - additions/del     |
                 |  - author login      |
                 |  - title             |
                 |                      |
                 | Separate query:      |
                 |  - Open authored PRs |
                 |  - Pending reviewers |
                 +----------+-----------+
                            |
                            v
                 +----------+-----------+
                 | Report Generation    |
                 |                      |
                 | Same output format   |
                 | as current tool      |
                 +----------------------+
```

### Fallback Paths

For repositories not cloned locally, the tool falls back to GraphQL-based PR search
(similar to current behavior but using GraphQL instead of REST). This ensures the
tool works even if zero repos are cloned, degrading gracefully to an API-only mode.

---

## 3. Phase 1: Local Git Commit Discovery

### 3.1 Configuration

Repos are configured via a YAML file at `~/.config/daily-report/repos.yaml` (or a
path specified by `--config`):

```yaml
# ~/.config/daily-report/repos.yaml

# Default organization for all repos (can be overridden per repo)
default_org: dashpay

# Default git author identity (GitHub username or email)
# If omitted, auto-detected from gh auth status
default_user: lklimek

# Optional: directory to scan for repos (alternative to explicit list)
# repos_dir: ~/git

# Explicit repo list
repos:
  - path: ~/git/platform
    org: dashpay          # optional if default_org is set
    name: platform        # optional: auto-detected from remote URL

  - path: ~/git/tenderdash
    name: tenderdash

  - path: ~/git/dash-evo-tool
    name: dash-evo-tool

# Bots to exclude from reviewer lists
excluded_bots:
  - coderabbitai
  - copilot-pull-request-reviewer
  - github-actions
  - copilot-swe-agent
```

If `--repos-dir` is provided on the command line, the tool scans that directory for
git repositories with remotes matching the target organization, bypassing the config
file repo list.

Auto-detection of `org` and `name` from the git remote URL:

```
origin  git@github.com:dashpay/platform.git (fetch)
  --> org=dashpay, name=platform

origin  https://github.com/dashevo/platform.git (fetch)
  --> org=dashevo, name=platform
```

### 3.2 Git Fetch for Freshness

Before scanning each repo, run `git fetch --all` to ensure remote branches are
up-to-date:

```bash
git -C /path/to/repo fetch --all --quiet
```

- **Timeout**: 30 seconds per repo. If fetch times out, log a warning and proceed
  with stale data.
- **Error handling**: If fetch fails (network error, auth error), log a warning and
  either use stale local data or fall back to API for that repo.
- **Parallelism**: Fetch all repos concurrently using `asyncio.create_subprocess_exec`
  or `concurrent.futures.ThreadPoolExecutor`.

Benchmark from research: fetching 3 repos takes ~1.85s total (sequential). With
parallelism, this drops to ~0.75s.

### 3.3 Git Log for Commit Discovery

For each configured repo, run:

```bash
git -C /path/to/repo log \
    --remotes \
    --all \
    --author="USER" \
    --no-merges \
    --after="DATE_FROM_MINUS_1" \
    --before="DATE_TO_PLUS_1" \
    --format="%H|%s|%ae|%aI"
```

Flags explained:

| Flag | Purpose |
|------|---------|
| `--remotes` | Search all remote-tracking branches |
| `--all` | Include all refs (catches commits on any branch) |
| `--author="USER"` | Filter by author name or email |
| `--no-merges` | Exclude merge commits (they map to the same PRs as their parents) |
| `--after` / `--before` | Date range filter (using day-expanded range to handle timezone edge cases) |
| `--format` | Custom output: hash, subject, author email, author date ISO |

The date range is expanded by one day on each side (`DATE_FROM - 1 day` to
`DATE_TO + 1 day`) to account for timezone differences between the git author
date and the calendar date. The exact date filtering is then done in Python using
the ISO date from the `%aI` format field.

### 3.4 Commit Deduplication

The same commit can appear on multiple branches (e.g., a commit on `feature-branch`
that has been merged into `v3.1-dev`). Deduplicate by commit SHA before processing.

```python
seen_shas = set()
unique_commits = []
for commit in all_commits:
    if commit.sha not in seen_shas:
        seen_shas.add(commit.sha)
        unique_commits.append(commit)
```

### 3.5 PR Number Extraction from Commit Messages

Squash-merged commits contain the PR number in the commit message, following the
pattern `(#NNN)`. Extract these with a regex:

```python
import re
PR_PATTERN = re.compile(r'\(#(\d+)\)')

def extract_pr_number(commit_subject: str) -> int | None:
    match = PR_PATTERN.search(commit_subject)
    return int(match.group(1)) if match else None
```

Research showed that ~38% of commits in the test case had PR references in the
message. These commits require zero API calls to map.

### 3.6 GraphQL Batch for Unmapped Commits

Commits without PR numbers in their messages need GraphQL lookup. Use the
`associatedPullRequests` field on `Commit` objects:

```graphql
{
  repository(owner: "dashpay", name: "platform") {
    c0: object(expression: "abc123") {
      ... on Commit {
        associatedPullRequests(first: 3) {
          nodes { number title author { login } }
        }
      }
    }
    c1: object(expression: "def456") {
      ... on Commit {
        associatedPullRequests(first: 3) {
          nodes { number title author { login } }
        }
      }
    }
  }
}
```

Group commits by repository and batch up to 20 commits per query. If a repo has
more than 20 unmapped commits, split into multiple queries.

Research benchmark: 13 commits mapped in ~1.5s via a single GraphQL call, vs ~8s
with individual REST calls.

### 3.7 Output

Phase 1 produces a set of tuples:

```python
@dataclass
class DiscoveredPR:
    org: str
    repo: str
    pr_number: int
    role: Literal["authored", "contributed"]
    # "authored" = user is PR author
    # "contributed" = user has commits but is not the PR author
```

The `role` classification is determined by comparing the PR author (from the GraphQL
`associatedPullRequests` response or from the commit message context) against the
target user.

### 3.8 Edge Cases

**Author identity mismatch**: Git uses name/email for author identification, while
GitHub uses login. Most developers configure their git `user.name` to match their
GitHub username, but this is not guaranteed. Mitigations:

- Support a `--git-email` CLI flag to specify the email to search for.
- Auto-detect from `git config user.email` in the first configured repo.
- Fall back to searching by both username and email.

**Cherry-picks**: A cherry-picked commit has a different SHA than the original. Both
will appear in `git log` and both will map to their respective PRs via
`associatedPullRequests`. This is correct behavior -- if the user cherry-picked a
commit into another branch/PR, both PRs should appear in the report.

**Commits not in any PR**: Commits pushed to a branch but not yet associated with a
PR will return empty `associatedPullRequests`. These are silently ignored (they
represent work not yet submitted for review).

---

## 4. Phase 2: Review Discovery

### 4.1 GraphQL Search Query

Replace the two current `gh search prs` calls (`--reviewed-by` and `--commenter`)
with a GraphQL-based search:

```graphql
{
  reviewed: search(
    query: "org:dashpay reviewed-by:lklimek updated:2026-02-03..2026-02-09 type:pr"
    type: ISSUE
    first: 100
  ) {
    nodes {
      ... on PullRequest {
        number
        title
        state
        isDraft
        url
        updatedAt
        author { login }
        repository { name owner { login } }
        reviews(first: 100) {
          nodes {
            author { login }
            submittedAt
            state
          }
        }
      }
    }
  }

  commented: search(
    query: "org:dashpay commenter:lklimek updated:2026-02-03..2026-02-09 type:pr"
    type: ISSUE
    first: 100
  ) {
    nodes {
      ... on PullRequest {
        number
        title
        state
        isDraft
        url
        updatedAt
        author { login }
        repository { name owner { login } }
        comments(first: 100) {
          nodes {
            author { login }
            createdAt
          }
        }
      }
    }
  }
}
```

This single GraphQL request replaces:
- `gh search prs --reviewed-by=USER --updated=DATE --owner=ORG`
- `gh search prs --commenter=USER --updated=DATE --owner=ORG`
- All subsequent `check_review_activity` REST calls (reviews and comments data is
  already in the response)

### 4.2 Date Filtering

The `updated:DATE_FROM..DATE_TO` qualifier in the search query provides coarse
filtering. However, a PR may have been updated within the range for reasons
unrelated to the user's activity (e.g., another user pushed a commit).

After receiving search results, verify that the user's actual review/comment activity
falls within the date range:

```python
def has_review_in_range(pr_node, user, date_from, date_to):
    """Check if user has review or comment activity within date range."""
    # Check reviews
    for review in pr_node.get("reviews", {}).get("nodes", []):
        review_author = (review.get("author") or {}).get("login", "")
        if review_author == user:
            submitted = review.get("submittedAt", "")[:10]
            if date_from <= submitted <= date_to:
                return True

    # Check comments
    for comment in pr_node.get("comments", {}).get("nodes", []):
        comment_author = (comment.get("author") or {}).get("login", "")
        if comment_author == user:
            created = comment.get("createdAt", "")[:10]
            if date_from <= created <= date_to:
                return True

    return False
```

This is functionally equivalent to the current `check_review_activity` function but
operates on data already present in the GraphQL response, requiring no additional
API calls.

### 4.3 Output

Phase 2 produces a set of `DiscoveredPR` tuples with `role="reviewed"`.

### 4.4 Deduplication Against Phase 1

PRs discovered in Phase 2 that were already found in Phase 1 are deduplicated. If a
PR appears in both phases, the Phase 1 role takes precedence (authored/contributed
over reviewed), since the report categorizes PRs by their primary relationship to
the user.

---

## 5. Phase 3: PR Detail Enrichment

### 5.1 Batch PR Detail Query

All PRs discovered in Phases 1 and 2 need detailed information for the report. Fetch
everything in a single batch GraphQL query:

```graphql
{
  r0_42: repository(owner: "dashpay", name: "platform") {
    pullRequest(number: 42) {
      number
      title
      state
      isDraft
      mergedAt
      additions
      deletions
      author { login }
      url
    }
  }
  r0_55: repository(owner: "dashpay", name: "platform") {
    pullRequest(number: 55) {
      number
      title
      state
      isDraft
      mergedAt
      additions
      deletions
      author { login }
      url
    }
  }
  r1_12: repository(owner: "dashpay", name: "tenderdash") {
    pullRequest(number: 12) {
      ...
    }
  }
}
```

Each PR is aliased with a unique key (e.g., `r0_42` for repo index 0, PR #42) to
allow multiple PRs to be fetched in a single query.

Note: If many of the PR details were already fetched in Phase 2 (for reviewed PRs),
those can be reused and excluded from this batch to reduce query size.

### 5.2 Waiting for Review (Open Authored PRs with Pending Reviewers)

Fetch open, non-draft PRs authored by the user that have pending review requests:

```graphql
{
  search(
    query: "org:dashpay author:lklimek state:open type:pr draft:false"
    type: ISSUE
    first: 50
  ) {
    nodes {
      ... on PullRequest {
        number
        title
        isDraft
        createdAt
        repository { name owner { login } }
        reviewRequests(first: 20) {
          nodes {
            requestedReviewer {
              ... on User { login }
              ... on Team { name slug }
            }
          }
        }
      }
    }
  }
}
```

This replaces:
- `gh search prs --author=USER --owner=ORG --state=open`
- All subsequent `get_requested_reviewers` REST calls per PR

Filter out bots from the reviewer list in Python (same logic as current
`get_requested_reviewers`).

### 5.3 Pagination Considerations

GitHub GraphQL API limits:
- Maximum 100 nodes per connection (e.g., `first: 100`)
- Maximum query complexity of 500,000 points
- Rate limit: 5,000 points per hour

For typical daily reports, pagination is unlikely to be needed (most users don't
touch 100+ PRs in a single day/week). However, implement cursor-based pagination
as a safety measure:

```graphql
search(query: "...", type: ISSUE, first: 100, after: "cursor_value") {
  pageInfo { hasNextPage endCursor }
  nodes { ... }
}
```

If `hasNextPage` is true, make a follow-up query with `after: endCursor`.

---

## 6. Configuration

### 6.1 Config File Format

YAML format at `~/.config/daily-report/repos.yaml`:

```yaml
# Required: list of locally cloned repos
repos:
  - path: ~/git/platform
    org: dashpay
    name: platform

  - path: ~/git/tenderdash
    # org and name auto-detected from git remote

  - path: ~/git/dash-evo-tool

# Optional: default organization (used when repo.org is omitted)
default_org: dashpay

# Optional: default user (overrides gh auth user)
default_user: lklimek

# Optional: additional git email addresses for commit matching
git_emails:
  - user@example.com
  - user@company.com

# Optional: bots to exclude from reviewer lists
excluded_bots:
  - coderabbitai
  - copilot-pull-request-reviewer
  - github-actions
  - copilot-swe-agent
```

### 6.2 Required Fields

- `repos`: list of repo objects, each with at least `path`

### 6.3 Optional Fields

- `default_org`: fallback organization for repos without explicit `org`
- `default_user`: override for the GitHub username
- `git_emails`: additional email addresses for git author matching
- `excluded_bots`: bot usernames to filter from reviewer lists
- `repos_dir`: directory to scan for repos (alternative to explicit `repos` list)

### 6.4 CLI Flags

New flags (additive, do not break existing interface):

| Flag | Description |
|------|-------------|
| `--config PATH` | Path to config file (default: `~/.config/daily-report/repos.yaml`) |
| `--repos-dir PATH` | Scan directory for repos (overrides config file repo list) |
| `--git-email EMAIL` | Additional git email for author matching |
| `--no-local` | Disable local git, use API-only mode (current behavior) |

Existing flags remain unchanged:

| Flag | Description |
|------|-------------|
| `--org ORG` | GitHub organization (default: `dashpay`) |
| `--user USER` | GitHub username (default: authenticated user) |
| `--date DATE` | Single date in YYYY-MM-DD |
| `--from DATE` | Start date |
| `--to DATE` | End date |

### 6.5 Auto-Discovery

When `--repos-dir` is provided (e.g., `--repos-dir ~/git`), the tool:

1. Scans all immediate subdirectories for `.git/` directories
2. For each git repo found, reads the `origin` remote URL
3. Extracts org/name from the URL
4. Includes repos whose org matches `--org`
5. Skips repos with no matching remote

This provides zero-configuration setup for users who keep all their repos in one
directory.

---

## 7. Error Handling & Fallback

### 7.1 Git Fetch Failure

```
Scenario: git fetch --all fails for a repo (network error, auth error, timeout)
Action:  Log warning to stderr, proceed with stale local data
         If local data is too old or empty, fall back to API for that repo
```

### 7.2 Non-Cloned Repo

```
Scenario: User's activity spans repos not in the config (e.g., grovestark)
Action:  Phase 2 (review discovery) covers all repos via GraphQL search
         For authored PRs on non-cloned repos, rely on GraphQL search fallback
         This fallback uses the same search as current behavior but via GraphQL
```

The fallback GraphQL search for authored PRs on non-cloned repos:

```graphql
{
  search(
    query: "org:dashpay author:USER updated:DATE_FROM..DATE_TO type:pr"
    type: ISSUE
    first: 100
  ) {
    nodes {
      ... on PullRequest {
        number
        repository { name owner { login } }
      }
    }
  }
}
```

This catches authored PRs on repos not cloned locally, though it still cannot catch
bot-authored PRs with user commits on non-cloned repos (that gap remains for
non-cloned repos only).

### 7.3 GraphQL Rate Limiting

GitHub's GraphQL API returns HTTP 200 with an `errors` array when rate-limited:

```json
{
  "errors": [
    {
      "type": "RATE_LIMITED",
      "message": "API rate limit exceeded"
    }
  ]
}
```

Handle with exponential backoff:

```python
import time

def graphql_with_retry(query, variables=None, max_retries=3):
    for attempt in range(max_retries):
        result = execute_graphql(query, variables)
        if not is_rate_limited(result):
            return result
        wait = 2 ** attempt  # 1s, 2s, 4s
        print(f"Rate limited, retrying in {wait}s...", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError("GraphQL rate limit exceeded after retries")
```

### 7.4 Author Identity Mismatch

If no commits are found for a user via `--author=USERNAME`, try:

1. Read `git config user.email` from the repo
2. Try `--author=EMAIL`
3. If `--git-email` flag was provided, try those emails
4. Log a warning if no commits found despite API showing activity

---

## 8. Data Flow Diagram

```
+------------------------------------------------------------------+
|                         Configuration                            |
|  repos.yaml / --repos-dir / --config / CLI flags                 |
+------------------------------------------------------------------+
         |                    |                    |
         v                    v                    v
   Cloned repos list    GitHub org/user       Date range
         |                    |                    |
         v                    |                    |
+-------------------+         |                    |
| Phase 1: Git      |         |                    |
|                   |         |                    |
| For each repo:    |         |                    |
|  git fetch --all  |         |                    |
|  git log ...      |         |                    |
|  Parse commits    |         |                    |
|                   |         |                    |
| Commit msgs with  |         |                    |
| PR# -> direct map |         |                    |
|                   |         |                    |
| Unmapped commits  |         |                    |
| -> GraphQL batch  |         |                    |
|    associatedPRs  |         |                    |
|                   |         |                    |
| API fallback for  |<--------+--------------------+
| non-cloned repos  |
+--------+----------+
         |
         | authored_prs: Set[(org, repo, pr#, role)]
         |
         v
+-------------------+         +-------------------+
| Deduplication     |<--------|  Phase 2: GraphQL |
|                   |         |  Review Discovery |
| Merge Phase 1 +   |         |                   |
| Phase 2 results   |         | GraphQL search:   |
| Assign roles:     |         |  reviewed-by:USER |
|  authored >       |         |  commenter:USER   |
|  contributed >    |         |  updated:RANGE    |
|  reviewed         |         |                   |
+--------+----------+         | Verify dates in   |
         |                    | range from inline  |
         |                    | review/comment data|
         | all_prs:           +-------------------+
         | Set[(org, repo, pr#, role)]
         |
         v
+-------------------+
| Phase 3: GraphQL  |
| Batch Enrichment  |
|                   |
| Query 1: PR       |
| details for all   |
| discovered PRs    |
|  - state          |
|  - mergedAt       |
|  - isDraft        |
|  - additions/del  |
|  - author login   |
|  - title          |
|                   |
| Query 2: Open     |
| authored PRs with |
| pending reviewers |
|  - reviewRequests |
+--------+----------+
         |
         v
+-------------------+
| Report Generation |
|                   |
| Same markdown     |
| output format as  |
| current tool      |
+-------------------+
```

---

## 9. GraphQL Query Examples

### 9.1 Commit-to-PR Batch Mapping

Maps multiple commits from a single repository to their associated PRs:

```graphql
query CommitToPR($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    c0: object(expression: "a1b2c3d4e5f6") {
      ... on Commit {
        oid
        associatedPullRequests(first: 5) {
          nodes {
            number
            title
            state
            author { login }
          }
        }
      }
    }
    c1: object(expression: "b2c3d4e5f6a7") {
      ... on Commit {
        oid
        associatedPullRequests(first: 5) {
          nodes {
            number
            title
            state
            author { login }
          }
        }
      }
    }
    c2: object(expression: "c3d4e5f6a7b8") {
      ... on Commit {
        oid
        associatedPullRequests(first: 5) {
          nodes {
            number
            title
            state
            author { login }
          }
        }
      }
    }
    # ... up to ~20-25 commits per query (stay under complexity limit)
  }
}
```

**Notes:**
- Use field aliases (`c0`, `c1`, ...) because GraphQL does not allow duplicate field
  names at the same level.
- `first: 5` on `associatedPullRequests` is sufficient; a single commit rarely
  belongs to more than 1-2 PRs.
- If a repository has more than ~25 unmapped commits, split into multiple queries.
- The `oid` field is included so we can correlate responses back to the original
  commit.

### 9.2 Review/Commenter Search

Combined search for PRs where the user has review or comment activity:

```graphql
query ReviewDiscovery($reviewQuery: String!, $commentQuery: String!) {
  reviewed: search(query: $reviewQuery, type: ISSUE, first: 100) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        title
        state
        isDraft
        url
        updatedAt
        author { login }
        repository {
          name
          owner { login }
        }
        reviews(first: 100) {
          nodes {
            author { login }
            submittedAt
            state
          }
        }
      }
    }
  }

  commented: search(query: $commentQuery, type: ISSUE, first: 100) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        title
        state
        isDraft
        url
        updatedAt
        author { login }
        repository {
          name
          owner { login }
        }
        comments(first: 100) {
          nodes {
            author { login }
            createdAt
          }
        }
      }
    }
  }
}
```

Variables:

```json
{
  "reviewQuery": "org:dashpay reviewed-by:lklimek updated:2026-02-03..2026-02-09 type:pr",
  "commentQuery": "org:dashpay commenter:lklimek updated:2026-02-03..2026-02-09 type:pr"
}
```

### 9.3 PR Detail Batch Fetch

Batch fetch details for all discovered PRs across multiple repositories:

```graphql
query PRDetails {
  pr_platform_3042: repository(owner: "dashpay", name: "platform") {
    pullRequest(number: 3042) {
      number
      title
      state
      isDraft
      mergedAt
      additions
      deletions
      author { login }
      url
    }
  }
  pr_platform_3075: repository(owner: "dashpay", name: "platform") {
    pullRequest(number: 3075) {
      number
      title
      state
      isDraft
      mergedAt
      additions
      deletions
      author { login }
      url
    }
  }
  pr_tenderdash_987: repository(owner: "dashpay", name: "tenderdash") {
    pullRequest(number: 987) {
      number
      title
      state
      isDraft
      mergedAt
      additions
      deletions
      author { login }
      url
    }
  }
}
```

**Notes:**
- Aliases encode the repo name and PR number for easy response parsing.
- For PRs already fetched in Phase 2 with sufficient detail, skip them here.
- `additions` and `deletions` are only needed for open/draft PRs in the report, but
  fetching them for all PRs in a single batch is cheaper than conditional queries.

### 9.4 Open PRs with Requested Reviewers

Fetch the user's open PRs along with pending review requests:

```graphql
query WaitingForReview($query: String!) {
  search(query: $query, type: ISSUE, first: 50) {
    nodes {
      ... on PullRequest {
        number
        title
        isDraft
        createdAt
        url
        repository {
          name
          owner { login }
        }
        reviewRequests(first: 20) {
          nodes {
            requestedReviewer {
              ... on User { login }
              ... on Team { name slug }
              ... on Mannequin { login }
            }
          }
        }
      }
    }
  }
}
```

Variables:

```json
{
  "query": "org:dashpay author:lklimek state:open type:pr draft:false"
}
```

Post-processing in Python:

```python
def extract_reviewers(pr_node, user, excluded_bots):
    """Extract pending reviewers, excluding bots and the user."""
    reviewers = []
    for req in pr_node.get("reviewRequests", {}).get("nodes", []):
        reviewer = req.get("requestedReviewer", {})
        login = reviewer.get("login") or reviewer.get("slug") or ""
        if login and login not in excluded_bots and login != user:
            reviewers.append(login)
    return reviewers
```

---

## 10. Migration Strategy

### 10.1 Backward Compatibility

The rewrite must maintain full backward compatibility:

- **Same CLI interface**: All existing flags (`--org`, `--user`, `--date`, `--from`,
  `--to`) continue to work with identical semantics.
- **Same output format**: The markdown report format remains identical. Existing
  users/scripts parsing the output will not break.
- **Same default behavior**: Without a config file or `--repos-dir`, the tool works
  with API-only mode (though using GraphQL instead of REST for speed).

### 10.2 New Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--config PATH` | Path to repos config file | `~/.config/daily-report/repos.yaml` |
| `--repos-dir PATH` | Scan directory for repos | None |
| `--git-email EMAIL` | Additional git email for matching | None |
| `--no-local` | Force API-only mode (skip local git) | False |

### 10.3 Graceful Degradation

The tool operates in three modes depending on configuration:

1. **Full hybrid mode** (config file or `--repos-dir` present): Local git + GraphQL.
   Maximum speed and coverage.

2. **GraphQL-only mode** (no config, no repos-dir, or `--no-local`): Pure GraphQL
   API. Faster than current REST approach (~15s vs ~50s) but does not catch
   bot-authored PRs with user commits.

3. **Mixed mode** (some repos cloned, some not): Local git for cloned repos, GraphQL
   fallback for others. Best effort coverage.

### 10.4 Testing Strategy

- Run both old and new implementations on the same inputs and compare output.
- The test case from research (user=lklimek, 2026-02-03..2026-02-09) serves as the
  baseline. The new implementation should produce identical output plus the
  previously missed PR #3075.
- Performance benchmarks: measure wall clock time and API call count.
