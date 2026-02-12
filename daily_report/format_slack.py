"""Slack Block Kit formatter and webhook poster for daily-report.

Uses only stdlib modules (json, urllib.request, urllib.error).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from daily_report.report_data import (
    ReportData, AuthoredPR, ReviewedPR, WaitingPR,
)


# Slack limits
_MAX_BLOCKS = 50
_MAX_TEXT_LENGTH = 3000
_TRUNCATION_SUFFIX = "\n\u2026 (truncated)"


def format_slack(report: ReportData) -> dict:
    """Build a Slack Block Kit payload from the report.

    Args:
        report: Complete report data.

    Returns:
        A dict suitable for JSON-encoding and posting to a Slack webhook.
    """
    blocks: list[dict] = []

    blocks.extend(_header_blocks(report))

    projects = _group_by_repo(report)
    if not projects:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "No PR activity found for this period."},
        })
        return {"blocks": blocks}

    # Budget: reserve 2 blocks for summary section (section + divider before it)
    budget = _MAX_BLOCKS - len(blocks) - 2
    sorted_repos = sorted(projects.keys())
    repos_added = 0

    for repo_name in sorted_repos:
        group = projects[repo_name]
        repo_blocks = _repo_blocks(
            repo_name, group["authored"], group["reviewed"], group["waiting"],
        )
        # Each repo section also gets a trailing divider
        needed = len(repo_blocks) + 1
        if needed > budget:
            remaining = len(sorted_repos) - repos_added
            if remaining > 0:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"\u2026 and {remaining} more repositories",
                    },
                })
            break
        blocks.extend(repo_blocks)
        blocks.append({"type": "divider"})
        budget -= needed
        repos_added += 1

    blocks.extend(_summary_blocks(report))

    return {"blocks": blocks}


def post_to_slack(webhook_url: str, payload: dict, timeout: int = 30) -> None:
    """Post a Block Kit payload to a Slack Incoming Webhook.

    Args:
        webhook_url: The Slack webhook URL.
        payload: The Block Kit payload dict.
        timeout: HTTP request timeout in seconds.

    Raises:
        ValueError: If the webhook URL is invalid.
        ConnectionError: If the HTTP request fails (network error).
        RuntimeError: If Slack returns a non-ok response.
    """
    if not webhook_url or not webhook_url.startswith("https://hooks.slack.com/"):
        raise ValueError(
            f"Invalid Slack webhook URL. Must start with https://hooks.slack.com/"
        )

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            response_body = resp.read().decode("utf-8", errors="replace")
            if response_body.strip() != "ok":
                raise RuntimeError(f"Slack returned unexpected response: {response_body}")
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(
            f"Slack webhook returned HTTP {e.code}: {body_text}"
        ) from e
    except urllib.error.URLError as e:
        raise ConnectionError(f"Failed to connect to Slack: {e.reason}") from e


# --- internal helpers (private) ---


def _header_blocks(report: ReportData) -> list[dict]:
    """Build the header block and divider."""
    if report.date_from == report.date_to:
        date_str = report.date_from
    else:
        date_str = f"{report.date_from} \u2014 {report.date_to}"

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":bar_chart: Activity Report \u2014 {report.user} \u2014 {date_str}",
                "emoji": True,
            },
        },
        {"type": "divider"},
    ]


def _repo_blocks(repo_name: str, authored: list[AuthoredPR],
                 reviewed: list[ReviewedPR],
                 waiting: list[WaitingPR]) -> list[dict]:
    """Build section blocks for a single repository."""
    blocks: list[dict] = []

    # Repo header
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*`{repo_name}`*"},
    })

    if authored:
        lines = [f"*Authored / Contributed*"]
        for pr in authored:
            lines.append(_authored_pr_line(pr))
        text = _truncate_text("\n".join(lines))
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        })

    if reviewed:
        lines = [f"*Reviewed*"]
        for pr in reviewed:
            lines.append(_reviewed_pr_line(pr))
        text = _truncate_text("\n".join(lines))
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        })

    if waiting:
        lines = [f"*Waiting for Review*"]
        for pr in waiting:
            lines.append(_waiting_pr_line(pr))
        text = _truncate_text("\n".join(lines))
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        })

    return blocks


def _summary_blocks(report: ReportData) -> list[dict]:
    """Build summary section blocks."""
    s = report.summary
    themes_str = ", ".join(s.themes) if s.themes else "general development"
    merged_label = "merged" if s.is_range else "merged today"

    lines = [
        "*Summary*",
        f"\u2022 {s.total_prs} PRs across {s.repo_count} repos",
        f"\u2022 {s.merged_count} {merged_label}",
        f"\u2022 {s.open_count} still open",
        f"\u2022 Key themes: {themes_str}",
    ]

    return [{
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(lines)},
    }]


def _group_by_repo(report: ReportData) -> dict[str, dict]:
    """Group all PR lists by repository name.

    Returns:
        Dict mapping repo name to {"authored": [...], "reviewed": [...], "waiting": [...]}.
        Only repos with at least one item are included.
    """
    projects: dict[str, dict] = {}
    for pr in report.authored_prs:
        projects.setdefault(pr.repo, {"authored": [], "reviewed": [], "waiting": []})
        projects[pr.repo]["authored"].append(pr)
    for pr in report.reviewed_prs:
        projects.setdefault(pr.repo, {"authored": [], "reviewed": [], "waiting": []})
        projects[pr.repo]["reviewed"].append(pr)
    for pr in report.waiting_prs:
        projects.setdefault(pr.repo, {"authored": [], "reviewed": [], "waiting": []})
        projects[pr.repo]["waiting"].append(pr)
    return projects


def _authored_pr_line(pr: AuthoredPR) -> str:
    """Build a Slack mrkdwn bullet line for an authored/contributed PR."""
    line = f"\u2022 {pr.title} #{pr.number}"
    if pr.contributed and pr.original_author:
        line += f" ({pr.original_author})"
    line += f" \u2014 *{pr.status}*"
    if pr.status in ("Open", "Draft"):
        line += f" (+{pr.additions}/-{pr.deletions})"
    return line


def _reviewed_pr_line(pr: ReviewedPR) -> str:
    """Build a Slack mrkdwn bullet line for a reviewed PR."""
    return f"\u2022 {pr.title} #{pr.number} ({pr.author}) \u2014 *{pr.status}*"


def _waiting_pr_line(pr: WaitingPR) -> str:
    """Build a Slack mrkdwn bullet line for a PR waiting for review."""
    reviewers = ", ".join(f"*{r}*" for r in pr.reviewers)
    return f"\u2022 {pr.title} #{pr.number} \u2014 reviewer: {reviewers} \u2014 {pr.days_waiting} days"


def _truncate_text(text: str, max_len: int = 2900) -> str:
    """Truncate text to stay within Slack's 3000-char/block limit.

    Uses a default of 2900 to leave room for the truncation suffix.

    Args:
        text: The text to potentially truncate.
        max_len: Maximum character count before truncation.

    Returns:
        The original text if within limits, otherwise truncated with suffix.
    """
    if len(text) <= max_len:
        return text
    return text[:max_len] + _TRUNCATION_SUFFIX
