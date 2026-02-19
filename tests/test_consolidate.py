"""Unit tests for daily_report/content.py: prepare_default_content,
prepare_consolidated_content, _call_via_sdk, _call_via_sdk_agent,
_parse_response, _parse_and_validate, _call_with_retry, and _load_schema.

Run with: python3 -m pytest tests/test_consolidate.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import daily_report.content as _content_module
from daily_report.content import (
    _build_repos_data,
    _call_with_retry,
    _dedup_pr_lists,
    _load_prompt,
    _load_schema,
    _parse_and_validate,
    _parse_response,
    _serialize_grouped_content,
    prepare_ai_summary,
    prepare_consolidated_content,
    prepare_default_content,
    regroup_content,
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


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset the schema and prompt caches before each test."""
    _content_module._schema_cache = None
    _content_module._prompt_cache.clear()
    yield
    _content_module._schema_cache = None
    _content_module._prompt_cache.clear()


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
            "org/alpha": {
                "authored": [{"title": "Auth improvements", "numbers": [10, 11]}],
            },
            "org/beta": {
                "reviewed": [{"title": "Bug fixes", "numbers": [20]}],
            },
        }
        result = _parse_response(json.dumps(data))
        assert len(result) == 2
        assert result[0].repo_name == "org/alpha"
        assert result[0].blocks[0].heading == "authored"
        assert result[0].blocks[0].items[0].title == "Auth improvements"
        assert result[0].blocks[0].items[0].numbers == [10, 11]
        assert result[1].repo_name == "org/beta"

    def test_strips_markdown_code_fences(self):
        data = {"org/repo": {"Summary": [{"title": "Summary", "numbers": [1]}]}}
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
        data = {"org/repo": {"sub": [{"title": long_title, "numbers": [1]}]}}
        result = _parse_response(json.dumps(data))
        assert len(result[0].blocks[0].items[0].title) == 500

    def test_non_int_numbers_filtered_out(self):
        data = {"org/repo": {"sub": [{"title": "Test", "numbers": [1, "two", 3, None]}]}}
        result = _parse_response(json.dumps(data))
        assert result[0].blocks[0].items[0].numbers == [1, 3]

    def test_groups_sorted_alphabetically(self):
        data = {
            "org/zebra": {"sub": [{"title": "Z", "numbers": []}]},
            "org/alpha": {"sub": [{"title": "A", "numbers": []}]},
        }
        result = _parse_response(json.dumps(data))
        assert result[0].repo_name == "org/alpha"
        assert result[1].repo_name == "org/zebra"

    def test_multiple_subgroups_produce_multiple_blocks(self):
        data = {
            "Authored / Contributed": {
                "org/alpha": [{"title": "Feature A", "numbers": [1]}],
                "org/beta": [{"title": "Feature B", "numbers": [2]}],
            },
        }
        result = _parse_response(json.dumps(data))
        assert len(result) == 1
        assert len(result[0].blocks) == 2
        assert result[0].blocks[0].heading == "org/alpha"
        assert result[0].blocks[1].heading == "org/beta"


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
        response_data = {
            "Authored / Contributed": {
                "org/alpha": [{"title": "Summary", "numbers": [10]}],
            },
        }
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

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=False)
    def test_group_by_project_sends_project_grouped_data(self):
        response_data = {
            "org/alpha": {
                "Open": [{"title": "Summary", "numbers": [10]}],
            },
        }
        self._mock_anthropic.Anthropic.return_value.messages.create.return_value = (
            self._make_mock_response(json.dumps(response_data))
        )

        report = self._report_with_prs()
        result = prepare_consolidated_content(report, group_by="project")

        assert len(result) == 1
        assert result[0].repo_name == "org/alpha"
        assert result[0].blocks[0].heading == "Open"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=False)
    def test_group_by_status_sends_status_grouped_data(self):
        response_data = {
            "Open": {
                "org/alpha": [{"title": "Summary", "numbers": [10]}],
            },
        }
        self._mock_anthropic.Anthropic.return_value.messages.create.return_value = (
            self._make_mock_response(json.dumps(response_data))
        )

        report = self._report_with_prs()
        result = prepare_consolidated_content(report, group_by="status")

        assert len(result) == 1
        assert result[0].repo_name == "Open"
        assert result[0].blocks[0].heading == "org/alpha"

    def test_empty_report_returns_empty_list(self):
        report = _make_report()
        result = prepare_consolidated_content(report)
        assert result == []


