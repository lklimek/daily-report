#!/usr/bin/env python3
"""Final priority scores for daily-priorities: scan scoreParts + model judgment.

    score.py --scan scan.json --template > judgment.json   # open parts as null
    score.py --scan scan.json --judgment judgment.json      # merged ranking

Judgment JSON, keyed by item id (TODO / extra rows need "kind"):
    {"DET-1094": {"parts": {"completion": 5,
                            "unblocks": {"value": 10, "basis": "TODO-ab12 waits"},
                            "defect_severity": {"value": 8, "provisional": true,
                                                "basis": "impact unverified"},
                            "recency": {"days": 3}, "change_size": {"lines": 420}},
                  "eligible"?: bool, "eligibilityReason"?: str,
                  "alsoIds"?: [str], "kind"/"title"/"url"?: str},
     "_resolvedErrors"?: {"<scan error prefix>": "basis"}}

Overriding a computed part or flipping eligibility needs a basis/reason.
"provisional": true records a best estimate that stays non-final. Items in
another row's "alsoIds" are merged into it (excluded). Exit 0 when every
row is final and every scan error is resolved, 2 otherwise (output still
printed), 1 on invalid input.
"""

from __future__ import annotations

import argparse
import json
import sys

import scoring

ITEM_KEYS = {"parts", "facts", "hints", "eligible", "eligibilityReason", "alsoIds"}
EXTRA_KEYS = {"kind", "title", "url"}
PART_KEYS = {"value", "basis", "provisional", "days", "lines"}
RESERVED = {"_legend", "_resolvedErrors"}
# Template facts per item: enough to judge without reading the scan.
FACT_KEYS = (
    "title relations author authorIsBot isDraft labels conventionalType "
    "breakingChange targetsReleaseBranch baseRefName linkedIssues ciEffective "
    "ciRedChecks ciPendingChecks mergeable reviewDecision changesRequestedBy "
    "awaitingOurReply lastCommentAuthor unresolvedThreadsTotal unresolvedBotFlaggedSeverities "
    "dependentPRs stackedOnPRs staleDays isNew updatedDays authorAssociation "
    "assignees commentsCount versionsMentioned latestRelease bodyExcerpt ownedChange"
).split()
HOW = (
    'Replace each null: a number or {"value": n, "basis": "..."}. Unsure -> '
    '{"value": best estimate, "provisional": true, "basis": "what is unknown"}, '
    'never a bare 0. recency: {"days": d}; change_size: {"lines": x} or 0. '
    'Duplicates: list them in the surviving row\'s "alsoIds". Scan errors: '
    'acknowledge in top-level "_resolvedErrors": {"<error prefix>": "basis"}. '
    "Judge from facts; where a hint says verify or depends on content not "
    "shown, check live (gh pr view / gh pr checks) or mark it provisional. "
    "facts omit empty/false/0 values."
)


class JudgmentError(ValueError):
    pass


