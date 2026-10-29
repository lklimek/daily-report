"""Mechanical priority-score components for daily-priorities (SKILL.md §2).

Pure functions, no I/O: scan.py feeds them fetched facts, score.py merges the
model's judgment on top. Every component is a "part":
``{"value", "status", "basis" | "hint"}`` where status is one of STATUSES.
A part the script cannot decide has ``value: None`` — never 0.
"""

from __future__ import annotations

import re

# name -> (allowed discrete values | None, (min, max) for continuous parts)
COMPONENTS: dict[str, tuple[tuple[float, ...] | None, tuple[float, float]]] = {
    "advancement": ((0, 10, 15, 20, 30), (0, 30)),
    "defect_severity": (None, (0, 20)),
    "unblocks": ((0, 5, 10, 15), (0, 15)),
    "human_waiting": ((0, 5, 10, 15), (0, 15)),
    "recency": (None, (-2, 15)),
    "change_impact": ((0, 5, 10), (0, 10)),
    "completion": ((0, 5, 10), (0, 10)),
    "change_size": (None, (0, 12.5)),
    "urgency_bonus": ((0, 30), (0, 30)),
    "blocked_penalty": ((0, -20), (-20, 0)),
}
PART_ORDER = list(COMPONENTS)
STATUSES = {
    "computed": "final script value; override only with stated evidence",
    "provisional": "script default the model must confirm or replace (see hint)",
    "needs_judgment": "value null; the model must assess it (see hint)",
    "not_applicable": "contributes 0 by definition (e.g. issue without a diff)",
}
TIERS = (("P0", 100.0, False), ("P1", 65.0, True), ("P2", 35.0, True))
INSTRUCTION = (
    "score/tier are NOT final until every needs_judgment and provisional part "
    "is filled; a null or missing part is unknown, never 0. Fill only those "
    "parts, then run score.py."
)

READY_LABEL = "ready-for-human"
BLOCKED_LABEL_RE = re.compile(r"\bblocked\b", re.I)
BLOCKED_TEXT_RE = re.compile(r"\b(blocked\s+(by|on)|depends\s+on)\b", re.I)
# Approval / policy / review-bot gates: never "CI red" (SKILL.md §2).
POLICY_CHECK_RE = re.compile(
    r"hygiene|^policy\b|^pr title$|semantic-pull-request|milestone|coderabbit"
    r"|^review$",
    re.I,
)
CHECK_FAILED = {
    "FAILURE",
    "TIMED_OUT",
    "CANCELLED",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
}
STATUS_FAILED = {"FAILURE", "ERROR"}
STATUS_PENDING = {"PENDING", "EXPECTED"}
# Review bots without the [bot] suffix (see project CLAUDE.md).
PSEUDO_BOTS = {"thepastaclaw", "claudius-maginificent", "dcg-claude"}

# One rule per component; item hints only add what differs per item.
LEGEND = {
    "advancement": "PR: verified bot-accepted (`ready-for-human`) 30, non-draft 20, "
    "draft 15; started TODO without a PR 10; unstarted work 0",
    "defect_severity": "20*(likelihood+impact)/2 via the severity skill; no defect 0",
    "unblocks": "distinct open tasks/PRs waiting on it: >=3 15, 2 10, 1 5, none 0",
    "human_waiting": "outstanding human review/reply/change request aged <=2d 15, "
    "<=7d 10, older 5; none 0",
    "recency": 'give {"days": d} since the last change; score.py computes it',
    "change_impact": "10 user-visible fix/feature; 5 internal with concrete "
    "effect; 0 housekeeping/docs/cosmetic",
    "completion": "10 only final verification/approval left and CI green; "
    "5 one bounded step left; 0 several steps/unknown",
    "change_size": 'give {"lines": additions+deletions} of the full diff; no diff 0',
    "urgency_bonus": "30 only with evidence: confirmed production defect, "
    "security issue, or release blocker; else 0",
    "blocked_penalty": "-20 only if a blocker is verified open; else 0",
}


