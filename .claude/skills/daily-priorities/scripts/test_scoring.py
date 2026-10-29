"""Tests for scoring.py — mechanical score components per SKILL.md §2."""

from __future__ import annotations


import pytest

import scoring

USERS = ["lklimek", "Claudius-Maginificent"]


def make_pr(**overrides) -> dict:
    pr = {
        "repo": "dashpay/dash-evo-tool",
        "number": 1,
        "title": "fix: thing",
        "author": "lklimek",
        "authorIsBot": False,
        "relations": ["authored"],
        "isDraft": False,
        "labels": [],
        "dependentPRs": [],
        "stackedOnPRs": [],
        "baseRefName": "v1.0-dev",
        "reviewPendingFor": [],
        "reviewRequestFreshnessDays": None,
        "reviewRequestFreshnessIsApprox": False,
        "reviewDecision": None,
        "changesRequestedByHuman": False,
        "changesRequestedHumanAgeDays": None,
        "awaitingOurReply": False,
        "lastCommentAuthor": None,
        "lastCommentAgeDays": None,
        "staleDays": 0.0,
        "additions": 0,
        "deletions": 0,
        "ciEffective": "green",
        "ciRedChecks": [],
        "mergeable": "MERGEABLE",
        "unresolvedThreadsTotal": 0,
        "conventionalType": "fix",
        "ownedChange": None,
    }
    pr.update(overrides)
    return pr


def make_issue(**overrides) -> dict:
    issue = {
        "repo": "dashpay/tenderdash",
        "number": 7,
        "title": "crash on start",
        "author": "someone",
        "relations": ["repo_open"],
        "labels": [],
        "updatedDays": 0.0,
        "isNew": False,
    }
    issue.update(overrides)
    return issue


# --- formulas ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "expected"),
    [(0, 15.0), (1, 12.74), (3, 9.08), (7, 4.26), (15, 0.0), (30, -1.76)],
)
def test_recency_points_matches_skill_examples(days, expected):
    assert scoring.recency_points(days) == pytest.approx(expected, abs=0.01)


def test_recency_points_floor_and_future_timestamps():
    assert scoring.recency_points(10_000) == pytest.approx(-2.0, abs=1e-6)
    assert scoring.recency_points(10_000) >= -2.0
    # Clock skew (updatedAt slightly in the future) must not exceed the max.
    assert scoring.recency_points(-0.5) == 15.0


@pytest.mark.parametrize(
    ("lines", "expected"),
    [(0, 12.5), (1000, 10.0), (5000, 4.096), (10000, 1.34217728)],
)
def test_change_size_points_matches_skill_examples(lines, expected):
    assert scoring.change_size_points(lines) == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize(
    ("age", "expected"),
    [(0.0, 15), (2.0, 15), (2.01, 10), (7.0, 10), (7.01, 5), (90.0, 5)],
)
def test_waiting_points_buckets(age, expected):
    assert scoring.waiting_points(age) == expected


@pytest.mark.parametrize(("n", "expected"), [(0, 0), (1, 5), (2, 10), (3, 15), (9, 15)])
def test_unblocks_points_buckets(n, expected):
    assert scoring.unblocks_points(n) == expected


@pytest.mark.parametrize(
    ("score", "tier"),
    [
        (157.5, "P0"),
        (100.0001, "P0"),
        (100.0, "P1"),
        (65.0, "P1"),
        (64.999, "P2"),
        (35.0, "P2"),
        (34.999, "P3"),
        (-22.0, "P3"),
    ],
)
def test_tier_thresholds(score, tier):
    assert scoring.tier(score) == tier


# --- part schema invariants ------------------------------------------------


def assert_part_schema(parts: dict) -> None:
    assert list(parts) == scoring.PART_ORDER
    for name, p in parts.items():
        assert p["status"] in scoring.STATUSES, name
        if p["status"] == "needs_judgment":
            assert p["value"] is None, name
            assert p.get("hint"), name
        else:
            assert isinstance(p["value"], (int, float)), name
            assert p.get("basis"), name
        if p["status"] == "provisional":
            assert p.get("hint"), name


def test_pr_parts_schema_and_judgment_components_left_open():
    parts = scoring.pr_score_parts(make_pr(), USERS)
    assert_part_schema(parts)
    for name in ("defect_severity", "urgency_bonus", "change_impact", "completion"):
        assert parts[name]["status"] == "needs_judgment"
        assert parts[name]["value"] is None


