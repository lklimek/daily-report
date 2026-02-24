"""Unit tests for format_slack module: Slack Block Kit formatter, truncation,
post_to_slack webhook poster, and CLI flag validation.

Run with: python3 -m pytest tests/test_format_slack.py -v
"""

import subprocess
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from daily_report.content import prepare_default_content
from daily_report.format_slack import (
    _MAX_BLOCKS,
    _TRUNCATION_SUFFIX,
    format_slack,
    post_to_slack,
)
from daily_report.report_data import (
    AuthoredPR,
    ContentBlock,
    ContentItem,
    RepoContent,
    ReportData,
    ReviewedPR,
    SummaryStats,
    WaitingPR,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers: reusable factories (same pattern as test_formatters.py)
# ---------------------------------------------------------------------------

def _make_report(**kwargs) -> ReportData:
    """Create a ReportData with sensible defaults, overridden by kwargs."""
    defaults = dict(
        user="testuser",
        date_from="2026-02-10",
        date_to="2026-02-10",
        authored_prs=[],
        reviewed_prs=[],
        waiting_prs=[],
        summary=SummaryStats(
            total_prs=0, repo_count=0, merged_count=0,
            open_count=0, themes=[], is_range=False,
        ),
    )
    defaults.update(kwargs)
    report = ReportData(**defaults)
    report.content = prepare_default_content(report)
    return report


def _make_full_report() -> ReportData:
    """Report with all sections populated across two repos."""
    return _make_report(
        user="alice",
        date_from="2026-02-10",
        date_to="2026-02-10",
        authored_prs=[
            AuthoredPR(
                repo="org/alpha", title="Add login", number=10,
                status="Open", additions=50, deletions=10,
                contributed=False, original_author=None,
            ),
            AuthoredPR(
                repo="org/beta", title="Fix crash", number=20,
                status="Merged", additions=0, deletions=0,
                contributed=True, original_author="bob",
            ),
        ],
        reviewed_prs=[
            ReviewedPR(
                repo="org/alpha", title="Update docs", number=11,
                author="charlie", status="Open",
            ),
        ],
        waiting_prs=[
            WaitingPR(
                repo="org/beta", title="Refactor DB", number=21,
                reviewers=["dave", "eve"], created_at="2026-02-08",
                days_waiting=2,
            ),
        ],
        summary=SummaryStats(
            total_prs=4, repo_count=2, merged_count=1,
            open_count=2, themes=["feat", "fix"], is_range=False,
        ),
    )


def _find_blocks_by_type(blocks, block_type):
    """Return all blocks of the given type."""
    return [b for b in blocks if b.get("type") == block_type]


def _all_section_texts(blocks):
    """Extract all mrkdwn text values from section blocks."""
    texts = []
    for b in blocks:
        if b.get("type") == "section":
            text_obj = b.get("text", {})
            if text_obj.get("type") == "mrkdwn":
                texts.append(text_obj["text"])
    return texts


# ---------------------------------------------------------------------------
# format_slack() tests
# ---------------------------------------------------------------------------

class TestFormatSlackBasic:
    """Basic structure of the Slack payload."""

    def test_returns_dict_with_blocks_key(self):
        report = _make_full_report()
        result = format_slack(report)
        assert isinstance(result, dict)
        assert "blocks" in result
        assert isinstance(result["blocks"], list)

    def test_header_block_type(self):
        report = _make_full_report()
        result = format_slack(report)
        headers = _find_blocks_by_type(result["blocks"], "header")
        assert len(headers) == 1
        header = headers[0]
        assert header["text"]["type"] == "plain_text"

    def test_header_contains_user_and_date(self):
        report = _make_full_report()
        result = format_slack(report)
        header = _find_blocks_by_type(result["blocks"], "header")[0]
        header_text = header["text"]["text"]
        assert "alice" in header_text
        assert "2026-02-10" in header_text

    def test_header_with_date_range(self):
        report = _make_report(
            user="rangeuser",
            date_from="2026-02-03",
            date_to="2026-02-09",
            summary=SummaryStats(
                total_prs=0, repo_count=0, merged_count=0,
                open_count=0, themes=[], is_range=True,
            ),
        )
        result = format_slack(report)
        header = _find_blocks_by_type(result["blocks"], "header")[0]
        header_text = header["text"]["text"]
        assert "2026-02-03" in header_text
        assert "2026-02-09" in header_text


class TestFormatSlackRepoSections:
    """Repository section formatting."""

    def test_repo_names_present(self):
        report = _make_full_report()
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        combined = "\n".join(texts)
        assert "`org/alpha`" in combined
        assert "`org/beta`" in combined

    def test_repos_sorted_alphabetically(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/zebra", title="Z PR", number=1,
                    status="Open", additions=1, deletions=0,
                    contributed=False, original_author=None,
                ),
                AuthoredPR(
                    repo="org/alpha", title="A PR", number=2,
                    status="Open", additions=1, deletions=0,
                    contributed=False, original_author=None,
                ),
            ],
            summary=SummaryStats(
                total_prs=2, repo_count=2, merged_count=0,
                open_count=2, themes=[], is_range=False,
            ),
        )
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        # Find repo header blocks
        repo_headers = [t for t in texts if t.startswith("*`")]
        assert len(repo_headers) >= 2
        alpha_idx = next(i for i, t in enumerate(texts) if "`org/alpha`" in t)
        zebra_idx = next(i for i, t in enumerate(texts) if "`org/zebra`" in t)
        assert alpha_idx < zebra_idx


