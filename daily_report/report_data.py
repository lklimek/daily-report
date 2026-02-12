"""Structured report data model, consumed by all formatters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class AuthoredPR:
    """A PR authored or contributed to by the user."""
    repo: str
    title: str
    number: int
    status: str              # "Open", "Draft", "Merged", "Closed"
    additions: int           # line additions (0 for Merged/Closed)
    deletions: int           # line deletions (0 for Merged/Closed)
    contributed: bool        # True if user is contributor, not author
    original_author: Optional[str]  # PR author login when contributed=True


@dataclass
class ReviewedPR:
    """A PR reviewed or approved by the user."""
    repo: str
    title: str
    number: int
    author: str              # PR author login
    status: str              # "Open", "Draft", "Merged", "Closed"


@dataclass
class WaitingPR:
    """A PR authored by the user that is waiting for review."""
    repo: str
    title: str
    number: int
    reviewers: List[str]     # logins of pending reviewers
    created_at: str          # YYYY-MM-DD
    days_waiting: int


@dataclass
class SummaryStats:
    """Aggregate metrics for the report."""
    total_prs: int
    repo_count: int
    merged_count: int
    open_count: int
    themes: List[str]        # conventional commit prefixes found
    is_range: bool           # True if date_from != date_to
    ai_summary: str = ""     # AI-generated summary; replaces default when set


@dataclass
class ContentItem:
    """A single renderable item with semantic fields. Formatters read fields and render."""
    title: str
    numbers: List[int] = field(default_factory=list)
    status: str = ""
    additions: int = 0
    deletions: int = 0
    author: str = ""
    reviewers: List[str] = field(default_factory=list)
    days_waiting: int = 0


@dataclass
class ContentBlock:
    """A group of items under a heading (e.g. 'Authored / Contributed')."""
    heading: str
    items: List[ContentItem] = field(default_factory=list)


@dataclass
class RepoContent:
    """All content blocks for a single repository."""
    repo_name: str
    blocks: List[ContentBlock] = field(default_factory=list)


@dataclass
class ReportData:
    """Complete report data, produced by the pipeline and consumed by formatters."""
    user: str
    date_from: str           # YYYY-MM-DD
    date_to: str             # YYYY-MM-DD
    authored_prs: List[AuthoredPR] = field(default_factory=list)
    reviewed_prs: List[ReviewedPR] = field(default_factory=list)
    waiting_prs: List[WaitingPR] = field(default_factory=list)
    summary: SummaryStats = field(default_factory=lambda: SummaryStats(
        total_prs=0, repo_count=0, merged_count=0, open_count=0,
        themes=[], is_range=False,
    ))
    content: List[RepoContent] = field(default_factory=list)