def test_issue_parts_schema_and_size_not_applicable():
    parts = scoring.issue_score_parts(make_issue(), body="")
    assert_part_schema(parts)
    assert parts["change_size"] == {
        "value": 0,
        "status": "not_applicable",
        "basis": "issue: no diff",
    }
    assert parts["recency"]["status"] == "computed"
    assert parts["recency"]["value"] == pytest.approx(15.0)
    for name in ("defect_severity", "urgency_bonus", "change_impact", "completion"):
        assert parts[name]["status"] == "needs_judgment"
    assert parts["unblocks"]["status"] == "needs_judgment"
    assert parts["human_waiting"]["status"] == "needs_judgment"
    # A standalone issue is unstarted work (SKILL table): 0, decided.
    assert parts["advancement"]["status"] == "computed"
    assert parts["advancement"]["value"] == 0


# --- advancement ------------------------------------------------------------


def test_advancement_draft_nondraft_ready_for_human():
    draft = scoring.pr_score_parts(make_pr(isDraft=True), USERS)["advancement"]
    assert (draft["value"], draft["status"]) == (15, "computed")
    ready = scoring.pr_score_parts(make_pr(), USERS)["advancement"]
    assert (ready["value"], ready["status"]) == (20, "computed")
    rfh = scoring.pr_score_parts(make_pr(labels=["Ready-For-Human"]), USERS)
    assert (rfh["advancement"]["value"], rfh["advancement"]["status"]) == (
        30,
        "provisional",
    )
    assert "bot acceptance" in rfh["advancement"]["hint"]


def test_advancement_draft_with_ready_label_stays_draft():
    parts = scoring.pr_score_parts(
        make_pr(isDraft=True, labels=["ready-for-human"]), USERS
    )
    assert (parts["advancement"]["value"], parts["advancement"]["status"]) == (
        15,
        "computed",
    )


# --- unblocks ---------------------------------------------------------------


def test_unblocks_counts_dependents_and_lookup_failure_is_not_zero():
    two = scoring.pr_score_parts(make_pr(dependentPRs=[5, 6]), USERS)["unblocks"]
    assert (two["value"], two["status"]) == (10, "computed")
    failed = scoring.pr_score_parts(make_pr(dependentPRs=None), USERS)["unblocks"]
    assert failed["status"] == "needs_judgment"
    assert failed["value"] is None


# --- human waiting ----------------------------------------------------------


def review_request(days, approx=False, author="alice", bot=False):
    return {
        "author": author,
        "authorIsBot": bot,
        "relations": ["review_requested"],
        "reviewPendingFor": ["lklimek"],
        "reviewRequestFreshnessDays": days,
        "reviewRequestFreshnessIsApprox": approx,
    }


@pytest.mark.parametrize(("days", "expected"), [(1.0, 15), (5.0, 10), (10.0, 5)])
def test_human_waiting_review_request_from_human(days, expected):
    p = scoring.pr_score_parts(make_pr(**review_request(days)), USERS)["human_waiting"]
    assert (p["value"], p["status"]) == (expected, "computed")


def test_human_waiting_approx_age_is_provisional():
    p = scoring.pr_score_parts(make_pr(**review_request(1.0, approx=True)), USERS)[
        "human_waiting"
    ]
    assert p["status"] == "provisional"
    assert p["value"] == 15
    assert "approx" in p["hint"]


# GraphQL returns Bot logins WITHOUT the [bot] suffix; the scanner carries
# the __typename verdict as authorIsBot.
@pytest.mark.parametrize(
    ("author", "bot"),
    [
        ("dependabot", True),
        ("coderabbitai", True),
        ("thepastaclaw", False),
        ("Claudius-Maginificent", False),
        ("lklimek", False),
    ],
)
def test_human_waiting_ignores_bot_and_own_authors(author, bot):
    p = scoring.pr_score_parts(
        make_pr(**review_request(1.0, author=author, bot=bot)), USERS
    )["human_waiting"]
    assert (p["value"], p["status"]) == (0, "computed")


@pytest.mark.parametrize(
    ("login", "typename", "expected"),
    [
        ("dependabot", "Bot", True),
        ("coderabbitai", "Bot", True),
        ("renovate[bot]", None, True),
        ("thepastaclaw", "User", True),
        ("DCG-Claude", None, True),
        ("alice", "User", False),
        (None, None, False),
    ],
)
def test_is_bot_uses_typename_and_pseudo_bots(login, typename, expected):
    assert scoring.is_bot(login, typename) is expected


