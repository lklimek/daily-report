---
name: daily-priorities
description: Build a daily work-priority digest from live GitHub state and open MemCan TODOs across projects — PRs authored by, assigned to, or awaiting review from lklimek and Claudius-Maginificent (any repo), plus all open issues in dashpay/dash-evo-tool and dashpay/tenderdash and open issues anywhere assigned to or mentioning them. Use when the user asks for "today's task list", "what should I work on today", "daily standup prep", "PR triage", or the Polish equivalent ("lista zadań na dziś", "co dzisiaj ogarnąć", "przegląd PR-ów").
model: sonnet
---

# daily-priorities

Same-day digest: **every** item that needs the user, in one list sorted by
priority — no cap. Every run re-fetches live from GitHub and MemCan; store nothing.
Steps 1-3 are read-only for GitHub. The only MemCan writes are the automatic
stale-TODO refresh (§1, "Refresh stale TODOs"). Step 4 writes to GitHub and
runs only on explicit request. A digest never creates TODOs or changes their
priority.
Run on a cheap model (`sonnet`); when delegating, spawn the agent with `model: sonnet`.

## 1. Run the scanner

```bash
T=/tmp   # or the heavy-scratch dir named in the user's CLAUDE.md
python3 ~/.claude/skills/daily-priorities/scripts/scan.py > $T/dp-scan.json
```

~1-2 min, one JSON document on stdout, warnings on stderr. **Read stderr**
and top-level `errors`: a failed search (rate limit, unsearchable user) or
a PR dropped on a fetch error silently shrinks the digest — re-fetch the
gap another way or list it under "What the scan missed".

Optional flags: `--users`, `--issue-repos`, `--lookback-days`,
`--enumerate-owners` (defaults: both accounts, the two issue repos, 7 days,
`dashpay,lklimek`).

Searches are spaced out and retried on GitHub's secondary rate limit.
Archived repos are excluded everywhere (`--archived=false`; the fallback
enumerates non-archived repos only); never rank items from one.
GitHub search refuses some accounts entirely ("cannot be searched" —
Claudius-Maginificent, in any scope, incl. `--owner`). For those the scanner
lists every non-archived repo of `--enumerate-owners` via GraphQL and
matches author / assignee / review request / issue @mention locally
(top-level `unsearchable_users`, `enumerated_owners`). Mentions cover the
issue body and last 50 comments only; repos outside those owners are not seen.
Never report such a user as "unknown" while the fallback ran.

Every PR and issue has a stable `id` — `<ALIAS>-<number>` (`DET-1094`,
`TD-1534`, `PLAT-5150`; table in top-level `repo_aliases`). It survives
re-runs, so the user can refer to items by it. Each item carries
`eligible` + `eligibilityReason` and `scoreParts` (§2).

Issues: every open issue in `--issue-repos`, plus open issues anywhere
assigned to or mentioning our users. Non-obvious PR fields: `ciEffective`
(policy gates excluded, latest run per check) vs raw `ciState`;
`reviewRequestFreshnessIsApprox` (fell back to `updatedAt`);
`dependentPRs` / `stackedOnPRs` (same-repo stacks); `authorIsBot`
(GraphQL `__typename`; Bot logins lack `[bot]`).

`ownedChange` is set only for `dashpay/platform` (`null` elsewhere). The
scanner reads the ownership policy
(`dashpay/stale_prs_are_bad:policies/platform.json`), takes the areas where
one of our users is an owner or reviewer (echoed as top-level
`owned_paths`), and counts changed lines there that are neither blank nor
comment-only, skipping docs and lockfiles:

- `verdict`: `none` (only comments/docs, or nothing in our areas), `minor`
  (few or thinly spread lines — call-site adjustments), `significant`
- `areas`, `codeFiles`, `codeLines`, `distinctCodeLines`, `topFiles`

### Fetch MemCan TODOs

The GitHub scanner does not fetch TODOs. Read them through the MemCan MCP
in the same run, across **all projects**, including projects absent from GitHub:

1. Call `export_collection(collection="todos", limit=1000, offset=0)`.
   The response contains `count`, `offset`, and `data` (JSONL). Parse each
   line of `data`; advance offset by `count` until a page has fewer than
   `limit` records. Process in memory, without writing an export to disk.
