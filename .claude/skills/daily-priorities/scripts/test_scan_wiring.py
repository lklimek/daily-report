"""scan.py wiring: fetched facts reach scoreParts; legacy score fields are gone."""

from __future__ import annotations

import datetime as dt

import pytest

import scan
import scoring

REF = dt.datetime(2026, 10, 29, 12, 0, tzinfo=dt.timezone.utc)
USERS = ["lklimek", "Claudius-Maginificent"]


def detail(**overrides) -> dict:
    d = {
        "title": "feat: thing",
        "url": "https://github.com/dashpay/dash-evo-tool/pull/5",
        "state": "OPEN",
        "isDraft": False,
        "additions": 600,
        "deletions": 400,
        "createdAt": "2026-10-20T12:00:00Z",
        "updatedAt": "2026-10-28T12:00:00Z",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "CHANGES_REQUESTED",
        "baseRefName": "feat/base",
        "headRefName": "feat/top",
        "baseRepository": {"defaultBranchRef": {"name": "v1.0-dev"}},
        "isCrossRepository": False,
        "body": "",
        "author": {"__typename": "User", "login": "alice"},
        "labels": {"nodes": []},
        "participants": {"totalCount": 2},
        "closingIssuesReferences": {"nodes": []},
        "assignees": {"nodes": []},
        "reviewRequests": {
            "nodes": [{"requestedReviewer": {"__typename": "User", "login": "lklimek"}}]
        },
        "reviews": {
            "nodes": [
                {
                    "author": {"__typename": "User", "login": "bob"},
                    "state": "CHANGES_REQUESTED",
                    "submittedAt": "2026-10-25T12:00:00Z",
                }
            ]
        },
        "comments": {"nodes": []},
        "reviewThreads": {"nodes": []},
        "timelineItems": {
            "nodes": [
                {
                    "createdAt": "2026-10-28T12:00:00Z",
                    "requestedReviewer": {"__typename": "User", "login": "lklimek"},
                }
            ]
        },
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "statusCheckRollup": {
                            "state": "PENDING",
                            "contexts": {
                                "pageInfo": {"hasNextPage": False},
                                "nodes": [
                                    {
                                        "__typename": "StatusContext",
                                        "context": "PR Hygiene",
                                        "state": "PENDING",
                                        "createdAt": "2026-10-28T12:00:00Z",
                                    }
                                ],
                            },
                        }
                    }
                }
            ]
        },
    }
    d.update(overrides)
    return d


def entry(relation="review_requested", user="lklimek") -> dict:
    return {
        "repo": "dashpay/dash-evo-tool",
        "number": 5,
        "title": "feat: thing",
        "url": "https://github.com/dashpay/dash-evo-tool/pull/5",
        "relations": {user: {relation}},
    }


def analyze(monkeypatch, users=USERS, relation="review_requested", **overrides):
    monkeypatch.setattr(scan, "fetch_pr_detail", lambda r, n: detail(**overrides))
    monkeypatch.setattr(scan, "fetch_dependents", lambda repo, head: [7])
    monkeypatch.setattr(scan, "fetch_stacked_on", lambda repo, base, default: [4])
    return scan.analyze_pr(entry(relation), users, REF, {})


@pytest.fixture
def analyzed(monkeypatch):
    return analyze(monkeypatch)


def test_analyze_pr_feeds_score_parts(analyzed):
    assert "score" not in analyzed and "scoreReasons" not in analyzed
    assert analyzed["eligible"] is True
    assert analyzed["ciEffective"] == "green"
    assert analyzed["ciPolicyNotPassing"] == ["PR Hygiene (PENDING)"]
    assert analyzed["changesRequestedHumanAgeDays"] == pytest.approx(4.0)
    parts = analyzed["scoreParts"]
    assert list(parts) == scoring.PART_ORDER
    assert parts["advancement"]["value"] == 20
    assert parts["unblocks"]["value"] == 5
    assert parts["human_waiting"]["value"] == 15  # review requested 1.0d ago
    assert parts["recency"]["value"] == pytest.approx(scoring.recency_points(1.0))
    assert parts["change_size"]["value"] == pytest.approx(10.0)
    assert parts["blocked_penalty"]["value"] == -20