def test_human_waiting_change_request_without_review_decision():
    # Repos without required reviews report reviewDecision: null.
    p = scoring.pr_score_parts(
        make_pr(
            reviewDecision=None,
            changesRequestedByHuman=True,
            changesRequestedHumanAgeDays=1.0,
        ),
        USERS,
    )["human_waiting"]
    assert (p["value"], p["status"]) == (15, "computed")


def test_human_waiting_decision_without_visible_change_request_is_provisional():
    p = scoring.pr_score_parts(
        make_pr(reviewDecision="CHANGES_REQUESTED", changesRequestedBy=[]),
        USERS,
    )["human_waiting"]
    assert p["status"] == "provisional"
    assert "window" in p["hint"]


def test_human_waiting_rerequested_change_request_waits_on_reviewer():
    p = scoring.pr_score_parts(
        make_pr(changesRequestedByHuman=True, changesRequestedHumanAgeDays=None),
        USERS,
    )["human_waiting"]
    assert (p["value"], p["status"]) == (0, "computed")
    assert "re-requested" in p["basis"]


def test_human_waiting_bot_only_change_request_in_window_is_provisional():
    p = scoring.pr_score_parts(
        make_pr(
            reviewDecision="CHANGES_REQUESTED",
            changesRequestedBy=["coderabbitai"],
            reviewsTruncated=True,
        ),
        USERS,
    )["human_waiting"]
    assert p["status"] == "provisional"
    # Every review fetched: a bot-only CR fully explains the decision.
    full = scoring.pr_score_parts(
        make_pr(
            reviewDecision="CHANGES_REQUESTED", changesRequestedBy=["coderabbitai"]
        ),
        USERS,
    )["human_waiting"]
    assert (full["value"], full["status"]) == (0, "computed")


def test_human_waiting_team_review_request_is_provisional():
    p = scoring.pr_score_parts(
        make_pr(author="alice", relations=["review_requested"], reviewPendingFor=[]),
        USERS,
    )["human_waiting"]
    assert p["status"] == "provisional"
    assert p["value"] == 0
    assert "team" in p["hint"]


def test_human_waiting_none_requested_is_computed_zero():
    p = scoring.pr_score_parts(make_pr(), USERS)["human_waiting"]
    assert (p["value"], p["status"]) == (0, "computed")


def test_human_waiting_human_change_request_on_own_pr():
    p = scoring.pr_score_parts(
        make_pr(
            reviewDecision="CHANGES_REQUESTED",
            changesRequestedByHuman=True,
            changesRequestedHumanAgeDays=4.0,
        ),
        USERS,
    )["human_waiting"]
    assert (p["value"], p["status"]) == (10, "computed")


def test_human_waiting_fresher_unanswered_comment_is_provisional():
    p = scoring.pr_score_parts(
        make_pr(
            **review_request(10.0),
            awaitingOurReply=True,
            lastCommentAuthor="bob",
            lastCommentAgeDays=1.0,
        ),
        USERS,
    )["human_waiting"]
    assert p["status"] == "provisional"
    assert p["value"] == 5
    assert "@bob" in p["hint"]


def test_human_waiting_older_unanswered_comment_does_not_change_result():
    p = scoring.pr_score_parts(
        make_pr(
            **review_request(1.0),
            awaitingOurReply=True,
            lastCommentAuthor="bob",
            lastCommentAgeDays=9.0,
        ),
        USERS,
    )["human_waiting"]
    assert (p["value"], p["status"]) == (15, "computed")


# --- recency / size ---------------------------------------------------------


def test_recency_part_uses_stale_days_and_missing_is_judgment():
    p = scoring.pr_score_parts(make_pr(staleDays=15.0), USERS)["recency"]
    assert p["status"] == "computed"
    assert p["value"] == pytest.approx(0.0, abs=1e-9)
    future = scoring.pr_score_parts(make_pr(staleDays=-0.001), USERS)["recency"]
    assert "-0.00" not in future["basis"]
    assert future["value"] == 15.0
    missing = scoring.pr_score_parts(make_pr(staleDays=None), USERS)["recency"]
    assert (missing["value"], missing["status"]) == (None, "needs_judgment")