class TestFormatSlackAuthoredPRs:
    """Authored / contributed PR line formatting."""

    def test_authored_pr_title_and_number(self):
        report = _make_full_report()
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        combined = "\n".join(texts)
        assert "Add login" in combined
        assert "#10" in combined

    def test_authored_pr_bold_status(self):
        report = _make_full_report()
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        combined = "\n".join(texts)
        assert "*Open*" in combined

    def test_contributed_pr_shows_original_author(self):
        report = _make_full_report()
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        combined = "\n".join(texts)
        # "Fix crash" is contributed by bob
        assert "(bob)" in combined


class TestFormatSlackReviewedPRs:
    """Reviewed PR line formatting."""

    def test_reviewed_pr_includes_author(self):
        report = _make_full_report()
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        combined = "\n".join(texts)
        assert "Update docs" in combined
        assert "(charlie)" in combined


class TestFormatSlackWaitingPRs:
    """Waiting for review PR line formatting."""

    def test_waiting_pr_reviewers_bold(self):
        report = _make_full_report()
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        combined = "\n".join(texts)
        assert "*dave*" in combined
        assert "*eve*" in combined

    def test_waiting_pr_days(self):
        report = _make_full_report()
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        combined = "\n".join(texts)
        assert "2 days" in combined


class TestFormatSlackStats:
    """Addition/deletion stats for Open/Draft vs Merged PRs."""

    def test_open_pr_shows_stats(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="Open PR", number=1,
                    status="Open", additions=30, deletions=12,
                    contributed=False, original_author=None,
                ),
            ],
            summary=SummaryStats(
                total_prs=1, repo_count=1, merged_count=0,
                open_count=1, themes=[], is_range=False,
            ),
        )
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        combined = "\n".join(texts)
        assert "+30/-12" in combined

    def test_draft_pr_shows_stats(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="Draft PR", number=2,
                    status="Draft", additions=15, deletions=3,
                    contributed=False, original_author=None,
                ),
            ],
            summary=SummaryStats(
                total_prs=1, repo_count=1, merged_count=0,
                open_count=1, themes=[], is_range=False,
            ),
        )
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        combined = "\n".join(texts)
        assert "+15/-3" in combined

    def test_merged_pr_no_stats(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="Merged PR", number=3,
                    status="Merged", additions=0, deletions=0,
                    contributed=False, original_author=None,
                ),
            ],
            summary=SummaryStats(
                total_prs=1, repo_count=1, merged_count=1,
                open_count=0, themes=[], is_range=False,
            ),
        )
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        # Find the line with the PR
        pr_lines = [t for t in texts if "Merged PR" in t]
        assert len(pr_lines) > 0
        assert "(+0" not in pr_lines[0]
        assert "/-0" not in pr_lines[0]


