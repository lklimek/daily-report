# Implementation Plan: daily_report.py Rewrite

This document describes a phased implementation plan for rewriting `daily_report.py`
to use a hybrid local-git + GraphQL approach. Each phase builds on the previous one
and can be tested independently.

---

## Phase 0: Preparation

Lay the groundwork for the rewrite by adding configuration support and a GraphQL
utility layer.

### Step 0.1: Create config file format and loader

**Description:** Define the YAML config schema and implement a loader that reads
`~/.config/daily-report/repos.yaml` (or a custom path). The loader should validate
required fields, expand `~` in paths, and provide sensible defaults.

**Files to create:**
- `config.py` -- Config dataclass and YAML loader

**Files to modify:**
- `daily_report.py` -- Import config module (no behavioral change yet)

**Dependencies:** None

**Complexity:** S

---

### Step 0.2: Add new CLI arguments

**Description:** Add `--config`, `--repos-dir`, `--git-email`, and `--no-local`
arguments to the argument parser. These flags are parsed but have no effect yet
(the underlying features will be implemented in later steps).

**Files to modify:**
- `daily_report.py` -- Add arguments to `argparse` setup

**Dependencies:** Step 0.1

**Complexity:** S

---

### Step 0.3: Write tests for config loading

**Description:** Write unit tests for config file parsing: valid configs, missing
fields, path expansion, auto-detection of org/name from remote URLs, and error cases.

**Files to create:**
- `tests/test_config.py`

**Dependencies:** Step 0.1

**Complexity:** S

---

### Step 0.4: Create GraphQL query builder/executor utility

**Description:** Implement a utility module for building and executing GraphQL
queries against GitHub's API. This module should:
- Execute queries via `gh api graphql`
- Handle response parsing and error extraction
- Implement retry with exponential backoff for rate limiting
- Support variable substitution
- Provide a batch query builder (alias-based approach for multiple resources)

**Files to create:**
- `graphql_client.py` -- GraphQL execution and query building utilities

**Files to modify:** None (standalone module for now)

**Dependencies:** None

**Complexity:** M

---

### Step 0.5: Write tests for GraphQL utility

**Description:** Unit tests for the GraphQL client: query construction, response
parsing, error handling, retry logic. Use mocked `gh` subprocess calls.

**Files to create:**
- `tests/test_graphql_client.py`

**Dependencies:** Step 0.4

**Complexity:** S

---

## Phase 1: GraphQL Migration (Quick Win)

Replace REST API calls with GraphQL batch queries. This phase does not introduce
local git functionality but provides a significant speedup (~50s to ~15s) and
validates the GraphQL infrastructure.

**Expected speedup: ~50s -> ~15s (3-4x improvement)**

### Step 1.1: Replace `get_merged_info` and `get_additions_deletions` with GraphQL batch

**Description:** Currently, each PR requires two separate REST calls to fetch its
state/merged_at and additions/deletions. Replace these with a single batch GraphQL
query that fetches details for all authored/contributed PRs at once.

The batch query uses aliases to fetch multiple PRs in one request:

```graphql
{
  pr_platform_42: repository(owner: "dashpay", name: "platform") {
    pullRequest(number: 42) {
      state mergedAt isDraft additions deletions author { login } title
    }
  }
  pr_platform_55: repository(owner: "dashpay", name: "platform") {
    pullRequest(number: 55) { ... }
  }
}
```

After this step, the enrichment loop in `main()` (Steps 5 & 6 in the current code)
should make a single GraphQL call instead of 2N REST calls.

**Files to modify:**
- `daily_report.py` -- Replace enrichment loop with GraphQL batch call

**Files to create:** None (uses `graphql_client.py` from Step 0.4)

**Dependencies:** Step 0.4

**Complexity:** M

---

### Step 1.2: Replace `check_review_activity` with GraphQL-based review checking

