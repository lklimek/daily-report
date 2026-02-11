#!/usr/bin/env python3
"""Daily GitHub PR report generator using hybrid local-git + GraphQL approach.

Three-phase pipeline:
  Phase 1: Commit discovery via local git repos + GraphQL API fallback
  Phase 2: Review discovery via GraphQL search
  Phase 3: PR detail enrichment via GraphQL batch queries

Falls back to GraphQL-only mode when no local repos are configured.
"""

import argparse
import json
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
)


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
        )
        return result.stdout.strip()
    except FileNotFoundError:
        print("Error: gh CLI is not installed.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"gh command failed: {' '.join(args)}\n{e.stderr}") from e


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
    variables = {
        "createdQuery": f"org:{org} author:{user} created:{date_query} type:pr",
        "updatedQuery": f"org:{org} author:{user} updated:{date_query} type:pr",
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
        fragments.append(
            f'  pr_{i}: repository(owner: "{org}", name: "{repo}") {{\n'
            f"    pullRequest(number: {number}) {{\n"
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
    parser = argparse.ArgumentParser(description="Generate daily GitHub PR report")
    parser.add_argument("--org", default="dashpay", help="GitHub organization (default: dashpay)")
    parser.add_argument("--user", default=None, help="GitHub username (default: authenticated user)")
    parser.add_argument("--date", default=None, help="Date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--from", dest="date_from", default=None, help="Start date in YYYY-MM-DD format (use with --to)")
    parser.add_argument("--to", dest="date_to", default=None, help="End date in YYYY-MM-DD format (use with --from)")
    parser.add_argument("--config", dest="config_path", default=None, help="Path to config file (default: ~/.config/daily-report/repos.yaml)")
    parser.add_argument("--repos-dir", dest="repos_dir", default=None, help="Scan directory for repos (overrides config file)")
    parser.add_argument("--git-email", dest="git_email", default=None, help="Additional git email for author matching")
    parser.add_argument("--no-local", dest="no_local", action="store_true", help="Force API-only mode (skip local git operations)")
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

    is_range = date_from != date_to

    # Load configuration
    cfg = load_config(args.config_path)
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
                if rc.org.lower() == org.lower() and rc.path:
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
                pr_org = (repo_info.get("owner") or {}).get("login", org)
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
                pr_org = (repo_info.get("owner") or {}).get("login", org)
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

    # Build authored_details list
    authored_details = []
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
        authored_details.append({
            "repo": repo_name,
            "title": title,
            "number": pr_number,
            "status": status,
            "additions": additions,
            "deletions": deletions,
            "contributed": role == "contributed",
            "original_author": pr_author if role == "contributed" else None,
        })

    # Sort for deterministic output
    authored_details.sort(key=lambda d: (d["repo"], d["number"]))

    # Build reviewed_prs list
    reviewed_prs = []
    for key in sorted(reviewed_pr_keys):
        pr_org, repo_name, pr_number = key
        detail = pr_details.get(key, {})
        title = detail.get("title", "")
        state = detail.get("state", "")
        is_draft = detail.get("isDraft", False)
        merged_at = detail.get("mergedAt")
        pr_author = (detail.get("author") or {}).get("login", "")
        status = format_status(state, is_draft, merged_at)
        reviewed_prs.append({
            "repo": repo_name,
            "title": title,
            "number": pr_number,
            "author": pr_author,
            "status": status,
        })

    # Waiting for review
    waiting_prs = []
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
            pr_number = node.get("number")
            if not repo_name or not pr_number:
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
                waiting_prs.append({
                    "repo": repo_name,
                    "title": node.get("title", ""),
                    "number": pr_number,
                    "reviewers": reviewers,
                    "created_at": created_at[:10],
                    "days_waiting": days_waiting,
                })
    except RuntimeError as e:
        print(f"Warning: waiting for review query failed: {e}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Build report
    # -----------------------------------------------------------------------
    all_titles = [d["title"] for d in authored_details] + [pr["title"] for pr in reviewed_prs]
    themes = extract_themes(all_titles)

    all_repos = set()
    for d in authored_details:
        all_repos.add(d["repo"])
    for pr in reviewed_prs:
        all_repos.add(pr["repo"])

    total_prs = len(authored_details) + len(reviewed_prs)
    merged_today = (
        sum(1 for d in authored_details if d["status"] == "Merged")
        + sum(1 for pr in reviewed_prs if pr["status"] == "Merged")
    )
    still_open = sum(1 for d in authored_details if d["status"] in ("Open", "Draft"))

    lines = []
    if is_range:
        lines.append(f"# Daily Report \u2014 {date_from} .. {date_to}")
    else:
        lines.append(f"# Daily Report \u2014 {date_from}")
    lines.append("")

    # Authored / Contributed PRs
    lines.append("**Authored / Contributed PRs**")
    lines.append("")
    if authored_details:
        for d in authored_details:
            stats = ""
            if d["status"] in ("Open", "Draft"):
                stats = f" (+{d['additions']}/\u2212{d['deletions']})"
            author_info = ""
            if d["contributed"] and d["original_author"]:
                author_info = f" ({d['original_author']})"
            lines.append(
                f"- `{d['repo']}` \u2014 {d['title']} #{d['number']}{author_info} \u2014 **{d['status']}**{stats}"
            )
    else:
        lines.append("_No authored or contributed PRs._")
    lines.append("")

    # Reviewed / Approved PRs
    lines.append("**Reviewed / Approved PRs**")
    lines.append("")
    if reviewed_prs:
        for pr in reviewed_prs:
            lines.append(
                f"- `{pr['repo']}` \u2014 {pr['title']} #{pr['number']} ({pr['author']}) \u2014 **{pr['status']}**"
            )
    else:
        lines.append("_No reviewed or approved PRs._")
    lines.append("")

    # Waiting for review
    lines.append("**Waiting for review**")
    lines.append("")
    if waiting_prs:
        for w in waiting_prs:
            reviewer_names = ", ".join(f"**{r}**" for r in w["reviewers"])
            lines.append(
                f"- `{w['repo']}` \u2014 {w['title']} #{w['number']} \u2014 reviewer: {reviewer_names} \u2014 since {w['created_at']} ({w['days_waiting']} days)"
            )
    else:
        lines.append("_No PRs waiting for review._")
    lines.append("")

    # Summary
    themes_str = ", ".join(themes) if themes else "general development"
    merged_label = "merged" if is_range else "merged today"
    lines.append(
        f"**Summary:** {total_prs} PRs across {len(all_repos)} repos, "
        f"{merged_today} {merged_label}, {still_open} still open. "
        f"Key themes: {themes_str}."
    )

    print("\n".join(lines))


if __name__ == "__main__":
    main()