class TestPrepareConsolidatedContentViaSDKAgent:
    """Tests using the SDK agent backend (no ANTHROPIC_API_KEY)."""

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
    @patch("daily_report.content._call_via_sdk_agent")
    def test_uses_sdk_agent_when_no_api_key(self, mock_agent, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        response_data = {
            "Authored / Contributed": {
                "org/alpha": [{"title": "Summary", "numbers": [10]}],
            },
        }
        mock_agent.return_value = json.dumps(response_data)

        report = self._report_with_prs()
        result = prepare_consolidated_content(report)

        assert len(result) == 1
        assert result[0].blocks[0].items[0].title == "Summary"
        mock_agent.assert_called_once()

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content._call_via_sdk_agent")
    def test_sdk_agent_error_raises_runtime_error(self, mock_agent, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_agent.side_effect = RuntimeError("Claude SDK call failed: auth error")

        report = self._report_with_prs()
        with pytest.raises(RuntimeError, match="Claude SDK call failed"):
            prepare_consolidated_content(report)

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content._call_via_sdk_agent")
    def test_sdk_agent_empty_response_raises_runtime_error(self, mock_agent, monkeypatch):
        """Empty SDK output should raise a clear error."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_agent.side_effect = RuntimeError("Claude SDK returned empty response")

        report = self._report_with_prs()
        with pytest.raises(RuntimeError, match="empty response"):
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
    @patch("daily_report.content._call_via_sdk_agent")
    def test_returns_trimmed_text(self, mock_agent, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_agent.return_value = "  Built login feature across repos.  "
        result = prepare_ai_summary(self._report_with_prs())
        assert result == "Built login feature across repos."

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content._call_via_sdk_agent")
    def test_no_hard_truncation(self, mock_agent, monkeypatch):
        """AI output is returned as-is (prompt controls length, no hard cut)."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_agent.return_value = "x" * 300
        result = prepare_ai_summary(self._report_with_prs())
        assert len(result) == 300

    def test_empty_report_returns_empty_string(self):
        result = prepare_ai_summary(_make_report())
        assert result == ""

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content._call_via_sdk_agent")
    def test_uses_custom_prompt(self, mock_agent, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_agent.return_value = "Custom summary."
        result = prepare_ai_summary(
            self._report_with_prs(), prompt="Custom prompt here",
        )
        assert result == "Custom summary."


# ---------------------------------------------------------------------------
# _build_repos_data tests
# ---------------------------------------------------------------------------

class TestBuildReposData:
    """Tests for _build_repos_data: verifies categorized structure and fields."""

    def test_authored_prs_categorized_as_authored(self):
        report = _make_report(authored_prs=[
            AuthoredPR(repo="org/alpha", title="Add login", number=10,
                       status="Open", additions=50, deletions=10,
                       contributed=False, original_author=None),
        ])
        result = _build_repos_data(report)
        assert "org/alpha" in result
        assert "authored" in result["org/alpha"]
        assert result["org/alpha"]["authored"][0]["number"] == 10

    def test_contributed_prs_categorized_as_contributed(self):
        report = _make_report(authored_prs=[
            AuthoredPR(repo="org/alpha", title="Fix typo", number=11,
                       status="Merged", additions=1, deletions=1,
                       contributed=True, original_author="other-user"),
        ])
        result = _build_repos_data(report)
        assert "contributed" in result["org/alpha"]
        assert "authored" not in result["org/alpha"]

    def test_reviewed_prs_categorized_as_reviewed(self):
        report = _make_report(reviewed_prs=[
            ReviewedPR(repo="org/beta", title="Refactor module", number=20,
                       author="colleague", status="Open"),
        ])
        result = _build_repos_data(report)
        assert "reviewed" in result["org/beta"]
        assert result["org/beta"]["reviewed"][0]["number"] == 20

    def test_waiting_prs_categorized_as_waiting(self):
        report = _make_report(waiting_prs=[
            WaitingPR(repo="org/gamma", title="Waiting PR", number=30,
                      reviewers=["reviewer1"], created_at="2026-02-10",
                      days_waiting=3),
        ])
        result = _build_repos_data(report)
        assert "waiting_for_review" in result["org/gamma"]

    def test_only_nonempty_categories_present(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(repo="org/alpha", title="Add login", number=10,
                           status="Open", additions=50, deletions=10,
                           contributed=False, original_author=None),
            ],
            reviewed_prs=[
                ReviewedPR(repo="org/alpha", title="Review something", number=20,
                           author="other", status="Open"),
            ],
        )
        result = _build_repos_data(report)
        cats = result["org/alpha"]
        assert "authored" in cats
        assert "reviewed" in cats
        assert "contributed" not in cats
        assert "waiting_for_review" not in cats

    def test_returns_plain_dicts_not_defaultdicts(self):
        report = _make_report(authored_prs=[
            AuthoredPR(repo="org/alpha", title="Test", number=1,
                       status="Open", additions=0, deletions=0,
                       contributed=False, original_author=None),
        ])
        result = _build_repos_data(report)
        assert type(result) is dict
        assert type(result["org/alpha"]) is dict

    def test_empty_report_returns_empty_dict(self):
        report = _make_report()
        result = _build_repos_data(report)
        assert result == {}

    def test_mixed_repos_and_categories(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(repo="org/alpha", title="Feature", number=1,
                           status="Open", additions=10, deletions=5,
                           contributed=False, original_author=None),
            ],
            reviewed_prs=[
                ReviewedPR(repo="org/beta", title="Review", number=2,
                           author="other", status="Merged"),
            ],
            waiting_prs=[
                WaitingPR(repo="org/alpha", title="Waiting", number=3,
                          reviewers=["rev"], created_at="2026-02-10",
                          days_waiting=1),
            ],
        )
        result = _build_repos_data(report)
        assert set(result.keys()) == {"org/alpha", "org/beta"}
        assert set(result["org/alpha"].keys()) == {"authored", "waiting_for_review"}
        assert set(result["org/beta"].keys()) == {"reviewed"}

    def test_authored_pr_includes_body_and_files(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="Add login", number=10,
                    status="Open", additions=50, deletions=10,
                    contributed=False, original_author=None,
                    body="Implements OAuth login flow",
                    changed_files=["src/auth.py", "tests/test_auth.py"],
                ),
            ],
        )
        data = _build_repos_data(report)
        pr = data["org/repo"]["authored"][0]
        assert pr["body"] == "Implements OAuth login flow"
        assert pr["changed_files"] == ["src/auth.py", "tests/test_auth.py"]
        assert pr["additions"] == 50
        assert pr["deletions"] == 10

    def test_reviewed_pr_includes_body_and_files(self):
        report = _make_report(
            reviewed_prs=[
                ReviewedPR(
                    repo="org/repo", title="Fix bug", number=20,
                    author="alice", status="Merged",
                    body="Fixes null pointer in parser",
                    changed_files=["src/parser.py"],
                ),
            ],
        )
        data = _build_repos_data(report)
        pr = data["org/repo"]["reviewed"][0]
        assert pr["body"] == "Fixes null pointer in parser"
        assert pr["changed_files"] == ["src/parser.py"]

    def test_empty_body_omitted(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="Minor fix", number=5,
                    status="Open", additions=1, deletions=0,
                    contributed=False, original_author=None,
                ),
            ],
        )
        data = _build_repos_data(report)
        pr = data["org/repo"]["authored"][0]
        assert "body" not in pr

    def test_empty_changed_files_omitted(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(
                    repo="org/repo", title="Minor fix", number=5,
                    status="Open", additions=1, deletions=0,
                    contributed=False, original_author=None,
                ),
            ],
        )
        data = _build_repos_data(report)
        pr = data["org/repo"]["authored"][0]
        assert "changed_files" not in pr

    def test_waiting_pr_structure(self):
        report = _make_report(
            waiting_prs=[
                WaitingPR(
                    repo="org/repo", title="Waiting", number=3,
                    reviewers=["alice"], created_at="2026-02-08",
                    days_waiting=2,
                ),
            ],
        )
        data = _build_repos_data(report)
        pr = data["org/repo"]["waiting_for_review"][0]
        assert pr == {
            "number": 3,
            "title": "Waiting",
        }