def part(status: str, value=None, basis: str | None = None, hint: str | None = None):
    p: dict = {"value": value, "status": status}
    if basis:
        p["basis"] = basis
    if hint:
        p["hint"] = hint
    return p


def judgment(name: str) -> dict:
    return part("needs_judgment", hint=LEGEND[name])


def recency_points(days: float) -> float:
    return 17 * (2 / 17) ** (max(days, 0.0) / 15) - 2


def change_size_points(lines: int) -> float:
    return 12.5 * 0.8 ** (lines / 1000)


def waiting_points(age_days: float) -> int:
    if age_days <= 2:
        return 15
    return 10 if age_days <= 7 else 5


def unblocks_points(n: int) -> int:
    return min(n, 3) * 5


def tier(score: float) -> str:
    for name, threshold, inclusive in TIERS:
        if score > threshold or (inclusive and score == threshold):
            return name
    return "P3"


def is_bot(login: str | None, typename: str | None = None) -> bool:
    """GraphQL Bot logins lack the [bot] suffix: pass __typename when known."""
    low = (login or "").lower()
    return typename == "Bot" or low in PSEUDO_BOTS or low.endswith("[bot]")


def _recency(days: float | None) -> dict:
    if days is None:
        return part("needs_judgment", hint="last-change time unknown; d = days since")
    days = max(days, 0.0)  # updated after the scan started
    return part(
        "computed",
        recency_points(days),
        f"updated {days:.2f}d ago (17*(2/17)**(d/15)-2)",
    )


# --- CI ----------------------------------------------------------------------


def classify_ci(rollup: dict | None) -> dict:
    """Split a statusCheckRollup into real build/test/lint vs policy gates.

    Latest run per check name wins (re-runs supersede older results).
    ``ciEffective``: green | red | pending | unknown (truncated) | none.
    """
    out = {"ciRedChecks": [], "ciPendingChecks": [], "ciPolicyNotPassing": []}
    if not rollup:
        return {"ciEffective": "none", **out}
    nodes = (rollup.get("contexts") or {}).get("nodes") or []
    if not nodes:
        return {"ciEffective": "pending", **out}
    # Key by (workflow, job): equal job names in other workflows are distinct.
    latest: dict[tuple[str, str], tuple[tuple[bool, str], dict]] = {}
    for node in nodes:
        name = node.get("name") or node.get("context") or "?"
        run = (node.get("checkSuite") or {}).get("workflowRun") or {}
        key = ((run.get("workflow") or {}).get("name") or "", name)
        ts = node.get("startedAt") or node.get("createdAt")
        # Queued re-runs have no start time yet: they are the newest run.
        when = (ts is None, ts or "")
        if key not in latest or when >= latest[key][0]:
            latest[key] = (when, node)
    name_counts: dict[str, int] = {}
    for _, name in latest:
        name_counts[name] = name_counts.get(name, 0) + 1
    shown = {
        key: f"{key[0]} / {key[1]}" if name_counts[key[1]] > 1 and key[0] else key[1]
        for key in latest
    }
    for key in sorted(latest, key=shown.get):
        name, node = shown[key], latest[key][1]
        if node.get("__typename") == "StatusContext":
            state = node.get("state")
            failed, pending = state in STATUS_FAILED, state in STATUS_PENDING
        else:
            state = node.get("conclusion") or node.get("status")
            failed = node.get("conclusion") in CHECK_FAILED
            pending = node.get("status") != "COMPLETED"
        if not (failed or pending):
            continue
        if POLICY_CHECK_RE.search(key[1]):
            out["ciPolicyNotPassing"].append(f"{name} ({state})")
        else:
            out["ciRedChecks" if failed else "ciPendingChecks"].append(name)
    truncated = ((rollup.get("contexts") or {}).get("pageInfo") or {}).get(
        "hasNextPage"
    )
    if out["ciRedChecks"]:
        effective = "red"
    elif out["ciPendingChecks"]:
        effective = "pending"
    else:
        effective = "unknown" if truncated else "green"
    return {"ciEffective": effective, **out}


