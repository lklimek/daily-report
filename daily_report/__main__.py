#!/usr/bin/env python3
"""Daily GitHub PR report generator using hybrid local-git + GraphQL approach.

Four-phase pipeline:
  Phase 1: Commit discovery via local git repos + GraphQL API fallback
  Phase 2: Review discovery via GraphQL search
  Phase 3: PR detail enrichment via GraphQL batch queries
  Phase 4: Content preparation (default grouping or AI consolidation)

Falls back to GraphQL-only mode when no local repos are configured.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, date

from daily_report.config import load_config, Config
from daily_report.git_local import discover_repos, fetch_repos, find_commits, extract_pr_numbers, RepoInfo
from daily_report.graphql_client import (
    graphql_with_retry,
    build_pr_details_query,
    parse_pr_details_response,
    build_commit_to_pr_query,
    parse_commit_to_pr_response,
    build_review_search_query,
    build_waiting_for_review_query,
    _sanitize_graphql_string,
)
from daily_report.report_data import (
    ReportData, AuthoredPR, ReviewedPR, WaitingPR, SummaryStats,
)
from daily_report.format_markdown import format_markdown


# AI bots to exclude from reviewer lists
AI_BOTS = {
    "coderabbitai",
    "copilot-pull-request-reviewer",
    "github-actions",
    "copilot-swe-agent",
}


def gh_command(args):
    """Run a gh CLI command and return stdout."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return result.stdout.strip()
    except FileNotFoundError:
        print("Error: gh CLI is not installed.", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh command timed out: {' '.join(args)}") from None
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[:500]
        raise RuntimeError(f"gh command failed: {' '.join(args)}\n{stderr}") from e


def gh_json(args):
    """Run a gh CLI command and parse JSON output."""
    output = gh_command(args)
    if not output:
        return []
    return json.loads(output)


def get_current_user():
    """Get the authenticated GitHub username."""
    try:
        data = gh_json(["api", "user"])
        return data["login"]
    except (RuntimeError, KeyError, TypeError):
        print("Error: gh CLI is not authenticated. Run 'gh auth login'.", file=sys.stderr)
        sys.exit(1)


def extract_themes(titles):
    """Extract conventional commit prefixes from PR titles."""
    known_prefixes = {
        "fix", "feat", "chore", "refactor", "build", "ci", "docs",
        "style", "perf", "test", "revert", "deps", "release",
    }
    found = []
    for title in titles:
        lower = title.lower().strip()
        for prefix in known_prefixes:
            if lower.startswith(prefix + ":") or lower.startswith(prefix + "("):
                if prefix not in found:
                    found.append(prefix)
                break
    return found


def _safe_filename_part(value: str) -> str:
    """Remove characters unsafe for filenames."""
    return re.sub(r'[^a-zA-Z0-9._-]', '_', value)


def format_status(state, is_draft, merged_at=None):
    """Format PR status string."""
    if merged_at:
        return "Merged"
    if is_draft:
        return "Draft"
    if state == "MERGED" or state == "merged":
        return "Merged"
    if state == "CLOSED" or state == "closed":
        return "Closed"
    return "Open"


# ---------------------------------------------------------------------------
# Phase 1 helpers
# ---------------------------------------------------------------------------

def _build_authored_search_query(org, user, date_from, date_to):
    """Build a GraphQL search query for authored PRs (API fallback)."""
    date_query = f"{date_from}..{date_to}" if date_from != date_to else date_from
    query = """\
query AuthoredSearch($createdQuery: String!, $updatedQuery: String!) {
  created: search(query: $createdQuery, type: ISSUE, first: 100) {
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
      }
    }
  }
  updated: search(query: $updatedQuery, type: ISSUE, first: 100) {
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
      }
    }
  }
}"""
    org_filter = f"org:{org} " if org else ""
    variables = {
        "createdQuery": f"{org_filter}author:{user} created:{date_query} type:pr",
        "updatedQuery": f"{org_filter}author:{user} updated:{date_query} type:pr",
    }
    return query, variables


def _build_commit_check_query(prs_to_check):
    """Build a GraphQL query to fetch commits for multiple PRs.

    Args:
        prs_to_check: List of (org, repo, number) tuples.

    Returns:
        A GraphQL query string with index-based aliases (pr_0, pr_1, ...).
    """
    if not prs_to_check:
        return None
    fragments = []
    for i, (org, repo, number) in enumerate(prs_to_check):
        safe_org = _sanitize_graphql_string(org)
        safe_repo = _sanitize_graphql_string(repo)
        fragments.append(
            f'  pr_{i}: repository(owner: "{safe_org}", name: "{safe_repo}") {{\n'
            f"    pullRequest(number: {int(number)}) {{\n"
            f"      number\n"
            f"      commits(first: 100) {{\n"
            f"        nodes {{\n"
            f"          commit {{\n"
            f"            author {{ user {{ login }} date }}\n"
            f"            committer {{ user {{ login }} date }}\n"
            f"          }}\n"
            f"        }}\n"
            f"      }}\n"
            f"    }}\n"
            f"  }}"
        )
    return "{\n" + "\n".join(fragments) + "\n}"


def _check_commits_in_response(data, prs_to_check, user, date_from, date_to):
    """Parse commit check response, return set of (org, repo, number) keys with user commits in range.

    Args:
        data: GraphQL response data dict.
        prs_to_check: Original list of (org, repo, number) tuples.
        user: GitHub username.
        date_from: Start date YYYY-MM-DD.
        date_to: End date YYYY-MM-DD.

    Returns:
        Set of (org, repo, number) tuples for PRs with user commits in range.
    """
    matched = set()
    for i, key in enumerate(prs_to_check):
        alias = f"pr_{i}"
        repo_data = data.get(alias)
        if repo_data is None:
            continue
        pr_info = repo_data.get("pullRequest")
        if pr_info is None:
            continue
        commits = (pr_info.get("commits") or {}).get("nodes", [])
        for node in commits:
            commit = node.get("commit", {})
            for field in ("author", "committer"):
                field_data = commit.get(field, {}) or {}
                user_data = field_data.get("user") or {}
                login = user_data.get("login", "")
                commit_date = field_data.get("date", "")
                if login == user and commit_date and date_from <= commit_date[:10] <= date_to:
                    matched.add(key)
                    break
            else:
                continue
            break
    return matched


def _has_review_in_range(pr_node, user, date_from, date_to):
    """Check if user has review or comment activity within date range."""
    for review in (pr_node.get("reviews") or {}).get("nodes", []):
        review_author = (review.get("author") or {}).get("login", "")
        if review_author == user:
            submitted = (review.get("submittedAt") or "")[:10]
            if submitted and date_from <= submitted <= date_to:
                return True

    for comment in (pr_node.get("comments") or {}).get("nodes", []):
        comment_author = (comment.get("author") or {}).get("login", "")
        if comment_author == user:
            created = (comment.get("createdAt") or "")[:10]
            if created and date_from <= created <= date_to:
                return True

    return False


def _extract_reviewers(pr_node, user, excluded_bots):
    """Extract pending reviewers, excluding bots and the user."""
    reviewers = []
    for req in (pr_node.get("reviewRequests") or {}).get("nodes", []):
        reviewer = req.get("requestedReviewer") or {}
        login = reviewer.get("login") or reviewer.get("slug") or ""
        if login and login not in excluded_bots and login != user:
            reviewers.append(login)
    return reviewers


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate daily GitHub PR report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="--date and --from/--to are mutually exclusive. When neither is given, defaults to today.",
    )
    parser.add_argument("--org", default=None, help="GitHub organization; omit to search all orgs (default: all)")
    parser.add_argument("--user", default=None, help="GitHub username (default: authenticated `gh` user)")
    parser.add_argument("--date", default=None, help="single date, YYYY-MM-DD (default: today); mutually exclusive with --from/--to")
    parser.add_argument("--from", dest="date_from", default=None, help="start of date range, YYYY-MM-DD (requires --to)")
    parser.add_argument("--to", dest="date_to", default=None, help="end of date range, YYYY-MM-DD (requires --from)")
    parser.add_argument("--config", dest="config_path", default=None, help="path to YAML config file (default: ~/.config/daily-report/repos.yaml)")
    parser.add_argument("--repos-dir", dest="repos_dir", default=None, help="scan directory for git repos; filters by --org if given (overrides config repos list)")
    parser.add_argument("--git-email", dest="git_email", default=None, help="additional git author email for commit matching")
    parser.add_argument("--no-local", dest="no_local", action="store_true", default=False, help="skip local git discovery, use GraphQL-only mode (default: %(default)s)")
    parser.add_argument(
        "--slides", action="store_true", default=False,
        help="generate .pptx slide deck instead of Markdown output",
    )
    parser.add_argument(
        "--slides-output", dest="slides_output", default=None,
        help="output path for .pptx file (default: auto-generated name in CWD)",
    )
    parser.add_argument(
        "--slack", action="store_true", default=False,
        help="post report to Slack webhook instead of Markdown output",
    )
    parser.add_argument(
        "--slack-webhook", dest="slack_webhook", default=None,
        help="Slack webhook URL (default: SLACK_WEBHOOK_URL env var or config file)",
    )
    parser.add_argument(
        "--consolidate", action="store_true", default=False,
        help="consolidate PR lists into AI-generated summaries per repository",
    )
    parser.add_argument(
        "--summary", action="store_true", default=False,
        help="replace default summary with a short AI-generated summary",
    )
    parser.add_argument(
        "--model", default=None,
        help="Claude model for --consolidate/--summary (default: claude-sonnet-4-5-20250929)",
    )
    parser.add_argument(
        "--group-by", dest="group_by", default="contribution",
        choices=["project", "status", "contribution"],
        help="group report by: project, status, or contribution type (default: contribution)",
    )
    args = parser.parse_args()

    org = args.org
    user = args.user or get_current_user()

    # Validate date arguments
    if args.date and (args.date_from or args.date_to):
        print("Error: --date cannot be combined with --from/--to.", file=sys.stderr)
        sys.exit(1)
    if (args.date_from is None) != (args.date_to is None):
        print("Error: --from and --to must be used together.", file=sys.stderr)
        sys.exit(1)

    def validate_date(d, label):
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            print(f"Error: Invalid date format '{d}' for {label}. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)

    if args.date_from:
        validate_date(args.date_from, "--from")
        validate_date(args.date_to, "--to")
        date_from = args.date_from
        date_to = args.date_to
    elif args.date:
        validate_date(args.date, "--date")
        date_from = args.date
        date_to = args.date
    else:
        date_from = date.today().isoformat()
        date_to = date_from

    if date_from > date_to:
        print(f"Error: --from date ({date_from}) must be <= --to date ({date_to}).", file=sys.stderr)
        sys.exit(1)

    if args.slides_output and not args.slides:
        print("Error: --slides-output requires --slides.", file=sys.stderr)
        sys.exit(1)

    if args.slack and args.slides:
        print("Error: --slack and --slides are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    if args.slack_webhook and not args.slack:
        print("Error: --slack-webhook requires --slack.", file=sys.stderr)
        sys.exit(1)

    if args.model and not (args.consolidate or args.summary):
        print("Error: --model requires --consolidate or --summary.", file=sys.stderr)
        sys.exit(1)

    is_range = date_from != date_to

    # Load configuration
    cfg = load_config(args.config_path)

    # Resolve Slack webhook URL (CLI > env var > config file)
    slack_webhook_url = None
    if args.slack:
        slack_webhook_url = (
            args.slack_webhook
            or os.environ.get("SLACK_WEBHOOK_URL", "")
            or cfg.slack_webhook
        )
        if not slack_webhook_url:
            print(
                "Error: --slack requires a webhook URL. Provide via --slack-webhook, "
                "SLACK_WEBHOOK_URL env var, or slack_webhook in config file.",
                file=sys.stderr,
            )
            sys.exit(1)

    excluded_bots = set(cfg.excluded_bots) if cfg.excluded_bots else set(AI_BOTS)
    git_emails = list(cfg.git_emails) if cfg.git_emails else []
    if args.git_email:
        git_emails.append(args.git_email)

    # Determine available local repos
    local_repos: list[RepoInfo] = []
    if not args.no_local:
        repos_dir = args.repos_dir or cfg.repos_dir
        if repos_dir:
            local_repos = discover_repos(repos_dir, org)
        elif cfg.repos:
            for rc in cfg.repos:
                if rc.path and (org is None or rc.org.lower() == org.lower()):
                    local_repos.append(RepoInfo(path=rc.path, org=rc.org, name=rc.name))

    use_local = len(local_repos) > 0

    # -----------------------------------------------------------------------
    # Phase 1: Commit Discovery
    # -----------------------------------------------------------------------
    # Keys: (org, repo_name, pr_number)
    authored_pr_keys: dict[tuple[str, str, int], str] = {}  # key -> role ("authored" | "contributed")
    authored_pr_authors: dict[tuple[str, str, int], str] = {}  # key -> PR author login

    local_repo_names: set[str] = set()

    if use_local:
        # Fetch repos in parallel
        print("Fetching repos...", file=sys.stderr)
        fetch_repos(local_repos)

        for repo in local_repos:
            local_repo_names.add(repo.name)
            # Find commits by user in date range
            commits = find_commits(
                repo.path, user, date_from, date_to, git_emails=git_emails or None
            )
            if not commits:
                continue

            # Extract PR numbers from commit messages
            pr_map, unmapped = extract_pr_numbers(commits)

            # PRs extracted from commit messages
            for pr_number in pr_map:
                key = (repo.org, repo.name, pr_number)
                if key not in authored_pr_keys:
                    # We don't know the author yet; will be resolved in Phase 3
                    authored_pr_keys[key] = "authored"

            # Use GraphQL to map unmapped commits to PRs
            if unmapped:
                shas = [c.sha for c in unmapped]
                # Process in batches of 25
                for i in range(0, len(shas), 25):
                    batch = shas[i:i + 25]
                    try:
                        query = build_commit_to_pr_query(repo.org, repo.name, batch)
                        data = graphql_with_retry(query)
                        sha_to_prs = parse_commit_to_pr_response(data)
                        for sha, prs in sha_to_prs.items():
                            for pr in prs:
                                pr_number = pr.get("number")
                                if pr_number:
                                    key = (repo.org, repo.name, pr_number)
                                    if key not in authored_pr_keys:
                                        pr_author = (pr.get("author") or {}).get("login", "")
                                        authored_pr_keys[key] = "authored"
                                        if pr_author:
                                            authored_pr_authors[key] = pr_author
                    except RuntimeError as e:
                        print(f"Warning: GraphQL commit mapping failed for {repo.name}: {e}", file=sys.stderr)

    # API fallback: search for authored PRs via GraphQL
    # Always run this to catch PRs in non-cloned repos
    try:
        query, variables = _build_authored_search_query(org, user, date_from, date_to)
        data = graphql_with_retry(query, variables)
        api_candidate_prs = []
        seen_api = set()
        for search_key in ("created", "updated"):
            for node in (data.get(search_key) or {}).get("nodes", []):
                if not node:
                    continue
                repo_info = node.get("repository") or {}
                repo_name = repo_info.get("name", "")
                pr_number = node.get("number")
                if not repo_name or not pr_number:
                    continue
                pr_org = (repo_info.get("owner") or {}).get("login", "")
                if not pr_org:
                    continue
                key = (pr_org, repo_name, pr_number)
                if key not in seen_api:
                    seen_api.add(key)
                    api_candidate_prs.append((key, node))
    except RuntimeError as e:
        print(f"Warning: authored PR search failed: {e}", file=sys.stderr)
        api_candidate_prs = []

    # For API-discovered PRs not already found locally, verify commits in date range
    prs_needing_commit_check = []
    api_node_map = {}
    for key, node in api_candidate_prs:
        if key not in authored_pr_keys:
            prs_needing_commit_check.append(key)
            api_node_map[key] = node

    if prs_needing_commit_check:
        # Batch commit check via GraphQL
        # Process in batches to avoid query complexity limits
        batch_size = 15
        for i in range(0, len(prs_needing_commit_check), batch_size):
            batch = prs_needing_commit_check[i:i + batch_size]
            try:
                query = _build_commit_check_query(batch)
                if query:
                    data = graphql_with_retry(query)
                    matched = _check_commits_in_response(data, batch, user, date_from, date_to)
                    for key in matched:
                        authored_pr_keys[key] = "authored"
            except RuntimeError as e:
                print(f"Warning: commit check failed: {e}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Phase 2: Review Discovery (GraphQL)
    # -----------------------------------------------------------------------
    reviewed_pr_keys: set[tuple[str, str, int]] = set()
    reviewed_pr_data: dict[tuple[str, str, int], dict] = {}

    try:
        query, variables = build_review_search_query(org, user, date_from, date_to)
        data = graphql_with_retry(query, variables)

        for search_key in ("reviewed", "commented"):
            for node in (data.get(search_key) or {}).get("nodes", []):
                if not node:
                    continue
                repo_info = node.get("repository") or {}
                repo_name = repo_info.get("name", "")
                pr_number = node.get("number")
                if not repo_name or not pr_number:
                    continue
                pr_org = (repo_info.get("owner") or {}).get("login", "")
                if not pr_org:
                    continue
                key = (pr_org, repo_name, pr_number)

                # Skip if already discovered as authored/contributed in Phase 1
                if key in authored_pr_keys:
                    continue

                # Verify review/comment activity is within date range
                if _has_review_in_range(node, user, date_from, date_to):
                    reviewed_pr_keys.add(key)
                    reviewed_pr_data[key] = node
    except RuntimeError as e:
        print(f"Warning: review discovery failed: {e}", file=sys.stderr)

    # Check for contributions on reviewed PRs (user has commits but is not author)
    if reviewed_pr_keys:
        review_prs_to_check = list(reviewed_pr_keys)
        batch_size = 15
        for i in range(0, len(review_prs_to_check), batch_size):
            batch = review_prs_to_check[i:i + batch_size]
            try:
                query = _build_commit_check_query(batch)
                if query:
                    data = graphql_with_retry(query)
                    matched = _check_commits_in_response(data, batch, user, date_from, date_to)
                    for key in matched:
                        reviewed_pr_keys.discard(key)
                        authored_pr_keys[key] = "contributed"
            except RuntimeError as e:
                print(f"Warning: contribution check failed: {e}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Phase 3: Enrichment (GraphQL Batch)
    # -----------------------------------------------------------------------
    all_pr_keys = set(authored_pr_keys.keys()) | reviewed_pr_keys

    # Batch fetch PR details
    pr_details: dict[tuple[str, str, int], dict] = {}
    if all_pr_keys:
        details_list = list(all_pr_keys)
        batch_size = 20
        for i in range(0, len(details_list), batch_size):
            batch = details_list[i:i + batch_size]
            try:
                query = build_pr_details_query(batch)
                data = graphql_with_retry(query)
                parsed = parse_pr_details_response(data, batch)
                pr_details.update(parsed)
            except RuntimeError as e:
                print(f"Warning: PR details fetch failed: {e}", file=sys.stderr)

    # Classify authored vs contributed based on PR author
    for key in list(authored_pr_keys.keys()):
        detail = pr_details.get(key)
        if detail:
            pr_author = (detail.get("author") or {}).get("login", "")
            # Also check pre-stored author info from commit mapping
            if not pr_author:
                pr_author = authored_pr_authors.get(key, "")
            if pr_author and pr_author != user:
                authored_pr_keys[key] = "contributed"
            elif pr_author == user:
                authored_pr_keys[key] = "authored"

    # Build authored_prs list
    authored_prs_list: list[AuthoredPR] = []
    for key, role in authored_pr_keys.items():
        pr_org, repo_name, pr_number = key
        detail = pr_details.get(key, {})
        title = detail.get("title", "")
        state = detail.get("state", "")
        is_draft = detail.get("isDraft", False)
        merged_at = detail.get("mergedAt")
        additions = detail.get("additions", 0) or 0
        deletions = detail.get("deletions", 0) or 0
        pr_author = (detail.get("author") or {}).get("login", "")
        status = format_status(state, is_draft, merged_at)
        if status not in ("Open", "Draft"):
            additions, deletions = 0, 0
        authored_prs_list.append(AuthoredPR(
            repo=f"{pr_org}/{repo_name}",
            title=title,
            number=pr_number,
            status=status,
            additions=additions,
            deletions=deletions,
            contributed=(role == "contributed"),
            original_author=pr_author if role == "contributed" else None,
        ))

    # Sort for deterministic output
    authored_prs_list.sort(key=lambda d: (d.repo, d.number))

    # Build reviewed_prs list
    reviewed_prs_list: list[ReviewedPR] = []
    for key in sorted(reviewed_pr_keys):
        pr_org, repo_name, pr_number = key
        detail = pr_details.get(key, {})
        title = detail.get("title", "")
        state = detail.get("state", "")
        is_draft = detail.get("isDraft", False)
        merged_at = detail.get("mergedAt")
        pr_author = (detail.get("author") or {}).get("login", "")
        status = format_status(state, is_draft, merged_at)
        reviewed_prs_list.append(ReviewedPR(
            repo=f"{pr_org}/{repo_name}",
            title=title,
            number=pr_number,
            author=pr_author,
            status=status,
        ))

    # Waiting for review
    waiting_prs_list: list[WaitingPR] = []
    try:
        query, variables = build_waiting_for_review_query(org, user)
        data = graphql_with_retry(query, variables)
        for node in (data.get("search") or {}).get("nodes", []):
            if not node:
                continue
            if node.get("isDraft", False):
                continue
            repo_info = node.get("repository") or {}
            repo_name = repo_info.get("name", "")
            pr_org = (repo_info.get("owner") or {}).get("login", "")
            pr_number = node.get("number")
            if not repo_name or not pr_number or not pr_org:
                continue
            reviewers = _extract_reviewers(node, user, excluded_bots)
            if reviewers:
                created_at = node.get("createdAt", "")
                try:
                    created_date = datetime.strptime(created_at[:10], "%Y-%m-%d").date()
                    ref_date = datetime.strptime(date_to, "%Y-%m-%d").date()
                    days_waiting = max(0, (ref_date - created_date).days)
                except (ValueError, TypeError):
                    days_waiting = 0
                waiting_prs_list.append(WaitingPR(
                    repo=f"{pr_org}/{repo_name}",
                    title=node.get("title", ""),
                    number=pr_number,
                    reviewers=reviewers,
                    created_at=created_at[:10],
                    days_waiting=days_waiting,
                ))
    except RuntimeError as e:
        print(f"Warning: waiting for review query failed: {e}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Build report
    # -----------------------------------------------------------------------
    all_titles = [p.title for p in authored_prs_list] + [p.title for p in reviewed_prs_list]
    themes = extract_themes(all_titles)

    all_repos = set()
    for p in authored_prs_list:
        all_repos.add(p.repo)
    for p in reviewed_prs_list:
        all_repos.add(p.repo)

    total_prs = len(authored_prs_list) + len(reviewed_prs_list)
    merged_count = (
        sum(1 for p in authored_prs_list if p.status == "Merged")
        + sum(1 for p in reviewed_prs_list if p.status == "Merged")
    )
    open_count = sum(1 for p in authored_prs_list if p.status in ("Open", "Draft"))

    report = ReportData(
        user=user,
        date_from=date_from,
        date_to=date_to,
        authored_prs=authored_prs_list,
        reviewed_prs=reviewed_prs_list,
        waiting_prs=waiting_prs_list,
        summary=SummaryStats(
            total_prs=total_prs,
            repo_count=len(all_repos),
            merged_count=merged_count,
            open_count=open_count,
            themes=themes,
            is_range=is_range,
        ),
    )

    # Prepare content (default or consolidated)
    if args.consolidate:
        if args.group_by != "contribution":
            print(
                "Warning: --group-by is ignored when --consolidate is used.",
                file=sys.stderr,
            )
        from daily_report.content import prepare_consolidated_content
        try:
            report.content = prepare_consolidated_content(
                report,
                model=args.model or "claude-sonnet-4-5-20250929",
                prompt=cfg.consolidate_prompt or None,
            )
        except ImportError:
            print(
                "Error: anthropic package required for --consolidate. "
                "Install it with: pip install anthropic",
                file=sys.stderr,
            )
            sys.exit(1)
        except RuntimeError as e:
            print(f"Error: consolidation failed: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        from daily_report.content import regroup_content
        report.content = regroup_content(report, args.group_by)

    # Prepare AI summary (replaces default summary stats)
    if args.summary:
        from daily_report.content import prepare_ai_summary
        try:
            report.summary.ai_summary = prepare_ai_summary(
                report,
                model=args.model or "claude-sonnet-4-5-20250929",
                prompt=cfg.summary_prompt or None,
            )
        except RuntimeError as e:
            print(f"Error: AI summary failed: {e}", file=sys.stderr)
            sys.exit(1)

    # Output
    if args.slack:
        from daily_report.format_slack import format_slack, post_to_slack
        payload = format_slack(report, group_by=args.group_by)
        try:
            post_to_slack(slack_webhook_url, payload)
            print("Report posted to Slack.", file=sys.stderr)
        except (ValueError, ConnectionError, RuntimeError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.slides:
        # Lazy import -- python-pptx is optional
        try:
            from daily_report.format_slides import format_slides
        except ImportError:
            print(
                "Error: python-pptx is required for --slides. "
                "Install it with: pip install python-pptx",
                file=sys.stderr,
            )
            sys.exit(1)

        if args.slides_output:
            output_path = args.slides_output
        else:
            safe_user = _safe_filename_part(user)
            if date_from == date_to:
                output_path = f"daily-report-{safe_user}-{date_from}.pptx"
            else:
                output_path = f"daily-report-{safe_user}-{date_from}_{date_to}.pptx"

        format_slides(report, output_path, group_by=args.group_by)
        print(f"Slides written to {output_path}", file=sys.stderr)
    else:
        output = format_markdown(report, group_by=args.group_by)
        print(output)


if __name__ == "__main__":
    main()