# ---------------------------------------------------------------------------
# _load_prompt tests
# ---------------------------------------------------------------------------

class TestLoadPrompt:
    """Tests for _load_prompt."""

    def test_loads_summary_prompt(self):
        text = _load_prompt("summary")
        assert isinstance(text, str)
        assert len(text) > 0
        assert "authored" in text.lower()

    def test_loads_consolidation_prompt(self):
        text = _load_prompt("consolidation")
        assert isinstance(text, str)
        assert len(text) > 0
        assert "subgroup" in text.lower()

    def test_caching_returns_same_string(self):
        t1 = _load_prompt("summary")
        t2 = _load_prompt("summary")
        assert t1 is t2

    def test_missing_prompt_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            _load_prompt("nonexistent")


# ---------------------------------------------------------------------------
# _load_schema tests
# ---------------------------------------------------------------------------

class TestLoadSchema:
    """Tests for _load_schema."""

    def test_returns_dict(self):
        schema = _load_schema()
        assert isinstance(schema, dict)

    def test_schema_has_expected_structure(self):
        schema = _load_schema()
        assert schema["type"] == "object"
        assert "additionalProperties" in schema
        inner = schema["additionalProperties"]
        assert inner["type"] == "object"
        assert "additionalProperties" in inner
        items = inner["additionalProperties"]["items"]
        assert "title" in items["properties"]
        assert "numbers" in items["properties"]

    def test_caching_returns_same_object(self):
        s1 = _load_schema()
        s2 = _load_schema()
        assert s1 is s2


