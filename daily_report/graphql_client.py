"""GraphQL client for GitHub API via gh CLI.

Provides query builders and executors for batching GitHub GraphQL API calls
used by the daily report tool. All queries are executed via `gh api graphql`.
"""

import json
import subprocess
import sys
import time
from typing import Optional


def graphql_query(query: str, variables: Optional[dict] = None) -> dict:
    """Execute a GraphQL query via gh api graphql and return the data dict.

    Args:
        query: The GraphQL query string.
        variables: Optional dict of variables to pass to the query.

    Returns:
        The 'data' dict from the GraphQL response.

    Raises:
        RuntimeError: If the response contains non-rate-limit errors or
            the gh command fails.
    """
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    if variables:
        for key, value in variables.items():
            cmd.extend(["-f", f"{key}={value}"])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        print("Error: gh CLI is not installed.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"gh api graphql failed:\n{e.stderr}"
        ) from e

    response = json.loads(result.stdout)

    errors = response.get("errors")
    if errors:
        # Check if all errors are rate-limit errors
        rate_limited = all(
            err.get("type") == "RATE_LIMITED" for err in errors
        )
        if rate_limited:
            raise _RateLimitError(errors[0].get("message", "Rate limited"))
        # Non-rate-limit errors are fatal
        messages = "; ".join(err.get("message", str(err)) for err in errors)
        raise RuntimeError(f"GraphQL errors: {messages}")

    return response.get("data", {})


class _RateLimitError(RuntimeError):
    """Internal error raised when GitHub returns RATE_LIMITED."""


def graphql_with_retry(
    query: str,
    variables: Optional[dict] = None,
    max_retries: int = 3,
) -> dict:
    """Execute a GraphQL query with retry on rate limiting.

    Retries with exponential backoff (1s, 2s, 4s) when the API returns
    RATE_LIMITED errors.

    Args:
        query: The GraphQL query string.
        variables: Optional dict of variables.
        max_retries: Maximum number of attempts (default 3).

    Returns:
        The 'data' dict from the GraphQL response.

    Raises:
        RuntimeError: After exhausting retries or on non-rate-limit errors.
    """
    for attempt in range(max_retries):
        try:
            return graphql_query(query, variables)
        except _RateLimitError:
            if attempt == max_retries - 1:
                raise RuntimeError(
                    "GraphQL rate limit exceeded after retries"
                )
            wait = 2 ** attempt  # 1s, 2s, 4s
            print(
                f"Rate limited, retrying in {wait}s...",
                file=sys.stderr,
            )
            time.sleep(wait)
    # Should not reach here, but satisfy type checker
    raise RuntimeError("GraphQL rate limit exceeded after retries")


# ---------------------------------------------------------------------------
# Query builders and response parsers
# ---------------------------------------------------------------------------

_PR_DETAIL_FIELDS = """\
      number
      title
      state
      isDraft
      mergedAt
      additions
      deletions
      author { login }
      url"""


def build_pr_details_query(prs: list[tuple[str, str, int]]) -> str:
    """Build a batch GraphQL query to fetch details for multiple PRs.

    Args:
        prs: List of (org, repo, pr_number) tuples.

    Returns:
        A GraphQL query string with index-based aliases (pr_0, pr_1, ...).
    """
    fragments = []
    for i, (org, repo, number) in enumerate(prs):
        fragments.append(
            f'  pr_{i}: repository(owner: "{org}", name: "{repo}") {{\n'
            f"    pullRequest(number: {number}) {{\n"
            f"{_PR_DETAIL_FIELDS}\n"
            f"    }}\n"
            f"  }}"
        )
    return "{\n" + "\n".join(fragments) + "\n}"


def parse_pr_details_response(
    data: dict,
    prs: list[tuple[str, str, int]],
) -> dict[tuple[str, str, int], dict]:
    """Parse the index-based PR details response.

    Args:
        data: The 'data' dict from a build_pr_details_query response.
        prs: The original list of (org, repo, number) tuples passed to
            build_pr_details_query (used to correlate by index).

    Returns:
        Dict mapping (org, repo, number) to PR detail dicts.
    """
    result: dict[tuple[str, str, int], dict] = {}
    for i, key in enumerate(prs):
        alias = f"pr_{i}"
        repo_data = data.get(alias)
        if repo_data is None:
            continue
        pr_info = repo_data.get("pullRequest")
        if pr_info is None:
            continue
        result[key] = pr_info
    return result


