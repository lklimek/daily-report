"""Slack Block Kit formatter and webhook poster for daily-report.

Uses only stdlib modules (json, urllib.request, urllib.error).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

from daily_report.report_data import ContentItem, RepoContent, ReportData


# Slack limits
_MAX_BLOCKS = 50
_MAX_TEXT_LENGTH = 3000
_TRUNCATION_SUFFIX = "\n\u2026 (truncated)"


def format_slack(report: ReportData, group_by: str = "project") -> dict:
    """Build a Slack Block Kit payload from the report.

    Args:
        report: Complete report data with content already prepared.
        group_by: Grouping mode — "project", "status", or "contribution".

    Returns:
        A dict suitable for JSON-encoding and posting to a Slack webhook.
    """
    blocks: list[dict] = []

    blocks.extend(_header_blocks(report))

    if not report.content:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "No PR activity found for this period."},
        })
        return {"blocks": blocks}

    # Budget: reserve 2 blocks for summary section (section + divider before it)
    budget = _MAX_BLOCKS - len(blocks) - 2
    repos_added = 0

    for repo in report.content:
        repo_blocks = _content_blocks(repo, group_by)
        # Each repo section also gets a trailing divider
        needed = len(repo_blocks) + 1
        if needed > budget:
            remaining = len(report.content) - repos_added
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
    if not webhook_url:
        raise ValueError("Invalid Slack webhook URL: must not be empty.")
    parsed = urlparse(webhook_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "hooks.slack.com"
        or not parsed.path.startswith("/services/")
    ):
        raise ValueError(
            "Invalid Slack webhook URL. Must be https://hooks.slack.com/services/..."
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
            body_text = e.read().decode("utf-8", errors="replace")[:500]
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


def _content_blocks(repo: RepoContent, group_by: str = "project") -> list[dict]:
    """Build section blocks for a single repository's content."""
    blocks: list[dict] = []

    # Repo header — use backticks for project mode (repo names), plain for status/contribution
    if group_by == "project":
        header_text = f"*`{repo.repo_name}`*"
    else:
        header_text = f"*{repo.repo_name}*"
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": header_text},
    })

    # Build all blocks as a single mrkdwn section with inline labels
    content_lines: list[str] = []
    for block in repo.blocks:
        if group_by == "project":
            label = f"*{block.heading}*"
        else:
            label = f"*`{block.heading}`*"
        skip_status = {repo.repo_name, block.heading}
        for item in block.items:
            content_lines.append(f"\u2022 {label}: {_render_item(item, skip_status)}")
    if content_lines:
        text = _truncate_text("\n".join(content_lines))
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        })

    return blocks


def _summary_blocks(report: ReportData) -> list[dict]:
    """Build summary section blocks."""
    s = report.summary

    if s.ai_summary:
        lines = ["*Summary*", s.ai_summary]
    else:
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


def _render_item(item: ContentItem, skip_status: set[str] | None = None) -> str:
    """Render a ContentItem as Slack mrkdwn bullet text."""
    text = item.title

    if item.numbers:
        if len(item.numbers) == 1:
            text += f" #{item.numbers[0]}"
        else:
            refs = ", ".join(f"#{n}" for n in item.numbers)
            text += f" ({refs})"

    if item.author:
        text += f" ({item.author})"

    if item.status and item.status not in (skip_status or set()):
        text += f" \u2014 *{item.status}*"

    if item.status in ("Open", "Draft") and (item.additions or item.deletions):
        text += f" (+{item.additions}/-{item.deletions})"

    if item.reviewers:
        reviewer_str = ", ".join(f"*{r}*" for r in item.reviewers)
        text += f" \u2014 reviewer: {reviewer_str}"

    if item.days_waiting:
        text += f" \u2014 {item.days_waiting} days"

    return text


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
