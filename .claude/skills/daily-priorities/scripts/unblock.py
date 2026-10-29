#!/usr/bin/env python3
"""Comment + reassign gated review-request PRs to their author.

Companion to scan.py for the daily-priorities skill's step 4: every PR
that's review-requested from our users but gated out by the
review-readiness rule (red CI, still a draft, or any unresolved review
thread -- human or bot) gets a concise,
marker-tagged status comment and gets assigned to whoever's responsible for
unblocking it, so GitHub itself reflects who needs to act next. Idempotent:
skips a PR whose most recent comment already carries the marker and already
states the same blockers -- posts fresh only when the state changed or
someone else commented since (so ours is no longer the last thing the
responsible person sees).

Scope is restricted to a repo allowlist (--repos; defaults to
dashpay/dash-evo-tool and dashpay/tenderdash) -- everything else is left
alone regardless of gate state.

True automation accounts (dependabot) are skipped entirely -- there's no
human to hand those back to. Pseudo-bot accounts that are actually our own
tooling (Claudius-Maginificent, thepastaclaw, DCG-Claude) are NOT skipped: their gated
PRs get assigned to --reassign-pseudo-bots-to (default lklimek) instead of
the bot account, since the bot doesn't monitor GitHub notifications.

Reads a scan.py JSON document (--scan-file, or stdin) and writes to GitHub
unless --dry-run is passed. Prints one JSON document to stdout summarizing
what happened per PR.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys

import scoring

MARKER = "<!-- daily-priorities:blocked-status -->"
TRUE_BOT_AUTHORS = {"dependabot", "dependabot[bot]"}
DEFAULT_REPOS = {"dashpay/dash-evo-tool", "dashpay/tenderdash"}
OUR_LOGINS = {"lklimek", "claudius-maginificent"}
# Gate facts (CI, draft, threads) go stale fast; comments are public.
MAX_SCAN_AGE_MIN = 30
BLOCKER_LINE_RE = re.compile(r"^\d+\. (.*)$", re.M)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def run_gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def is_true_bot_author(login: str) -> bool:
    login = (login or "").lower()
    return login in TRUE_BOT_AUTHORS or login.endswith("[bot]")


def is_pseudo_bot_author(login: str) -> bool:
    return (login or "").lower() in scoring.PSEUDO_BOTS


def is_gated(pr: dict) -> bool:
    if "review_requested" not in pr.get("relations", []):
        return False
    # Real build/test/lint failures only; policy gates and running CI are not.
    ci_bad = pr.get("ciEffective") == "red"
    draft_bad = bool(pr.get("isDraft"))
    thread_bad = (pr.get("unresolvedThreadsTotal") or 0) > 0
    return ci_bad or draft_bad or thread_bad


def build_blocker_body(pr: dict) -> tuple[str, list[str]]:
    lines = []
    if pr.get("ciEffective") == "red":
        job_str = ", ".join(pr.get("ciRedChecks") or []) or "see PR checks"
        lines.append(f"CI failing ({job_str}).")
    if pr.get("isDraft"):
        lines.append("Still a draft.")
    unresolved_total = pr.get("unresolvedThreadsTotal") or 0
    bot_flagged = pr.get("unresolvedBotFlagged") or 0
    if unresolved_total > 0:
        if bot_flagged > 0:
            # "(bot-emoji)" is an internal detection-signal marker (see
            # scan.py), not a real severity -- don't leak it into PR-facing
            # text.
            severities = [
                s
                for s in (pr.get("unresolvedBotFlaggedSeverities") or [])
                if s != "(bot-emoji)"
            ]
            sev = "/".join(severities) or "flagged"
            lines.append(
                f"{unresolved_total} unresolved review thread(s) "
                f"({bot_flagged} bot-flagged [{sev}])."
            )
        else:
            lines.append(f"{unresolved_total} unresolved review thread(s).")
    numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    body = (
        f"Not ready for review yet:\n{numbered}\n\n"
        f"Please address and re-request review when ready.\n\n{MARKER}"
    )
    return body, lines


def get_pr_state(repo: str, number: int) -> dict:
    out = run_gh(
        [
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "comments,assignees,author",
        ]
    )
    return json.loads(out)


def last_comment_is_current(state: dict, blocker_lines: list[str]) -> bool:
    comments = state.get("comments") or []
    if not comments:
        return False
    last = comments[-1]
    author = ((last.get("author") or {}).get("login") or "").lower()
    if author not in OUR_LOGINS:
        return False
    body = last.get("body") or ""
    if MARKER not in body:
        return False
    return BLOCKER_LINE_RE.findall(body) == blocker_lines


def process_pr(
    pr: dict, dry_run: bool, repos: set[str], reassign_pseudo_bots_to: str
) -> dict:
    repo, number = pr["repo"], pr["number"]
    author = pr.get("author") or ""
    label = f"{repo}#{number}"

    if repo not in repos:
        return {"pr": label, "author": author, "action": "skip-out-of-scope"}

    # GraphQL Bot logins lack the [bot] suffix: trust the scanner's typename.
    if is_true_bot_author(author) or (
        pr.get("authorIsBot") and not is_pseudo_bot_author(author)
    ):
        return {"pr": label, "author": author, "action": "skip-bot"}

    # A pseudo-bot (our own AI agent) doesn't watch GitHub notifications --
    # hand the PR to a human instead of the bot account, but keep `author`
    # as the true author for reporting.
    assignee = reassign_pseudo_bots_to if is_pseudo_bot_author(author) else author

    body, lines = build_blocker_body(pr)

    try:
        state = get_pr_state(repo, number)
    except RuntimeError as exc:
        return {"pr": label, "author": author, "action": "error", "error": str(exc)}

    need_comment = not last_comment_is_current(state, lines)
    assignee_logins = {
        (a.get("login") or "").lower() for a in state.get("assignees") or []
    }
    need_assign = assignee.lower() not in assignee_logins and bool(assignee)

    actions = []
    errors = []

    if need_comment:
        try:
            if not dry_run:
                run_gh(["pr", "comment", str(number), "--repo", repo, "--body", body])
            actions.append("comment")
        except RuntimeError as exc:
            errors.append(f"comment: {exc}")

    if need_assign:
        if dry_run:
            actions.append("assign")
        else:
            try:
                run_gh(
                    [
                        "pr",
                        "edit",
                        str(number),
                        "--repo",
                        repo,
                        "--add-assignee",
                        assignee,
                    ]
                )
                actions.append("assign")
            except RuntimeError as exc:
                # `gh pr edit` can report a permission error on some
                # secondary internal call even though the assignee mutation
                # itself already went through -- verify actual state before
                # believing the exit code over GitHub's own record.
                try:
                    recheck = get_pr_state(repo, number)
                    recheck_logins = {
                        (a.get("login") or "").lower()
                        for a in recheck.get("assignees") or []
                    }
                    if assignee.lower() in recheck_logins:
                        actions.append("assign")
                    else:
                        errors.append(f"assign: {exc}")
                except RuntimeError:
                    errors.append(f"assign: {exc}")

    return {
        "pr": label,
        "author": author,
        "assignee": assignee,
        "action": "+".join(actions) if actions else "already-current",
        "errors": errors,
        "blockers": lines,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--scan-file", help="path to a scan.py JSON dump; defaults to stdin"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="compute actions, write nothing"
    )
    ap.add_argument(
        "--allow-stale",
        action="store_true",
        help=f"accept a scan older than {MAX_SCAN_AGE_MIN} min",
    )
    ap.add_argument(
        "--repos",
        default=",".join(sorted(DEFAULT_REPOS)),
        help="comma-separated owner/repo allowlist -- PRs in any other repo are left alone",
    )
    ap.add_argument(
        "--reassign-pseudo-bots-to",
        default="lklimek",
        help="who a Claudius-Maginificent/thepastaclaw/DCG-Claude-authored gated PR gets assigned to",
    )
    args = ap.parse_args()

    repos = {r.strip() for r in args.repos.split(",") if r.strip()}
    raw = open(args.scan_file).read() if args.scan_file else sys.stdin.read()
    data = json.loads(raw)
    generated = dt.datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
    age_min = (now_utc() - generated).total_seconds() / 60
    if age_min > MAX_SCAN_AGE_MIN and not args.allow_stale:
        print(
            f"error: scan is stale ({age_min:.0f} min > {MAX_SCAN_AGE_MIN}); "
            "re-run scan.py and repeat the preview and approval",
            file=sys.stderr,
        )
        return 1

    results = []
    for pr in data["prs"]:
        if not is_gated(pr):
            continue
        label = f"{pr['repo']}#{pr['number']}"
        try:
            results.append(
                process_pr(pr, args.dry_run, repos, args.reassign_pseudo_bots_to)
            )
        except Exception as exc:  # noqa: BLE001 - one PR's crash must not kill the batch
            results.append(
                {
                    "pr": label,
                    "author": pr.get("author"),
                    "action": "error",
                    "errors": [str(exc)],
                }
            )

    print(json.dumps({"dry_run": args.dry_run, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