**Description:** Currently, each reviewed/commented PR candidate requires two REST
calls (one for reviews, one for comments) to verify the user's activity is within
the date range. Replace the two `gh search prs` calls (`--reviewed-by` and
`--commenter`) plus all `check_review_activity` verification calls with a single
GraphQL search query that returns reviews and comments inline.

The GraphQL search query fetches `reviews` and `comments` connections on each PR,
allowing date verification in Python without additional API calls.

**Files to modify:**
- `daily_report.py` -- Replace Step 2 (reviewed/commented search) and Step 4
  (review activity verification) with GraphQL search

**Dependencies:** Step 0.4

**Complexity:** M

---

### Step 1.3: Replace `get_requested_reviewers` with GraphQL

**Description:** Currently, each open authored PR requires a separate REST call to
fetch pending reviewers. Replace the "waiting for review" section with a single
GraphQL search that returns `reviewRequests` inline.

```graphql
{
  search(query: "org:dashpay author:USER state:open type:pr draft:false", ...) {
    nodes {
      ... on PullRequest {
        number title createdAt isDraft
        repository { name owner { login } }
        reviewRequests(first: 20) {
          nodes { requestedReviewer { ... on User { login } ... on Team { slug } } }
        }
      }
    }
  }
}
```

**Files to modify:**
- `daily_report.py` -- Replace Step 7 (waiting for review) with GraphQL

**Dependencies:** Step 0.4

**Complexity:** M

---

### Step 1.4: Replace `check_commits_for_user` with GraphQL batch

**Description:** Currently, each authored/contributed PR candidate requires a REST
call to fetch commits and verify the user authored commits within the date range.
Replace with a GraphQL batch that fetches commit data for all candidate PRs at once.

Note: This is the most complex Step in Phase 1 because `check_commits_for_user` is
called for both authored PR candidates (Step 1 in current code) and
contributed/reviewed candidates (Step 3). The GraphQL approach should batch all
these checks into one or two queries.

**Files to modify:**
- `daily_report.py` -- Replace commit checking in Steps 1 and 3 with GraphQL batch

**Dependencies:** Step 0.4

**Complexity:** L

---

### Step 1.5: Run existing tests, verify identical output

**Description:** Run the existing test suite and manually verify that the report
output is identical to the old REST-based approach. Compare outputs for the test
case: user=lklimek, 2026-02-03..2026-02-09.

Create a benchmark script that measures wall clock time and counts API calls.

**Files to create:**
- `tests/test_graphql_migration.py` -- Integration tests comparing old vs new output
- `benchmark.sh` -- Script to time the tool and count API calls

**Files to modify:** None

**Dependencies:** Steps 1.1, 1.2, 1.3, 1.4

**Complexity:** M

---

## Phase 2: Local Git Commit Discovery

Introduce local git repository scanning to replace API-based PR discovery for
cloned repos. This phase closes the coverage gap (bot-authored PRs with user
commits) and provides the biggest remaining speedup.

**Expected speedup: ~15s -> ~6-7s (2x additional improvement)**

### Step 2.1: Implement git repo discovery and configuration

**Description:** Implement the logic to:
1. Read repo list from config file
2. If `--repos-dir` is specified, scan the directory for git repos
3. For each repo, extract org/name from the git remote URL
4. Validate that repos exist and are accessible

**Files to create:**
- `git_repos.py` -- Repo discovery, remote URL parsing, configuration resolution

**Files to modify:**
- `daily_report.py` -- Integrate repo discovery into `main()`

**Dependencies:** Step 0.1

**Complexity:** M

---

### Step 2.2: Implement `git fetch --all` with timeout

**Description:** Implement a function that runs `git fetch --all --quiet` for each
configured repo with a 30-second timeout. Run fetches in parallel using
`concurrent.futures.ThreadPoolExecutor` or `asyncio`. Handle failures gracefully:
log a warning and continue with stale data.

**Files to modify:**
- `git_repos.py` -- Add fetch function