def test_fetch_issues_feeds_score_parts(monkeypatch):
    issue = {
        "number": 9,
        "title": "crash",
        "url": "https://github.com/dashpay/tenderdash/issues/9",
        "author": {"login": "carol"},
        "authorAssociation": "NONE",
        "assignees": [],
        "body": "Depends on #3",
        "createdAt": "2026-10-28T12:00:00Z",
        "updatedAt": "2026-10-28T12:00:00Z",
        "commentsCount": 0,
        "labels": [],
        "repository": {"nameWithOwner": "dashpay/tenderdash"},
    }
    monkeypatch.setattr(scan, "_search_issues", lambda q, what: [issue])
    (out,) = scan.fetch_issues(["dashpay/tenderdash"], [], 7.0, REF, set(), [])
    assert "score" not in out
    assert out["eligible"] is True
    assert out["scoreParts"]["change_size"]["status"] == "not_applicable"
    assert out["scoreParts"]["blocked_penalty"]["status"] == "needs_judgment"


# --- GraphQL-shaped bot fixtures: Bot logins carry no [bot] suffix ----------


def test_bot_authored_pr_is_not_a_human_waiting(monkeypatch):
    pr = analyze(
        monkeypatch,
        author={"__typename": "Bot", "login": "dependabot"},
        reviews={"nodes": []},
    )
    assert pr["authorIsBot"] is True
    assert "dependabot" not in pr["humansInvolved"]
    hw = pr["scoreParts"]["human_waiting"]
    assert (hw["value"], hw["status"]) == (0, "computed")


def test_bot_change_request_on_our_pr_is_not_human(monkeypatch):
    pr = analyze(
        monkeypatch,
        relation="authored",
        author={"__typename": "User", "login": "lklimek"},
        reviewRequests={"nodes": []},
        timelineItems={"nodes": []},
        reviews={
            "nodes": [
                {
                    "author": {"__typename": "Bot", "login": "coderabbitai"},
                    "state": "CHANGES_REQUESTED",
                    "submittedAt": "2026-10-28T12:00:00Z",
                }
            ]
        },
    )
    assert pr["changesRequestedBy"] == ["coderabbitai"]
    assert pr["changesRequestedByHuman"] is False
    assert pr["changesRequestedHumanAgeDays"] is None
    hw = pr["scoreParts"]["human_waiting"]
    assert (hw["value"], hw["status"]) == (0, "computed")


def test_review_request_login_match_is_case_insensitive(monkeypatch):
    pr = analyze(monkeypatch, users=["LKlimek"])
    assert pr["reviewPendingFor"] == ["LKlimek"]
    assert pr["reviewRequestFreshnessIsApprox"] is False


def test_pr_body_dependency_reaches_blocked_penalty(monkeypatch):
    monkeypatch.setattr(
        scan,
        "fetch_pr_detail",
        lambda r, n: detail(
            body="Depends on dashpay/platform#5588", baseRefName="v1.0-dev"
        ),
    )
    monkeypatch.setattr(scan, "fetch_dependents", lambda repo, head: [])
    monkeypatch.setattr(scan, "fetch_stacked_on", lambda repo, base, default: [])
    pr = scan.analyze_pr(entry(), USERS, REF, {})
    assert "body" not in pr
    assert pr["scoreParts"]["blocked_penalty"]["status"] == "needs_judgment"


# --- dependents / stacked lookups ----------------------------------------------


@pytest.fixture
def gh_calls(monkeypatch):
    calls = []

    def fake(args):
        calls.append(args)
        return [
            {"number": 11, "isCrossRepository": False},
            {"number": 12, "isCrossRepository": True},
        ]

    monkeypatch.setattr(scan, "run_gh_json", fake)
    return calls


@pytest.mark.parametrize(
    "base",
    ["v1.0-dev", "v6.0-dev", "master", "main", "2.1", "release/1.2", "hotfix-3"],
)
def test_stacked_on_skips_long_lived_bases(gh_calls, base):
    assert scan.fetch_stacked_on("o/r", base, "develop") == []
    assert gh_calls == []


def test_stacked_on_feature_base_ignores_fork_heads(gh_calls):
    assert scan.fetch_stacked_on("o/r", "feat/base", "v1.0-dev") == [11]
    assert len(gh_calls) == 1


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, [11, 12]),  # fork-headed PRs may still target our branch
        ({"isCrossRepository": True}, []),
        ({"headRefName": "v1.8-dev"}, []),
        ({"headRefName": "v1.0-dev"}, []),
    ],
)
def test_dependents_skip_fork_and_long_lived_heads(
    monkeypatch, gh_calls, overrides, expected
):
    monkeypatch.setattr(scan, "fetch_pr_detail", lambda r, n: detail(**overrides))
    monkeypatch.setattr(scan, "fetch_stacked_on", lambda repo, base, default: [])
    pr = scan.analyze_pr(entry(), USERS, REF, {})
    assert pr["dependentPRs"] == expected


# --- errors surface on stderr ---------------------------------------------------