def test_change_size_part_uses_full_diff_and_missing_is_judgment():
    p = scoring.pr_score_parts(make_pr(additions=600, deletions=400), USERS)[
        "change_size"
    ]
    assert p["status"] == "computed"
    assert p["value"] == pytest.approx(10.0)
    assert "1000" in p["basis"]
    missing = scoring.pr_score_parts(make_pr(additions=None, deletions=None), USERS)
    assert missing["change_size"]["status"] == "needs_judgment"
    assert missing["change_size"]["value"] is None


# --- blocked penalty --------------------------------------------------------


def test_blocked_penalty_stacked_on_open_pr():
    p = scoring.pr_score_parts(
        make_pr(baseRefName="feat/base", stackedOnPRs=[42]), USERS
    )["blocked_penalty"]
    assert (p["value"], p["status"]) == (-20, "computed")
    assert "#42" in p["basis"]


def test_blocked_penalty_label_needs_judgment_and_failed_lookup_too():
    labelled = scoring.pr_score_parts(make_pr(labels=["blocked"]), USERS)
    assert labelled["blocked_penalty"]["status"] == "needs_judgment"
    failed = scoring.pr_score_parts(make_pr(stackedOnPRs=None), USERS)
    assert failed["blocked_penalty"]["status"] == "needs_judgment"


def test_blocked_penalty_stacked_wins_over_label_applied_once():
    p = scoring.pr_score_parts(make_pr(labels=["blocked"], stackedOnPRs=[42]), USERS)[
        "blocked_penalty"
    ]
    assert (p["value"], p["status"]) == (-20, "computed")


def test_blocked_penalty_pr_body_dependency_needs_judgment():
    p = scoring.pr_score_parts(
        make_pr(), USERS, body="Depends on dashpay/platform#5588"
    )["blocked_penalty"]
    assert (p["value"], p["status"]) == (None, "needs_judgment")


def test_unblocks_basis_states_what_was_not_checked():
    p = scoring.pr_score_parts(make_pr(), USERS)["unblocks"]
    assert "not checked" in p["basis"]


def test_blocked_penalty_unblocked_is_computed_zero():
    p = scoring.pr_score_parts(make_pr(), USERS)["blocked_penalty"]
    assert (p["value"], p["status"]) == (0, "computed")


def test_issue_blocked_penalty_label_or_text():
    assert (
        scoring.issue_score_parts(make_issue(labels=["Blocked"]), body="")[
            "blocked_penalty"
        ]["status"]
        == "needs_judgment"
    )
    assert (
        scoring.issue_score_parts(make_issue(), body="This is blocked by #12.")[
            "blocked_penalty"
        ]["status"]
        == "needs_judgment"
    )
    clear = scoring.issue_score_parts(make_issue(), body="plain report")
    assert (clear["blocked_penalty"]["value"], clear["blocked_penalty"]["status"]) == (
        0,
        "computed",
    )


# --- CI classification ------------------------------------------------------


def check(
    name,
    conclusion="SUCCESS",
    status="COMPLETED",
    started="2026-10-01T00:00Z",
    workflow="CI",
):
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "startedAt": started,
        "checkSuite": {"workflowRun": {"workflow": {"name": workflow}}},
    }


def ctx(name, state):
    return {
        "__typename": "StatusContext",
        "context": name,
        "state": state,
        "createdAt": "2026-10-01T00:00Z",
    }


def rollup(nodes, has_next=False, state="PENDING"):
    return {
        "state": state,
        "contexts": {"pageInfo": {"hasNextPage": has_next}, "nodes": nodes},
    }


def test_ci_policy_only_failures_are_green():
    ci = scoring.classify_ci(
        rollup(
            [
                check("Clippy"),
                ctx("PR Hygiene", "PENDING"),
                check("policy / reconcile", "FAILURE"),
            ]
        )
    )
    assert ci["ciEffective"] == "green"
    assert ci["ciRedChecks"] == []
    assert ci["ciPolicyNotPassing"] == [
        "PR Hygiene (PENDING)",
        "policy / reconcile (FAILURE)",
    ]


def test_ci_real_failure_is_red_with_names():
    ci = scoring.classify_ci(
        rollup(
            [
                check("Test Suite", "FAILURE"),
                check("Clippy", "TIMED_OUT"),
                ctx("ci/legacy", "ERROR"),
                ctx("PR Hygiene", "FAILURE"),
            ],
            state="FAILURE",
        )
    )
    assert ci["ciEffective"] == "red"
    assert ci["ciRedChecks"] == ["Clippy", "Test Suite", "ci/legacy"]
    assert ci["ciPolicyNotPassing"] == ["PR Hygiene (FAILURE)"]


