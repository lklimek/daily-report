"""Unit tests for daily_report/content.py: prepare_default_content,
prepare_consolidated_content, _call_via_sdk, _call_via_cli, and _parse_response.

Run with: python3 -m pytest tests/test_consolidate.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from daily_report.content import (
    _parse_response,
    prepare_ai_summary,
    prepare_consolidated_content,
    prepare_default_content,
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


# ---------------------------------------------------------------------------
# Fake anthropic module for tests (real anthropic may not be installed)
# ---------------------------------------------------------------------------

def _make_mock_anthropic():
    """Create a fake anthropic module with the required classes."""
    mock_module = MagicMock()
    mock_module.APIError = type("APIError", (Exception,), {
        "__init__": lambda self, message="", request=None, body=None: (
            Exception.__init__(self, message),
            setattr(self, "message", message),
            setattr(self, "request", request),
            setattr(self, "body", body),
        )[-1],
    })
    return mock_module


# ---------------------------------------------------------------------------
# Helpers
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
    return ReportData(**defaults)


# ---------------------------------------------------------------------------
# prepare_default_content tests
# ---------------------------------------------------------------------------

class TestPrepareDefaultContentGrouping:
    """Grouping, sorting, and structure."""

    def test_two_repos_produce_two_repo_contents(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/alpha", title="A", number=1,
                    status="Open", additions=1, deletions=0,
                    contributed=False, original_author=None,
                ),
                AuthoredPR(
                    repo="org/beta", title="B", number=2,
                    status="Open", additions=1, deletions=0,
                    contributed=False, original_author=None,
                ),
            ],
        )
        result = prepare_default_content(report)
        assert len(result) == 2

    def test_repos_sorted_alphabetically(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/zebra", title="Z", number=1,
                    status="Open", additions=1, deletions=0,
                    contributed=False, original_author=None,
                ),
                AuthoredPR(
                    repo="org/alpha", title="A", number=2,
                    status="Open", additions=1, deletions=0,
                    contributed=False, original_author=None,
                ),
            ],
        )
        result = prepare_default_content(report)
        assert result[0].repo_name == "org/alpha"
        assert result[1].repo_name == "org/zebra"

    def test_prs_across_all_types_grouped_by_repo(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="Authored", number=1,
                    status="Open", additions=1, deletions=0,
                    contributed=False, original_author=None,
                ),
            ],
            reviewed_prs=[
                ReviewedPR(
                    repo="org/repo", title="Reviewed", number=2,
                    author="bob", status="Merged",
                ),
            ],
            waiting_prs=[
                WaitingPR(
                    repo="org/repo", title="Waiting", number=3,
                    reviewers=["alice"], created_at="2026-02-08",
                    days_waiting=2,
                ),
            ],
        )
        result = prepare_default_content(report)
        assert len(result) == 1
        assert len(result[0].blocks) == 3


class TestPrepareDefaultContentBlockHeadings:
    """Verify correct block headings for each PR type."""

    def test_authored_block_heading(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="PR", number=1,
                    status="Open", additions=1, deletions=0,
                    contributed=False, original_author=None,
                ),
            ],
        )
        result = prepare_default_content(report)
        assert result[0].blocks[0].heading == "Authored / Contributed"

    def test_reviewed_block_heading(self):
        report = _make_report(
            reviewed_prs=[
                ReviewedPR(
                    repo="org/repo", title="PR", number=1,
                    author="bob", status="Merged",
                ),
            ],
        )
        result = prepare_default_content(report)
        assert result[0].blocks[0].heading == "Reviewed"

    def test_waiting_block_heading(self):
        report = _make_report(
            waiting_prs=[
                WaitingPR(
                    repo="org/repo", title="PR", number=1,
                    reviewers=["alice"], created_at="2026-02-08",
                    days_waiting=2,
                ),
            ],
        )
        result = prepare_default_content(report)
        assert result[0].blocks[0].heading == "Waiting for Review"


class TestPrepareDefaultContentAuthoredItems:
    """ContentItem fields from AuthoredPR."""

    def test_authored_item_fields(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="Add login", number=10,
                    status="Open", additions=50, deletions=10,
                    contributed=False, original_author=None,
                ),
            ],
        )
        result = prepare_default_content(report)
        item = result[0].blocks[0].items[0]
        assert item.title == "Add login"
        assert item.numbers == [10]
        assert item.status == "Open"
        assert item.additions == 50
        assert item.deletions == 10
        assert item.author == ""

    def test_contributed_item_shows_original_author(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="Fix crash", number=20,
                    status="Merged", additions=0, deletions=0,
                    contributed=True, original_author="bob",
                ),
            ],
        )
        result = prepare_default_content(report)
        item = result[0].blocks[0].items[0]
        assert item.author == "bob"


class TestPrepareDefaultContentReviewedItems:
    """ContentItem fields from ReviewedPR."""

    def test_reviewed_item_fields(self):
        report = _make_report(
            reviewed_prs=[
                ReviewedPR(
                    repo="org/repo", title="Update docs", number=11,
                    author="charlie", status="Open",
                ),
            ],
        )
        result = prepare_default_content(report)
        item = result[0].blocks[0].items[0]
        assert item.title == "Update docs"
        assert item.numbers == [11]
        assert item.status == "Open"
        assert item.author == "charlie"


class TestPrepareDefaultContentWaitingItems:
    """ContentItem fields from WaitingPR."""

    def test_waiting_item_fields(self):
        report = _make_report(
            waiting_prs=[
                WaitingPR(
                    repo="org/repo", title="Refactor DB", number=21,
                    reviewers=["dave", "eve"], created_at="2026-02-08",
                    days_waiting=2,
                ),
            ],
        )
        result = prepare_default_content(report)
        item = result[0].blocks[0].items[0]
        assert item.title == "Refactor DB"
        assert item.numbers == [21]
        assert item.reviewers == ["dave", "eve"]
        assert item.days_waiting == 2


class TestPrepareDefaultContentEmptyCases:
    """Empty blocks are skipped, empty report returns empty list."""

    def test_empty_report_returns_empty_list(self):
        report = _make_report()
        result = prepare_default_content(report)
        assert result == []

    def test_repo_with_only_authored_has_single_block(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="Solo PR", number=1,
                    status="Open", additions=5, deletions=2,
                    contributed=False, original_author=None,
                ),
            ],
        )
        result = prepare_default_content(report)
        assert len(result) == 1
        assert len(result[0].blocks) == 1
        assert result[0].blocks[0].heading == "Authored / Contributed"

    def test_repo_with_only_waiting_has_single_block(self):
        report = _make_report(
            waiting_prs=[
                WaitingPR(
                    repo="org/repo", title="Waiting PR", number=3,
                    reviewers=["alice"], created_at="2026-02-08",
                    days_waiting=5,
                ),
            ],
        )
        result = prepare_default_content(report)
        assert len(result) == 1
        assert len(result[0].blocks) == 1
        assert result[0].blocks[0].heading == "Waiting for Review"


# ---------------------------------------------------------------------------
# _parse_response tests
# ---------------------------------------------------------------------------

class TestParseResponse:
    """Tests for _parse_response JSON parsing and RepoContent building."""

    def test_valid_json_produces_repo_contents(self):
        data = {
            "org/alpha": [
                {"title": "Auth improvements", "numbers": [10, 11]},
            ],
            "org/beta": [
                {"title": "Bug fixes", "numbers": [20]},
            ],
        }
        result = _parse_response(json.dumps(data))
        assert len(result) == 2
        assert result[0].repo_name == "org/alpha"
        assert result[0].blocks[0].heading == "Summary"
        assert result[0].blocks[0].items[0].title == "Auth improvements"
        assert result[0].blocks[0].items[0].numbers == [10, 11]
        assert result[1].repo_name == "org/beta"

    def test_strips_markdown_code_fences(self):
        data = {"org/repo": [{"title": "Summary", "numbers": [1]}]}
        wrapped = "```json\n" + json.dumps(data) + "\n```"
        result = _parse_response(wrapped)
        assert len(result) == 1
        assert result[0].blocks[0].items[0].title == "Summary"

    def test_invalid_json_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="Failed to parse Claude response"):
            _parse_response("This is not valid JSON")

    def test_non_dict_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="not a JSON object"):
            _parse_response('["not", "a", "dict"]')

    def test_title_truncated_to_500_chars(self):
        long_title = "x" * 1000
        data = {"org/repo": [{"title": long_title, "numbers": [1]}]}
        result = _parse_response(json.dumps(data))
        assert len(result[0].blocks[0].items[0].title) == 500

    def test_non_int_numbers_filtered_out(self):
        data = {"org/repo": [{"title": "Test", "numbers": [1, "two", 3, None]}]}
        result = _parse_response(json.dumps(data))
        assert result[0].blocks[0].items[0].numbers == [1, 3]

    def test_repos_sorted_alphabetically(self):
        data = {
            "org/zebra": [{"title": "Z", "numbers": []}],
            "org/alpha": [{"title": "A", "numbers": []}],
        }
        result = _parse_response(json.dumps(data))
        assert result[0].repo_name == "org/alpha"
        assert result[1].repo_name == "org/zebra"


# ---------------------------------------------------------------------------
# prepare_consolidated_content tests (mocked backends)
# ---------------------------------------------------------------------------

class TestPrepareConsolidatedContentViaSDK:
    """Tests using the SDK backend (ANTHROPIC_API_KEY set)."""

    @pytest.fixture(autouse=True)
    def _install_mock_anthropic(self):
        """Install fake anthropic module into sys.modules for lazy imports."""
        self._mock_anthropic = _make_mock_anthropic()
        with patch.dict(sys.modules, {"anthropic": self._mock_anthropic}):
            yield

    def _make_mock_response(self, text: str) -> MagicMock:
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = text
        response = MagicMock()
        response.content = [text_block]
        return response

    def _report_with_prs(self) -> ReportData:
        return _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/alpha", title="Add login", number=10,
                    status="Open", additions=50, deletions=10,
                    contributed=False, original_author=None,
                ),
            ],
        )

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=False)
    def test_uses_sdk_when_api_key_set(self):
        response_data = {"org/alpha": [{"title": "Summary", "numbers": [10]}]}
        self._mock_anthropic.Anthropic.return_value.messages.create.return_value = (
            self._make_mock_response(json.dumps(response_data))
        )

        report = self._report_with_prs()
        result = prepare_consolidated_content(report)

        assert len(result) == 1
        assert result[0].blocks[0].items[0].title == "Summary"
        # Verify SDK was called
        self._mock_anthropic.Anthropic.assert_called_once_with(api_key="sk-test")

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=False)
    def test_sdk_api_error_raises_runtime_error(self):
        self._mock_anthropic.Anthropic.return_value.messages.create.side_effect = (
            self._mock_anthropic.APIError(message="rate limit", request=MagicMock(), body=None)
        )

        report = self._report_with_prs()
        with pytest.raises(RuntimeError, match="Claude API call failed"):
            prepare_consolidated_content(report)

    def test_empty_report_returns_empty_list(self):
        report = _make_report()
        result = prepare_consolidated_content(report)
        assert result == []


class TestPrepareConsolidatedContentViaCLI:
    """Tests using the CLI backend (no ANTHROPIC_API_KEY)."""

    def _report_with_prs(self) -> ReportData:
        return _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/alpha", title="Add login", number=10,
                    status="Open", additions=50, deletions=10,
                    contributed=False, original_author=None,
                ),
            ],
        )

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content.subprocess.run")
    def test_uses_cli_when_no_api_key(self, mock_run, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        response_data = {"org/alpha": [{"title": "Summary", "numbers": [10]}]}
        # CLI with --output-format text returns raw text
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(response_data), stderr="")

        report = self._report_with_prs()
        result = prepare_consolidated_content(report)

        assert len(result) == 1
        assert result[0].blocks[0].items[0].title == "Summary"
        # Verify CLI was called with correct args
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[:2] == ["claude", "-p"]
        assert "--model" in cmd
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "text"

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content.subprocess.run")
    def test_cli_failure_raises_runtime_error(self, mock_run, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="auth failed")

        report = self._report_with_prs()
        with pytest.raises(RuntimeError, match="Claude CLI failed"):
            prepare_consolidated_content(report)

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content.subprocess.run")
    def test_cli_not_found_raises_runtime_error(self, mock_run, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_run.side_effect = FileNotFoundError()

        report = self._report_with_prs()
        with pytest.raises(RuntimeError, match="claude.*CLI not found"):
            prepare_consolidated_content(report)

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content.subprocess.run")
    def test_cli_empty_response_raises_runtime_error(self, mock_run, monkeypatch):
        """Empty CLI output should raise a clear error."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        report = self._report_with_prs()
        with pytest.raises(RuntimeError, match="empty response"):
            prepare_consolidated_content(report)

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content.subprocess.run")
    def test_cli_timeout_raises_runtime_error(self, mock_run, monkeypatch):
        """CLI timeout should raise a clear error."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd="claude", timeout=180)

        report = self._report_with_prs()
        with pytest.raises(RuntimeError, match="timed out"):
            prepare_consolidated_content(report)


# ---------------------------------------------------------------------------
# prepare_ai_summary tests
# ---------------------------------------------------------------------------

class TestPrepareAiSummary:
    """Tests for prepare_ai_summary."""

    def _report_with_prs(self) -> ReportData:
        return _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/alpha", title="Add login", number=10,
                    status="Open", additions=50, deletions=10,
                    contributed=False, original_author=None,
                ),
            ],
        )

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content.subprocess.run")
    def test_returns_trimmed_text(self, mock_run, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_run.return_value = MagicMock(
            returncode=0, stdout="  Built login feature across repos.  ", stderr="",
        )
        result = prepare_ai_summary(self._report_with_prs())
        assert result == "Built login feature across repos."

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content.subprocess.run")
    def test_no_hard_truncation(self, mock_run, monkeypatch):
        """AI output is returned as-is (prompt controls length, no hard cut)."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_run.return_value = MagicMock(
            returncode=0, stdout="x" * 300, stderr="",
        )
        result = prepare_ai_summary(self._report_with_prs())
        assert len(result) == 300

    def test_empty_report_returns_empty_string(self):
        result = prepare_ai_summary(_make_report())
        assert result == ""

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content.subprocess.run")
    def test_uses_custom_prompt(self, mock_run, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Custom summary.", stderr="",
        )
        result = prepare_ai_summary(
            self._report_with_prs(), prompt="Custom prompt here",
        )
        assert result == "Custom summary."
        call_input = mock_run.call_args[1].get("input") or mock_run.call_args[0][0]
        # Verify the CLI received our custom prompt in stdin
        stdin_text = mock_run.call_args.kwargs.get("input", "")
        assert "Custom prompt here" in stdin_text