class TestFormatSlackSummary:
    """Summary section at the end of the payload."""

    def test_summary_block_present(self):
        report = _make_full_report()
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        summary_texts = [t for t in texts if "*Summary*" in t]
        assert len(summary_texts) == 1

    def test_summary_contains_metrics(self):
        report = _make_full_report()
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        summary = [t for t in texts if "*Summary*" in t][0]
        assert "4 PRs across 2 repos" in summary
        assert "1 merged today" in summary
        assert "2 still open" in summary
        assert "feat, fix" in summary


class TestFormatSlackAiSummary:
    """AI-generated summary replaces default summary bullets."""

    def test_ai_summary_replaces_default(self):
        report = _make_full_report()
        report.summary.ai_summary = "Auth and bug fixes across platform."
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        summary = [t for t in texts if "*Summary*" in t][0]
        assert "Auth and bug fixes across platform." in summary
        assert "PRs across" not in summary

    def test_empty_ai_summary_uses_default(self):
        report = _make_full_report()
        report.summary.ai_summary = ""
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        summary = [t for t in texts if "*Summary*" in t][0]
        assert "PRs across" in summary


class TestFormatSlackDividers:
    """Divider blocks between repo sections."""

    def test_dividers_between_sections(self):
        report = _make_full_report()
        result = format_slack(report)
        dividers = _find_blocks_by_type(result["blocks"], "divider")
        # At least: 1 after header + 1 per repo (2 repos) = 3 dividers
        assert len(dividers) >= 3


class TestFormatSlackEmpty:
    """Empty report with no PRs."""

    def test_empty_report_no_activity_message(self):
        report = _make_report()
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        combined = "\n".join(texts)
        assert "No PR activity" in combined

    def test_empty_subsections_omitted(self):
        """Only authored PRs, no reviewed or waiting -- those sections are omitted."""
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="Solo PR", number=1,
                    status="Open", additions=5, deletions=2,
                    contributed=False, original_author=None,
                ),
            ],
            summary=SummaryStats(
                total_prs=1, repo_count=1, merged_count=0,
                open_count=1, themes=[], is_range=False,
            ),
        )
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        combined = "\n".join(texts)
        assert "*Worked on*" in combined
        assert "*Reviewed*" not in combined
        assert "*Waiting for Review*" not in combined


# ---------------------------------------------------------------------------
# Truncation tests
# ---------------------------------------------------------------------------

class TestFormatSlackTruncation:
    """Block count and text length truncation."""

    def test_block_count_never_exceeds_max(self):
        """Generate many repos to trigger block budget exhaustion."""
        authored_prs = []
        for i in range(60):
            authored_prs.append(AuthoredPR(
                repo=f"org/repo-{i:03d}", title=f"PR {i}", number=i + 1,
                status="Open", additions=1, deletions=0,
                contributed=False, original_author=None,
            ))
        report = _make_report(
            authored_prs=authored_prs,
            summary=SummaryStats(
                total_prs=60, repo_count=60, merged_count=0,
                open_count=60, themes=[], is_range=False,
            ),
        )
        result = format_slack(report)
        assert len(result["blocks"]) <= _MAX_BLOCKS

    def test_text_truncation_long_text(self):
        """When a single section has extremely long text, it gets truncated."""
        # Create many PRs in a single repo to produce a very long authored section
        authored_prs = []
        for i in range(200):
            authored_prs.append(AuthoredPR(
                repo="org/single-repo",
                title="A" * 50 + f" PR number {i}",
                number=i + 1,
                status="Open", additions=999, deletions=999,
                contributed=False, original_author=None,
            ))
        report = _make_report(
            authored_prs=authored_prs,
            summary=SummaryStats(
                total_prs=200, repo_count=1, merged_count=0,
                open_count=200, themes=[], is_range=False,
            ),
        )
        result = format_slack(report)
        # Check that no text block exceeds 3000 characters
        for block in result["blocks"]:
            text_obj = block.get("text", {})
            if isinstance(text_obj, dict) and "text" in text_obj:
                assert len(text_obj["text"]) <= 3000

    def test_truncation_notice_appended(self):
        """When text is truncated, the truncation suffix is present."""
        authored_prs = []
        for i in range(200):
            authored_prs.append(AuthoredPR(
                repo="org/single-repo",
                title="B" * 50 + f" item {i}",
                number=i + 1,
                status="Open", additions=100, deletions=50,
                contributed=False, original_author=None,
            ))
        report = _make_report(
            authored_prs=authored_prs,
            summary=SummaryStats(
                total_prs=200, repo_count=1, merged_count=0,
                open_count=200, themes=[], is_range=False,
            ),
        )
        result = format_slack(report)
        texts = _all_section_texts(result["blocks"])
        truncated = [t for t in texts if _TRUNCATION_SUFFIX in t]
        assert len(truncated) >= 1