# ---------------------------------------------------------------------------
# _parse_and_validate tests
# ---------------------------------------------------------------------------

class TestParseAndValidate:
    """Tests for _parse_and_validate with schema validation."""

    def _schema(self):
        return _load_schema()

    def test_valid_json_returns_repo_contents(self):
        data = {"org/repo": {"Summary": [{"title": "Summary", "numbers": [1]}]}}
        result = _parse_and_validate(json.dumps(data), self._schema())
        assert len(result) == 1
        assert result[0].repo_name == "org/repo"
        assert result[0].blocks[0].heading == "Summary"
        assert result[0].blocks[0].items[0].title == "Summary"

    def test_invalid_json_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="Failed to parse"):
            _parse_and_validate("not json", self._schema())

    def test_non_dict_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="not a JSON object"):
            _parse_and_validate("[1, 2, 3]", self._schema())

    def test_schema_violation_raises_runtime_error(self):
        """Response with wrong types should fail schema validation."""
        pytest.importorskip("jsonschema")
        # numbers should be array of ints, not a string
        bad_data = {"org/repo": {"sub": [{"title": "Summary", "numbers": "not-a-list"}]}}
        with pytest.raises(RuntimeError, match="schema validation"):
            _parse_and_validate(json.dumps(bad_data), self._schema())

    def test_extra_properties_rejected_by_schema(self):
        """Items with extra properties should fail additionalProperties: false."""
        pytest.importorskip("jsonschema")
        bad_data = {"org/repo": {"sub": [{"title": "Summary", "numbers": [1], "extra": "bad"}]}}
        with pytest.raises(RuntimeError, match="schema validation"):
            _parse_and_validate(json.dumps(bad_data), self._schema())

    def test_works_without_jsonschema_installed(self):
        """When jsonschema is not installed, validation is skipped gracefully."""
        data = {"org/repo": {"sub": [{"title": "Summary", "numbers": [1]}]}}
        with patch.dict(sys.modules, {"jsonschema": None}):
            result = _parse_and_validate(json.dumps(data), self._schema())
        assert len(result) == 1

    def test_fenced_json_extracted(self):
        data = {"org/repo": {"sub": [{"title": "Fenced", "numbers": [2]}]}}
        wrapped = "Here is the JSON:\n```json\n" + json.dumps(data) + "\n```"
        result = _parse_and_validate(wrapped, self._schema())
        assert result[0].blocks[0].items[0].title == "Fenced"