def build_commit_to_pr_query(
    org: str, repo: str, shas: list[str]
) -> str:
    """Build a batch GraphQL query to map commits to PRs.

    Args:
        org: Repository owner/organization.
        repo: Repository name.
        shas: List of commit SHAs (max ~25 per query).

    Returns:
        A GraphQL query string.
    """
    fragments = []
    for i, sha in enumerate(shas[:25]):
        fragments.append(
            f'    c{i}: object(expression: "{sha}") {{\n'
            f"      ... on Commit {{\n"
            f"        oid\n"
            f"        associatedPullRequests(first: 5) {{\n"
            f"          nodes {{\n"
            f"            number\n"
            f"            title\n"
            f"            author {{ login }}\n"
            f"          }}\n"
            f"        }}\n"
            f"      }}\n"
            f"    }}"
        )
    return (
        "{\n"
        f'  repository(owner: "{org}", name: "{repo}") {{\n'
        + "\n".join(fragments)
        + "\n  }\n}"
    )


def parse_commit_to_pr_response(data: dict) -> dict[str, list[dict]]:
    """Parse the commit-to-PR batch response.

    Args:
        data: The 'data' dict from a build_commit_to_pr_query response.

    Returns:
        Dict mapping commit SHA to list of associated PR dicts.
        Each PR dict has keys: number, title, author (dict with login).
    """
    result: dict[str, list[dict]] = {}
    repo_data = data.get("repository")
    if not repo_data:
        return result

    for key, obj in repo_data.items():
        if not key.startswith("c") or obj is None:
            continue
        oid = obj.get("oid", "")
        prs = (
            obj.get("associatedPullRequests", {}).get("nodes", [])
        )
        if oid:
            result[oid] = prs
    return result


def build_review_search_query(
    org: str, user: str, date_from: str, date_to: str
) -> tuple[str, dict]:
    """Build a GraphQL search query for review/comment activity.

    Args:
        org: GitHub organization.
        user: GitHub username.
        date_from: Start date (YYYY-MM-DD).
        date_to: End date (YYYY-MM-DD).

    Returns:
        Tuple of (query_string, variables_dict).
    """
    query = """\
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
}"""
    variables = {
        "reviewQuery": (
            f"org:{org} reviewed-by:{user} "
            f"updated:{date_from}..{date_to} type:pr"
        ),
        "commentQuery": (
            f"org:{org} commenter:{user} "
            f"updated:{date_from}..{date_to} type:pr"
        ),
    }
    return query, variables


def build_waiting_for_review_query(
    org: str, user: str
) -> tuple[str, dict]:
    """Build a GraphQL query for open PRs awaiting review.

    Searches for open, non-draft PRs authored by the user that have
    pending review requests.

    Args:
        org: GitHub organization.
        user: GitHub username.

    Returns:
        Tuple of (query_string, variables_dict).
    """
    query = """\
query WaitingForReview($searchQuery: String!) {
  search(query: $searchQuery, type: ISSUE, first: 50) {
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
            }
          }
        }
      }
    }
  }
}"""
    variables = {
        "searchQuery": (
            f"org:{org} author:{user} state:open type:pr draft:false"
        ),
    }
    return query, variables


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_alias(name: str) -> str:
    """Convert a repo name to a valid GraphQL alias component.

    GraphQL aliases must match /[_A-Za-z][_0-9A-Za-z]*/. Replace
    hyphens and other invalid characters with underscores.
    """
    return name.replace("-", "_").replace(".", "_")


def _extract_org_from_url(url: str) -> str:
    """Extract the organization from a GitHub PR URL.

    Example: https://github.com/dashpay/platform/pull/42 -> dashpay
    """
    # URL format: https://github.com/{org}/{repo}/pull/{number}
    parts = url.split("/")
    try:
        github_idx = parts.index("github.com")
        return parts[github_idx + 1]
    except (ValueError, IndexError):
        return ""