2. Keep `pending`, `in_progress`, `blocked`, and `postponed`; exclude
   `done` and `cancelled`. Preserve `id`, `project`, `title`, `description`,
   `priority`, `status`, `owner`, `blocked_by`, and available timestamps.
   For missing dependency/owner fields, use `get_todo(todo_id=...)` before
   treating the task as unblocked or unassigned. Keep owners visible;
   distinguish the user's next action from work delegated to someone else.
3. Resolve dependencies with `get_todo`; verify linked PRs/issues live,
   including references outside the scanner's discovery scope. TODO text
   can be stale: do not assume its PR is still open or its blocker unresolved.
   A closed/merged link alone does not prove the whole TODO is complete.

### Refresh stale TODOs (automatic)

Do this before scoring, without asking. A TODO is stale when its text
names a PR, issue, or blocker whose live state contradicts the TODO's
status or next step, e.g. "draft PR is open" when the PR was closed or
merged, or `blocked` when the blocker is resolved or was only environmental
(sandbox, read-only path, missing tool).

1. Verify the evidence live: PR/issue state, closing comment ("replaced by
   #N"), merge commit, and whether the outcome actually landed (for example,
   check that a downstream pin contains the merge commit).
2. Apply the smallest correct change with `update_todo` / `complete_todo`:
   - Every remaining step is verified done → `complete_todo`.
   - PR replaced by another open PR → keep the status, prepend a note
     pointing at the successor, and merge the TODO with that PR's row.
   - Waits on an open item → `blocked`, naming that item.
   - Blocker resolved or only environmental → `pending`, with the
     next concrete action.
   - Only optional or conditional work remains → `postponed`.
3. Prepend `[YYYY-MM-DD status check] <evidence> → <next action>` to the
   description and keep the original text below it. Never change
   `priority` or `title` meaning, never delete a TODO, and never cancel
   one unless a successor or merged PR provably covers all of it.
4. Unsure → leave the TODO as it is and list it under "What the scan missed".
   Duplicate TODOs are merged in the ranking only; never changed.

Score refreshed TODOs by their new state. Report every change in the digest
as `TODO-<prefix>: <old> → <new> (<evidence>)`.

If export is unavailable, use `list_todos(project=..., limit=200)` for known
project scopes (repository short names from GitHub, the current origin,
and user-provided scopes). Omit `status` and `owner`, then filter terminal
states locally. An empty project is not an all-project query. If `has_more`
is true, split by exact open status; report any remaining truncation.
Search can discover more projects but is not an exhaustive listing. Disclose
this fallback's scope or any MemCan failure under "What the scan missed".

### Previous working day stats