# ---------------------------------------------------------------------------
# _call_with_retry tests
# ---------------------------------------------------------------------------

class TestCallWithRetry:
    """Tests for _call_with_retry retry logic."""

    def _schema(self):
        return _load_schema()

    @patch("daily_report.content._call_backend")
    def test_success_on_first_call(self, mock_backend):
        data = {"org/repo": {"Summary": [{"title": "Good", "numbers": [1]}]}}
        mock_backend.return_value = json.dumps(data)

        result = _call_with_retry("key", "model", "sys", "user", self._schema())
        assert len(result) == 1
        assert result[0].blocks[0].items[0].title == "Good"
        assert mock_backend.call_count == 1

    @patch("daily_report.content._call_backend")
    def test_retry_on_first_failure(self, mock_backend):
        good_data = {"org/repo": {"Summary": [{"title": "Fixed", "numbers": [1]}]}}
        mock_backend.side_effect = [
            "This is not valid JSON at all",  # first call fails
            json.dumps(good_data),             # retry succeeds
        ]

        result = _call_with_retry("key", "model", "sys", "user", self._schema())
        assert len(result) == 1
        assert result[0].blocks[0].items[0].title == "Fixed"
        assert mock_backend.call_count == 2

    @patch("daily_report.content._call_backend")
    def test_retry_includes_error_and_schema(self, mock_backend):
        good_data = {"org/repo": {"Summary": [{"title": "OK", "numbers": [1]}]}}
        mock_backend.side_effect = [
            "bad response",
            json.dumps(good_data),
        ]

        _call_with_retry("key", "model", "sys", "user", self._schema())
        retry_msg = mock_backend.call_args_list[1][0][3]  # 4th arg = user_message
        assert "could not be parsed" in retry_msg
        assert "bad response" in retry_msg
        assert "Expected JSON schema" in retry_msg

    @patch("daily_report.content._call_backend")
    def test_both_calls_fail_raises_error(self, mock_backend):
        mock_backend.side_effect = [
            "bad json first",
            "bad json second",
        ]

        with pytest.raises(RuntimeError, match="Failed to parse"):
            _call_with_retry("key", "model", "sys", "user", self._schema())
        assert mock_backend.call_count == 2

    @patch("daily_report.content._call_backend")
    def test_schema_validation_failure_triggers_retry(self, mock_backend):
        """Schema validation error (not just JSON parse) triggers retry."""
        pytest.importorskip("jsonschema")
        bad_data = {"org/repo": {"sub": [{"title": "X", "numbers": "not-a-list"}]}}
        good_data = {"org/repo": {"sub": [{"title": "Fixed", "numbers": [1]}]}}
        mock_backend.side_effect = [
            json.dumps(bad_data),
            json.dumps(good_data),
        ]

        result = _call_with_retry("key", "model", "sys", "user", self._schema())
        assert result[0].blocks[0].items[0].title == "Fixed"
        assert mock_backend.call_count == 2


