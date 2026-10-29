"""Tests for score.py — merging model judgment into scanner scoreParts."""

from __future__ import annotations

import json

import pytest

import score
import scoring
from test_scoring import USERS, make_issue, make_pr

JUDGED = {
    "defect_severity": 0,
    "urgency_bonus": 0,
    "change_impact": 5,
    "completion": 5,
}


def scan_item(item_id: str, kind: str, parts: dict, eligible=True, **fields) -> dict:
    return {
        **fields,
        "id": item_id,
        "kind": kind,
        "title": f"title {item_id}",
        "url": f"https://example.invalid/{item_id}",
        "eligible": eligible,
        "eligibilityReason": "ok" if eligible else "draft by alice",
        "scoreParts": parts,
    }


def pr_parts(**overrides) -> dict:
    return scoring.pr_score_parts(make_pr(**overrides), USERS)


@pytest.fixture
def scan() -> dict:
    return {
        "generated_at": "2026-10-29T08:00:00+00:00",
        "prs": [
            scan_item("DET-1", "pr", pr_parts()),
            scan_item("DET-2", "pr", pr_parts(isDraft=True), eligible=False),
        ],
        "issues": [
            scan_item(
                "TD-7", "issue", scoring.issue_score_parts(make_issue(), body="")
            ),
        ],
    }


def full_judgment() -> dict:
    return {
        "DET-1": {"parts": dict(JUDGED)},
        "TD-7": {
            "parts": {
                **JUDGED,
                "advancement": 0,
                "unblocks": 0,
                "human_waiting": 0,
            }
        },
    }


def test_complete_judgment_scores_tiers_and_sorts(scan):
    result = score.merge(scan, full_judgment())
    assert result["complete"] is True
    assert result["unscored"] == []
    ids = [r["id"] for r in result["ranked"]]
    assert ids == ["DET-1", "TD-7"]
    det = result["ranked"][0]
    # advancement 20 + recency 15 + size 12.5 + impact 5 + completion 5
    assert det["score"] == pytest.approx(57.5)
    assert det["tier"] == "P2"
    assert det["final"] is True
    assert det["breakdown"] == "20+0+0+0+15+5+5+12.5+0+0=57.5"


def test_ineligible_rows_are_excluded_without_judgment(scan):
    result = score.merge(scan, full_judgment())
    assert [r["id"] for r in result["excluded"]] == ["DET-2"]
    assert result["excluded"][0]["reason"] == "draft by alice"


def test_missing_judgment_leaves_row_unscored_not_zero(scan):
    judgment = full_judgment()
    del judgment["DET-1"]["parts"]["completion"]
    result = score.merge(scan, judgment)
    assert result["complete"] is False
    unscored = {r["id"]: r for r in result["unscored"]}
    assert unscored["DET-1"]["missing"] == ["completion"]
    assert "score" not in unscored["DET-1"]
    assert [r["id"] for r in result["ranked"]] == ["TD-7"]


def test_null_judgment_value_counts_as_missing(scan):
    judgment = full_judgment()
    judgment["DET-1"]["parts"]["completion"] = None
    result = score.merge(scan, judgment)
    assert result["unscored"][0]["missing"] == ["completion"]


def test_unconfirmed_provisional_part_marks_row_not_final(scan):
    scan["prs"][0]["scoreParts"] = pr_parts(labels=["ready-for-human"])
    result = score.merge(scan, full_judgment())
    row = next(r for r in result["ranked"] if r["id"] == "DET-1")
    assert row["final"] is False
    assert row["unconfirmed"] == ["advancement"]
    assert row["score"] == pytest.approx(67.5)
    assert result["complete"] is False


def test_confirming_provisional_part_makes_row_final(scan):
    scan["prs"][0]["scoreParts"] = pr_parts(labels=["ready-for-human"])
    judgment = full_judgment()
    judgment["DET-1"]["parts"]["advancement"] = {"value": 20, "basis": "stale label"}
    result = score.merge(scan, judgment)
    row = result["ranked"][0]
    assert row["final"] is True
    assert row["parts"]["advancement"]["value"] == 20
    assert row["parts"]["advancement"]["status"] == "judged"
    assert row["parts"]["advancement"]["basis"] == "stale label"