Report throughput for the **previous working day** (D): Monday → previous
Friday, otherwise yesterday; local date (Europe/Warsaw). Collect for both
users (`lklimek` and Claudius-Maginificent), then report combined totals —
deduplicate PRs both touched (each PR counts once per kind). Search qualifiers fail for an
unsearchable user; count theirs from the Events API instead (scope:
`enumerated_owners` only — that is sufficient):
`gh api 'users/<u>/events?per_page=100' --paginate` (concatenated pages:
replace `][` with `,` before parsing). Keep events whose local date is D and
whose repo owner is in `enumerated_owners`; collect PR numbers from
`PullRequestEvent` / `PullRequestReview[Comment]Event`, then
`gh pr view --json author,state,mergedAt,closedAt,createdAt` each and
classify by author and timestamps — a `merged` event means the user merged
it, not authored it; reviews on our own PRs (either user's) do not count. The API
keeps ≤300 events / 90 days: if the oldest event is after D, fall back to
GraphQL `pullRequests(orderBy:UPDATED_AT)` per repo, or report unknown:

- PRs authored: merged / closed unmerged / opened —
  `gh search prs --author <u> --merged-at D`, `--closed D`, `--created D`
  (`--json number,repository,title,state`). A PR opened and merged on D
  counts in both.
- Reviews: distinct PRs **not authored by lklimek or Claudius-Maginificent**
  on which either submitted a review on D, of which merged. Candidates:
  `--reviewed-by <u> --updated ">=D"` plus the Events review PRs; confirm via
  GraphQL `reviews(last:100){nodes{author{login} submittedAt}}` (a later
  update hides D's reviews from `--updated D`).
- Issues in `--issue-repos` + assigned to us: closed / opened on D
  (`gh search issues ... --closed D` / `--created D`).
- MemCan TODOs from the same export: `completed_at` on D split into `done`
  and `cancelled`; `created_at` on D (new). TODOs carry no close-by field,
  so a batch of stale cancellations is cleanup — say so.

Net = opened − closed per kind (negative = backlog shrank). Search quota
is 30/min: run these after the scanner, and on a 403 rate limit wait for
reset and retry; report any count still missing as unknown, never 0.

## 2. Prioritize

Merge GitHub items and TODOs into one ranking. Deduplicate by explicit
repo/number links or the same concrete outcome, not title similarity alone.
Keep both IDs on a merged row and score the work once: list the others in
the survivor's `alsoIds`; override its recency (newest item) and
advancement (highest) with a `basis`.

**Eligibility** (scanner sets `eligible`; override via judgment after the
sanity check): include a `dashpay/platform` PR only when
`ownedChange.verdict == "significant"`, including own PRs. Sanity-check
`topFiles` for P0/P1 candidates: all tests, mechanical renames, or one
repeated edit are minor. If unclear, read the owned part of its diff.
A TODO must not reintroduce an excluded PR as a review task; independent
TODO work remains eligible. Other repositories are unaffected by this rule.
In every repository, exclude draft PRs authored by anyone other than lklimek
or Claudius-Maginificent: drafts are not ready for our review.

### Scoring (−22–157.5)

Favor completing open work. Choose one applicable value per row; use the
highest applicable advancement state.

The scanner computes the mechanical parts (`scoreParts`; `null` = unknown,
**never 0**). Judge only the open ones from the template (per-item `facts`
+ `hints`; rules and format in `_legend`), not the scan; where a hint says
verify or needs unseen content, check live (`gh pr view` / `gh pr checks`)
or mark it provisional:

```bash
S=~/.claude/skills/daily-priorities/scripts
python3 $S/score.py --scan $T/dp-scan.json --template > $T/dp-judgment.json
# fill every null per _legend, then:
python3 $S/score.py --scan $T/dp-scan.json --judgment $T/dp-judgment.json
```

Add TODOs as `"TODO-<prefix>": {"kind": "todo", "title", "url", "parts":
{all ten}}`. Override a `computed` part only with a `basis` (e.g. a TODO
dependency raising `unblocks`). Exit 2 (`complete: false`) = rows
`unscored` / `final: false` or unresolved `scanErrors`: present per §3,
never as final.

| Component | Assessment → points | Maximum |
|---|---|---:|
| Advancement | Non-draft PR accepted by bots (`ready-for-human`) → 30; other open non-draft PR → 20; draft PR → 15; started TODO without a PR → 10; unstarted work → 0 | 30 |
| Defect severity | `20 * overall_severity`, where `overall_severity = (likelihood + impact) / 2`, assessed under the `severity` skill | 20 |
| Unblocks other work | At least 3 distinct tasks/PRs → 15; 2 → 10; 1 → 5; none → 0 | 15 |
| Human waiting for our action | Outstanding review/reply/change request aged ≤2 days → 15; >2–7 days → 10; >7 days → 5; no current request → 0 | 15 |
| Recency | `17 * (2/17) ** (d / 15) - 2`, `d` = days since the item's last change (below) | 15 |
| Change impact | User-visible fix or feature → 10; internal improvement with a concrete effect → 5; housekeeping, documentation, or cosmetic work → 0 | 10 |
| Proximity to completion | Only final verification/approval remains and actual CI is green → 10; one clearly bounded step remains → 5; several steps or unknown scope → 0 | 10 |
| Change size | `12.5 * (0.8 ** (x / 1000))`, where `x` is added plus deleted lines in the measured diff; no measured diff → 0 | 12.5 |
| Urgency bonus | Confirmed production defect, security issue, or release blocker → 30 once; otherwise → 0 | 30 |
| Blocked penalty | Verified unresolved blocker (see below) → −20; otherwise → 0 | 0 |

`score = advancement + defect_severity + unblocks + human_waiting + recency + change_impact + completion + change_size + urgency_bonus + blocked_penalty`.

| Tier | Score |
|---|---:|
| P0 | >100 |
| P1 | 65–100 |
| P2 | 35–<65 |
| P3 | <35 |

Keep full precision for thresholds and sorting; round only for display.
Exactly 100 is P1. Sort by score descending, then advancement, defect
severity, and unblocks descending; use stable ID ascending for remaining
ties. No tier overrides: drafts, conflicts, red CI, `not ready`, MemCan
priority, and `blocked`/`postponed` status never force a tier or reorder
the calculated ranking; blocking acts only through the numeric penalty.

**Blocked penalty:** apply −20 once when the item's next action cannot
start until another open item or external party acts: a PR stacked on an
unmerged base PR, a TODO with an open `blocked_by` entry, or a verified
open dependency named in the item (`blocked` label, TODO text). Verify the
blocker is still open; a stale `blocked` status or label whose blocker is
resolved gets 0. Not blocked: red CI, conflicts, change requests, drafts,
or waiting for review — that is our own work or completion state.

**Recency:** `d` = days since the last change: PR/issue `updatedAt`
(scanner); TODO → the newest of `created_at` and a `[YYYY-MM-DD status
check]` date whose refresh changed the status; merged row → newest item.
Give `{"days": d}`; score.py applies the decay.

**Change size:** PRs use the scanner's full-diff `additions + deletions`;
if null, `gh pr view <n> --repo <repo> --json additions,deletions` →
`{"lines": x}`. Issue/TODO without a diff → 0 (N/A), not `f(0)`; a merged
row uses the PR's size once.

### Evidence and severity

- Load `severity` before rating a defect; reuse current verified
  `likelihood`/`impact` floats with its backstop-zone limits. For several
  current defects related to the work, use the maximum overall severity.
  A feature with no defect scores zero here; missing assessment is not
  zero: assess it or judge it provisional (estimate + basis, §2).
- `relevance` and estimated fix effort do not enter severity. Do not
  derive floats from bot labels, keyword matches, or MemCan `high`.
  `merge_class` stays independent: a blocking finding still prevents
  merging, regardless of the task's priority score.
- Award the urgency bonus once with evidence of the qualifying condition.
  A HIGH/CRITICAL label, `merge_class: blocking`, `fix` title, or release
  branch alone is insufficient. Confirm production/version exposure,
  the security issue, or an actual release dependency as applicable.
- Verify bot acceptance before awarding 30 advancement points; stale
  `ready-for-human` labels alone are insufficient. Count only outstanding
  human requests, not bot activity or an approximate `updatedAt` date.
  Treat Claudius-Maginificent, thepastaclaw, DCG-Claude, and real bots as bots.
- Count verified unresolved dependencies, deduplicated across GitHub and
  MemCan. A blocked task names its blocker and the concrete unblocking
  action. Stale TODO status is refreshed per §1 "Refresh stale TODOs".

**CI red means a failing build/test/lint check, never a policy check.**
`ciState` is the raw rollup (red when only `PR Hygiene` fails). Use
`ciEffective` / `ciRedChecks` instead: latest run per check, approval /
policy gates moved to `ciPolicyNotPassing`. Only gates non-passing → CI is
green: no CI red tag, no `not ready` from CI, completion points allowed.
`unknown` (truncated) or a doubtful gate name → `gh pr checks <n> --repo <repo>`.
`none` = no checks on the head commit: green only if the repo runs no CI
at all (check its other PRs), otherwise treat as pending.

**Review readiness:** tag requested reviews `not ready` when a build/test
check fails, or unresolved threads remain; include conflicts in the next
action. This is an actionability tag, not a tier override. Own PR failures
and conflicts are work to finish.

**Red CI dependency check:** inspect this run's items and open PRs in the
same repository for a matching fix, using `gh pr checks` and targeted PR
searches as needed. Confirm the failed check's cause before linking a fix.
Found → tag `CI red — fixed by <ID>, pending merge`, count the dependency
in the fix's unblocks component, and score both items normally. Otherwise
report the failure and any unresolved cause; do not assume another CI PR
fixes it or bypass the numeric order.

## 3. Present the digest

Reply in the user's configured language. Never dump the JSON.
For a presentation-only redisplay, preserve the existing digest's snapshot date,
scores, and ordering. A new prioritization run still fetches live data.

1. **One list, all items, sorted by priority**, grouped under tier
   headings. One short bullet each, no tables:
   `[ID](url) · score · @author · short title — next action/state`.
   **Every ID is a Markdown link** to the item (PR/issue `url`; TODO →
   `[TODO-<prefix>](<memcan-ui>/ui/tasks/<full-uuid>)`; `<memcan-ui>` = MemCan UI
   base URL from the user's CLAUDE.md, else `$MEMCAN_URL`), also in
   grouped entries and blocker references ("blocked by [ID](url)").
   Keep each line terse: drop `/157.5`, tier (the heading has it), repo#n,
   and conventional-commit prefixes; omit `@author` for our own PRs in P3.
   State tags inline in plain words or backticks: `review`, `not ready`,
   draft, CI red, conflicts, `CI red — fixed by <ID>`, what it blocks.
   Same-kind items with one score may share a bullet with sub-items.
   **Chat omits the ten-component breakdown**; it goes to the full
   digest file (below; score.py `breakdown`), e.g. `20+14+10+15+9.1+10+5+10+0-20=73.1`.
   A `final: false` row shows its score as `~73.1?`; `unscored` rows go in
   a trailing "Unscored" group without a score.
   TODOs: `[TODO-<prefix>](url) · score · project · title — next action`,
   plus status, owner if present, unresolved blocker links. Start with 8
   UUID characters, extend on collision; the URL uses the full UUID
   (resolve via `get_todo`; never build a URL from a prefix). A merged row
   keeps the GitHub ID and TODO ID, plus any remaining work not covered by
   the PR/issue. Never invent a GitHub author for a TODO.
   **Full digest file:** write every item (all tiers, with breakdown) in
   the same bullet format to `<durable-artifacts-dir>/<project>/YYYY-MM-DD/priorities.md`
   and link its published URL (both per CLAUDE.md). Chat shows P0–P2
   in full plus P3 PRs; P3 TODOs/issues may be summarized as counts there.
2. **Previous working day (D)** — compact block before the gap list:
   per kind `closed X (merged/done Y, cancelled Z) · new N · net ±K`, for
   PRs, issues, and TODOs, plus reviews done — one combined line per kind,
   never split by user; name merged PRs as linked IDs
   and note what they unblocked. Short, no per-item detail beyond that.
3. **What the scan missed** — failed searches, approximations leaned on,
   MemCan availability, unenumerated projects, truncated TODO listings,
   and unresolved dependencies or stale references used in ranking;
   every `scanErrors` / `resolvedErrors` entry, and every non-final or unscored row with its
   `unconfirmed` / `missing` parts.
4. **TODOs refreshed** — one line per automatic change (§1), or "none".

Later requests may refer to items by `id` alone ("open DET-1094", "why is
TD-1534 P1?"); resolve via `repo_aliases`.
Resolve `TODO-<prefix>` through `get_todo(todo_id="<prefix>")`; on an
ambiguous prefix, use a longer prefix or the full UUID.

## 4. Unblock gated PRs (explicit request only)

Never part of a normal digest. Run only when the user asks ("unblock the
gated PRs", "comment and reassign them"), and even then preview first.

Scope: `dashpay/dash-evo-tool` and `dashpay/tenderdash` only. Each gated
PR gets a comment naming the blocker and an assignee: a human author →
themselves; Claudius-Maginificent / thepastaclaw / DCG-Claude → lklimek; real bots
(`...[bot]`) → skipped.

1. Re-run the scanner, then dry run. Any run refused as stale (>30 min):
   repeat steps 1–3; `--allow-stale` only if the user says so.
   `python3 ~/.claude/skills/daily-priorities/scripts/unblock.py --scan-file <scan.json> --dry-run`
2. Show the user the **full** list: repo#number, author, assignee, action,
   blocker text. No summarizing.
3. Wait for explicit approval; on changes, revise and show again.
4. Re-run without `--dry-run`. It is idempotent (skips PRs whose last
   comment already carries the current blocker text).
5. Report the script's own tally, not raw `gh` exit codes — it re-verifies
   PR state before calling something a failure.

## Limitations

- **Priority signals are heuristics**: conventional-commit type, label
  regexes, and `targetsReleaseBranch` miss repos with other conventions;
  `dependentPRs` sees only PRs based directly on the head branch.

- **`ownedChange.verdict` is a heuristic.** It cannot tell a mechanical
  rename from logic, and counts a file with no patch (too large / binary)
  at its raw line total.
- **CI gate classification** matches check names (`POLICY_CHECK_RE` in
  `scripts/scoring.py`); add new approval/policy gate names there.
- **Bot severity is best-effort**: tuned to BLOCKER/CRITICAL/HIGH/MEDIUM as
  words; other bots are caught only by 🔴/🟠.
- **Review-request freshness** reads the last 50 timeline events; older
  requests fall back to `updatedAt` (flagged approximate).
- **`mentioned` has no recency**: GitHub matches any past @mention, so an
  old, already-answered ping still counts — check `updatedDays`.
- **PR search is global**, no repo allowlist — an unrelated repo can
  surface; use judgment.