# ---------------------------------------------------------------------------
# post_to_slack() tests
# ---------------------------------------------------------------------------

class TestPostToSlack:
    """Webhook posting via urllib, all network calls mocked."""

    VALID_URL = "https://hooks.slack.com/services/T00/B00/XXXX"

    @patch("daily_report.format_slack.urllib.request.urlopen")
    def test_successful_post(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"ok"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        # Should not raise
        post_to_slack(self.VALID_URL, {"blocks": []})
        mock_urlopen.assert_called_once()

    def test_invalid_url_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid Slack webhook URL"):
            post_to_slack("https://example.com/bad", {"blocks": []})

    def test_empty_url_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid Slack webhook URL"):
            post_to_slack("", {"blocks": []})

    @patch("daily_report.format_slack.urllib.request.urlopen")
    def test_http_error_raises_runtime_error(self, mock_urlopen):
        http_err = urllib.error.HTTPError(
            url=self.VALID_URL, code=400, msg="Bad Request",
            hdrs=None, fp=BytesIO(b"invalid_payload"),
        )
        mock_urlopen.side_effect = http_err

        with pytest.raises(RuntimeError, match="HTTP 400"):
            post_to_slack(self.VALID_URL, {"blocks": []})

    @patch("daily_report.format_slack.urllib.request.urlopen")
    def test_url_error_raises_connection_error(self, mock_urlopen):
        url_err = urllib.error.URLError(reason="Name resolution failed")
        mock_urlopen.side_effect = url_err

        with pytest.raises(ConnectionError, match="Failed to connect"):
            post_to_slack(self.VALID_URL, {"blocks": []})

    @patch("daily_report.format_slack.urllib.request.urlopen")
    def test_non_ok_response_raises_runtime_error(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"no_service"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with pytest.raises(RuntimeError, match="unexpected response"):
            post_to_slack(self.VALID_URL, {"blocks": []})

    def test_subdomain_bypass_rejected(self):
        """URL like hooks.slack.com.evil.com should be rejected."""
        with pytest.raises(ValueError, match="Invalid Slack webhook URL"):
            post_to_slack("https://hooks.slack.com.evil.com/services/T/B/X", {"blocks": []})

    def test_missing_services_path_rejected(self):
        """URL without /services/ path should be rejected."""
        with pytest.raises(ValueError, match="Invalid Slack webhook URL"):
            post_to_slack("https://hooks.slack.com/other/path", {"blocks": []})

    def test_http_scheme_rejected(self):
        """HTTP (not HTTPS) should be rejected."""
        with pytest.raises(ValueError, match="Invalid Slack webhook URL"):
            post_to_slack("http://hooks.slack.com/services/T/B/X", {"blocks": []})


# ---------------------------------------------------------------------------
# CLI flag tests
# ---------------------------------------------------------------------------

class TestCLISlackFlags:
    """CLI argument validation for --slack and --slack-webhook."""

    def test_slack_webhook_without_slack_errors(self):
        """--slack-webhook without --slack should exit with error."""
        cmd = [
            sys.executable, "-m", "daily_report",
            "--slack-webhook", "https://hooks.slack.com/services/T/B/X",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode != 0
        assert "--slack-webhook requires --slack" in result.stderr

    def test_slack_and_slides_mutual_exclusion(self):
        """--slack and --slides together should exit with error."""
        cmd = [
            sys.executable, "-m", "daily_report",
            "--slack", "--slides",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode != 0
        assert "mutually exclusive" in result.stderr