def test_ci_pending_real_check_is_pending():
    ci = scoring.classify_ci(rollup([check("Build", None, status="IN_PROGRESS")]))
    assert ci["ciEffective"] == "pending"
    assert ci["ciPendingChecks"] == ["Build"]


def test_ci_latest_run_per_name_wins():
    ci = scoring.classify_ci(
        rollup(
            [
                check("Test Suite", "SUCCESS", started="2026-10-02T00:00Z"),
                check("Test Suite", "FAILURE", started="2026-10-01T00:00Z"),
            ]
        )
    )
    assert ci["ciEffective"] == "green"
    rerun = scoring.classify_ci(
        rollup(
            [
                check("Test Suite", "FAILURE", started="2026-10-01T00:00Z"),
                check("Test Suite", None, status="QUEUED", started=None),
            ]
        )
    )
    assert rerun["ciEffective"] == "pending"


def test_ci_same_job_name_in_other_workflow_does_not_mask_failure():
    ci = scoring.classify_ci(
        rollup(
            [
                check("build", "FAILURE", started="2026-10-01T00:00Z", workflow="A"),
                check("build", "SUCCESS", started="2026-10-02T00:00Z", workflow="B"),
            ]
        )
    )
    assert ci["ciEffective"] == "red"
    assert ci["ciRedChecks"] == ["A / build"]


def test_ci_rollup_without_contexts_is_pending():
    assert scoring.classify_ci(rollup([]))["ciEffective"] == "pending"


def test_ci_skipped_and_neutral_are_passing():
    ci = scoring.classify_ci(
        rollup([check("a", "SKIPPED"), check("b", "NEUTRAL")], state="SUCCESS")
    )
    assert ci["ciEffective"] == "green"


def test_ci_truncated_without_red_is_unknown():
    ci = scoring.classify_ci(rollup([check("a")], has_next=True))
    assert ci["ciEffective"] == "unknown"
    red = scoring.classify_ci(rollup([check("a", "FAILURE")], has_next=True))
    assert red["ciEffective"] == "red"


def test_ci_no_rollup_is_none():
    ci = scoring.classify_ci(None)
    assert ci["ciEffective"] == "none"
    assert ci["ciRedChecks"] == []


# --- eligibility ------------------------------------------------------------


def test_eligibility_platform_requires_significant_owned_change():
    plat = {"repo": "dashpay/platform", "author": "lklimek", "isDraft": False}
    ok, reason = scoring.pr_eligibility(
        {**plat, "ownedChange": {"verdict": "significant"}}, USERS
    )
    assert ok and reason
    ok, reason = scoring.pr_eligibility(
        {**plat, "ownedChange": {"verdict": "minor"}}, USERS
    )
    assert not ok
    assert "minor" in reason
    ok, _ = scoring.pr_eligibility({**plat, "ownedChange": None}, USERS)
    assert not ok


@pytest.mark.parametrize(
    ("author", "eligible"),
    [("lklimek", True), ("claudius-maginificent", True), ("alice", False)],
)
def test_eligibility_drafts_only_ours(author, eligible):
    ok, reason = scoring.pr_eligibility(make_pr(isDraft=True, author=author), USERS)
    assert ok is eligible
    assert reason


def test_eligibility_non_draft_elsewhere_is_eligible():
    ok, _ = scoring.pr_eligibility(make_pr(author="alice"), USERS)
    assert ok


# --- scoring doc block ------------------------------------------------------


def test_scoring_doc_block_is_minimal_and_derived():
    doc = scoring.scoring_doc()
    assert set(doc) == {"instruction", "formula", "tiers", "statuses", "helper"}
    assert "NOT final" in doc["instruction"]
    assert doc["tiers"] == {"P0": ">100", "P1": "65-100", "P2": "35-<65", "P3": "<35"}
    assert doc["formula"] == "score = " + " + ".join(scoring.PART_ORDER)
    assert set(doc["statuses"]) == scoring.STATUSES.keys()


def test_advancement_legend_matches_skill_table():
    # SKILL.md: started TODO without a PR -> 10; unstarted work -> 0.
    assert "started TODO without a PR 10" in scoring.LEGEND["advancement"]
    assert "issue" not in scoring.LEGEND["advancement"]