def _check_value(item_id: str, name: str, value) -> None:
    allowed, (lo, hi) = scoring.COMPONENTS[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JudgmentError(f"{item_id}: {name} must be a number, got {value!r}")
    if allowed is not None and value not in allowed:
        raise JudgmentError(f"{item_id}: {name}={value} not in {list(allowed)}")
    if not lo <= value <= hi:
        raise JudgmentError(f"{item_id}: {name}={value} outside [{lo}, {hi}]")


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0


def _derived_value(item_id: str, name: str, entry: dict, value):
    """recency / change_size come from days / lines: no hand arithmetic."""
    days, lines = entry.get("days"), entry.get("lines")
    if days is not None and name != "recency":
        raise JudgmentError(f"{item_id}: days only applies to recency, not {name}")
    if lines is not None and name != "change_size":
        raise JudgmentError(f"{item_id}: lines only applies to change_size, not {name}")
    if name == "recency" and value is not None:
        raise JudgmentError(f'{item_id}: give recency as {{"days": d}}, not a value')
    if days is not None:
        if not _is_number(days):
            raise JudgmentError(f"{item_id}: recency days must be a number >= 0")
        return scoring.recency_points(days)
    if lines is not None:
        if not _is_number(lines) or value is not None:
            raise JudgmentError(
                f'{item_id}: change_size takes {{"lines": x >= 0}} or 0 alone'
            )
        return scoring.change_size_points(lines)
    if name == "change_size" and value not in (None, 0):
        raise JudgmentError(f'{item_id}: give change_size as {{"lines": x}} or 0')
    return value


def _judged_parts(item_id: str, raw: dict) -> dict[str, dict]:
    """{name: {"value", "basis", "provisional"}} for non-null judgment entries."""
    out = {}
    for name, entry in (raw or {}).items():
        if name not in scoring.COMPONENTS:
            raise JudgmentError(f"{item_id}: unknown part {name!r}")
        if not isinstance(entry, dict):
            entry = {"value": entry}
        unknown = set(entry) - PART_KEYS
        if unknown:
            raise JudgmentError(
                f"{item_id}: {name} has unknown keys {sorted(unknown)}; "
                "mark a guess with provisional: true"
            )
        basis, provisional = entry.get("basis"), bool(entry.get("provisional"))
        value = _derived_value(item_id, name, entry, entry.get("value"))
        if value is None:
            continue
        _check_value(item_id, name, value)
        if provisional and not basis:
            raise JudgmentError(f"{item_id}: provisional {name} needs a basis")
        out[name] = {"value": value, "basis": basis, "provisional": provisional}
    return out


def _merge_part(item_id: str, name: str, scanned: dict | None, judged: dict | None):
    if judged is None:
        return dict(scanned) if scanned else scoring.part("needs_judgment")
    scanned = scanned or {}
    fixed = scanned.get("status") in ("computed", "not_applicable")
    same = judged["value"] == scanned.get("value")
    if fixed and same and not judged["provisional"]:
        return dict(scanned)
    if fixed and not same and not judged["basis"]:
        raise JudgmentError(
            f"{item_id}: overriding computed {name} needs a basis (evidence)"
        )
    merged = {"value": judged["value"]}
    if judged["provisional"]:
        merged["status"] = "provisional"
    elif fixed:
        merged["status"] = "overridden"
    else:
        merged["status"] = "judged"
    if scanned.get("value") is not None and not same:
        merged["scannerValue"] = scanned["value"]
    # The scanner's basis only supports the scanner's own value.
    basis = judged["basis"] or (scanned.get("basis") if same else None)
    if basis:
        merged["basis"] = basis
    return merged


def _fmt(value: float) -> str:
    return f"{round(value, 2):g}"


def _breakdown(parts: dict, total: float) -> str:
    values = [parts[name]["value"] for name in scoring.PART_ORDER]
    text = _fmt(values[0])
    for v in values[1:]:
        text += f"+{_fmt(v)}" if v >= 0 else _fmt(v)
    return f"{text}={_fmt(total)}"


def _id_key(item_id: str):
    prefix, _, rest = item_id.rpartition("-")
    return (prefix, 0, int(rest), "") if rest.isdigit() else (prefix, 1, 0, rest)


def _sort_key(row: dict):
    p = row["parts"]
    return (
        -row["score"],
        -p["advancement"]["value"],
        -p["defect_severity"]["value"],
        -p["unblocks"]["value"],
        _id_key(row["id"]),
    )


def _scan_items(scan: dict) -> dict[str, dict]:
    return {x["id"]: x for x in scan.get("prs", []) + scan.get("issues", [])}


def _absorbed(judgment: dict, items: dict) -> dict[str, str]:
    """{merged id: surviving row id} from every row's alsoIds."""
    absorbed: dict[str, str] = {}
    for owner, entry in judgment.items():
        for target in entry.get("alsoIds") or []:
            if target == owner or target in absorbed:
                raise JudgmentError(
                    f"{owner}: alsoIds {target!r} is itself or already merged"
                )
            if not (
                target in items or target in judgment or target.startswith("TODO-")
            ):
                raise JudgmentError(f"{owner}: alsoIds {target!r} is not a known id")
            absorbed[target] = owner
    for target, owner in absorbed.items():
        if target in judgment and judgment[target].get("alsoIds"):
            raise JudgmentError(f"{target}: merged into {owner} but has alsoIds")
        parts = (judgment.get(target) or {}).get("parts") or {}
        if any(v is not None for v in parts.values()):
            raise JudgmentError(
                f"{target}: merged into {owner}; score it there, null its parts"
            )
    return absorbed


def _resolve_errors(scan: dict, acks) -> tuple[list[str], list[dict]]:
    errors = list(scan.get("errors") or [])
    if not isinstance(acks, dict):
        raise JudgmentError('_resolvedErrors must be {"<error prefix>": "basis"}')
    resolved = []
    for prefix, basis in acks.items():
        hits = [e for e in errors if prefix and e.startswith(prefix)]
        if len(hits) != 1 or not isinstance(basis, str) or not basis.strip():
            raise JudgmentError(
                f"_resolvedErrors {prefix!r}: needs a basis and must match "
                f"exactly one scan error (matched {len(hits)})"
            )
        resolved.append({"error": hits[0], "basis": basis})
    done = {r["error"] for r in resolved}
    return [e for e in errors if e not in done], resolved


def merge(scan: dict, judgment: dict) -> dict:
    items = _scan_items(scan)
    if not isinstance(judgment, dict):
        raise JudgmentError("top level must be an object keyed by item id")
    bad = [k for k in judgment if k.startswith("_") and k not in RESERVED]
    if bad:
        raise JudgmentError(f"unknown top-level keys {bad}")
    scan_errors, resolved_errors = _resolve_errors(
        scan, judgment.get("_resolvedErrors", {})
    )
    judgment = {k: v for k, v in judgment.items() if k not in RESERVED}
    for item_id, entry in judgment.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("parts", {}), dict):
            raise JudgmentError(f'{item_id}: expected {{"parts": {{...}}, ...}}')
        unknown = set(entry) - ITEM_KEYS - EXTRA_KEYS
        if unknown:
            raise JudgmentError(f"{item_id}: unknown keys {sorted(unknown)}")
        if item_id not in items and not entry.get("kind"):
            raise JudgmentError(
                f'{item_id}: not in scan; set "kind" for TODO/extra rows'
            )
    absorbed = _absorbed(judgment, items)
    ranked, unscored, excluded = [], [], []
    ids = list(items) + [i for i in judgment if i not in items]
    for item_id in ids:
        item = items.get(item_id, {})
        entry = judgment.get(item_id, {})
        row = {
            "id": item_id,
            "kind": item.get("kind") or entry.get("kind"),
            "title": item.get("title") or entry.get("title"),
            "url": item.get("url") or entry.get("url"),
        }
        if entry.get("alsoIds"):
            row["alsoIds"] = entry["alsoIds"]
        if item_id in absorbed:
            excluded.append({**row, "reason": f"merged into {absorbed[item_id]}"})
            continue
        scanned_eligible = item.get("eligible", True)
        eligible = bool(entry.get("eligible", scanned_eligible))
        reason = entry.get("eligibilityReason")
        if eligible != scanned_eligible and not reason:
            raise JudgmentError(f"{item_id}: flipping eligible needs eligibilityReason")
        if not eligible:
            excluded.append({**row, "reason": reason or item.get("eligibilityReason")})
            continue
        judged = _judged_parts(item_id, entry.get("parts"))
        scanned = item.get("scoreParts") or {}
        parts = {
            name: _merge_part(item_id, name, scanned.get(name), judged.get(name))
            for name in scoring.PART_ORDER
        }
        missing = [n for n, p in parts.items() if p["value"] is None]
        unconfirmed = [n for n, p in parts.items() if p["status"] == "provisional"]
        if missing:
            unscored.append({**row, "missing": missing, "unconfirmed": unconfirmed})
            continue
        total = sum(p["value"] for p in parts.values())
        row.update(
            score=total,
            tier=scoring.tier(total),
            final=not unconfirmed,
            unconfirmed=unconfirmed,
            breakdown=_breakdown(parts, total),
            parts=parts,
        )
        ranked.append(row)
    ranked.sort(key=_sort_key)
    unscored.sort(key=lambda r: _id_key(r["id"]))
    excluded.sort(key=lambda r: _id_key(r["id"]))
    return {
        "generated_at": scan.get("generated_at"),
        "complete": not scan_errors
        and not unscored
        and all(r["final"] for r in ranked),
        "scanErrors": scan_errors,
        "resolvedErrors": resolved_errors,
        "ranked": ranked,
        "unscored": unscored,
        "excluded": excluded,
    }


