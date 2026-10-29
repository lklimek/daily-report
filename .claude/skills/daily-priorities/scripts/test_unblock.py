"""unblock.py gates on real CI failures (ciEffective), not the raw rollup."""

from __future__ import annotations

import pytest

import unblock


def pr(**overrides) -> dict:
    p = {
        "repo": "dashpay/dash-evo-tool",
        "number": 3,
        "author": "alice",
        "authorIsBot": False,
        "relations": ["review_requested"],
        "isDraft": False,
        "ciState": "SUCCESS",
        "ciEffective": "green",
        "ciRedChecks": [],
        "unresolvedThreadsTotal": 0,
        "unresolvedBotFlagged": 0,
        "unresolvedBotFlaggedSeverities": [],
    }
    p.update(overrides)
    return p


@pytest.mark.parametrize(
    ("ci_state", "effective", "gated"),
    [
        ("FAILURE", "red", True),
        ("PENDING", "green", False),  # only PR Hygiene pending
        ("FAILURE", "green", False),  # only a policy gate failed
        ("PENDING", "pending", False),  # still running
        ("SUCCESS", "unknown", False),
    ],
)
def test_is_gated_uses_effective_ci(ci_state, effective, gated):
    assert unblock.is_gated(pr(ciState=ci_state, ciEffective=effective)) is gated


def test_blocker_body_names_red_checks_only():
    body, lines = unblock.build_blocker_body(
        pr(ciEffective="red", ciRedChecks=["Test Suite", "Clippy"])
    )
    assert lines[0] == "CI failing (Test Suite, Clippy)."
    green_body, green_lines = unblock.build_blocker_body(
        pr(ciState="FAILURE", isDraft=True)
    )
    assert green_lines == ["Still a draft."]


def test_graphql_bot_author_is_skipped(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("must not touch GitHub")

    monkeypatch.setattr(unblock, "get_pr_state", no_network)
    result = unblock.process_pr(
        pr(author="coderabbitai", authorIsBot=True),
        True,
        unblock.DEFAULT_REPOS,
        "lklimek",
    )
    assert result["action"] == "skip-bot"


def test_pseudo_bot_author_is_reassigned_not_skipped(monkeypatch):
    monkeypatch.setattr(
        unblock, "get_pr_state", lambda repo, number: {"comments": [], "assignees": []}
    )
    result = unblock.process_pr(
        pr(author="thepastaclaw", authorIsBot=True, isDraft=True),
        True,
        unblock.DEFAULT_REPOS,
        "lklimek",
    )
    assert result["assignee"] == "lklimek"
    assert result["action"] == "comment+assign"


def test_comment_is_current_only_for_identical_blockers():
    body = (
        "Not ready for review yet:\n1. CI failing (T).\n2. Still a draft.\n\n"
        f"Please address and re-request review when ready.\n\n{unblock.MARKER}"
    )
    state = {"comments": [{"author": {"login": "lklimek"}, "body": body}]}
    assert unblock.last_comment_is_current(state, ["CI failing (T).", "Still a draft."])
    # Blockers shrank: the old comment overstates them and must be replaced.
    assert not unblock.last_comment_is_current(state, ["Still a draft."])


def write_scan(tmp_path, generated_at):
    import json

    path = tmp_path / "scan.json"
    path.write_text(json.dumps({"generated_at": generated_at, "prs": []}))
    return str(path)


def test_stale_scan_is_refused(tmp_path, monkeypatch, capsys):
    path = write_scan(tmp_path, "2026-10-29T08:00:00+00:00")
    now = unblock.dt.datetime(2026, 10, 29, 9, 0, tzinfo=unblock.dt.timezone.utc)
    monkeypatch.setattr(unblock, "now_utc", lambda: now)
    monkeypatch.setattr(unblock.sys, "argv", ["unblock.py", "--scan-file", path])
    assert unblock.main() == 1
    err = capsys.readouterr().err
    assert "stale" in err
    # Overriding is the user's call, so the error must not suggest it.
    assert "--allow-stale" not in err
    monkeypatch.setattr(
        unblock.sys, "argv", ["unblock.py", "--scan-file", path, "--allow-stale"]
    )
    assert unblock.main() == 0


def test_fresh_scan_is_accepted(tmp_path, monkeypatch, capsys):
    path = write_scan(tmp_path, "2026-10-29T08:50:00+00:00")
    now = unblock.dt.datetime(2026, 10, 29, 9, 0, tzinfo=unblock.dt.timezone.utc)
    monkeypatch.setattr(unblock, "now_utc", lambda: now)
    monkeypatch.setattr(unblock.sys, "argv", ["unblock.py", "--scan-file", path])
    assert unblock.main() == 0
