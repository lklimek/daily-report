#!/usr/bin/env python3
"""Daily GitHub PR report generator using gh CLI."""

import argparse
import json
import subprocess
import sys
from datetime import datetime, date


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


def gh_search(query_args, json_fields):
    """Run gh search prs and return parsed JSON list."""
    fields = ",".join(json_fields)
    try:
        return gh_json(["search", "prs"] + query_args + ["--json", fields])
    except RuntimeError:
        return []


def get_current_user():
    """Get the authenticated GitHub username."""
    try:
        data = gh_json(["api", "user"])
        return data["login"]
    except (RuntimeError, KeyError, TypeError):
        print("Error: gh CLI is not authenticated. Run 'gh auth login'.", file=sys.stderr)
        sys.exit(1)


def repo_name(pr):
    """Extract repository name from a PR object."""
    repo = pr.get("repository", {})
    if isinstance(repo, dict):
        return repo.get("name", "")
    return str(repo)


def pr_key(pr):
    """Return a unique key for deduplication."""
    return (repo_name(pr), pr["number"])


def deduplicate(prs):
    """Deduplicate PRs by (repo, number)."""
    seen = set()
    result = []
    for pr in prs:
        key = pr_key(pr)
        if key not in seen:
            seen.add(key)
            result.append(pr)
    return result


def get_repo_fullname(pr, org):
    """Get the full 'org/repo' string."""
    return f"{org}/{repo_name(pr)}"


def get_pr_detail(org, repo, number, jq_expr=None):
    """Fetch PR detail via gh api."""
    args = ["api", f"repos/{org}/{repo}/pulls/{number}"]
    if jq_expr:
        args += ["--jq", jq_expr]
    try:
        return gh_json(args) if not jq_expr else gh_command(args)
    except RuntimeError:
        return None


def check_commits_for_user(org, repo, number, user, date_from, date_to):
    """Check if user has commits on a PR within [date_from, date_to]."""
    try:
        commits = gh_json(["api", f"repos/{org}/{repo}/pulls/{number}/commits"])
    except RuntimeError:
        return False
    for commit in commits:
        author_login = (commit.get("author") or {}).get("login", "")
        committer_login = (commit.get("committer") or {}).get("login", "")
        if author_login == user or committer_login == user:
            commit_info = commit.get("commit", {})
            for date_field in ("author", "committer"):
                date_str = (commit_info.get(date_field) or {}).get("date", "")
                if date_str and date_from <= date_str[:10] <= date_to:
                    return True
    return False


def check_review_activity(org, repo, number, user, date_from, date_to):
    """Check if user has review or comment activity on a PR within [date_from, date_to]."""
    # Check reviews
    try:
        reviews_output = gh_command([
            "api", f"repos/{org}/{repo}/pulls/{number}/reviews",
            "--jq", f'.[] | select(.user.login == "{user}") | .submitted_at',
        ])
        if reviews_output:
            for line in reviews_output.splitlines():
                review_date = line.strip()[:10]
                if date_from <= review_date <= date_to:
                    return True
    except RuntimeError:
        pass

    # Check issue comments
    try:
        comments_output = gh_command([
            "api", f"repos/{org}/{repo}/issues/{number}/comments",
            "--jq", f'.[] | select(.user.login == "{user}") | .created_at',
        ])
        if comments_output:
            for line in comments_output.splitlines():
                comment_date = line.strip()[:10]
                if date_from <= comment_date <= date_to:
                    return True
    except RuntimeError:
        pass

    return False


def get_additions_deletions(org, repo, number):
    """Get +/- stats for a PR."""
    try:
        data = gh_json(["api", f"repos/{org}/{repo}/pulls/{number}"])
        return data.get("additions", 0), data.get("deletions", 0)
    except RuntimeError:
        return 0, 0


def get_merged_info(org, repo, number):
    """Get merged_at and state for a PR."""
    try:
        data = gh_json(["api", f"repos/{org}/{repo}/pulls/{number}"])
        return data.get("merged_at"), data.get("state", "")
    except RuntimeError:
        return None, ""


def get_requested_reviewers(org, repo, number, user):
    """Get pending requested reviewers, excluding bots and the user."""
    try:
        data = gh_json(["api", f"repos/{org}/{repo}/pulls/{number}/requested_reviewers"])
    except RuntimeError:
        return []
    reviewers = []
    for u in data.get("users", []):
        login = u.get("login", "")
        if login and login not in AI_BOTS and login != user:
            reviewers.append(login)
    for t in data.get("teams", []):
        slug = t.get("slug", "")
        if slug and slug not in AI_BOTS:
            reviewers.append(slug)
    return reviewers


def pr_author_login(pr):
    """Extract PR author login."""
    author = pr.get("author", {})
    if isinstance(author, dict):
        return author.get("login", "")
    return str(author)


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


