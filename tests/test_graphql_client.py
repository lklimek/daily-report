"""Unit tests for graphql_client.py.

Tests query builders and response parsers with mocked subprocess calls.
Run with: python3 -m pytest tests/test_graphql_client.py -v
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from daily_report.graphql_client import (
    build_commit_to_pr_query,
    build_pr_details_query,
    build_review_search_query,
    build_waiting_for_review_query,
    graphql_query,
    graphql_with_retry,
    parse_commit_to_pr_response,
    parse_pr_details_response,
)


# ---------------------------------------------------------------------------
# graphql_query
# ---------------------------------------------------------------------------

class TestGraphqlQuery:
    """Tests for the graphql_query function."""

    @patch("daily_report.graphql_client.subprocess.run")
    def test_basic_query(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"data": {"viewer": {"login": "testuser"}}}),
            returncode=0,
        )
        result = graphql_query("{ viewer { login } }")
        assert result == {"viewer": {"login": "testuser"}}
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["gh", "api", "graphql"]

    @patch("daily_report.graphql_client.subprocess.run")
    def test_query_with_variables(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"data": {"repository": {}}}),
            returncode=0,
        )
        result = graphql_query(
            "query($owner: String!) { repository(owner: $owner) { name } }",
            variables={"owner": "dashpay"},
        )
        assert result == {"repository": {}}
        cmd = mock_run.call_args[0][0]
        assert "-f" in cmd
        # Should have -f query=... and -f owner=dashpay
        f_flags = [
            cmd[i + 1] for i, v in enumerate(cmd) if v == "-f"
        ]
        assert any(f.startswith("owner=") for f in f_flags)

    @patch("daily_report.graphql_client.subprocess.run")
    def test_rate_limit_error_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({
                "data": None,
                "errors": [{"type": "RATE_LIMITED", "message": "rate limited"}],
            }),
            returncode=0,
        )
        with pytest.raises(Exception, match="[Rr]ate"):
            graphql_query("{ viewer { login } }")

    @patch("daily_report.graphql_client.subprocess.run")
    def test_non_rate_limit_error_raises_runtime(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({
                "data": None,
                "errors": [{"type": "NOT_FOUND", "message": "not found"}],
            }),
            returncode=0,
        )
        with pytest.raises(RuntimeError, match="not found"):
            graphql_query("{ viewer { login } }")

    @patch("daily_report.graphql_client.subprocess.run")
    def test_subprocess_failure_raises(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "gh", stderr="auth required"
        )
        with pytest.raises(RuntimeError, match="gh api graphql failed"):
            graphql_query("{ viewer { login } }")


# ---------------------------------------------------------------------------
# graphql_with_retry
# ---------------------------------------------------------------------------

class TestGraphqlWithRetry:
    """Tests for the graphql_with_retry function."""

    @patch("daily_report.graphql_client.graphql_query")
    def test_success_no_retry(self, mock_query):
        mock_query.return_value = {"viewer": {"login": "testuser"}}
        result = graphql_with_retry("{ viewer { login } }")
        assert result == {"viewer": {"login": "testuser"}}
        assert mock_query.call_count == 1

    @patch("daily_report.graphql_client.time.sleep")
    @patch("daily_report.graphql_client.graphql_query")
    def test_retry_on_rate_limit(self, mock_query, mock_sleep):
        from daily_report.graphql_client import _RateLimitError
        mock_query.side_effect = [
            _RateLimitError("rate limited"),
            {"viewer": {"login": "testuser"}},
        ]
        result = graphql_with_retry("{ viewer { login } }")
        assert result == {"viewer": {"login": "testuser"}}
        assert mock_query.call_count == 2
        mock_sleep.assert_called_once_with(1)  # 2^0 = 1

    @patch("daily_report.graphql_client.time.sleep")
    @patch("daily_report.graphql_client.graphql_query")
    def test_max_retries_exceeded(self, mock_query, mock_sleep):
        from daily_report.graphql_client import _RateLimitError
        mock_query.side_effect = _RateLimitError("rate limited")
        with pytest.raises(RuntimeError, match="rate limit exceeded after retries"):
            graphql_with_retry("{ viewer { login } }", max_retries=3)
        assert mock_query.call_count == 3
        # Backoff: 2^0=1, 2^1=2 (third attempt raises immediately)
        assert mock_sleep.call_count == 2

    @patch("daily_report.graphql_client.graphql_query")
    def test_non_rate_limit_error_not_retried(self, mock_query):
        mock_query.side_effect = RuntimeError("NOT_FOUND")
        with pytest.raises(RuntimeError, match="NOT_FOUND"):
            graphql_with_retry("{ viewer { login } }")
        assert mock_query.call_count == 1


# ---------------------------------------------------------------------------
# build_pr_details_query / parse_pr_details_response
# ---------------------------------------------------------------------------

class TestPRDetailsQuery:
    """Tests for PR details query builder and parser."""

    def test_build_single_pr(self):
        query = build_pr_details_query([("dashpay", "platform", 42)])
        assert "pr_0:" in query
        assert 'owner: "dashpay"' in query
        assert 'name: "platform"' in query
        assert "pullRequest(number: 42)" in query
        assert "mergedAt" in query
        assert "additions" in query
        assert "deletions" in query
        assert "body" in query
        assert "files(" in query

    def test_build_multiple_prs(self):
        prs = [
            ("dashpay", "platform", 42),
            ("dashpay", "platform", 55),
            ("dashpay", "tenderdash", 12),
        ]
        query = build_pr_details_query(prs)
        assert "pr_0:" in query
        assert "pr_1:" in query
        assert "pr_2:" in query

    def test_build_hyphenated_repo_name(self):
        query = build_pr_details_query([("dashpay", "dash-evo-tool", 100)])
        # Index-based alias, no name mangling needed
        assert "pr_0:" in query
        assert 'name: "dash-evo-tool"' in query

    def test_parse_response(self):
        prs = [("dashpay", "platform", 42)]
        data = {
            "pr_0": {
                "pullRequest": {
                    "number": 42,
                    "title": "Fix bug",
                    "state": "MERGED",
                    "isDraft": False,
                    "mergedAt": "2026-02-09T10:00:00Z",
                    "additions": 10,
                    "deletions": 5,
                    "author": {"login": "testuser"},
                    "url": "https://github.com/dashpay/platform/pull/42",
                }
            }
        }
        result = parse_pr_details_response(data, prs)
        assert ("dashpay", "platform", 42) in result
        pr = result[("dashpay", "platform", 42)]
        assert pr["title"] == "Fix bug"
        assert pr["state"] == "MERGED"

    def test_parse_response_null_repo(self):
        prs = [("dashpay", "platform", 42)]
        data = {"pr_0": None}
        result = parse_pr_details_response(data, prs)
        assert len(result) == 0

    def test_parse_response_null_pr(self):
        prs = [("dashpay", "platform", 42)]
        data = {"pr_0": {"pullRequest": None}}
        result = parse_pr_details_response(data, prs)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# build_commit_to_pr_query / parse_commit_to_pr_response
# ---------------------------------------------------------------------------

class TestCommitToPRQuery:
    """Tests for commit-to-PR query builder and parser."""

    def test_build_query(self):
        shas = ["abc123", "def456", "ghi789"]
        query = build_commit_to_pr_query("dashpay", "platform", shas)
        assert 'repository(owner: "dashpay", name: "platform")' in query
        assert "c0:" in query
        assert "c1:" in query
        assert "c2:" in query
        assert '"abc123"' in query
        assert '"def456"' in query
        assert "associatedPullRequests" in query
        assert "oid" in query

    def test_build_query_limits_to_25(self):
        shas = [f"sha{i:04d}" for i in range(30)]
        query = build_commit_to_pr_query("dashpay", "platform", shas)
        assert "c24:" in query
        assert "c25:" not in query

    def test_parse_response(self):
        data = {
            "repository": {
                "c0": {
                    "oid": "abc123full",
                    "associatedPullRequests": {
                        "nodes": [
                            {"number": 42, "title": "Fix", "author": {"login": "user1"}},
                        ]
                    },
                },
                "c1": {
                    "oid": "def456full",
                    "associatedPullRequests": {"nodes": []},
                },
            }
        }
        result = parse_commit_to_pr_response(data)
        assert "abc123full" in result
        assert len(result["abc123full"]) == 1
        assert result["abc123full"][0]["number"] == 42
        assert "def456full" in result
        assert len(result["def456full"]) == 0

    def test_parse_response_null_object(self):
        data = {"repository": {"c0": None}}
        result = parse_commit_to_pr_response(data)
        assert len(result) == 0

    def test_parse_response_no_repository(self):
        data = {}
        result = parse_commit_to_pr_response(data)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# build_review_search_query
# ---------------------------------------------------------------------------

class TestReviewSearchQuery:
    """Tests for review search query builder."""

    def test_returns_query_and_variables(self):
        query, variables = build_review_search_query(
            "dashpay", "lklimek", "2026-02-03", "2026-02-09"
        )
        assert "ReviewDiscovery" in query
        assert "reviewed:" in query
        assert "commented:" in query
        assert "reviewQuery" in variables
        assert "commentQuery" in variables
        assert "org:dashpay" in variables["reviewQuery"]
        assert "reviewed-by:lklimek" in variables["reviewQuery"]
        assert "commenter:lklimek" in variables["commentQuery"]
        assert "2026-02-03..2026-02-09" in variables["reviewQuery"]

    def test_query_contains_review_fields(self):
        query, _ = build_review_search_query(
            "dashpay", "lklimek", "2026-02-03", "2026-02-09"
        )
        assert "reviews(first: 100)" in query
        assert "submittedAt" in query
        assert "comments(first: 100)" in query
        assert "createdAt" in query
        assert "repository" in query
        assert "owner { login }" in query

    def test_none_org_omits_org_filter(self):
        query, variables = build_review_search_query(
            None, "lklimek", "2026-02-03", "2026-02-09"
        )
        assert "ReviewDiscovery" in query
        assert "org:" not in variables["reviewQuery"]
        assert "org:" not in variables["commentQuery"]
        assert "reviewed-by:lklimek" in variables["reviewQuery"]
        assert "commenter:lklimek" in variables["commentQuery"]
        assert "2026-02-03..2026-02-09" in variables["reviewQuery"]


# ---------------------------------------------------------------------------
# build_waiting_for_review_query
# ---------------------------------------------------------------------------

class TestWaitingForReviewQuery:
    """Tests for waiting-for-review query builder."""

    def test_returns_query_and_variables(self):
        query, variables = build_waiting_for_review_query("dashpay", "lklimek")
        assert "WaitingForReview" in query
        assert "searchQuery" in variables
        assert "author:lklimek" in variables["searchQuery"]
        assert "state:open" in variables["searchQuery"]
        assert "draft:false" in variables["searchQuery"]

    def test_query_contains_review_request_fields(self):
        query, _ = build_waiting_for_review_query("dashpay", "lklimek")
        assert "reviewRequests(first: 20)" in query
        assert "requestedReviewer" in query
        assert "... on User { login }" in query
        assert "... on Team { name slug }" in query

    def test_query_contains_timeline_items(self):
        query, _ = build_waiting_for_review_query("dashpay", "lklimek")
        assert "timelineItems" in query
        assert "REVIEW_REQUESTED_EVENT" in query
        assert "ReviewRequestedEvent" in query

    def test_none_org_omits_org_filter(self):
        query, variables = build_waiting_for_review_query(None, "lklimek")
        assert "WaitingForReview" in query
        assert "org:" not in variables["searchQuery"]
        assert "author:lklimek" in variables["searchQuery"]
        assert "state:open" in variables["searchQuery"]