# --- eligibility ---------------------------------------------------------------


def pr_eligibility(pr: dict, users: list[str]) -> tuple[bool, str]:
    ours = {u.lower() for u in users}
    author = pr.get("author") or "?"
    if pr.get("isDraft") and author.lower() not in ours:
        return False, f"draft by @{author} (only our own drafts count)"
    if pr.get("repo") == "dashpay/platform":
        verdict = (pr.get("ownedChange") or {}).get("verdict")
        if verdict != "significant":
            return False, f"dashpay/platform ownedChange.verdict={verdict}"
        return True, "dashpay/platform: significant owned change"
    return True, "draft by us" if pr.get("isDraft") else "open non-draft PR"


# --- PR parts -----------------------------------------------------------------


def _advancement(pr: dict) -> dict:
    if pr["isDraft"]:
        return part("computed", 15, "draft PR")
    if any(lbl.lower() == READY_LABEL for lbl in pr["labels"]):
        return part(
            "provisional",
            30,
            f"`{READY_LABEL}` label",
            "verify bot acceptance is current (no newer unaddressed bot review); "
            "stale label -> 20",
        )
    return part("computed", 20, "open non-draft PR")


def _unblocks(deps: list[int] | None) -> dict:
    if deps is None:
        return part(
            "needs_judgment", hint="dependents lookup failed; count PRs stacked on it"
        )
    found = f"{len(deps)} {deps}" if deps else "none"
    basis = (
        f"same-repo PRs stacked on it: {found} "
        "(cross-repo/issue/TODO/CI-fix dependents not checked)"
    )
    return part("computed", unblocks_points(len(deps)), basis)


def _human_waiting(pr: dict, users: list[str]) -> dict:
    """Outstanding human request (review / change request) -> age bucket.

    Approximate request ages (updatedAt fallback), team review requests,
    change requests outside the fetched review window, and unanswered
    comments that may be questions only make the value provisional.
    """
    ours = {u.lower() for u in users}
    author = pr.get("author")
    author_human = (
        bool(author)
        and author.lower() not in ours
        and not pr.get("authorIsBot")
        and not is_bot(author)
    )
    relations = pr["relations"]
    definite: list[tuple[float, str]] = []
    hints: list[str] = []
    approx = None
    if pr["reviewPendingFor"] and author_human:
        days = pr["reviewRequestFreshnessDays"]
        if pr["reviewRequestFreshnessIsApprox"] or days is None:
            approx = days if days is not None else 999.0
        else:
            definite.append((days, f"review request by @{author} {days:.1f}d ago"))
    elif "review_requested" in relations and author_human:
        hints.append(
            f"review requested via a team (not a direct request) on @{author}'s PR: "
            "if they wait on us -> by request age"
        )
    cr_age = pr.get("changesRequestedHumanAgeDays")
    cr_rerequested = (
        "authored" in relations and pr["changesRequestedByHuman"] and cr_age is None
    )
    if "authored" in relations and pr["changesRequestedByHuman"] and cr_age is not None:
        definite.append((cr_age, f"human change request {cr_age:.1f}d ago"))
    elif (
        "authored" in relations
        and pr["reviewDecision"] == "CHANGES_REQUESTED"
        and not pr["changesRequestedByHuman"]
        and (pr.get("reviewsTruncated") or not pr.get("changesRequestedBy"))
    ):
        hints.append(
            "reviewDecision=CHANGES_REQUESTED but no change request in the "
            "last-20-reviews window: verify; human -> by age"
        )
    value, basis = 0, "no outstanding human request"
    if cr_rerequested:
        basis = "human change request re-requested: waiting on the reviewer"
    if definite:
        age, basis = min(definite)
        value = waiting_points(age)
    if approx is not None and waiting_points(approx) > value:
        value = waiting_points(approx)
        basis = f"review request by @{author}, approx {approx:.1f}d (updatedAt)"
        hints.append("approx request age: verify the request date in the timeline")
    reply_age = pr.get("lastCommentAgeDays")
    if (
        pr.get("awaitingOurReply")
        and reply_age is not None
        and waiting_points(reply_age) > value
    ):
        hints.append(
            f"last comment by @{pr.get('lastCommentAuthor')} {reply_age:.1f}d ago "
            f"unanswered: if it asks us something -> {waiting_points(reply_age)}"
        )
    status = "provisional" if hints else "computed"
    return part(status, value, basis, "; ".join(hints) or None)