**Files to create:**
- `tests/test_git_repos.py` -- Tests for fetch (with mocked subprocess)

**Dependencies:** Step 2.1

**Complexity:** M

---

### Step 2.3: Implement `git log` based commit finder

**Description:** Implement a function that runs `git log` with the appropriate flags
to find commits by a given author within a date range. Parse the output into
structured commit objects (SHA, subject, author email, author date).

```python
@dataclass
class GitCommit:
    sha: str
    subject: str
    author_email: str
    author_date: str  # ISO format

def find_commits(repo_path, author, date_from, date_to) -> list[GitCommit]:
    ...
```

Handle deduplication of commits that appear on multiple branches.

**Files to create:**
- `git_log.py` -- Git log parser and commit finder

**Files to modify:** None

**Dependencies:** Step 2.1

**Complexity:** M

---

### Step 2.4: Implement PR number extraction from commit messages

**Description:** Implement regex-based extraction of PR numbers from commit subjects.
Pattern: `\(#(\d+)\)` matches squash-merge commit messages like
`feat: add feature X (#42)`.

```python
def extract_pr_numbers(commits: list[GitCommit]) -> tuple[
    dict[int, list[GitCommit]],   # pr_number -> commits (mapped)
    list[GitCommit],               # unmapped commits
]:
    ...
```

**Files to modify:**
- `git_log.py` -- Add PR extraction function

**Dependencies:** Step 2.3

**Complexity:** S

---

### Step 2.5: Implement GraphQL batch commit-to-PR mapping

**Description:** For commits without PR numbers in their messages, use the GraphQL
`associatedPullRequests` field to map commits to PRs in batch. Group commits by
repository and batch up to 20 per query.

```graphql
{
  repository(owner: "dashpay", name: "platform") {
    c0: object(expression: "SHA1") {
      ... on Commit { associatedPullRequests(first: 5) { nodes { number author { login } } } }
    }
    c1: object(expression: "SHA2") { ... }
  }
}
```

**Files to create:**
- `commit_mapper.py` -- Commit-to-PR mapping via GraphQL

**Files to modify:** None

**Dependencies:** Steps 0.4, 2.3

**Complexity:** M

---

### Step 2.6: Replace authored PR search with local git approach

**Description:** Replace the current authored PR discovery (two `gh search prs`
calls + commit verification) with the local git approach:

1. For each cloned repo: `git log` to find user's commits
2. Extract PR numbers from commit messages
3. GraphQL batch for unmapped commits
4. For non-cloned repos: fall back to GraphQL search

This is the core integration point where the git-based discovery replaces the
API-based discovery for authored PRs.

**Files to modify:**
- `daily_report.py` -- Replace Steps 1 and 3 (authored PR discovery) with
  local git pipeline

**Dependencies:** Steps 2.1, 2.2, 2.3, 2.4, 2.5

**Complexity:** L

---

### Step 2.7: Implement "contributed PR" detection

**Description:** A "contributed PR" is one where the user has commits but is not
the PR author. After mapping commits to PRs in Step 2.6, classify each PR:

- If PR author matches the target user: `role = "authored"`
- If PR author is different: `role = "contributed"`

The PR author can be obtained from:
- The GraphQL `associatedPullRequests` response (which includes `author { login }`)
- The Phase 3 enrichment batch query

**Files to modify:**
- `daily_report.py` -- Add contributed PR classification logic

**Dependencies:** Step 2.6

**Complexity:** S

---

### Step 2.8: Add API fallback for non-cloned repos

**Description:** For repos not in the config or `--repos-dir`, use a GraphQL search
query as fallback to discover authored PRs. This ensures the tool still works for
repos the user has not cloned.

The fallback query:

```graphql
{
  search(query: "org:dashpay author:USER updated:FROM..TO type:pr", ...) {
    nodes { ... on PullRequest { number repository { name owner { login } } } }
  }
}
```