# ---------------------------------------------------------------------------
# _serialize_grouped_content tests
# ---------------------------------------------------------------------------

class TestSerializeGroupedContent:
    """Tests for _serialize_grouped_content."""

    def test_empty_list_returns_empty_dict(self):
        assert _serialize_grouped_content([]) == {}

    def test_single_repo_single_block(self):
        content = [
            RepoContent(
                repo_name="Authored / Contributed",
                blocks=[
                    ContentBlock(
                        heading="org/repo",
                        items=[
                            ContentItem(title="Add login", numbers=[10],
                                        status="Open", additions=50, deletions=10),
                        ],
                    ),
                ],
            ),
        ]
        result = _serialize_grouped_content(content)
        assert "Authored / Contributed" in result
        assert "org/repo" in result["Authored / Contributed"]
        item = result["Authored / Contributed"]["org/repo"][0]
        assert item["title"] == "Add login"
        assert item["numbers"] == [10]
        assert item["status"] == "Open"
        assert item["additions"] == 50
        assert item["deletions"] == 10

    def test_empty_fields_omitted(self):
        content = [
            RepoContent(
                repo_name="group",
                blocks=[
                    ContentBlock(
                        heading="sub",
                        items=[ContentItem(title="Basic PR", numbers=[1])],
                    ),
                ],
            ),
        ]
        result = _serialize_grouped_content(content)
        item = result["group"]["sub"][0]
        assert "status" not in item
        assert "additions" not in item
        assert "author" not in item

    def test_waiting_item_serializes_reviewers_and_days(self):
        content = [
            RepoContent(
                repo_name="Waiting for Review",
                blocks=[
                    ContentBlock(
                        heading="org/repo",
                        items=[
                            ContentItem(title="Waiting PR", numbers=[3],
                                        reviewers=["alice"], days_waiting=5),
                        ],
                    ),
                ],
            ),
        ]
        result = _serialize_grouped_content(content)
        item = result["Waiting for Review"]["org/repo"][0]
        assert item["reviewers"] == ["alice"]
        assert item["days_waiting"] == 5

    def test_roundtrip_with_regroup_contribution(self):
        """regroup_content → _serialize_grouped_content produces expected keys."""
        report = _make_report(
            authored_prs=[
                AuthoredPR(repo="org/alpha", title="Feature", number=1,
                           status="Open", additions=10, deletions=5,
                           contributed=False, original_author=None),
            ],
            reviewed_prs=[
                ReviewedPR(repo="org/beta", title="Review", number=2,
                           author="other", status="Merged"),
            ],
        )
        grouped = regroup_content(report, "contribution")
        serialized = _serialize_grouped_content(grouped)
        assert "Authored / Contributed" in serialized
        assert "org/alpha" in serialized["Authored / Contributed"]
        assert "Reviewed" in serialized
        assert "org/beta" in serialized["Reviewed"]

    def test_roundtrip_with_regroup_project(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(repo="org/alpha", title="Feature", number=1,
                           status="Open", additions=10, deletions=5,
                           contributed=False, original_author=None),
            ],
        )
        grouped = regroup_content(report, "project")
        serialized = _serialize_grouped_content(grouped)
        assert "org/alpha" in serialized
        assert "Open" in serialized["org/alpha"]

    def test_roundtrip_with_regroup_status(self):
        report = _make_report(
            authored_prs=[
                AuthoredPR(repo="org/alpha", title="Feature", number=1,
                           status="Open", additions=10, deletions=5,
                           contributed=False, original_author=None),
            ],
        )
        grouped = regroup_content(report, "status")
        serialized = _serialize_grouped_content(grouped)
        assert "Open" in serialized
        assert "org/alpha" in serialized["Open"]


