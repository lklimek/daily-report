"""PPTX slide deck formatter for daily-report.

Requires python-pptx: pip install python-pptx
"""

from __future__ import annotations

from pptx import Presentation
from pptx.util import Inches, Pt

from daily_report.report_data import ContentItem, RepoContent, ReportData


def format_slides(report: ReportData, output_path: str) -> None:
    """Render the report as a PPTX slide deck.

    Args:
        report: Complete report data with content already prepared.
        output_path: File path to write the .pptx file.

    Raises:
        OSError: If the file cannot be written (permissions, missing directory).
    """
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    _add_title_slide(prs, report)

    for repo in report.content:
        _add_content_slide(prs, repo)

    _add_summary_slide(prs, report)

    prs.save(output_path)


# --- internal helpers (private) ---


def _add_title_slide(prs: Presentation, report: ReportData) -> None:
    """Add the title slide with user and date range."""
    layout = prs.slide_layouts[0]  # Title Slide
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = "Activity Report"
    if report.date_from == report.date_to:
        subtitle_text = f"{report.user}\n{report.date_from}"
    else:
        subtitle_text = f"{report.user}\n{report.date_from} .. {report.date_to}"
    slide.placeholders[1].text = subtitle_text


def _add_content_slide(prs: Presentation, repo: RepoContent) -> None:
    """Add a content slide for a single repository."""
    layout = prs.slide_layouts[1]  # Title and Content
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = repo.repo_name

    tf = slide.placeholders[1].text_frame
    tf.clear()
    first_paragraph = True

    for block in repo.blocks:
        # Block heading
        p = tf.paragraphs[0] if first_paragraph else tf.add_paragraph()
        first_paragraph = False
        p.text = block.heading
        p.level = 0
        run = p.runs[0]
        run.font.bold = True
        run.font.size = Pt(14)

        # Block items
        for item in block.items:
            p = tf.add_paragraph()
            p.text = _render_item(item)
            p.level = 1
            p.runs[0].font.size = Pt(12)


def _add_summary_slide(prs: Presentation, report: ReportData) -> None:
    """Add the summary slide with aggregate metrics."""
    layout = prs.slide_layouts[1]  # Title and Content
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = "Summary"

    s = report.summary

    if s.ai_summary:
        bullets = [s.ai_summary]
    else:
        themes_str = ", ".join(s.themes) if s.themes else "general development"
        merged_label = "merged" if s.is_range else "merged today"
        bullets = [
            f"Total PRs: {s.total_prs}",
            f"Repositories: {s.repo_count}",
            f"{s.merged_count} {merged_label}",
            f"{s.open_count} still open",
            f"Key themes: {themes_str}",
        ]

    tf = slide.placeholders[1].text_frame
    tf.clear()
    for i, text in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = text
        p.level = 0
        p.runs[0].font.size = Pt(14)


def _render_item(item: ContentItem) -> str:
    """Render a ContentItem as plain text for slides."""
    text = item.title

    if item.numbers:
        if len(item.numbers) == 1:
            text += f" #{item.numbers[0]}"
        else:
            refs = ", ".join(f"#{n}" for n in item.numbers)
            text += f" ({refs})"

    if item.author:
        text += f" ({item.author})"

    if item.status:
        text += f" -- {item.status}"

    if item.status in ("Open", "Draft") and (item.additions or item.deletions):
        text += f" (+{item.additions}/-{item.deletions})"

    if item.reviewers:
        reviewer_str = ", ".join(item.reviewers)
        text += f" -- reviewer: {reviewer_str}"

    if item.days_waiting:
        text += f" -- {item.days_waiting} days"

    return text
