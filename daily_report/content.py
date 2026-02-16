"""Content preparation layer for daily-report.

Transforms raw PR lists (authored_prs, reviewed_prs, waiting_prs) into
renderer-agnostic RepoContent structures. Supports two modes:

- prepare_default_content(): groups PRs by repo with semantic ContentItems
- prepare_consolidated_content(): AI-powered summarisation via Claude API
- prepare_ai_summary(): AI-powered one-line summary (<320 chars)

Authentication for consolidation (resolution order):
1. ANTHROPIC_API_KEY env var  → uses anthropic Python SDK directly
2. Claude CLI (``claude -p``) → uses whatever auth Claude Code has configured
   (subscription, CLAUDE_CODE_OAUTH_TOKEN, etc.)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict

from daily_report.report_data import (
    ContentBlock,
    ContentItem,
    RepoContent,
    ReportData,
)

_DEFAULT_SUMMARY_PROMPT = (
    "You are given a list of GitHub pull requests grouped by repository. "
    "Write a single-sentence summary of the overall work (max 320 characters). "
    "Focus on the high-level goals and themes, not individual PRs. "
    "Return ONLY the summary text, nothing else — no quotes, no labels, no JSON."
)

_DEFAULT_PROMPT = (
    "You are given a list of GitHub pull requests grouped by repository. "
    "For each repository, summarize the work into 2-5 concise bullet points "
    "describing the PURPOSE and GOALS of the work. Reference PR numbers. "
    "Return valid JSON only, no markdown fences. "
    'Format: {"repo_name": [{"title": "summary line", "numbers": [1,2,3]}, ...], ...}'
)


def prepare_default_content(report: ReportData) -> list[RepoContent]:
    """Build RepoContent list from raw PR lists, grouped by repo.

    Args:
        report: Complete report data with populated PR lists.

    Returns:
        Alphabetically sorted list of RepoContent objects.
    """
    # Group PRs by repo
    authored_by_repo: dict[str, list] = defaultdict(list)
    reviewed_by_repo: dict[str, list] = defaultdict(list)
    waiting_by_repo: dict[str, list] = defaultdict(list)

    for pr in report.authored_prs:
        authored_by_repo[pr.repo].append(pr)
    for pr in report.reviewed_prs:
        reviewed_by_repo[pr.repo].append(pr)
    for pr in report.waiting_prs:
        waiting_by_repo[pr.repo].append(pr)

    all_repos = sorted(
        set(authored_by_repo) | set(reviewed_by_repo) | set(waiting_by_repo)
    )

    result: list[RepoContent] = []
    for repo_name in all_repos:
        blocks: list[ContentBlock] = []

        # Authored / Contributed
        authored = authored_by_repo.get(repo_name, [])
        if authored:
            items: list[ContentItem] = []
            for pr in authored:
                items.append(ContentItem(
                    title=pr.title,
                    numbers=[pr.number],
                    status=pr.status,
                    additions=pr.additions,
                    deletions=pr.deletions,
                    author=pr.original_author if pr.contributed else "",
                ))
            blocks.append(ContentBlock(heading="Authored / Contributed", items=items))

        # Reviewed
        reviewed = reviewed_by_repo.get(repo_name, [])
        if reviewed:
            items = []
            for pr in reviewed:
                items.append(ContentItem(
                    title=pr.title,
                    numbers=[pr.number],
                    status=pr.status,
                    author=pr.author,
                ))
            blocks.append(ContentBlock(heading="Reviewed", items=items))

        # Waiting for Review
        waiting = waiting_by_repo.get(repo_name, [])
        if waiting:
            items = []
            for pr in waiting:
                items.append(ContentItem(
                    title=pr.title,
                    numbers=[pr.number],
                    reviewers=list(pr.reviewers),
                    days_waiting=pr.days_waiting,
                ))
            blocks.append(ContentBlock(heading="Waiting for Review", items=items))

        if blocks:
            result.append(RepoContent(repo_name=repo_name, blocks=blocks))

    return result


_STATUS_ORDER = ["Open", "Draft", "Merged", "Closed", "Waiting for Review"]


def regroup_content(report: ReportData, group_by: str = "contribution") -> list[RepoContent]:
    """Regroup content by the specified grouping mode.

    Args:
        report: Complete report data with populated PR lists.
        group_by: Grouping mode — "project", "status", or "contribution".

    Returns:
        List of RepoContent objects organized by the requested grouping.
    """
    if group_by == "project":
        return _regroup_by_project(report)
    elif group_by == "status":
        return _regroup_by_status(report)
    else:
        return _regroup_by_contribution(report)


def _make_authored_item(pr) -> ContentItem:
    """Create a ContentItem from an AuthoredPR."""
    return ContentItem(
        title=pr.title,
        numbers=[pr.number],
        status=pr.status,
        additions=pr.additions,
        deletions=pr.deletions,
        author=pr.original_author if pr.contributed else "",
    )


def _make_reviewed_item(pr) -> ContentItem:
    """Create a ContentItem from a ReviewedPR."""
    return ContentItem(
        title=pr.title,
        numbers=[pr.number],
        status=pr.status,
        author=pr.author,
    )


def _make_waiting_item(pr) -> ContentItem:
    """Create a ContentItem from a WaitingPR."""
    return ContentItem(
        title=pr.title,
        numbers=[pr.number],
        reviewers=list(pr.reviewers),
        days_waiting=pr.days_waiting,
    )


def _regroup_by_contribution(report: ReportData) -> list[RepoContent]:
    """Group by contribution type, then by project within each type."""
    result: list[RepoContent] = []

    # Authored / Contributed
    authored_by_repo: dict[str, list[ContentItem]] = defaultdict(list)
    for pr in report.authored_prs:
        authored_by_repo[pr.repo].append(_make_authored_item(pr))
    if authored_by_repo:
        blocks = [
            ContentBlock(heading=repo, items=items)
            for repo, items in sorted(authored_by_repo.items())
        ]
        result.append(RepoContent(repo_name="Authored / Contributed", blocks=blocks))

    # Reviewed
    reviewed_by_repo: dict[str, list[ContentItem]] = defaultdict(list)
    for pr in report.reviewed_prs:
        reviewed_by_repo[pr.repo].append(_make_reviewed_item(pr))
    if reviewed_by_repo:
        blocks = [
            ContentBlock(heading=repo, items=items)
            for repo, items in sorted(reviewed_by_repo.items())
        ]
        result.append(RepoContent(repo_name="Reviewed", blocks=blocks))

    # Waiting for Review
    waiting_by_repo: dict[str, list[ContentItem]] = defaultdict(list)
    for pr in report.waiting_prs:
        waiting_by_repo[pr.repo].append(_make_waiting_item(pr))
    if waiting_by_repo:
        blocks = [
            ContentBlock(heading=repo, items=items)
            for repo, items in sorted(waiting_by_repo.items())
        ]
        result.append(RepoContent(repo_name="Waiting for Review", blocks=blocks))

    return result


def _regroup_by_project(report: ReportData) -> list[RepoContent]:
    """Group by project, then by status within each project."""
    # Collect all PRs by (repo, status)
    repo_status: dict[str, dict[str, list[ContentItem]]] = defaultdict(lambda: defaultdict(list))

    for pr in report.authored_prs:
        repo_status[pr.repo][pr.status].append(_make_authored_item(pr))

    for pr in report.reviewed_prs:
        repo_status[pr.repo][pr.status].append(_make_reviewed_item(pr))

    for pr in report.waiting_prs:
        repo_status[pr.repo]["Waiting for Review"].append(_make_waiting_item(pr))

    result: list[RepoContent] = []
    for repo in sorted(repo_status):
        blocks: list[ContentBlock] = []
        statuses = repo_status[repo]
        for status in _STATUS_ORDER:
            items = statuses.get(status)
            if items:
                blocks.append(ContentBlock(heading=status, items=items))
        if blocks:
            result.append(RepoContent(repo_name=repo, blocks=blocks))

    return result


def _regroup_by_status(report: ReportData) -> list[RepoContent]:
    """Group by status, then by project within each status."""
    # Collect all PRs by (status, repo)
    status_repo: dict[str, dict[str, list[ContentItem]]] = defaultdict(lambda: defaultdict(list))

    for pr in report.authored_prs:
        item = _make_authored_item(pr)
        # Clear status on item since the parent group IS the status
        item.status = ""
        status_repo[pr.status][pr.repo].append(item)

    for pr in report.reviewed_prs:
        item = _make_reviewed_item(pr)
        item.status = ""
        status_repo[pr.status][pr.repo].append(item)

    for pr in report.waiting_prs:
        status_repo["Waiting for Review"][pr.repo].append(_make_waiting_item(pr))

    result: list[RepoContent] = []
    for status in _STATUS_ORDER:
        repos = status_repo.get(status)
        if not repos:
            continue
        blocks = [
            ContentBlock(heading=repo, items=items)
            for repo, items in sorted(repos.items())
        ]
        result.append(RepoContent(repo_name=status, blocks=blocks))

    return result


def prepare_consolidated_content(
    report: ReportData,
    model: str = "claude-haiku-4-5-20251001",
    prompt: str | None = None,
) -> list[RepoContent]:
    """Build AI-consolidated RepoContent list using the Claude API.

    Groups PRs by repo, sends all repos in one Claude API call, and
    returns summarised RepoContent objects.

    Authentication: uses ANTHROPIC_API_KEY with the SDK when available,
    otherwise falls back to the ``claude`` CLI (which handles subscription
    and OAuth tokens natively).

    Args:
        report: Complete report data with populated PR lists.
        model: Claude model ID or alias (e.g. "sonnet") for consolidation.
        prompt: Custom system prompt. Uses default if None.

    Returns:
        List of RepoContent objects with summarised content.

    Raises:
        RuntimeError: If the API call fails or no auth method is available.
    """
    repos_data = _build_repos_data(report)
    if not repos_data:
        return []

    system_prompt = prompt or _DEFAULT_PROMPT
    user_message = json.dumps(repos_data, indent=2)

    # Choose backend: SDK (for API key) or CLI (for subscription/OAuth)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        text = _call_via_sdk(api_key, model, system_prompt, user_message)
    else:
        text = _call_via_cli(model, system_prompt, user_message)

    return _parse_response(text)


def prepare_ai_summary(
    report: ReportData,
    model: str = "claude-haiku-4-5-20251001",
    prompt: str | None = None,
) -> str:
    """Generate a short AI-powered summary of the report (<320 chars).

    Uses the same dual-backend as consolidation (SDK or CLI).

    Args:
        report: Complete report data with populated PR lists.
        model: Claude model ID for summarisation.
        prompt: Custom system prompt. Uses default if None.

    Returns:
        Summary string (AI is prompted to stay under 320 characters).

    Raises:
        RuntimeError: If the API call fails.
    """
    repos_data = _build_repos_data(report)
    if not repos_data:
        return ""

    system_prompt = prompt or _DEFAULT_SUMMARY_PROMPT
    user_message = json.dumps(repos_data, indent=2)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        text = _call_via_sdk(api_key, model, system_prompt, user_message)
    else:
        text = _call_via_cli(model, system_prompt, user_message)

    return text.strip()


def _call_via_sdk(
    api_key: str, model: str, system_prompt: str, user_message: str,
) -> str:
    """Call Claude via the anthropic Python SDK (API key auth)."""
    import anthropic  # lazy import

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            timeout=120.0,
            messages=[{"role": "user", "content": user_message}],
            system=system_prompt,
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"Claude API call failed: {e}") from e

    text = ""
    for block in response.content:
        if block.type == "text":
            text += block.text
    return text


def _call_via_cli(
    model: str, system_prompt: str, user_message: str,
) -> str:
    """Call Claude via the ``claude`` CLI (subscription / OAuth auth).

    Uses ``claude -p`` (print mode) which respects whatever authentication
    the user has configured in Claude Code (subscription, OAuth token, etc.).
    """
    full_prompt = f"{system_prompt}\n\n{user_message}"
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", model, "--output-format", "text"],
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "No ANTHROPIC_API_KEY set and 'claude' CLI not found. "
            "Either set ANTHROPIC_API_KEY or install Claude Code (claude)."
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude CLI call timed out after 180s") from None

    if result.returncode != 0:
        stderr = (result.stderr or "")[:500]
        raise RuntimeError(f"Claude CLI failed (exit {result.returncode}): {stderr}")

    text = result.stdout.strip()
    if not text:
        stderr = (result.stderr or "")[:500]
        raise RuntimeError(
            f"Claude CLI returned empty response. stderr: {stderr}"
        )
    return text


def _parse_response(text: str) -> list[RepoContent]:
    """Parse Claude's JSON response into RepoContent objects."""
    # Strip markdown code fences if present
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        # Remove first and last lines (``` markers)
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse Claude response as JSON: {e}") from e

    if not isinstance(parsed, dict):
        raise RuntimeError("Claude response is not a JSON object")

    result: list[RepoContent] = []
    for repo_name in sorted(parsed):
        items_data = parsed[repo_name]
        if not isinstance(items_data, list):
            continue
        items: list[ContentItem] = []
        for item in items_data:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", ""))[:500]
            numbers = item.get("numbers", [])
            if not isinstance(numbers, list):
                numbers = []
            numbers = [n for n in numbers if isinstance(n, int)]
            if title:
                items.append(ContentItem(title=title, numbers=numbers))
        if items:
            block = ContentBlock(heading="Summary", items=items)
            result.append(RepoContent(repo_name=repo_name, blocks=[block]))

    return result


def _build_repos_data(report: ReportData) -> dict[str, list[dict]]:
    """Build a dict of repo -> PR summaries for the AI prompt."""
    repos: dict[str, list[dict]] = defaultdict(list)

    for pr in report.authored_prs:
        repos[pr.repo].append({
            "number": pr.number,
            "title": pr.title,
            "status": pr.status,
            "type": "contributed" if pr.contributed else "authored",
        })

    for pr in report.reviewed_prs:
        repos[pr.repo].append({
            "number": pr.number,
            "title": pr.title,
            "status": pr.status,
            "type": "reviewed",
        })

    for pr in report.waiting_prs:
        repos[pr.repo].append({
            "number": pr.number,
            "title": pr.title,
            "type": "waiting_for_review",
        })

    return dict(repos)