# ---------------------------------------------------------------------------
# PR deduplication tests
# ---------------------------------------------------------------------------

class TestPRDeduplication:
    """Ensure each PR appears in only one section, with priority:
    Waiting for Review > Authored/Contributed > Reviewed."""

    def _pr_authored(self, repo="org/repo", number=1, title="PR"):
        return AuthoredPR(
            repo=repo, title=title, number=number,
            status="Open", additions=10, deletions=5,
            contributed=False, original_author=None,
        )

    def _pr_reviewed(self, repo="org/repo", number=1, title="PR"):
        return ReviewedPR(
            repo=repo, title=title, number=number,
            author="other-user", status="Open",
        )

    def _pr_waiting(self, repo="org/repo", number=1, title="PR"):
        return WaitingPR(
            repo=repo, title=title, number=number,
            reviewers=["reviewer1"], created_at="2026-02-10",
            days_waiting=3,
        )

    def test_dedup_waiting_removes_from_authored_and_reviewed(self):
        report = _make_report(
            authored_prs=[self._pr_authored(number=1)],
            reviewed_prs=[self._pr_reviewed(number=1)],
            waiting_prs=[self._pr_waiting(number=1)],
        )
        authored, reviewed, waiting = _dedup_pr_lists(report)
        assert len(waiting) == 1
        assert len(authored) == 0
        assert len(reviewed) == 0

    def test_dedup_authored_removes_from_reviewed(self):
        report = _make_report(
            authored_prs=[self._pr_authored(number=1)],
            reviewed_prs=[self._pr_reviewed(number=1)],
        )
        authored, reviewed, waiting = _dedup_pr_lists(report)
        assert len(authored) == 1
        assert len(reviewed) == 0

    def test_dedup_no_overlap_keeps_all(self):
        report = _make_report(
            authored_prs=[self._pr_authored(number=1)],
            reviewed_prs=[self._pr_reviewed(number=2)],
            waiting_prs=[self._pr_waiting(number=3)],
        )
        authored, reviewed, waiting = _dedup_pr_lists(report)
        assert len(authored) == 1
        assert len(reviewed) == 1
        assert len(waiting) == 1

    def test_dedup_different_repos_not_conflated(self):
        report = _make_report(
            authored_prs=[self._pr_authored(repo="org/alpha", number=1)],
            reviewed_prs=[self._pr_reviewed(repo="org/beta", number=1)],
        )
        authored, reviewed, waiting = _dedup_pr_lists(report)
        assert len(authored) == 1
        assert len(reviewed) == 1

    def test_default_content_dedup_waiting_over_authored(self):
        report = _make_report(
            authored_prs=[self._pr_authored(number=5)],
            waiting_prs=[self._pr_waiting(number=5)],
        )
        result = prepare_default_content(report)
        assert len(result) == 1
        headings = [b.heading for b in result[0].blocks]
        assert "Waiting for Review" in headings
        assert "Authored / Contributed" not in headings

    def test_default_content_dedup_authored_over_reviewed(self):
        report = _make_report(
            authored_prs=[self._pr_authored(number=5)],
            reviewed_prs=[self._pr_reviewed(number=5)],
        )
        result = prepare_default_content(report)
        assert len(result) == 1
        headings = [b.heading for b in result[0].blocks]
        assert "Authored / Contributed" in headings
        assert "Reviewed" not in headings

    def test_build_repos_data_dedup(self):
        report = _make_report(
            authored_prs=[self._pr_authored(number=1)],
            reviewed_prs=[self._pr_reviewed(number=1)],
            waiting_prs=[self._pr_waiting(number=1)],
        )
        data = _build_repos_data(report)
        repo_data = data["org/repo"]
        assert "waiting_for_review" in repo_data
        assert "authored" not in repo_data
        assert "reviewed" not in repo_data
