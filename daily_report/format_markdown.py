"""Markdown formatter for daily-report."""

from __future__ import annotations

from daily_report.report_data import ContentItem, ReportData


def format_markdown(report: ReportData, group_by: str = "project") -> str:
    """Render the report as a Markdown string.

    Args:
        report: Complete report data with content already prepared.
        group_by: Grouping mode — "project", "status", or "contribution".

    Returns:
        The full Markdown report as a single string.
    """
    lines: list[str] = []
    is_range = report.summary.is_range

    if is_range:
        lines.append(f"# Daily Report \u2014 {report.date_from} .. {report.date_to}")
    else:
        lines.append(f"# Daily Report \u2014 {report.date_from}")
    lines.append("")

    if report.content:
        for repo in report.content:
            # Section header (H2)
            if group_by == "project":
                lines.append(f"## `{repo.repo_name}`")
            else:
                lines.append(f"## {repo.repo_name}")
            lines.append("")
            for block in repo.blocks:
                # Block heading as L1 bullet
                if group_by == "project":
                    lines.append(f"- **{block.heading}**")
                else:
                    lines.append(f"- **`{block.heading}`**")
                # Determine repo name for PR links
                if group_by == "project":
                    link_repo = repo.repo_name
                else:
                    link_repo = block.heading
                # Items as indented L2 bullets
                for item in block.items:
                    lines.append(f"  - {_render_item(item, link_repo)}")
            lines.append("")
    else:
        lines.append("_No PR activity found._")
        lines.append("")

    # Summary
    s = report.summary
    if s.ai_summary:
        lines.append(f"**Summary:** {s.ai_summary}")
    else:
        themes_str = ", ".join(s.themes) if s.themes else "general development"
        merged_label = "merged" if is_range else "merged today"
        lines.append(
            f"**Summary:** {s.total_prs} PRs across {s.repo_count} repos, "
            f"{s.merged_count} {merged_label}, {s.open_count} still open. "
            f"Key themes: {themes_str}."
        )

    return "\n".join(lines)


def _pr_link(repo: str, number: int) -> str:
    """Build a Markdown link to a GitHub PR."""
    return f"[#{number}](https://github.com/{repo}/pull/{number})"


def _render_item(item: ContentItem, repo: str) -> str:
    """Render a ContentItem as Markdown text."""
    text = item.title

    if item.numbers:
        if len(item.numbers) == 1:
            text += f" {_pr_link(repo, item.numbers[0])}"
        else:
            refs = ", ".join(_pr_link(repo, n) for n in item.numbers)
            text += f" ({refs})"

    if item.author:
        text += f" ({item.author})"

    if item.status:
        text += f" \u2014 **{item.status}**"

    if item.status in ("Open", "Draft") and (item.additions or item.deletions):
        text += f" (+{item.additions}/\u2212{item.deletions})"

    if item.reviewers:
        reviewer_str = ", ".join(f"**{r}**" for r in item.reviewers)
        text += f" \u2014 reviewer: {reviewer_str}"

    if item.days_waiting:
        text += f" \u2014 {item.days_waiting} days"

    return text