def main():
    parser = argparse.ArgumentParser(description="Generate daily GitHub PR report")
    parser.add_argument("--org", default="dashpay", help="GitHub organization (default: dashpay)")
    parser.add_argument("--user", default=None, help="GitHub username (default: authenticated user)")
    parser.add_argument("--date", default=None, help="Date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--from", dest="date_from", default=None, help="Start date in YYYY-MM-DD format (use with --to)")
    parser.add_argument("--to", dest="date_to", default=None, help="End date in YYYY-MM-DD format (use with --from)")
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

    json_fields = ["repository", "title", "number", "state", "isDraft", "url", "updatedAt"]
    json_fields_with_author = json_fields + ["author"]

    # Build date query string for gh search
    date_query = f"{date_from}..{date_to}" if is_range else date_from

    # Step 1: Get authored PRs
    authored_created = gh_search(
        [f"--author={user}", f"--created={date_query}", f"--owner={org}"],
        json_fields,
    )
    authored_updated = gh_search(
        [f"--author={user}", f"--updated={date_query}", f"--owner={org}"],
        json_fields,
    )
    authored_prs = deduplicate(authored_created + authored_updated)

    authored_keys = {pr_key(pr) for pr in authored_prs}

    # Step 2: Get reviewed/commented PRs
    reviewed = gh_search(
        [f"--reviewed-by={user}", f"--updated={date_query}", f"--owner={org}"],
        json_fields_with_author,
    )
    commented = gh_search(
        [f"--commenter={user}", f"--updated={date_query}", f"--owner={org}"],
        json_fields_with_author,
    )
    candidate_prs = deduplicate(reviewed + commented)
    # Remove authored PRs
    candidate_prs = [pr for pr in candidate_prs if pr_key(pr) not in authored_keys]

    # Step 3: Check for contributions on non-authored PRs
    contributed_prs = []
    remaining_prs = []
    for pr in candidate_prs:
        repo = repo_name(pr)
        number = pr["number"]
        if check_commits_for_user(org, repo, number, user, date_from, date_to):
            contributed_prs.append(pr)
        else:
            remaining_prs.append(pr)

    # Step 4: Verify review activity is within date range
    reviewed_prs = []
    for pr in remaining_prs:
        repo = repo_name(pr)
        number = pr["number"]
        if check_review_activity(org, repo, number, user, date_from, date_to):
            reviewed_prs.append(pr)

    # Step 5 & 6: Get stats and merged info for authored/contributed PRs
    authored_details = []
    for pr in authored_prs:
        repo = repo_name(pr)
        number = pr["number"]
        state = pr.get("state", "")
        is_draft = pr.get("isDraft", False)
        merged_at, api_state = get_merged_info(org, repo, number)
        status = format_status(api_state or state, is_draft, merged_at)
        additions, deletions = (0, 0)
        if status in ("Open", "Draft"):
            additions, deletions = get_additions_deletions(org, repo, number)
        authored_details.append({
            "repo": repo,
            "title": pr["title"],
            "number": number,
            "status": status,
            "additions": additions,
            "deletions": deletions,
            "contributed": False,
            "original_author": None,
        })

    for pr in contributed_prs:
        repo = repo_name(pr)
        number = pr["number"]
        state = pr.get("state", "")
        is_draft = pr.get("isDraft", False)
        merged_at, api_state = get_merged_info(org, repo, number)
        status = format_status(api_state or state, is_draft, merged_at)
        additions, deletions = (0, 0)
        if status in ("Open", "Draft"):
            additions, deletions = get_additions_deletions(org, repo, number)
        authored_details.append({
            "repo": repo,
            "title": pr["title"],
            "number": number,
            "status": status,
            "additions": additions,
            "deletions": deletions,
            "contributed": True,
            "original_author": pr_author_login(pr),
        })

    # Step 7: Waiting for review
    waiting_prs = []
    try:
        open_authored = gh_search(
            [f"--author={user}", f"--owner={org}", "--state=open"],
            ["repository", "title", "number", "isDraft", "url", "createdAt"],
        )
    except RuntimeError:
        open_authored = []

    for pr in open_authored:
        if pr.get("isDraft", False):
            continue
        repo = repo_name(pr)
        number = pr["number"]
        reviewers = get_requested_reviewers(org, repo, number, user)
        if reviewers:
            created_at = pr.get("createdAt", "")
            try:
                created_date = datetime.strptime(created_at[:10], "%Y-%m-%d").date()
                ref_date = datetime.strptime(date_to, "%Y-%m-%d").date()
                days_waiting = max(0, (ref_date - created_date).days)
            except (ValueError, TypeError):
                days_waiting = 0
            waiting_prs.append({
                "repo": repo,
                "title": pr["title"],
                "number": number,
                "reviewers": reviewers,
                "created_at": created_at[:10],
                "days_waiting": days_waiting,
            })

    # Build report
    all_titles = [d["title"] for d in authored_details] + [pr["title"] for pr in reviewed_prs]
    themes = extract_themes(all_titles)

    all_repos = set()
    for d in authored_details:
        all_repos.add(d["repo"])
    for pr in reviewed_prs:
        all_repos.add(repo_name(pr))

    total_prs = len(authored_details) + len(reviewed_prs)
    merged_today = (
        sum(1 for d in authored_details if d["status"] == "Merged")
        + sum(1 for pr in reviewed_prs if pr.get("state", "").upper() == "MERGED")
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
            repo = repo_name(pr)
            author = pr_author_login(pr)
            state = pr.get("state", "")
            is_draft = pr.get("isDraft", False)
            status = format_status(state, is_draft)
            lines.append(
                f"- `{repo}` \u2014 {pr['title']} #{pr['number']} ({author}) \u2014 **{status}**"
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