def _change_size(pr: dict) -> dict:
    if pr.get("additions") is None or pr.get("deletions") is None:
        return part(
            "needs_judgment", hint="diff totals unavailable; x = additions+deletions"
        )
    lines = pr["additions"] + pr["deletions"]
    return part(
        "computed",
        change_size_points(lines),
        f"x={lines} (+{pr['additions']}/-{pr['deletions']}, full PR diff)",
    )


def _blocked_pr(pr: dict, body: str) -> dict:
    stacked = pr.get("stackedOnPRs")
    if stacked:
        nums = ", ".join(f"#{n}" for n in stacked)
        return part(
            "computed", -20, f"stacked on open {nums} (base `{pr['baseRefName']}`)"
        )
    if any(BLOCKED_LABEL_RE.search(lbl) for lbl in pr["labels"]) or (
        BLOCKED_TEXT_RE.search(body or "")
    ):
        return part(
            "needs_judgment",
            hint="`blocked` label or blocked-by/depends-on text in the body: "
            "-20 only if the blocker is verified open",
        )
    if stacked is None:
        return part(
            "needs_judgment",
            hint=f"base-PR lookup failed for `{pr['baseRefName']}`; "
            "-20 if stacked on an open PR",
        )
    return part("computed", 0, "no open base PR, no blocked label or dependency text")


def pr_score_parts(pr: dict, users: list[str], body: str = "") -> dict:
    return {
        "advancement": _advancement(pr),
        "defect_severity": judgment("defect_severity"),
        "unblocks": _unblocks(pr["dependentPRs"]),
        "human_waiting": _human_waiting(pr, users),
        "recency": _recency(pr.get("staleDays")),
        "change_impact": judgment("change_impact"),
        "completion": judgment("completion"),
        "change_size": _change_size(pr),
        "urgency_bonus": judgment("urgency_bonus"),
        "blocked_penalty": _blocked_pr(pr, body),
    }


# --- issue parts --------------------------------------------------------------


def issue_score_parts(issue: dict, body: str) -> dict:
    if any(BLOCKED_LABEL_RE.search(lbl) for lbl in issue["labels"]) or (
        BLOCKED_TEXT_RE.search(body or "")
    ):
        blocked = part(
            "needs_judgment",
            hint="blocked label or blocked-by/depends-on text: "
            "-20 only if the blocker is verified open",
        )
    else:
        blocked = part("computed", 0, "no blocked label or blocked-by/depends-on text")
    return {
        "advancement": part("computed", 0, "standalone issue: unstarted work"),
        "defect_severity": judgment("defect_severity"),
        "unblocks": judgment("unblocks"),
        "human_waiting": judgment("human_waiting"),
        "recency": _recency(issue.get("updatedDays")),
        "change_impact": judgment("change_impact"),
        "completion": judgment("completion"),
        "change_size": part("not_applicable", 0, "issue: no diff"),
        "urgency_bonus": judgment("urgency_bonus"),
        "blocked_penalty": blocked,
    }


# --- output documentation -----------------------------------------------------


def scoring_doc() -> dict:
    """Top-level `scoring` block: the per-part status is the authority."""
    return {
        "instruction": INSTRUCTION,
        "formula": "score = " + " + ".join(PART_ORDER),
        "tiers": {"P0": ">100", "P1": "65-100", "P2": "35-<65", "P3": "<35"},
        "statuses": STATUSES,
        "helper": "score.py --scan scan.json --template > judgment.json; fill nulls; "
        "score.py --scan scan.json --judgment judgment.json",
    }