Merge fallback results with local git results, deduplicating by (org, repo, pr#).

**Files to modify:**
- `daily_report.py` -- Add fallback search after local git discovery

**Dependencies:** Step 2.6

**Complexity:** M

---

### Step 2.9: Run tests, verify output includes previously missed PRs

**Description:** Run the full test suite and compare output against the known test
case. Verify that:

1. All PRs from the previous output are still present
2. PR #3075 (bot-authored with user commits) is now included
3. Performance target of ~6-7s is met

**Files to create:**
- `tests/test_local_git_integration.py` -- Integration tests for local git flow

**Files to modify:**
- `tests/test_graphql_migration.py` -- Update expected output to include new PRs

**Dependencies:** Steps 2.6, 2.7, 2.8

**Complexity:** M

---

## Phase 3: Polish

Final polish, documentation, and optional features.

### Step 3.1: Add `--no-local` flag for API-only mode

**Description:** When `--no-local` is passed, skip all local git operations and use
the GraphQL-only path (Phase 1 approach). This provides a fallback for environments
where local repos are not available or git fetch is problematic.

**Files to modify:**
- `daily_report.py` -- Add conditional logic gated on `--no-local`

**Dependencies:** Steps 1.5, 2.9

**Complexity:** S

---

### Step 3.2: Add auto-discovery of repos by scanning directory

**Description:** When `--repos-dir ~/git` is provided, automatically scan the
directory for git repositories with remotes matching the target organization.
This eliminates the need for an explicit config file in many setups.

**Files to modify:**
- `git_repos.py` -- Add directory scanning logic
- `daily_report.py` -- Wire up `--repos-dir` flag

**Dependencies:** Step 2.1

**Complexity:** S

---

### Step 3.3: Update README.md

**Description:** Update the project README to document:
- New CLI flags (`--config`, `--repos-dir`, `--git-email`, `--no-local`)
- Config file format and location
- Performance comparison (old vs new)
- Setup instructions for local git repos

**Files to modify:**
- `README.md`

**Dependencies:** Steps 2.9, 3.1

**Complexity:** S

---

### Step 3.4: Add new test cases for local git scenarios

**Description:** Add test cases specifically for local git features:
- Config file parsing edge cases
- Git remote URL parsing (SSH, HTTPS, various formats)
- Commit deduplication across branches
- PR number extraction from various commit message formats
- Handling of git fetch failures
- Fallback behavior for non-cloned repos
- Author identity mismatch scenarios

**Files to create:**
- `tests/test_git_log.py`
- `tests/test_commit_mapper.py`

**Files to modify:**
- `tests/test_config.py` -- Add edge case tests
- `tests/test_git_repos.py` -- Add edge case tests

**Dependencies:** Steps 2.3, 2.4, 2.5

**Complexity:** M

---

### Step 3.5: Performance benchmarks

**Description:** Create a benchmark script that:
- Runs the tool in all three modes (full hybrid, GraphQL-only, REST legacy)
- Measures wall clock time for each mode
- Counts API calls for each mode
- Reports results in a table format

Document the benchmark results and include them in the README.

**Files to create:**
- `benchmark.sh` -- Benchmark runner script

**Files to modify:**
- `README.md` -- Add performance comparison table

**Dependencies:** Steps 2.9, 3.1

**Complexity:** S

---

## Summary

### Dependency Graph

```
Phase 0 (Preparation)
  0.1 Config loader --------+----> 0.3 Config tests
  0.2 CLI args (needs 0.1) -+
  0.4 GraphQL client -------+----> 0.5 GraphQL tests
                            |
Phase 1 (GraphQL Migration) |
  1.1 PR details batch  <--+
  1.2 Review checking   <--+
  1.3 Waiting for review <--+
  1.4 Commit checking   <--+
  1.5 Verify output (needs 1.1-1.4)
                            |
Phase 2 (Local Git)         |
  2.1 Repo discovery (needs 0.1)
  2.2 Git fetch (needs 2.1) -----> 2.2 Tests
  2.3 Git log parser (needs 2.1)
  2.4 PR extraction (needs 2.3)
  2.5 GraphQL commit mapper (needs 0.4, 2.3)
  2.6 Replace authored search (needs 2.1-2.5) -- CORE INTEGRATION
  2.7 Contributed PR detection (needs 2.6)
  2.8 API fallback (needs 2.6)
  2.9 Verify output (needs 2.6-2.8)
                            |
Phase 3 (Polish)            |
  3.1 --no-local flag (needs 1.5, 2.9)
  3.2 Auto-discovery (needs 2.1)
  3.3 README update (needs 2.9, 3.1)
  3.4 Additional tests (needs 2.3-2.5)
  3.5 Benchmarks (needs 2.9, 3.1)
```

### Effort Estimates

| Step | Description | Complexity | Est. Effort |
|------|-------------|------------|-------------|
| 0.1  | Config file format and loader | S | 1-2 hours |
| 0.2  | Add new CLI arguments | S | 30 min |
| 0.3  | Config loading tests | S | 1 hour |
| 0.4  | GraphQL query builder/executor | M | 2-3 hours |
| 0.5  | GraphQL utility tests | S | 1 hour |
| 1.1  | Replace PR detail fetching with GraphQL batch | M | 2-3 hours |
| 1.2  | Replace review activity checking with GraphQL | M | 2-3 hours |
| 1.3  | Replace requested reviewers with GraphQL | M | 1-2 hours |
| 1.4  | Replace commit checking with GraphQL batch | L | 3-4 hours |
| 1.5  | Verify identical output | M | 2-3 hours |
| 2.1  | Git repo discovery and configuration | M | 2-3 hours |
| 2.2  | Git fetch with timeout and parallelism | M | 2-3 hours |
| 2.3  | Git log commit finder | M | 2-3 hours |
| 2.4  | PR number extraction from commit messages | S | 1 hour |
| 2.5  | GraphQL batch commit-to-PR mapping | M | 2-3 hours |
| 2.6  | Replace authored PR search with local git | L | 4-5 hours |
| 2.7  | Contributed PR detection | S | 1 hour |
| 2.8  | API fallback for non-cloned repos | M | 2-3 hours |
| 2.9  | Verify output with new PRs | M | 2-3 hours |
| 3.1  | --no-local flag | S | 30 min |
| 3.2  | Auto-discovery of repos | S | 1-2 hours |
| 3.3  | README update | S | 1 hour |
| 3.4  | Additional test cases | M | 2-3 hours |
| 3.5  | Performance benchmarks | S | 1-2 hours |

**Total estimated effort: ~40-55 hours**

### Milestones

| Milestone | Steps | Deliverable | Key Metric |
|-----------|-------|-------------|------------|
| M1: Foundation | 0.1-0.5 | Config loader + GraphQL client with tests | Infrastructure ready |
| M2: GraphQL quick win | 1.1-1.5 | Same output, 3-4x faster | ~15s runtime, ~10-15 API calls |
| M3: Local git | 2.1-2.9 | Bot-authored PR coverage, 7-8x faster | ~6-7s runtime, ~5-7 API calls |
| M4: Polish | 3.1-3.5 | Feature-complete with docs | README, benchmarks, full test suite |

### Risk Mitigation

| Risk | Mitigation |
|------|------------|
| GraphQL API behavior differs from REST | Phase 1 has explicit output comparison step (1.5) |
| Git author identity does not match GitHub login | Support `--git-email` flag, auto-detect from git config |
| Config file adds setup burden | Auto-discovery via `--repos-dir` requires no config file |
| GraphQL rate limiting | Exponential backoff retry logic built into GraphQL client |
| Large number of commits overwhelms batch query | Split into multiple queries (max ~25 commits per batch) |
| Phase 2 local git introduces regressions | `--no-local` flag preserves GraphQL-only mode as fallback |