def test_scan_errors_make_run_incomplete(scan):
    scan["errors"] = ["lklimek/penny#35: forbidden"]
    result = score.merge(scan, full_judgment())
    assert result["scanErrors"] == ["lklimek/penny#35: forbidden"]
    assert result["complete"] is False
    assert all(r["final"] for r in result["ranked"])


def test_also_ids_absorb_scan_item(scan):
    judgment = full_judgment()
    judgment["DET-1"]["alsoIds"] = ["TD-7", "TODO-deadbeef"]
    del judgment["TD-7"]
    result = score.merge(scan, judgment)
    assert [r["id"] for r in result["ranked"]] == ["DET-1"]
    excluded = {r["id"]: r["reason"] for r in result["excluded"]}
    assert excluded["TD-7"] == "merged into DET-1"
    assert result["complete"] is True


def test_absorbed_item_with_template_nulls_is_fine_but_values_are_rejected(scan):
    judgment = full_judgment()
    judgment["DET-1"]["alsoIds"] = ["TD-7"]
    judgment["TD-7"] = {"parts": dict.fromkeys(judgment["TD-7"]["parts"])}
    assert score.merge(scan, judgment)["complete"] is True
    judgment["TD-7"]["parts"]["completion"] = 5
    with pytest.raises(score.JudgmentError, match="merged into DET-1"):
        score.merge(scan, judgment)


@pytest.mark.parametrize(
    "also", [{"DET-1": ["DET-1"]}, {"DET-1": ["TD-7"], "TODO-x": ["TD-7"]}]
)
def test_invalid_also_ids_are_rejected(scan, also):
    judgment = full_judgment()
    judgment["TODO-x"] = {"kind": "todo", "parts": {}}
    for item_id, ids in also.items():
        judgment[item_id]["alsoIds"] = ids
    with pytest.raises(score.JudgmentError, match="alsoIds"):
        score.merge(scan, judgment)


def test_judged_provisional_value_keeps_row_not_final(scan):
    judgment = full_judgment()
    judgment["DET-1"]["parts"]["completion"] = {
        "value": 5,
        "provisional": True,
        "basis": "CI rerun pending",
    }
    row = score.merge(scan, judgment)["ranked"][0]
    assert row["parts"]["completion"]["status"] == "provisional"
    assert row["final"] is False
    assert row["unconfirmed"] == ["completion"]
    judgment["DET-1"]["parts"]["completion"] = {"value": 5, "provisional": True}
    with pytest.raises(score.JudgmentError, match="basis"):
        score.merge(scan, judgment)


def test_override_without_basis_is_rejected(scan):
    judgment = full_judgment()
    judgment["DET-1"]["parts"]["unblocks"] = 5
    with pytest.raises(score.JudgmentError, match="basis"):
        score.merge(scan, judgment)


def test_replaced_provisional_does_not_inherit_scanner_basis(scan):
    scan["prs"][0]["scoreParts"] = pr_parts(labels=["ready-for-human"])
    judgment = full_judgment()
    judgment["DET-1"]["parts"]["advancement"] = 20
    part = score.merge(scan, judgment)["ranked"][0]["parts"]["advancement"]
    assert part["value"] == 20
    assert "basis" not in part
    judgment["DET-1"]["parts"]["advancement"] = 30
    part = score.merge(scan, judgment)["ranked"][0]["parts"]["advancement"]
    assert part["basis"] == "`ready-for-human` label"


def test_override_of_computed_part_is_visible(scan):
    judgment = full_judgment()
    judgment["DET-1"]["parts"]["unblocks"] = {"value": 5, "basis": "TODO-abc waits"}
    row = score.merge(scan, judgment)["ranked"][0]
    part = row["parts"]["unblocks"]
    assert part["status"] == "overridden"
    assert part["value"] == 5
    assert part["scannerValue"] == 0


