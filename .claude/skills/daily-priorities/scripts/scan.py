#!/usr/bin/env python3
"""Gather live GitHub PR/issue facts for the daily-priorities skill.

Pure data-gathering: runs `gh` (search + GraphQL), computes the mechanical
priority-score parts per item (`scoreParts`, see scoring.py), and prints one
JSON document to stdout. Judgment parts stay null for the calling agent;
score.py merges them into the final ranking. See SKILL.md.

Nothing here is stored anywhere: every run re-fetches live from the GitHub
API, matching the "PR/issue status is never cached" rule.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import re
import subprocess
import sys
import time

import scoring

DEFAULT_USERS = ["lklimek", "Claudius-Maginificent"]
DEFAULT_ISSUE_REPOS = ["dashpay/dash-evo-tool", "dashpay/tenderdash"]
DEFAULT_LOOKBACK_DAYS = 7.0
# Owners whose repos are enumerated via GraphQL for users that GitHub search
# refuses ("cannot be searched", e.g. Claudius-Maginificent).
DEFAULT_ENUMERATE_OWNERS = ["dashpay", "lklimek"]
UNSEARCHABLE_MARK = "cannot be searched"

# Claudius/thepastaclaw's own severity vocabulary (report-format/severity
# skills): plain words. Best-effort only — other bots (e.g. coderabbitai) use
# their own emoji taxonomy; 🔴/🟠 are treated as a weak secondary signal for
# "critical/major"-ish. See SKILL.md's documented limitation.
SEVERITY_WORD_RE = re.compile(
    r"\b(BLOCKER|BLOCKING|CRITICAL|HIGH|MEDIUM)\b", re.IGNORECASE
)
SEVERITY_EMOJI_HIGH = ("\U0001f534", "\U0001f7e0")  # red, orange circle

# Stable short IDs ("DET-1234") so items can be discussed across runs.
# Unlisted repos get the initials of their name; colliding aliases fall back
# to the full repo name.
REPO_ALIASES = {
    "dashpay/dash-evo-tool": "DET",
    "dashpay/tenderdash": "TD",
    "dashpay/platform": "PLAT",
    "dashpay/rust-dashcore": "RDC",
    "dashpay/dash": "CORE",
    "dashpay/stale_prs_are_bad": "SPB",
}

# Priority signals (all best-effort heuristics, judged on top by the agent).
CONVENTIONAL_RE = re.compile(r"^\s*(\w+)(?:\([^)]*\))?(!)?\s*:")
BREAKING_LABEL_RE = re.compile(r"breaking", re.I)
# Branches that ship to users: a fix aimed here is a hotfix for a release.
# `*-dev` branches and `main` are development lines.
RELEASE_BRANCH_RE = re.compile(r"^(master|release[/-].*|hotfix[/-].*|v?\d+(\.\d+)+)$")
# Long-lived lines: PRs targeting them are not stacked on a PR from them.
LONG_LIVED_BRANCH_RE = re.compile(
    r"^(main|master|develop|release[/-].*|hotfix[/-].*|v?\d+(\.\d+)+|.*-dev)$"
)
VERSION_RE = re.compile(r"\bv?\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?\b")

PR_DETAIL_QUERY = """
query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      title
      url
      state
      isDraft
      isCrossRepository
      body
      additions
      deletions
      createdAt
      updatedAt
      mergeable
      mergeStateStatus
      reviewDecision
      baseRefName
      headRefName
      baseRepository { defaultBranchRef { name } }
      author { __typename login }
      labels(first:20) { nodes { name } }
      participants { totalCount }
      closingIssuesReferences(first:5) {
        nodes { number title labels(first:10) { nodes { name } } }
      }
      assignees(first:10) { nodes { login } }
      reviewRequests(first:20) {
        nodes {
          requestedReviewer {
            __typename
            ... on User { login }
            ... on Bot { login }
            ... on Team { name }
          }
        }
      }
      reviews(last:20) {
        totalCount
        nodes { author { __typename login } state submittedAt }
      }
      comments(last:30) { nodes { author { __typename login } body createdAt } }
      reviewThreads(first:100) {
        nodes {
          isResolved
          isOutdated
          comments(first:5) {
            nodes { author { __typename login } body createdAt }
          }
        }
      }
      timelineItems(last:50, itemTypes:[REVIEW_REQUESTED_EVENT]) {
        nodes {
          ... on ReviewRequestedEvent {
            createdAt
            requestedReviewer {
              __typename
              ... on User { login }
              ... on Bot { login }
            }
          }
        }
      }
      commits(last:1) {
        nodes {
          commit {
            statusCheckRollup {
              state
              contexts(first:100) {
                pageInfo { hasNextPage }
                nodes {
                  __typename
                  ... on CheckRun {
                    name status conclusion startedAt
                    checkSuite { workflowRun { workflow { name } } }
                  }
                  ... on StatusContext { context state createdAt }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

# Repos with an ownership policy in dashpay/stale_prs_are_bad. For these, a PR
# only counts when it makes a substantive change inside an area where one of
# our users is an owner or reviewer -- see owned_change().
POLICY_REPO = "dashpay/stale_prs_are_bad"
POLICY_FILES = {"dashpay/platform": "policies/platform.json"}
# Used only when the policy cannot be fetched.
FALLBACK_OWNED_PATHS = {
    "dashpay/platform": {
        "wallet-storage": ["packages/rs-platform-wallet-storage/"],
        "rust-sdk": ["packages/rs-sdk/"],
        "rust-sdk-ffi": ["packages/rs-sdk-ffi/"],
        "rust-dapi": ["packages/rs-dapi/"],
    }
}

# Files that never count as a substantive change.
NON_CODE_SUFFIXES = (".md", ".txt", ".lock", ".snap", ".svg", ".png")
HASH_COMMENT_SUFFIXES = (".py", ".sh", ".toml", ".yml", ".yaml")
# Heuristic cut-offs for owned_change()'s verdict (changed code lines).
SIGNIFICANT_DISTINCT_LINES = 12
SIGNIFICANT_MAX_FILE_LINES = 8


def run_gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def run_gh_json(args: list[str]):
    return json.loads(run_gh(args))


def gh_message(exc: Exception) -> str:
    """gh failures embed the whole command/query; keep only gh's message."""
    return str(exc).rsplit(" failed: ", 1)[-1]


# Fetches that silently shrank or changed the result. main() reports them in
# `errors`. Appended from the main thread only (never from pool workers).
DEGRADED: list[str] = []


def degraded(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)
    DEGRADED.append(msg)


# GitHub's search API has a secondary (anti-abuse) rate limit that trips on
# bursts of back-to-back searches even when the 30/min quota is untouched.
SEARCH_MIN_INTERVAL_S = 3.0
SEARCH_RETRY_WAITS_S = (60.0, 120.0)
_last_search_at = 0.0


def run_gh_search_json(args: list[str]):
    """Run a `gh search ...` call, spaced out and retried on rate limits."""
    global _last_search_at
    for attempt in range(len(SEARCH_RETRY_WAITS_S) + 1):
        wait = _last_search_at + SEARCH_MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return run_gh_json(args)
        except RuntimeError as exc:
            if "rate limit" not in str(exc) or attempt == len(SEARCH_RETRY_WAITS_S):
                raise
            delay = SEARCH_RETRY_WAITS_S[attempt]
            print(
                f"warning: search rate-limited, retrying in {delay:.0f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
        finally:
            _last_search_at = time.monotonic()


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_iso(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def age_days(ts: str | None, ref: dt.datetime) -> float | None:
    parsed = parse_iso(ts)
    if parsed is None:
        return None
    return (ref - parsed).total_seconds() / 86400.0


def fetch_owned_paths(repo: str, users: list[str]) -> dict[str, list[str]]:
    """{area id: [path prefixes]} where one of `users` is an owner or reviewer."""
    try:
        policy = json.loads(
            run_gh(
                [
                    "api",
                    f"repos/{POLICY_REPO}/contents/{POLICY_FILES[repo]}",
                    "-H",
                    "Accept: application/vnd.github.raw",
                ]
            )
        )
    except (RuntimeError, ValueError) as exc:
        degraded(
            f"ownership policy fetch failed ({repo}), using fallback paths: "
            f"{gh_message(exc)}"
        )
        return FALLBACK_OWNED_PATHS.get(repo, {})
    ours = {u.lower() for u in users}
    owned: dict[str, list[str]] = {}
    for area in policy.get("areas", []):
        people = area.get("owners", []) + area.get("reviewers", [])
        if ours & {p.lower() for p in people}:
            owned[area["id"]] = area.get("paths", [])
    return owned


def is_code_line(line: str, path: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if path.endswith(HASH_COMMENT_SUFFIXES):
        return not text.startswith("#")
    return not (text.startswith(("//", "/*", "*/", "* ")) or text == "*")


def owned_change(repo: str, number: int, owned: dict[str, list[str]]) -> dict:
    """How much real code a PR changes inside our owned areas.

    Counts added/removed lines that are neither blank nor comment-only, in
    files under an owned path. `verdict` is a first-pass heuristic:
    none = nothing but comments/docs, minor = small or thinly spread edits
    (call-site adjustments), significant = a concentrated change.
    """
    files = json.loads(
        run_gh(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repo}/pulls/{number}/files?per_page=100",
            ]
        )
    )
    areas: set[str] = set()
    per_file: list[tuple[int, str]] = []
    distinct: set[str] = set()
    for f in (f for page in files for f in page):
        path = f["filename"]
        area = next(
            (a for a, paths in owned.items() if path.startswith(tuple(paths))), None
        )
        if area is None:
            continue
        areas.add(area)
        if path.endswith(NON_CODE_SUFFIXES):
            continue
        patch = f.get("patch")
        if patch is None:
            # Too large or binary: no patch to inspect, trust the raw count.
            count = f.get("additions", 0) + f.get("deletions", 0)
            distinct.update(f"{path}:{i}" for i in range(count))
        else:
            lines = [
                ln[1:].strip()
                for ln in patch.splitlines()
                if ln[:1] in "+-" and is_code_line(ln[1:], path)
            ]
            count = len(lines)
            distinct.update(lines)
        if count:
            per_file.append((count, path))
    per_file.sort(reverse=True)
    code_lines = sum(c for c, _ in per_file)
    max_file = per_file[0][0] if per_file else 0
    if code_lines == 0:
        verdict = "none"
    elif (
        len(distinct) >= SIGNIFICANT_DISTINCT_LINES
        and max_file >= SIGNIFICANT_MAX_FILE_LINES
    ):
        verdict = "significant"
    else:
        verdict = "minor"
    return {
        "verdict": verdict,
        "areas": sorted(areas),
        "codeFiles": len(per_file),
        "codeLines": code_lines,
        "distinctCodeLines": len(distinct),
        "topFiles": [f"{path} ({c})" for c, path in per_file[:3]],
    }


def is_bot_author(typename: str | None, login: str | None) -> bool:
    return scoring.is_bot(login, typename)


def is_long_lived(branch: str | None, default: str | None) -> bool:
    return branch == default or bool(LONG_LIVED_BRANCH_RE.match(branch or ""))


def repo_aliases(repos: set[str]) -> dict[str, str]:
    """{repo: short alias}, collision-free within this run."""
    proposed = {}
    for repo in repos:
        alias = REPO_ALIASES.get(repo)
        if alias is None:
            parts = [x for x in re.split(r"[-_.]", repo.split("/", 1)[1]) if x]
            alias = (
                "".join(x[0] for x in parts) if len(parts) > 1 else parts[0][:4]
            ).upper()
        proposed[repo] = alias
    taken: dict[str, int] = {}
    for alias in proposed.values():
        taken[alias] = taken.get(alias, 0) + 1
    return {
        repo: alias
        if taken[alias] == 1
        else re.sub(r"[^A-Z0-9]+", "-", repo.split("/", 1)[1].upper())
        for repo, alias in proposed.items()
    }


def conventional_type(title: str) -> tuple[str | None, bool]:
    """(type, breaking) parsed from a conventional-commit title."""
    m = CONVENTIONAL_RE.match(title or "")
    if not m:
        return None, False
    return m.group(1).lower(), bool(m.group(2))


def fetch_dependents(repo: str, head: str | None) -> list[int] | None:
    """Open PRs stacked on this PR's head branch (they wait for it to merge)."""
    if not head:
        return []
    try:
        prs = run_gh_json(
            [
                "pr",
                "list",
                "--repo",
                repo,
                "--base",
                head,
                "--state",
                "open",
                "--json",
                "number",
                "-L",
                "20",
            ]
        )
    except (RuntimeError, ValueError) as exc:
        print(
            f"warning: dependents lookup failed ({repo}:{head}): {exc}", file=sys.stderr
        )
        return None
    return sorted(pr["number"] for pr in prs)


def fetch_stacked_on(
    repo: str, base: str | None, default: str | None
) -> list[int] | None:
    """Open same-repo PRs whose head is this PR's base (it waits for them)."""
    if not base or is_long_lived(base, default):
        return []
    try:
        prs = run_gh_json(
            [
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                base,
                "--state",
                "open",
                "--json",
                "number,isCrossRepository",
            ]
        )
    except (RuntimeError, ValueError) as exc:
        print(f"warning: base-PR lookup failed ({repo}:{base}): {exc}", file=sys.stderr)
        return None
    return sorted(pr["number"] for pr in prs if not pr.get("isCrossRepository"))


def fetch_latest_release(repo: str) -> str | None:
    try:
        return (
            run_gh(
                ["api", f"repos/{repo}/releases/latest", "--jq", ".tag_name"]
            ).strip()
            or None
        )
    except RuntimeError as exc:
        print(f"warning: latest release lookup failed ({repo}): {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Candidate discovery
# ---------------------------------------------------------------------------


def discover_pr_candidates(
    users: list[str], unsearchable: set[str], extra: list[tuple[dict, str, str]]
) -> dict[str, dict]:
    """Returns {url: {"repo":..., "number":..., "title":..., "relations": {user: set(relation)}}}.

    Users GitHub search refuses are added to `unsearchable`; `extra` holds
    (pr, user, relation) hits found by repo enumeration instead.
    """
    candidates: dict[str, dict] = {}

    def add(pr, user, relation):
        url = pr["url"]
        entry = candidates.setdefault(
            url,
            {
                "repo": pr["repository"]["nameWithOwner"],
                "number": pr["number"],
                "title": pr["title"],
                "url": url,
                "relations": {},
            },
        )
        entry["relations"].setdefault(user, set()).add(relation)

    for user in users:
        if user in unsearchable:
            continue
        for flag, relation in (
            ("--author", "authored"),
            ("--assignee", "assigned"),
            ("--review-requested", "review_requested"),
        ):
            try:
                prs = run_gh_search_json(
                    [
                        "search",
                        "prs",
                        "--state",
                        "open",
                        "--archived=false",
                        flag,
                        user,
                        "--json",
                        "number,title,url,repository",
                        "-L",
                        "100",
                    ]
                )
            except RuntimeError as exc:
                if UNSEARCHABLE_MARK in str(exc):
                    unsearchable.add(user)
                    print(
                        f"warning: {user} is not searchable; "
                        "falling back to repo enumeration",
                        file=sys.stderr,
                    )
                    break
                degraded(
                    f"candidate search failed ({user} {relation}): {gh_message(exc)}"
                )
                continue
            for pr in prs:
                add(pr, user, relation)

    for pr, user, relation in extra:
        add(pr, user, relation)
    return candidates


def fetch_pr_detail(repo: str, number: int) -> dict:
    owner, name = repo.split("/", 1)
    out = run_gh(
        [
            "api",
            "graphql",
            "-f",
            f"query={PR_DETAIL_QUERY}",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={name}",
            "-F",
            f"number={number}",
        ]
    )
    payload = json.loads(out)
    if "errors" in payload:
        raise RuntimeError(str(payload["errors"]))
    pr = payload["data"]["repository"]["pullRequest"]
    if pr is None:
        raise RuntimeError("pull request not found (renamed repo / deleted?)")
    return pr


def analyze_pr(
    entry: dict, users: list[str], ref: dt.datetime, owned_paths: dict
) -> dict:
    repo, number = entry["repo"], entry["number"]
    detail = fetch_pr_detail(repo, number)

    requested_logins = set()
    for node in detail["reviewRequests"]["nodes"]:
        rr = node.get("requestedReviewer") or {}
        login = rr.get("login")
        if login:
            requested_logins.add(login.lower())
    review_pending_for = sorted(u for u in users if u.lower() in requested_logins)

    # Freshness of the most recent pending review request for our users.
    request_ts = None
    for node in detail["timelineItems"]["nodes"]:
        rr = node.get("requestedReviewer") or {}
        login = rr.get("login")
        if login and login.lower() in {u.lower() for u in users}:
            ts = parse_iso(node.get("createdAt"))
            if ts and (request_ts is None or ts > request_ts):
                request_ts = ts
    request_freshness_days = None
    request_freshness_is_approx = False
    if review_pending_for:
        if request_ts is not None:
            request_freshness_days = (ref - request_ts).total_seconds() / 86400.0
        else:
            # Timeline page didn't include the event (paginated out) — fall
            # back to updatedAt as an approximation.
            request_freshness_days = age_days(detail["updatedAt"], ref)
            request_freshness_is_approx = True

    ci_nodes = detail["commits"]["nodes"]
    ci_state = None
    rollup = None
    if ci_nodes:
        rollup = ci_nodes[0]["commit"].get("statusCheckRollup")
        ci_state = rollup["state"] if rollup else None

    unresolved_total = 0
    unresolved_bot_flagged = 0
    flagged_severities: set[str] = set()
    last_comment_author = None
    last_comment_typename = None
    last_comment_ts = None

    for c in detail["comments"]["nodes"]:
        ts = parse_iso(c.get("createdAt"))
        if ts and (last_comment_ts is None or ts > last_comment_ts):
            last_comment_ts = ts
            last_comment_author = (c.get("author") or {}).get("login")
            last_comment_typename = (c.get("author") or {}).get("__typename")

    for thread in detail["reviewThreads"]["nodes"]:
        if thread["isResolved"]:
            continue
        unresolved_total += 1
        thread_flagged = False
        for c in thread["comments"]["nodes"]:
            author = c.get("author") or {}
            if not is_bot_author(author.get("__typename"), author.get("login")):
                continue
            body = c.get("body") or ""
            words = {m.upper() for m in SEVERITY_WORD_RE.findall(body)}
            has_emoji = any(e in body for e in SEVERITY_EMOJI_HIGH)
            if words or has_emoji:
                thread_flagged = True
                flagged_severities |= words
                if has_emoji:
                    flagged_severities.add("(bot-emoji)")
        if thread_flagged:
            unresolved_bot_flagged += 1

    our = {u.lower() for u in users}
    humans: set[str] = set()
    changes_requested_by: set[str] = set()
    latest_review_state: dict[str, str] = {}
    latest_review_at: dict[str, str | None] = {}
    review_typename: dict[str, str | None] = {}
    for r in detail["reviews"]["nodes"]:
        author = r.get("author") or {}
        login = author.get("login")
        if login and r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest_review_state[login] = r["state"]
            latest_review_at[login] = r.get("submittedAt")
            review_typename[login] = author.get("__typename")
    for login, state in latest_review_state.items():
        if state == "CHANGES_REQUESTED":
            changes_requested_by.add(login)
    authors = [
        (c.get("author") or {})
        for c in detail["comments"]["nodes"]
        + [c for t in detail["reviewThreads"]["nodes"] for c in t["comments"]["nodes"]]
    ] + [(r.get("author") or {}) for r in detail["reviews"]["nodes"]]
    for a in authors:
        login = a.get("login")
        if (
            login
            and login.lower() not in our
            and not is_bot_author(a.get("__typename"), login)
        ):
            humans.add(login)
    pr_author = (detail["author"] or {}).get("login")
    author_is_bot = is_bot_author((detail["author"] or {}).get("__typename"), pr_author)
    if pr_author and pr_author.lower() not in our and not author_is_bot:
        humans.add(pr_author)

    labels = [n["name"] for n in detail["labels"]["nodes"]]
    linked = [
        {
            "number": n["number"],
            "title": n["title"],
            "labels": [x["name"] for x in n["labels"]["nodes"]],
        }
        for n in detail["closingIssuesReferences"]["nodes"]
    ]
    ctype, breaking_bang = conventional_type(detail["title"])

    relations = sorted({rel for rels in entry["relations"].values() for rel in rels})

    result = dict(entry)
    result.pop("relations")
    result["relations"] = relations
    result["relations_by_user"] = {u: sorted(v) for u, v in entry["relations"].items()}
    result["author"] = pr_author
    result["baseRefName"] = detail["baseRefName"]
    result["headRefName"] = detail["headRefName"]
    result["targetsReleaseBranch"] = bool(
        RELEASE_BRANCH_RE.match(detail["baseRefName"] or "")
    )
    result["labels"] = labels
    result["linkedIssues"] = linked
    result["conventionalType"] = ctype
    result["breakingChange"] = breaking_bang or any(
        BREAKING_LABEL_RE.search(x) for x in labels
    )
    result["humansInvolved"] = sorted(humans)
    result["participantsTotal"] = detail["participants"]["totalCount"]
    result["changesRequestedBy"] = sorted(changes_requested_by)
    human_cr = [
        x
        for x in changes_requested_by
        if not is_bot_author(review_typename.get(x), x) and x.lower() not in our
    ]
    # A re-requested reviewer owes the next step: that CR no longer waits on us.
    human_cr_ages = [
        a
        for x in human_cr
        if x.lower() not in requested_logins
        if (a := age_days(latest_review_at.get(x), ref)) is not None
    ]
    result["changesRequestedByHuman"] = bool(human_cr)
    result["changesRequestedHumanAgeDays"] = min(human_cr_ages, default=None)
    result["reviewsTruncated"] = detail["reviews"].get("totalCount", 0) > len(
        detail["reviews"]["nodes"]
    )
    result["authorIsBot"] = author_is_bot
    default_branch = (
        (detail.get("baseRepository") or {}).get("defaultBranchRef") or {}
    ).get("name")
    # A fork head or a long-lived head (release merge) has no stacked dependents.
    result["dependentPRs"] = (
        []
        if detail.get("isCrossRepository")
        or is_long_lived(detail["headRefName"], default_branch)
        else fetch_dependents(repo, detail["headRefName"])
    )
    result["stackedOnPRs"] = fetch_stacked_on(
        repo, detail["baseRefName"], default_branch
    )
    result["isDraft"] = detail["isDraft"]
    result["createdAt"] = detail["createdAt"]
    result["updatedAt"] = detail["updatedAt"]
    result["ageDays"] = age_days(detail["createdAt"], ref)
    result["staleDays"] = age_days(detail["updatedAt"], ref)
    result["mergeable"] = detail["mergeable"]
    result["mergeStateStatus"] = detail["mergeStateStatus"]
    result["reviewDecision"] = detail["reviewDecision"]
    result["ciState"] = ci_state
    result.update(scoring.classify_ci(rollup))
    result["additions"] = detail.get("additions")
    result["deletions"] = detail.get("deletions")
    result["reviewPendingFor"] = review_pending_for
    result["reviewRequestFreshnessDays"] = request_freshness_days
    result["reviewRequestFreshnessIsApprox"] = request_freshness_is_approx
    result["unresolvedThreadsTotal"] = unresolved_total
    result["unresolvedBotFlagged"] = unresolved_bot_flagged
    result["unresolvedBotFlaggedSeverities"] = sorted(flagged_severities)
    result["lastCommentAuthor"] = last_comment_author
    result["lastCommentAgeDays"] = (
        (ref - last_comment_ts).total_seconds() / 86400.0 if last_comment_ts else None
    )
    result["lastCommentIsFromUs"] = (
        last_comment_author in users if last_comment_author else None
    )
    result["awaitingOurReply"] = bool(
        last_comment_author
        and last_comment_author.lower() not in our
        and not is_bot_author(last_comment_typename, last_comment_author)
    )
    # null for repos without an ownership policy (no filter applies there).
    result["ownedChange"] = (
        owned_change(repo, number, owned_paths[repo]) if repo in owned_paths else None
    )

    result["eligible"], result["eligibilityReason"] = scoring.pr_eligibility(
        result, users
    )
    result["scoreParts"] = scoring.pr_score_parts(
        result, users, detail.get("body") or ""
    )
    return result


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


ISSUE_FIELDS = (
    "number,title,url,author,authorAssociation,assignees,body,createdAt,"
    "updatedAt,commentsCount,labels,repository"
)


def _search_issues(qualifiers: list[str], what: str) -> list[dict]:
    try:
        return run_gh_search_json(
            ["search", "issues", "--state", "open", "--archived=false", *qualifiers]
            + ["--json", ISSUE_FIELDS, "-L", "1000"]
        )
    except RuntimeError as exc:
        degraded(f"issue search failed ({what}): {gh_message(exc)}")
        return []


def _graphql_pages(query: str, variables: dict[str, str], path: list[str]):
    """Yield nodes of a paginated connection; `query` takes `$after`."""
    after = None
    while True:
        args = ["api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            args += ["-f", f"{key}={value}"]
        if after:
            args += ["-f", f"after={after}"]
        conn = run_gh_json(args)["data"]
        for key in path:
            conn = conn[key]
        yield from conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return
        after = conn["pageInfo"]["endCursor"]


OWNER_REPOS_QUERY = """
query($login:String!, $after:String) {
  repositoryOwner(login:$login) {
    repositories(first:100, after:$after, isArchived:false) {
      pageInfo { hasNextPage endCursor }
      nodes {
        nameWithOwner
        pullRequests(states:OPEN) { totalCount }
        issues(states:OPEN) { totalCount }
      }
    }
  }
}
"""

REPO_OPEN_PRS_QUERY = """
query($owner:String!, $name:String!, $after:String) {
  repository(owner:$owner, name:$name) {
    pullRequests(states:OPEN, first:50, after:$after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url
        repository { nameWithOwner }
        author { login }
        assignees(first:20) { nodes { login } }
        reviewRequests(first:20) {
          nodes { requestedReviewer { ... on User { login } } }
        }
      }
    }
  }
}
"""

REPO_OPEN_ISSUES_QUERY = """
query($owner:String!, $name:String!, $after:String) {
  repository(owner:$owner, name:$name) {
    issues(states:OPEN, first:50, after:$after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url body createdAt updatedAt authorAssociation
        repository { nameWithOwner }
        author { login }
        assignees(first:20) { nodes { login } }
        labels(first:20) { nodes { name } }
        comments(last:50) { totalCount nodes { body } }
      }
    }
  }
}
"""


def _logins(conn: dict | None) -> set[str]:
    return {(n or {}).get("login", "").lower() for n in (conn or {}).get("nodes", [])}


def _enumerate_repo(repo: str, has_prs: bool, has_issues: bool, users: list[str]):
    owner, name = repo.split("/", 1)
    variables = {"owner": owner, "name": name}
    pr_hits: list[tuple[dict, str, str]] = []
    issue_hits: list[tuple[dict, str]] = []
    if has_prs:
        path = ["repository", "pullRequests"]
        for pr in _graphql_pages(REPO_OPEN_PRS_QUERY, variables, path):
            author = ((pr.get("author") or {}).get("login") or "").lower()
            assignees = _logins(pr.get("assignees"))
            reviewers = {
                ((n.get("requestedReviewer") or {}).get("login") or "").lower()
                for n in (pr.get("reviewRequests") or {}).get("nodes", [])
            }
            for user in users:
                low = user.lower()
                for hit, relation in (
                    (author == low, "authored"),
                    (low in assignees, "assigned"),
                    (low in reviewers, "review_requested"),
                ):
                    if hit:
                        pr_hits.append((pr, user, relation))
    if has_issues:
        path = ["repository", "issues"]
        for issue in _graphql_pages(REPO_OPEN_ISSUES_QUERY, variables, path):
            assignees = _logins(issue.get("assignees"))
            comments = issue.get("comments") or {}
            texts = [issue.get("body") or ""] + [
                c.get("body") or "" for c in comments.get("nodes", [])
            ]
            relations = set()
            for user in users:
                if user.lower() in assignees:
                    relations.add("assigned")
                mention = re.compile(rf"@{re.escape(user)}\b", re.IGNORECASE)
                if any(mention.search(t) for t in texts):
                    relations.add("mentioned")
            if relations:
                issue["assignees"] = (issue.get("assignees") or {}).get("nodes", [])
                issue["labels"] = (issue.get("labels") or {}).get("nodes", [])
                issue["commentsCount"] = comments.get("totalCount", 0)
                issue.pop("comments", None)
                issue_hits.extend((issue, r) for r in sorted(relations))
    return pr_hits, issue_hits


def enumerate_owner_items(
    owners: list[str], users: list[str], workers: int
) -> tuple[list[tuple[dict, str, str]], list[tuple[dict, str]]]:
    """Find open PRs/issues involving `users` by listing every non-archived
    repo of `owners` via GraphQL — for users GitHub search refuses.

    Mentions are matched in issue bodies and the last 50 comments only.
    """
    repos = []
    for owner in owners:
        try:
            path = ["repositoryOwner", "repositories"]
            for repo in _graphql_pages(OWNER_REPOS_QUERY, {"login": owner}, path):
                prs = repo["pullRequests"]["totalCount"] > 0
                issues = repo["issues"]["totalCount"] > 0
                if prs or issues:
                    repos.append((repo["nameWithOwner"], prs, issues))
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            degraded(f"repo listing failed ({owner}): {gh_message(exc)}")
    pr_hits: list[tuple[dict, str, str]] = []
    issue_hits: list[tuple[dict, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_enumerate_repo, repo, has_prs, has_issues, users): repo
            for repo, has_prs, has_issues in repos
        }
        for fut in concurrent.futures.as_completed(futures):
            try:
                prs, issues = fut.result()
            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                degraded(f"enumeration failed ({futures[fut]}): {gh_message(exc)}")
                continue
            pr_hits += prs
            issue_hits += issues
    return pr_hits, issue_hits


def fetch_issues(
    repos: list[str],
    users: list[str],
    lookback_days: float,
    ref: dt.datetime,
    unsearchable: set[str],
    extra: list[tuple[dict, str]],
) -> list[dict]:
    """Every open issue in `repos`, plus open issues anywhere that are
    assigned to or mention one of `users`. Users in `unsearchable` are not
    searched; `extra` holds their (issue, relation) hits from enumeration."""
    found: dict[str, tuple[dict, set[str]]] = {}

    def add(issue: dict, relation: str) -> None:
        found.setdefault(issue["url"], (issue, set()))[1].add(relation)

    for repo in repos:
        for issue in _search_issues(["--repo", repo], repo):
            add(issue, "repo_open")
    for user in users:
        if user in unsearchable:
            continue
        for flag, relation in (("--assignee", "assigned"), ("--mentions", "mentioned")):
            for issue in _search_issues([flag, user], f"{user} {relation}"):
                add(issue, relation)
    for issue, relation in extra:
        add(issue, relation)

    out = []
    for issue, relations in found.values():
        repo = issue["repository"]["nameWithOwner"]
        age = age_days(issue["createdAt"], ref)
        entry = {
            "repo": repo,
            "number": issue["number"],
            "title": issue["title"],
            "url": issue["url"],
            "relations": sorted(relations),
            "isNew": age is not None and age <= lookback_days,
            "author": (issue.get("author") or {}).get("login"),
            "assignees": [a.get("login") for a in issue.get("assignees") or []],
            "createdAt": issue["createdAt"],
            "updatedAt": issue["updatedAt"],
            "ageDays": age,
            "updatedDays": age_days(issue["updatedAt"], ref),
            "commentsCount": issue.get("commentsCount", 0),
            "labels": [label.get("name") for label in issue.get("labels", [])],
            "authorAssociation": issue.get("authorAssociation"),
            "versionsMentioned": sorted(
                set(VERSION_RE.findall(issue.get("body") or ""))
            )[:5],
            "bodyExcerpt": " ".join((issue.get("body") or "").split())[:300],
            "eligible": True,
            "eligibilityReason": "open issue",
        }
        entry["scoreParts"] = scoring.issue_score_parts(entry, issue.get("body") or "")
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--users", default=",".join(DEFAULT_USERS), help="comma-separated GitHub logins"
    )
    parser.add_argument(
        "--issue-repos",
        default=",".join(DEFAULT_ISSUE_REPOS),
        help="comma-separated owner/repo",
    )
    parser.add_argument("--lookback-days", type=float, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument(
        "--enumerate-owners",
        default=",".join(DEFAULT_ENUMERATE_OWNERS),
        help="comma-separated orgs/users whose repos are listed for unsearchable users",
    )
    args = parser.parse_args()

    users = [u.strip() for u in args.users.split(",") if u.strip()]
    issue_repos = [r.strip() for r in args.issue_repos.split(",") if r.strip()]
    ref = now_utc()
    DEGRADED.clear()

    owners = [o.strip() for o in args.enumerate_owners.split(",") if o.strip()]
    unsearchable: set[str] = set()
    candidates = discover_pr_candidates(users, unsearchable, [])
    enum_pr_hits: list[tuple[dict, str, str]] = []
    enum_issue_hits: list[tuple[dict, str]] = []
    if unsearchable and owners:
        enum_pr_hits, enum_issue_hits = enumerate_owner_items(
            owners, sorted(unsearchable), min(args.workers, 4)
        )
        for url, entry in discover_pr_candidates(
            [], unsearchable, enum_pr_hits
        ).items():
            target = candidates.setdefault(url, entry)
            if target is not entry:
                for user, rels in entry["relations"].items():
                    target["relations"].setdefault(user, set()).update(rels)
    policy_repos = {c["repo"] for c in candidates.values()} & set(POLICY_FILES)
    owned_paths = {repo: fetch_owned_paths(repo, users) for repo in policy_repos}
    prs: list[dict] = []
    errors: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(analyze_pr, entry, users, ref, owned_paths): entry
            for entry in candidates.values()
        }
        for fut in concurrent.futures.as_completed(futures):
            entry = futures[fut]
            try:
                prs.append(fut.result())
            except Exception as exc:  # noqa: BLE001 - report and continue
                msg = gh_message(exc)
                errors.append(f"{entry['repo']}#{entry['number']}: {msg}")
                print(
                    f"warning: PR analysis failed, dropped from output "
                    f"({entry['repo']}#{entry['number']}): {msg}",
                    file=sys.stderr,
                )

    issues = fetch_issues(
        issue_repos, users, args.lookback_days, ref, unsearchable, enum_issue_hits
    )
    releases = {
        repo: fetch_latest_release(repo)
        for repo in sorted(set(issue_repos) | {i["repo"] for i in issues})
    }
    for issue in issues:
        issue["latestRelease"] = releases.get(issue["repo"])

    aliases = repo_aliases({x["repo"] for x in prs + issues})
    for kind, items in (("pr", prs), ("issue", issues)):
        for item in items:
            item["id"] = f"{aliases[item['repo']]}-{item['number']}"
            item["kind"] = kind
    prs.sort(key=lambda p: (p["repo"], p["number"]))
    issues.sort(key=lambda i: (i["repo"], i["number"]))

    output = {
        "generated_at": ref.isoformat(),
        "users": users,
        "issue_repos": issue_repos,
        "unsearchable_users": sorted(unsearchable),
        "enumerated_owners": owners if unsearchable else [],
        "lookback_days": args.lookback_days,
        "owned_paths": owned_paths,
        "repo_aliases": aliases,
        "latest_releases": releases,
        "scoring": scoring.scoring_doc(),
        "summary": {
            "total_prs": len(prs),
            "review_pending": sum(1 for p in prs if p["reviewPendingFor"]),
            "changes_requested_on_mine": sum(
                1
                for p in prs
                if "authored" in p["relations"]
                and p["reviewDecision"] == "CHANGES_REQUESTED"
            ),
            "ci_failing_on_mine": sum(
                1
                for p in prs
                if "authored" in p["relations"] and p["ciEffective"] == "red"
            ),
            "merge_conflicts": sum(1 for p in prs if p["mergeable"] == "CONFLICTING"),
            "prs_with_flagged_bot_comments": sum(
                1 for p in prs if p["unresolvedBotFlagged"] > 0
            ),
            "total_issues": len(issues),
            "new_issues": sum(1 for i in issues if i["isNew"]),
        },
        "prs": prs,
        "issues": issues,
        "errors": errors + DEGRADED,
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