def _compact(key: str, value):
    if isinstance(value, float):
        return round(value, 1)
    if key == "linkedIssues":
        return [f"#{i['number']} {i['title']}" for i in value]
    if key == "ownedChange":
        return {k: value.get(k) for k in ("verdict", "topFiles")}
    return value


def _facts(item: dict) -> dict:
    return {
        k: _compact(k, item[k])
        for k in FACT_KEYS
        if item.get(k) not in (None, [], "", False, 0, {})
    }


def _item_hints(item: dict) -> dict:
    hints = {}
    for name, p in (item.get("scoreParts") or {}).items():
        hint = p.get("hint")
        extra = None if hint == scoring.LEGEND[name] else hint
        if p["status"] == "provisional":
            hints[name] = f"default {p['value']}" + (f": {extra}" if extra else "")
        elif p["status"] == "needs_judgment" and extra:
            hints[name] = extra
    return hints


def template(scan: dict) -> dict:
    out: dict = {
        "_legend": {
            "how": HOW,
            "parts": scoring.LEGEND,
            "scanErrors": list(scan.get("errors") or []),
        }
    }
    for item_id, item in _scan_items(scan).items():
        if not item.get("eligible", True):
            continue
        open_parts = [
            name
            for name, p in (item.get("scoreParts") or {}).items()
            if p["status"] in ("needs_judgment", "provisional")
        ]
        out[item_id] = {
            "facts": _facts(item),
            "parts": dict.fromkeys(open_parts),
            "hints": _item_hints(item),
        }
    return out


def _dump_lines(obj: dict) -> str:
    """JSON object with one compact top-level entry per line."""
    rows = [
        f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in obj.items()
    ]
    return "{\n" + ",\n".join(rows) + "\n}"


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--scan", required=True, help="scan.py JSON output")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--judgment", help="model judgment JSON (see module doc)")
    mode.add_argument("--template", action="store_true", help="print judgment skeleton")
    args = parser.parse_args(argv)
    try:
        scan = _load(args.scan)
        if args.template:
            print(_dump_lines(template(scan)))
            return 0
        result = merge(scan, _load(args.judgment))
    except (OSError, ValueError) as exc:  # JudgmentError, JSONDecodeError
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if result["complete"]:
        return 0
    pending = [r["id"] for r in result["unscored"]] + [
        r["id"] for r in result["ranked"] if not r["final"]
    ]
    if pending:
        print(
            f"warning: {len(pending)} row(s) NOT final (missing or unconfirmed "
            "parts): " + ", ".join(pending),
            file=sys.stderr,
        )
    for err in result["scanErrors"]:
        print(f"warning: unresolved scan error (data missing): {err}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