def test_invalid_values_are_rejected(scan):
    for name, value in (
        ("change_impact", 7),
        ("defect_severity", 21),
        ("completion", True),
        ("completion", "5"),
    ):
        judgment = full_judgment()
        judgment["DET-1"]["parts"][name] = value
        with pytest.raises(score.JudgmentError, match=name):
            score.merge(scan, judgment)


def test_unknown_part_is_rejected(scan):
    judgment = full_judgment()
    judgment["DET-1"]["parts"]["vibes"] = 10
    with pytest.raises(score.JudgmentError, match="vibes"):
        score.merge(scan, judgment)


@pytest.mark.parametrize(
    "judgment", [[], {"DET-1": 5}, {"DET-1": {"parts": [1]}}, {"DET-1": {"x": 1}}]
)
def test_malformed_judgment_is_rejected(scan, judgment):
    with pytest.raises(score.JudgmentError):
        score.merge(scan, judgment)


def test_unknown_id_without_kind_is_rejected(scan):
    judgment = full_judgment()
    judgment["DET-1049"] = {"parts": dict(JUDGED)}
    with pytest.raises(score.JudgmentError, match="DET-1049"):
        score.merge(scan, judgment)


def test_extra_todo_row_needs_all_parts(scan):
    judgment = full_judgment()
    judgment["TODO-abc12345"] = {
        "kind": "todo",
        "title": "write docs",
        "url": "https://memcan.example.invalid/ui/tasks/abc",
        "parts": {name: 0 for name in scoring.PART_ORDER if name != "recency"},
    }
    result = score.merge(scan, judgment)
    todo = next(r for r in result["unscored"] if r["id"] == "TODO-abc12345")
    assert todo["missing"] == ["recency"]
    judgment["TODO-abc12345"]["parts"]["recency"] = {"days": 0}
    result = score.merge(scan, judgment)
    todo = next(r for r in result["ranked"] if r["id"] == "TODO-abc12345")
    assert todo["score"] == 15
    assert todo["kind"] == "todo"
    assert todo["final"] is True


def test_judgment_can_flip_eligibility_and_keeps_also_ids(scan):
    judgment = full_judgment()
    judgment["DET-1"]["eligible"] = False
    judgment["DET-1"]["eligibilityReason"] = "only mechanical renames"
    judgment["TD-7"]["alsoIds"] = ["TODO-deadbeef"]
    result = score.merge(scan, judgment)
    assert {r["id"]: r["reason"] for r in result["excluded"]}["DET-1"] == (
        "only mechanical renames"
    )
    assert result["ranked"][0]["alsoIds"] == ["TODO-deadbeef"]


def tie_row(item_id, adv, sev, unb):
    parts = {name: 0 for name in scoring.PART_ORDER}
    parts.update(advancement=adv, defect_severity=sev, unblocks=unb)
    parts["recency"] = {"days": 0}  # exactly 15 for every row
    # Keep the total equal across rows: pad with change_impact/completion.
    pad = 30 - adv - sev - unb
    parts["completion"] = 10 if pad >= 10 else 0
    parts["change_impact"] = pad - parts["completion"]
    return item_id, {"kind": "todo", "title": item_id, "parts": parts}


def test_tie_break_order_then_natural_id():
    judgment = dict(
        [
            tie_row("DET-1000", 15, 0, 5),
            tie_row("DET-999", 15, 0, 5),
            tie_row("A-1", 10, 5, 5),
            tie_row("B-1", 15, 5, 0),
            tie_row("C-1", 20, 0, 0),
        ]
    )
    result = score.merge({"prs": [], "issues": []}, judgment)
    assert [r["score"] for r in result["ranked"]] == [45] * 5
    assert [r["id"] for r in result["ranked"]] == [
        "C-1",
        "B-1",
        "DET-999",
        "DET-1000",
        "A-1",
    ]