def test_main_prints_pr_failures_to_stderr(monkeypatch, capsys):
    candidate = entry()
    monkeypatch.setattr(
        scan,
        "discover_pr_candidates",
        lambda users, unsearchable, extra: {candidate["url"]: candidate},
    )

    def boom(*args):
        raise RuntimeError(
            "gh api graphql -f query=\nquery($owner:String!) {...}\n failed: "
            "gh: Resource not accessible by personal access token"
        )

    monkeypatch.setattr(scan, "analyze_pr", boom)
    monkeypatch.setattr(scan, "fetch_issues", lambda *a: [])
    monkeypatch.setattr(scan, "fetch_latest_release", lambda repo: None)
    monkeypatch.setattr(scan.sys, "argv", ["scan.py", "--issue-repos", ""])
    assert scan.main() == 0
    captured = capsys.readouterr()
    assert "dashpay/dash-evo-tool#5" in captured.err
    assert "not accessible" in captured.err
    assert "query=" not in captured.err


def test_rerequested_human_change_request_is_not_waiting_on_us(monkeypatch):
    pr = analyze(
        monkeypatch,
        relation="authored",
        author={"__typename": "User", "login": "lklimek"},
        reviewRequests={
            "nodes": [{"requestedReviewer": {"__typename": "User", "login": "Bob"}}]
        },
        timelineItems={"nodes": []},
    )
    assert pr["changesRequestedByHuman"] is True
    assert pr["changesRequestedHumanAgeDays"] is None
    hw = pr["scoreParts"]["human_waiting"]
    assert (hw["value"], hw["status"]) == (0, "computed")


# --- degraded fetches land in `errors`, not only on stderr ---------------------


@pytest.fixture(autouse=True)
def clean_degraded():
    scan.DEGRADED.clear()
    yield
    scan.DEGRADED.clear()


def failing(*args, **kwargs):
    raise RuntimeError("gh x failed: HTTP 403 rate limit")


def test_issue_search_failure_is_recorded(monkeypatch):
    monkeypatch.setattr(scan, "run_gh_search_json", failing)
    assert scan.fetch_issues(["o/r"], [], 7.0, REF, set(), []) == []
    assert any("issue search" in e and "o/r" in e for e in scan.DEGRADED)


def test_candidate_search_failure_is_recorded(monkeypatch):
    monkeypatch.setattr(scan, "run_gh_search_json", failing)
    assert scan.discover_pr_candidates(["alice"], set(), []) == {}
    assert len(scan.DEGRADED) == 3


def test_policy_fallback_is_recorded(monkeypatch):
    monkeypatch.setattr(scan, "run_gh", failing)
    assert scan.fetch_owned_paths("dashpay/platform", USERS)
    assert any("ownership policy" in e for e in scan.DEGRADED)


def test_repo_listing_failure_is_recorded(monkeypatch):
    monkeypatch.setattr(scan, "_graphql_pages", failing)
    assert scan.enumerate_owner_items(["dashpay"], ["u"], 1) == ([], [])
    assert any("repo listing" in e for e in scan.DEGRADED)


def test_main_merges_degraded_into_errors(monkeypatch, capsys):
    import json

    monkeypatch.setattr(scan, "run_gh_search_json", failing)
    monkeypatch.setattr(scan, "fetch_latest_release", lambda repo: None)
    monkeypatch.setattr(scan.sys, "argv", ["scan.py", "--issue-repos", "o/r"])
    scan.DEGRADED.append("left over from an earlier run")
    assert scan.main() == 0
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert "left over from an earlier run" not in errors
    assert any("issue search" in e for e in errors)
    assert any("candidate search" in e for e in errors)


def bad_json(*args, **kwargs):
    raise ValueError("Expecting value: line 1 column 1 (char 0)")


def test_repo_listing_bad_json_is_recorded_not_fatal(monkeypatch):
    monkeypatch.setattr(scan, "_graphql_pages", bad_json)
    assert scan.enumerate_owner_items(["dashpay"], ["u"], 1) == ([], [])
    assert any("repo listing" in e for e in scan.DEGRADED)


def test_repo_enumeration_bad_json_is_recorded_not_fatal(monkeypatch):
    def pages(query, variables, path):
        if path[0] == "repositoryOwner":
            return iter(
                [
                    {
                        "nameWithOwner": "dashpay/x",
                        "pullRequests": {"totalCount": 1},
                        "issues": {"totalCount": 0},
                    }
                ]
            )
        raise ValueError("truncated JSON")

    monkeypatch.setattr(scan, "_graphql_pages", pages)
    assert scan.enumerate_owner_items(["dashpay"], ["u"], 1) == ([], [])
    assert any("enumeration failed (dashpay/x)" in e for e in scan.DEGRADED)
