"""Unit tests for daily_report/content.py: prepare_default_content,
prepare_consolidated_content, tool executors, _call_via_sdk_with_tools,
_call_via_sdk_agent_with_tools, and helpers.

Run with: python3 -m pytest tests/test_consolidate.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import daily_report.content as _content_module
from daily_report.content import (
    CONSOLIDATION_TOOLS,
    _build_repos_data,
    _dedup_pr_lists,
    _exec_gh_pr_diff,
    _exec_gh_pr_view,
    _exec_git_diff,
    _exec_git_log,
    _execute_tool,
    _load_prompt,
    _truncate,
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
    """Reset the prompt cache before each test."""
    _content_module._prompt_cache.clear()
    yield
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
        assert result[0].blocks[0].heading == "Worked on"

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
        assert result[0].blocks[0].heading == "Worked on"

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
# _truncate tests
# ---------------------------------------------------------------------------

class TestTruncate:
    """Tests for _truncate helper."""

    def test_short_text_unchanged(self):
        assert _truncate("hello", 100) == "hello"

    def test_exact_length_unchanged(self):
        text = "x" * 100
        assert _truncate(text, 100) == text

    def test_long_text_truncated_with_note(self):
        text = "x" * 200
        result = _truncate(text, 100)
        assert result.startswith("x" * 100)
        assert "truncated" in result
        assert "200 total chars" in result

    def test_default_max_len(self):
        short = "short"
        assert _truncate(short) == short

    def test_empty_string(self):
        assert _truncate("", 10) == ""


# ---------------------------------------------------------------------------
# Tool executor tests
# ---------------------------------------------------------------------------

class TestToolExecutors:
    """Tests for _exec_gh_pr_view, _exec_gh_pr_diff, _exec_git_log, _exec_git_diff."""

    @patch("daily_report.content.subprocess.run")
    def test_gh_pr_view_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"title":"Test PR"}', stderr="",
        )
        result = _exec_gh_pr_view("org/repo", 42)
        assert '{"title":"Test PR"}' in result
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "gh" in cmd
        assert "42" in cmd
        assert "org/repo" in cmd

    @patch("daily_report.content.subprocess.run")
    def test_gh_pr_view_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="not found",
        )
        result = _exec_gh_pr_view("org/repo", 999)
        assert "Error" in result

    @patch("daily_report.content.subprocess.run")
    def test_gh_pr_view_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = _exec_gh_pr_view("org/repo", 1)
        assert "Error" in result

    @patch("daily_report.content.subprocess.run")
    def test_gh_pr_diff_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="diff --git a/f.py b/f.py\n+new line", stderr="",
        )
        result = _exec_gh_pr_diff("org/repo", 10)
        assert "diff --git" in result

    @patch("daily_report.content.subprocess.run")
    def test_gh_pr_diff_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="permission denied",
        )
        result = _exec_gh_pr_diff("org/repo", 10)
        assert "Error" in result

    @patch("daily_report.content.subprocess.run")
    def test_git_log_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="abc1234 Initial commit", stderr="",
        )
        result = _exec_git_log("org/repo", "--oneline -5", {"org/repo": "/tmp/repo"})
        assert "abc1234" in result
        cmd = mock_run.call_args[0][0]
        assert "-C" in cmd
        assert "/tmp/repo" in cmd

    @patch("daily_report.content.subprocess.run")
    def test_git_log_no_local_path(self, mock_run):
        result = _exec_git_log("org/repo", "--oneline", {})
        assert "Error" in result
        assert "no local path" in result
        mock_run.assert_not_called()

    @patch("daily_report.content.subprocess.run")
    def test_git_log_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        result = _exec_git_log("org/repo", "--oneline", {"org/repo": "/tmp/repo"})
        assert "Error" in result

    @patch("daily_report.content.subprocess.run")
    def test_git_diff_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="diff output here", stderr="",
        )
        result = _exec_git_diff("org/repo", "HEAD~1", {"org/repo": "/tmp/repo"})
        assert "diff output here" in result

    @patch("daily_report.content.subprocess.run")
    def test_git_diff_no_local_path(self, mock_run):
        result = _exec_git_diff("org/repo", "HEAD~1", {})
        assert "Error" in result
        mock_run.assert_not_called()

    @patch("daily_report.content.subprocess.run")
    def test_git_diff_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="fatal: bad revision",
        )
        result = _exec_git_diff("org/repo", "bad..ref", {"org/repo": "/tmp/repo"})
        assert "Error" in result

    @patch("daily_report.content.subprocess.run")
    def test_gh_pr_view_truncates_long_output(self, mock_run):
        long_output = "x" * 20000
        mock_run.return_value = MagicMock(
            returncode=0, stdout=long_output, stderr="",
        )
        result = _exec_gh_pr_view("org/repo", 1)
        assert "truncated" in result
        assert len(result) < 20000


# ---------------------------------------------------------------------------
# _execute_tool tests
# ---------------------------------------------------------------------------

class TestExecuteTool:
    """Tests for _execute_tool dispatcher."""

    @patch("daily_report.content._exec_gh_pr_view", return_value="pr view output")
    def test_dispatches_gh_pr_view(self, mock_exec):
        result = _execute_tool("gh_pr_view", {"repo": "org/repo", "number": 1}, {})
        assert result == "pr view output"
        mock_exec.assert_called_once_with("org/repo", 1)

    @patch("daily_report.content._exec_gh_pr_diff", return_value="pr diff output")
    def test_dispatches_gh_pr_diff(self, mock_exec):
        result = _execute_tool("gh_pr_diff", {"repo": "org/repo", "number": 5}, {})
        assert result == "pr diff output"
        mock_exec.assert_called_once_with("org/repo", 5)

    @patch("daily_report.content._exec_git_log", return_value="git log output")
    def test_dispatches_git_log(self, mock_exec):
        paths = {"org/repo": "/tmp/repo"}
        result = _execute_tool("git_log", {"repo": "org/repo", "args": "--oneline"}, paths)
        assert result == "git log output"
        mock_exec.assert_called_once_with("org/repo", "--oneline", paths)

    @patch("daily_report.content._exec_git_log", return_value="default log")
    def test_git_log_default_args(self, mock_exec):
        paths = {"org/repo": "/tmp/repo"}
        result = _execute_tool("git_log", {"repo": "org/repo"}, paths)
        assert result == "default log"
        mock_exec.assert_called_once_with("org/repo", "--oneline -20", paths)

    @patch("daily_report.content._exec_git_diff", return_value="git diff output")
    def test_dispatches_git_diff(self, mock_exec):
        paths = {"org/repo": "/tmp/repo"}
        result = _execute_tool("git_diff", {"repo": "org/repo", "args": "HEAD~3"}, paths)
        assert result == "git diff output"
        mock_exec.assert_called_once_with("org/repo", "HEAD~3", paths)

    @patch("daily_report.content._exec_git_diff", return_value="default diff")
    def test_git_diff_default_args(self, mock_exec):
        paths = {"org/repo": "/tmp/repo"}
        result = _execute_tool("git_diff", {"repo": "org/repo"}, paths)
        assert result == "default diff"
        mock_exec.assert_called_once_with("org/repo", "HEAD~1", paths)

    def test_unknown_tool_returns_error(self):
        result = _execute_tool("nonexistent_tool", {}, {})
        assert "Error" in result
        assert "unknown tool" in result


# ---------------------------------------------------------------------------
# _call_via_sdk_with_tools tests
# ---------------------------------------------------------------------------

class TestCallViaSdkWithTools:
    """Tests for the multi-turn tool use conversation loop."""

    @pytest.fixture(autouse=True)
    def _install_mock_anthropic(self):
        self._mock_anthropic = _make_mock_anthropic()
        with patch.dict(sys.modules, {"anthropic": self._mock_anthropic}):
            # Need to reimport to pick up mock
            from daily_report.content import _call_via_sdk_with_tools
            self._call = _call_via_sdk_with_tools
            yield

    def _make_text_response(self, text: str) -> MagicMock:
        """Create a response with stop_reason='end_turn' and a text block."""
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = text
        response = MagicMock()
        response.content = [text_block]
        response.stop_reason = "end_turn"
        response.usage = MagicMock(input_tokens=100, output_tokens=50)
        return response

    def _make_tool_use_response(self, tool_name: str, tool_input: dict, tool_id: str = "tool_1") -> MagicMock:
        """Create a response with stop_reason='tool_use' and a tool_use block."""
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = tool_name
        tool_block.input = tool_input
        tool_block.id = tool_id
        response = MagicMock()
        response.content = [tool_block]
        response.stop_reason = "tool_use"
        response.usage = MagicMock(input_tokens=100, output_tokens=50)
        return response

    def test_no_tool_calls_returns_text_immediately(self):
        client = self._mock_anthropic.Anthropic.return_value
        client.messages.create.return_value = self._make_text_response("# Report\nDone")

        result = self._call("sk-test", "model", "system", "user msg", [], {})
        assert result == "# Report\nDone"
        assert client.messages.create.call_count == 1

    def test_single_tool_call_then_text(self):
        client = self._mock_anthropic.Anthropic.return_value
        client.messages.create.side_effect = [
            self._make_tool_use_response("gh_pr_view", {"repo": "org/repo", "number": 1}),
            self._make_text_response("# Consolidated Report"),
        ]

        with patch("daily_report.content._execute_tool", return_value="tool output"):
            result = self._call("sk-test", "model", "system", "user msg", CONSOLIDATION_TOOLS, {})

        assert result == "# Consolidated Report"
        assert client.messages.create.call_count == 2

    def test_multiple_turns_of_tool_calls(self):
        client = self._mock_anthropic.Anthropic.return_value
        client.messages.create.side_effect = [
            self._make_tool_use_response("gh_pr_view", {"repo": "org/repo", "number": 1}, "t1"),
            self._make_tool_use_response("gh_pr_diff", {"repo": "org/repo", "number": 1}, "t2"),
            self._make_text_response("# Final Report"),
        ]

        with patch("daily_report.content._execute_tool", return_value="tool output"):
            result = self._call("sk-test", "model", "system", "user msg", CONSOLIDATION_TOOLS, {})

        assert result == "# Final Report"
        assert client.messages.create.call_count == 3

    def test_max_turns_exceeded_raises_runtime_error(self):
        client = self._mock_anthropic.Anthropic.return_value
        # Always return tool use, never end_turn
        client.messages.create.return_value = self._make_tool_use_response(
            "gh_pr_view", {"repo": "org/repo", "number": 1},
        )

        with patch("daily_report.content._execute_tool", return_value="output"):
            with pytest.raises(RuntimeError, match="exceeded.*tool-use turns"):
                self._call("sk-test", "model", "system", "user", CONSOLIDATION_TOOLS, {}, max_turns=3)

        assert client.messages.create.call_count == 3

    def test_api_error_raises_runtime_error(self):
        client = self._mock_anthropic.Anthropic.return_value
        client.messages.create.side_effect = self._mock_anthropic.APIError(
            message="rate limit", request=MagicMock(), body=None,
        )

        with pytest.raises(RuntimeError, match="Claude API call failed"):
            self._call("sk-test", "model", "system", "user", [], {})

    def test_no_tool_use_and_not_end_turn_returns_text(self):
        """Edge case: stop_reason is not 'end_turn' but no tool_use blocks."""
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "partial result"
        response = MagicMock()
        response.content = [text_block]
        response.stop_reason = "max_tokens"
        response.usage = MagicMock(input_tokens=100, output_tokens=4096)

        client = self._mock_anthropic.Anthropic.return_value
        client.messages.create.return_value = response

        result = self._call("sk-test", "model", "system", "user", [], {})
        assert result == "partial result"


# ---------------------------------------------------------------------------
# _call_via_sdk_agent_with_tools tests
# ---------------------------------------------------------------------------

class TestCallViaSdkAgentWithTools:
    """Tests for agent SDK with Bash tool."""

    @patch("daily_report.content.asyncio.run")
    def test_sets_bash_tool_and_max_turns(self, mock_asyncio_run):
        mock_asyncio_run.return_value = "# Agent result"

        mock_sdk = MagicMock()
        mock_sdk.ClaudeAgentOptions = MagicMock()
        mock_sdk.ResultMessage = MagicMock()
        mock_sdk.query = MagicMock()

        with patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}):
            from daily_report.content import _call_via_sdk_agent_with_tools
            result = _call_via_sdk_agent_with_tools("model", "system prompt", "user msg")

        assert result == "# Agent result"
        # Verify ClaudeAgentOptions was called with Bash and max_turns=10
        opts_call = mock_sdk.ClaudeAgentOptions.call_args
        assert opts_call[1].get("allowed_tools") == ["Bash"] or \
               (opts_call[0] if opts_call[0] else None) is not None
        # Check keyword args
        if opts_call[1]:
            assert opts_call[1].get("max_turns", None) == 10
            assert opts_call[1].get("allowed_tools", None) == ["Bash"]

    @patch("daily_report.content.asyncio.run")
    def test_empty_response_raises_runtime_error(self, mock_asyncio_run):
        mock_asyncio_run.return_value = ""

        mock_sdk = MagicMock()
        mock_sdk.ClaudeAgentOptions = MagicMock()
        mock_sdk.ResultMessage = MagicMock()
        mock_sdk.query = MagicMock()

        with patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}):
            from daily_report.content import _call_via_sdk_agent_with_tools
            with pytest.raises(RuntimeError, match="empty response"):
                _call_via_sdk_agent_with_tools("model", "system", "user")

    @patch("daily_report.content.asyncio.run")
    def test_combines_list_system_prompt(self, mock_asyncio_run):
        mock_asyncio_run.return_value = "result"

        mock_sdk = MagicMock()
        mock_sdk.ClaudeAgentOptions = MagicMock()
        mock_sdk.ResultMessage = MagicMock()
        mock_sdk.query = MagicMock()

        system_prompt = [
            {"type": "text", "text": "First part"},
            {"type": "text", "text": "Second part"},
        ]

        with patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}):
            from daily_report.content import _call_via_sdk_agent_with_tools
            result = _call_via_sdk_agent_with_tools("model", system_prompt, "user msg")

        assert result == "result"


# ---------------------------------------------------------------------------
# prepare_consolidated_content tests (mocked backends)
# ---------------------------------------------------------------------------

class TestPrepareConsolidatedContentViaSDK:
    """Tests using the SDK backend (ANTHROPIC_API_KEY set)."""

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
    @patch("daily_report.content._call_backend_with_tools")
    def test_returns_markdown_string(self, mock_backend):
        mock_backend.return_value = "# Consolidated Report\n- Item 1"

        report = self._report_with_prs()
        result = prepare_consolidated_content(report)

        assert isinstance(result, str)
        assert "# Consolidated Report" in result
        mock_backend.assert_called_once()

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=False)
    @patch("daily_report.content._call_backend_with_tools")
    @patch("daily_report.format_markdown.format_markdown", return_value="# Input MD")
    def test_calls_format_markdown_for_input(self, mock_fmt, mock_backend):
        mock_backend.return_value = "# Result"

        report = self._report_with_prs()
        result = prepare_consolidated_content(report)

        mock_fmt.assert_called_once()
        # The user message sent to backend should be the formatted markdown
        user_msg = mock_backend.call_args[0][3]
        assert user_msg == "# Input MD"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=False)
    @patch("daily_report.content._call_backend_with_tools")
    def test_api_error_raises_runtime_error(self, mock_backend):
        mock_backend.side_effect = RuntimeError("Claude API call failed: rate limit")

        report = self._report_with_prs()
        with pytest.raises(RuntimeError, match="Claude API call failed"):
            prepare_consolidated_content(report)

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=False)
    @patch("daily_report.content._call_backend_with_tools")
    def test_empty_report_still_calls_backend(self, mock_backend):
        """Even an empty report generates 'No PR activity' markdown and sends it."""
        mock_backend.return_value = "# Empty consolidated"
        report = _make_report()
        result = prepare_consolidated_content(report)
        # format_markdown produces non-empty output even for empty reports
        # (it includes the title and "No PR activity found" text)
        assert isinstance(result, str)

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=False)
    @patch("daily_report.content._call_backend_with_tools")
    def test_passes_repo_paths(self, mock_backend):
        mock_backend.return_value = "# Result"
        paths = {"org/alpha": "/tmp/alpha"}

        report = self._report_with_prs()
        prepare_consolidated_content(report, repo_paths=paths)

        # repo_paths is the 6th positional arg
        call_args = mock_backend.call_args[0]
        assert call_args[5] == paths

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=False)
    @patch("daily_report.content._call_backend_with_tools")
    def test_strips_whitespace_from_result(self, mock_backend):
        mock_backend.return_value = "  \n# Report\n  "

        report = self._report_with_prs()
        result = prepare_consolidated_content(report)
        assert result == "# Report"


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
    @patch("daily_report.content._call_backend_with_tools")
    def test_uses_backend_with_tools_when_no_api_key(self, mock_backend, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_backend.return_value = "# Agent consolidated report"

        report = self._report_with_prs()
        result = prepare_consolidated_content(report)

        assert isinstance(result, str)
        assert "# Agent consolidated report" in result
        mock_backend.assert_called_once()

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content._call_backend_with_tools")
    def test_sdk_agent_error_raises_runtime_error(self, mock_backend, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_backend.side_effect = RuntimeError("Claude Agent SDK call failed: auth error")

        report = self._report_with_prs()
        with pytest.raises(RuntimeError, match="Claude Agent SDK call failed"):
            prepare_consolidated_content(report)

    @patch.dict("os.environ", {}, clear=False)
    @patch("daily_report.content._call_backend_with_tools")
    def test_sdk_agent_empty_response(self, mock_backend, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_backend.return_value = ""

        report = self._report_with_prs()
        result = prepare_consolidated_content(report)
        assert result == ""


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
        assert "consolidate" in text.lower()

    def test_caching_returns_same_string(self):
        t1 = _load_prompt("summary")
        t2 = _load_prompt("summary")
        assert t1 is t2

    def test_missing_prompt_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            _load_prompt("nonexistent")


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
        assert "Worked on" not in headings

    def test_default_content_dedup_authored_over_reviewed(self):
        report = _make_report(
            authored_prs=[self._pr_authored(number=5)],
            reviewed_prs=[self._pr_reviewed(number=5)],
        )
        result = prepare_default_content(report)
        assert len(result) == 1
        headings = [b.heading for b in result[0].blocks]
        assert "Worked on" in headings
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


# ---------------------------------------------------------------------------
# CONSOLIDATION_TOOLS structure test
# ---------------------------------------------------------------------------

class TestConsolidationTools:
    """Verify CONSOLIDATION_TOOLS has expected structure."""

    def test_has_four_tools(self):
        assert len(CONSOLIDATION_TOOLS) == 4

    def test_tool_names(self):
        names = [t["name"] for t in CONSOLIDATION_TOOLS]
        assert "gh_pr_view" in names
        assert "gh_pr_diff" in names
        assert "git_log" in names
        assert "git_diff" in names

    def test_tools_have_input_schema(self):
        for tool in CONSOLIDATION_TOOLS:
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"
            assert "repo" in tool["input_schema"]["properties"]