def test_template_lists_only_open_parts_of_eligible_items(scan):
    tpl = score.template(scan)
    assert set(tpl) == {"_legend", "DET-1", "TD-7"}
    assert tpl["DET-1"]["parts"] == {
        "defect_severity": None,
        "change_impact": None,
        "completion": None,
        "urgency_bonus": None,
    }
    # Static rules live once in _legend; items carry only item-specific hints.
    assert tpl["DET-1"]["hints"] == {}
    assert "advancement" not in tpl["TD-7"]["parts"]
    assert "unblocks" in tpl["TD-7"]["parts"]
    # Filling the template's nulls is exactly what makes the run complete.
    for item_id in ("DET-1", "TD-7"):
        tpl[item_id]["parts"] = {k: 0 for k in tpl[item_id]["parts"]}
    assert score.merge(scan, tpl)["complete"] is True


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


def test_main_exit_codes(scan, tmp_path, capsys):
    scan_path = write(tmp_path, "scan.json", scan)
    ok = write(tmp_path, "ok.json", full_judgment())
    assert score.main(["--scan", scan_path, "--judgment", ok]) == 0
    assert json.loads(capsys.readouterr().out)["complete"] is True

    partial = write(tmp_path, "partial.json", {})
    assert score.main(["--scan", scan_path, "--judgment", partial]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["complete"] is False
    assert "NOT final" in captured.err

    bad = write(tmp_path, "bad.json", {"DET-1": {"parts": {"completion": 3}}})
    assert score.main(["--scan", scan_path, "--judgment", bad]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "completion" in captured.err

    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert score.main(["--scan", scan_path, "--judgment", str(broken)]) == 1
    assert score.main(["--scan", str(tmp_path / "nope.json"), "--template"]) == 1
    capsys.readouterr()

    assert score.main(["--scan", scan_path, "--template"]) == 0
    out = capsys.readouterr().out
    assert "DET-1" in json.loads(out)
    # One item per line keeps the template small and greppable by id.
    assert sum(1 for line in out.splitlines() if line.startswith('"DET-1"')) == 1


# --- round 2 -----------------------------------------------------------------


def test_scanner_shaped_part_entry_is_rejected(scan):
    judgment = full_judgment()
    judgment["DET-1"]["parts"]["defect_severity"] = {
        "value": 8,
        "status": "provisional",
    }
    with pytest.raises(score.JudgmentError, match="provisional: true"):
        score.merge(scan, judgment)


def test_recency_and_size_are_computed_from_days_and_lines():
    parts = {name: 0 for name in scoring.PART_ORDER}
    parts["recency"] = {"days": 3, "basis": "status changed 3d ago"}
    parts["change_size"] = {"lines": 1000, "basis": "PR diff"}
    judgment = {"TODO-a": {"kind": "todo", "parts": parts}}
    row = score.merge({"prs": [], "issues": []}, judgment)["ranked"][0]
    assert row["parts"]["recency"]["value"] == pytest.approx(9.08, abs=0.01)
    assert row["parts"]["change_size"]["value"] == pytest.approx(10.0)
    assert row["final"] is True


@pytest.mark.parametrize(
    ("name", "entry", "match"),
    [
        ("recency", 9.08, "days"),
        ("recency", {"value": 9.08}, "days"),
        ("change_size", 10.0, "lines"),
        ("change_size", {"lines": -5}, "lines"),
        ("recency", {"days": "3"}, "days"),
        ("completion", {"days": 3}, "days"),
    ],
)
def test_hand_computed_recency_and_size_are_rejected(name, entry, match):
    parts = {n: 0 for n in scoring.PART_ORDER}
    parts["recency"] = {"days": 1}
    parts[name] = entry
    with pytest.raises(score.JudgmentError, match=match):
        score.merge(
            {"prs": [], "issues": []}, {"TODO-a": {"kind": "todo", "parts": parts}}
        )


def test_change_size_zero_means_no_diff():
    parts = {n: 0 for n in scoring.PART_ORDER}
    parts["recency"] = {"days": 0}
    row = score.merge(
        {"prs": [], "issues": []}, {"TODO-a": {"kind": "todo", "parts": parts}}
    )["ranked"][0]
    assert row["parts"]["change_size"]["value"] == 0


def test_also_ids_typo_is_rejected_but_todo_ids_pass(scan):
    judgment = full_judgment()
    judgment["DET-1"]["alsoIds"] = ["DET-99999"]
    with pytest.raises(score.JudgmentError, match="DET-99999"):
        score.merge(scan, judgment)
    judgment["DET-1"]["alsoIds"] = ["TODO-1234abcd"]
    assert score.merge(scan, judgment)["complete"] is True


def test_eligibility_flip_needs_own_reason(scan):
    judgment = full_judgment()
    judgment["DET-1"]["eligible"] = False
    with pytest.raises(score.JudgmentError, match="eligibilityReason"):
        score.merge(scan, judgment)
    judgment["DET-1"]["eligibilityReason"] = "only test renames"
    result = score.merge(scan, judgment)
    assert {r["id"]: r["reason"] for r in result["excluded"]}["DET-1"] == (
        "only test renames"
    )


def test_acknowledged_scan_error_no_longer_blocks_completion(scan):
    scan["errors"] = ["lklimek/penny#35: forbidden", "issue search o/r: rate limit"]
    judgment = full_judgment()
    judgment["_resolvedErrors"] = {"lklimek/penny#35": "token lacks access; disclosed"}
    result = score.merge(scan, judgment)
    assert result["scanErrors"] == ["issue search o/r: rate limit"]
    assert result["resolvedErrors"] == [
        {
            "error": "lklimek/penny#35: forbidden",
            "basis": "token lacks access; disclosed",
        }
    ]
    assert result["complete"] is False
    judgment["_resolvedErrors"]["issue search o/r"] = "re-fetched via gh"
    assert score.merge(scan, judgment)["complete"] is True


@pytest.mark.parametrize(
    "extra",
    [{"_resolvedErrors": {"nope#1": "x"}}, {"_resolvedErrors": {"a#1": ""}}, {"_x": 1}],
)
def test_bad_reserved_keys_are_rejected(scan, extra):
    scan["errors"] = ["a#1: boom"]
    with pytest.raises(score.JudgmentError):
        score.merge(scan, {**full_judgment(), **extra})


def test_template_is_self_sufficient_and_compact():
    pr = make_pr(author="alice", labels=["bug"], ciEffective="red", ciRedChecks=["T"])
    scan = {
        "errors": ["x#1: boom"],
        "prs": [scan_item("DET-1", "pr", scoring.pr_score_parts(pr, USERS), **pr)],
        "issues": [],
    }
    tpl = score.template(scan)
    legend = tpl["_legend"]
    assert set(legend["parts"]) == set(scoring.PART_ORDER)
    assert "provisional" in legend["how"]
    assert legend["scanErrors"] == ["x#1: boom"]
    facts = tpl["DET-1"]["facts"]
    assert facts["title"] == "title DET-1"
    assert facts["ciEffective"] == "red"
    assert facts["ciRedChecks"] == ["T"]
    assert facts["labels"] == ["bug"]
    assert all(v not in (None, [], "", False) for v in facts.values())
    # The filled template is valid judgment input as-is.
    tpl["DET-1"]["parts"] = {k: 0 for k in tpl["DET-1"]["parts"]}
    tpl["_resolvedErrors"] = {"x#1": "disclosed"}
    assert score.merge(scan, tpl)["complete"] is True


# --- round 3 -----------------------------------------------------------------


def test_merge_output_carries_no_stale_instruction(scan):
    assert "instruction" not in score.merge(scan, full_judgment())


@pytest.mark.parametrize(
    "acks",
    [{"": "everything is fine"}, {"issue search failed": "one basis for two repos"}],
)
def test_resolved_error_key_must_match_exactly_one_error(scan, acks):
    scan["errors"] = ["issue search failed (a/b): x", "issue search failed (c/d): y"]
    with pytest.raises(score.JudgmentError, match="exactly one"):
        score.merge(scan, {**full_judgment(), "_resolvedErrors": acks})


def test_how_tells_to_verify_live_or_mark_provisional():
    assert "verify" in score.HOW and "provisional" in score.HOW
    assert "lastCommentAuthor" in score.FACT_KEYS
