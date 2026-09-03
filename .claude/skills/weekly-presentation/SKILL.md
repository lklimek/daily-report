---
name: weekly-presentation
description: "Generate a self-contained HTML slide deck from a daily-report markdown output. Use when the user asks to create a weekly presentation, sprint report slides, or an HTML presentation of their PR activity. Triggers on: 'create presentation', 'make slides', 'weekly presentation', 'sprint report presentation', 'HTML slides'."
allowed-tools: ["Bash", "Read", "Write", "Glob", "Agent"]
---

# Weekly Presentation

Generate a self-contained HTML slide deck summarizing PR activity from `daily-report` output. Dash-branded dark theme, arrow-key navigation, progress dots, touch support.

## Workflow

### Phase 1: Generate the Report

The author runs PRs under **two GitHub identities** — both must be covered:

1. **Primary user** — default authenticated `gh` user (typically `lklimek`)
2. **AI co-author** — `Claudius-Maginificent` (note the spelling: "Mag**i**nificent" — actual handle, bio: "Claudius the Magnificent AI, on behalf of lklimek"). PRs opened by this bot account are part of the same sprint output and MUST be included.

Run the tool twice and merge the two markdown reports before designing slides:

```bash
cd /home/ubuntu/git/daily-report

# Primary user (authenticated gh user)
python3 -m daily_report --from YYYY-MM-DD --to YYYY-MM-DD \
  --repos-dir /home/ubuntu/git --org dashpay > /tmp/report-primary.md

# Claudius the Magnificent AI account
python3 -m daily_report --from YYYY-MM-DD --to YYYY-MM-DD \
  --repos-dir /home/ubuntu/git --org dashpay \
  --user Claudius-Maginificent > /tmp/report-claudius.md
```

Then read both files and treat the union as the input for Phase 2. Deduplicate PRs that appear in both (same `#number` in same repo) — a PR co-authored by both identities should only be presented once. Attribute it to whichever identity actually opened it (the `author` field), and note the AI co-authorship if relevant to the slide narrative.

- Default scope: `dashpay` org only. Broaden only if user explicitly asks.
- **Date range derivation** — derive `--from`/`--to` from the previous deck, not a fixed "2 weeks back" formula:
  1. Find the previous deck: `ls presentations/ | sort | tail -1` gives the most recent `YYYY-MM-DDTHHMMSS` directory. The date prefix is the previous `--to`.
  2. New `--from` = previous_to + 1 day. New `--to` = most recent past Tuesday on or before today (sprint cycle is bi-weekly Wed→Tue). If today IS a Tuesday and previous deck is ≥14 days ago, use today.
  3. Fallback to "last 2 weeks Wed→Tue" only when no `presentations/` directory exists yet.
- **Exclude stale PRs**: Only include PRs that had actual activity (commits, reviews, status changes) within the date range. PRs that were merely open/waiting with no activity during the period must be excluded — they clutter the presentation with non-progress.
- Skip if report output is already provided for both identities.

### Phase 1b: Gather Issue Stats (optional)

Use GitHub API (`search_issues`) to get issue counts for the primary repo:

```
Created in period:  search_issues query="repo:dashpay/REPO created:FROM..TO"  → total_count
Closed in period:   search_issues query="repo:dashpay/REPO closed:FROM..TO"   → total_count
Open before period: search_issues query="repo:dashpay/REPO state:open created:<=BEFORE_FROM" → total_count
Current open:       list_issues state=OPEN → totalCount
```

Derive: `open_before = current_open - created_after_period + closed_after_period` (approximate from API counts).

Use for the closing slide waterfall chart when issue tracking is more relevant than AI agent stats.

### Phase 1c: Read Every PR Body (mandatory)

**Do not write a single bullet from PR titles alone.** PR titles compress; bodies have the user-impact, the numbers, and the gotchas. Skipping this step produces generic, undersold slides.

For every PR you intend to put on a slide (authored *and* notable reviews), fetch the body via `gh`:

```bash
gh pr view <NUMBER> --repo dashpay/<REPO> --json title,state,isDraft,additions,deletions,body,author,createdAt,mergedAt,url
```

Batch them with `xargs -P 8` or parallel `Bash` calls — they're independent and cheap. Save raw JSON to `/tmp/pr-bodies/<repo>-<num>.json` so reruns don't re-fetch.

For each PR, extract three things and use them when writing the bullet:

1. **What changed (one line)** — the *user-visible* change, not the file diff. Bodies usually have a "What was done" or "Summary" section; lift the lede.
2. **Hard evidence** — concrete numbers ("passes on testnet in 24.76s", "+13.4k LoC", "reduces 100→16", "first end-to-end transfer"). These are what make a slide feel real.
3. **Sibling/dependency relationships** — bodies often reveal that PR-A *contains* PR-B's fix, or that PR-A is a doc-PR for crate-X built in PR-B. Group siblings into the same bullet (or attribute correctly across bullets) instead of double-counting.

If a PR body is empty or thin (dependabot, mechanical bumps, broken-fetch entries), it's a candidate for the closing-slide stats but probably not a content bullet. Drop it from theme slides.

**Then proceed to Phase 2 with PR-body context in working memory** — not just the title list.

### Phase 1d: Code-volume metrics (optional, use with caution)

If a headline number like "N lines shipped" or "X% of the codebase rewritten" is requested, do **NOT** derive it by summing `additions`/`deletions` across many `gh pr view` results. Stacked/rebased PR series inflate this badly — the same lines get counted again in every downstream PR that rebases on top of an earlier one, and mixing repos in one sum obscures which codebase actually changed. A run of 60 PRs across 4 repos this way produced a number in the hundreds of thousands that had no defensible relationship to the real diff.

Prefer a **`git diff --shortstat` between two meaningful refs in a single repo**:

```bash
git -C /path/to/repo diff <ref-before> <ref-after> --shortstat
# "percentage rewritten" = insertions / total_lines_at(ref-after) × 100
git -C /path/to/repo diff 4b825dc642cbc239bd8...(empty-tree-sha) <ref-after> --shortstat  # total lines at ref-after
```

This is reproducible, not double-counted, and scoped to one codebase — defensible if a viewer asks "how did you get that number?" Only fall back to summed-PR additions/deletions when no two clean refs exist to diff, and say so explicitly if asked.

### Phase 2: Design the Slide Content

Analyze the consolidated report and group PRs into **user-facing themes** (not repo-by-repo). Target 5-6 content slides in this fixed order:

1. **Title slide** — "My Focus: repo1, repo2", date range, author
2. **Convergence diagram slide (mandatory)** — "The map: all roads lead to <integration target>". Shows how every PR this sprint converges onto a single downstream target (typically the consumer app — DET, a wallet, an SDK release). Sets the frame that subsequent slides drill into. See *Convergence diagram (slide #2)* in Slide HTML Patterns. Always slide #2, never elsewhere.
3. **2-3 pillar slides** — themed around user benefits, grouping related PRs across repos. Each pillar slide MUST mirror a tile in the convergence diagram and lead with a `PILLAR N` prefix on its heading so the audience can anchor it back to the map.
4. **Closing stats slide** — two-column layout pairing PR/test/issue chart on the left with a bug-triage donut on the right (when bug-pin data exists). See *Stats closer slide* in Slide HTML Patterns.

**Rationale — diagram-first not reveal-last**: for a recurring bi-weekly with mixed audience (engineering + PM + leadership), placing the convergence diagram early gives the audience a stable mental model they refer back to throughout the deck. Burying it as a closing reveal forces slides 3-5 to be parsed as isolated artifacts and loses the strategic-alignment headline.

#### Content slide rules
- Catchy headline describing user benefit, not technical change
- "Who benefits:" subtitle identifying target audience
- **3 bullets max per slide** — readability over completeness
- Each bullet: **large headline lead** (block, ~1.35rem, accent blue) + **description below** (block, ~0.95rem, muted) — two-line rhythm, not inline
- **Repo labels go FIRST**: each bullet's `<span class="bullet-lead">` opens with a `<span class="repo-label repo-det">DET</span>` (or `repo-platform`, `repo-tenderdash`, `repo-dashcore`) pill as a visual prefix to the headline. Buried at the end of long descriptions they get missed; as a prefix they're scannable at a glance
- PR numbers as small muted pill-badges at end of description — NOT prominent
- Endpoint/API names in `<code>` tags
- All bullet titles must be **unique across the entire deck** — no duplicates

#### Bullet voice — value, not mechanics
Every `bullet-desc` opens with the **affected audience**: "Clients...", "Operators...", "QA...", "Wallet UI...", "Embedders...", "Release engineering...". State what *changes for that audience*, not what the PR refactored.

If you cannot articulate a user-visible benefit from the PR body, drop it from theme slides — let it appear only in closing-slide donut counts.

Example: PR "Backport dash-network-seeds bootstrap to SDK" → bullet: "Clients no longer ship hardcoded SDK endpoint lists" (not "Backports network-seeds module").

#### Framing checklist — positive claims, no overclaiming
This deck goes to a mixed exec/PM/engineering audience; every claim gets scrutinized. Before finalizing:

- **Audit every headline for implied-prior-brokenness.** Phrases like "closes gaps," "no longer confusing," "fixes the mess" imply the prior state was broken or embarrassing. Prefer a positive-framing equivalent that states what now exists: "all data migrated" (not "no data left behind"), "new stack merged" (not "closes gaps in the old one").
- **Never let a release-cadence claim imply a QA guarantee it doesn't have.** "Ships automatically when CI is green" describes a build gate, not a QA certification — don't word it as if a human/QA team signed off if that didn't happen.
- **Drop immature or not-well-tested work from headline pillars entirely** — don't downplay it, remove it. A pillar slide implies "this is done and load-bearing"; a feature the user flags as not-yet-validated (even if the underlying PR is substantial) belongs nowhere in the deck until it's hardened, not softened into a smaller bullet.

#### Audience jargon test
Bi-weekly audience is mixed: engineering + PM + leadership + cross-team. Internal-developer shorthand FAILS for this audience. Run every bullet through this filter:

- **Replace abbreviations with concrete release labels**: not "PV_11" / "PV_12+" / "v3.1-dev", but "Platform v3.0 testnet" / "Platform v3.1+". The audience tracks released versions, not internal protocol-version constants.
- **Replace type names with what they DO**: not "`Fetch::Query` vs `Fetch::Request`" or "`QueryContext`", but "the encoder now picks the right wire shape for the right network". Type names go in the `<code>` decoration of the description, not the bullet headline.
- **Replace wire-format jargon with consequence**: not "V0 wire (CBOR `where`) vs V1 (structured)", but "the legacy shape for v3.0 testnet, the structured shape for v3.1+".
- **Rule of thumb**: if a non-engineer would not recognize the term within 1 second, replace it. Symbol names belong in monospace decoration of the description, never in the headline lead.
- **Presenter-chosen version labels take precedence**: the presenter may relabel internal constants to public-facing release names (e.g. "Platform v4.0 devnet" for PRs that say `v3.1-dev`/`v3.0 testnet`). Use the label the user specifies. When a version appears on a slide, confirm the public-facing name with the user rather than lifting the literal constant from the PR.

#### Cause → consequence framing
When a single slide describes both an infrastructure investment (e.g. an e2e test campaign) AND the bug fixes it surfaced, lead with the investment and prefix each consequence bullet with an explicit causal phrase: *"Surfaced & guarded by the harness:"*, *"Also surfaced by:"*, *"Made possible by:"*. The audience reads top-down — cause must precede consequence, not the reverse. Trade-off: this contradicts the natural urge to put a 31k-LoC PR last as the "big finale" — resist it; the campaign is the *cause* of every other bullet on the slide.

#### Stage badges
- Add `<span class="stage-badge stage-alpha">In Progress</span>` to slide headings for features not yet production-ready
- CSS classes: `stage-alpha` (amber gradient) for alpha/in-progress, `stage-beta` (purple gradient) for beta
- Position: inline after heading text, auto-sized ~0.45em relative to heading

#### Status badge rules
- Each bullet gets exactly one status badge after the PR links
- Badge types: `badge-merged` (green), `badge-draft` (amber, for in-progress)
- **If a bullet references multiple PRs and at least one is not merged, mark the whole bullet as "in progress"** — the most conservative status wins
- Only mark as `merged` when ALL referenced PRs are merged

#### Closing slide: two-column layout (always)
Use `class="two-col"` grid with two `col-card` elements. See *Stats closer slide* in Slide HTML Patterns for the full layout including the mandatory bug-triage donut category split.

**Left card** — choose the most informative chart for the available data:
- **Test-coverage horizontal stacked bar** when an e2e/test-spec source is available (preferred when work touched the test surface)
- **PR donut** (merged / in progress / closed) for pure PR activity
- **Issue waterfall** (before → +created → −closed → after) when issue churn dominates

**Right card** — `Found-*` / bug-pin triage donut whenever bug-pin metrics exist. Mandatory 4-way split: `Fixed prod bug` (green) / `Has PR` (Dash blue) / `To triage` (amber) / `False positive` (purple). False-positive bucket includes test-framework-only fixes AND spec drift — these are NOT counted as production bug fixes.

**Chart type selection:**
- **Donut**: good when segments have comparable proportions (e.g., 60/30/10 split)
- **Waterfall**: better for before/after comparisons with small deltas (e.g., issue counts 60→62 with +3/−1). Donuts fail here because a 95%/5% split is visually meaningless

Each donut card: chart (170-180px), legend with colored dots + labels + counts + space-padded percentage.
Each waterfall: 4 bars (Before, +Created, −Closed, After) with zoomed Y-axis range, dashed connectors between bars, colored delta labels.

**Number-reconciliation rule**: when a concrete count appears in both the convergence diagram (slide #2) AND the closing donut, they MUST be identical. Always `grep` for the number in the rendered HTML before declaring done. When the triage tile uses a qualitative phrase instead of a count, no reconciliation is needed.

### Phase 3: Generate the HTML

Read template at `assets/presentation-template.html`. Replace `{{TITLE}}` and `{{SLIDES}}`.

1. First slide: `class="slide active"`, all others: `class="slide"`
2. `data-index` sequential from 0
3. Create timestamped directory: `presentations/YYYY-MM-DDTHHMMSS/`
4. Write as `index.html` inside that directory

### Phase 4: Preview and publish

The local HTTP server is an **author-side QA tool only** — never hand its URL to the user as the final deliverable.

```bash
python3 -m http.server 8244 --directory /home/ubuntu/git/daily-report/presentations/YYYY-MM-DDTHHMMSS
```

Run in background, use it for your own Playwright QA pass (see below), then **kill it**. Once the deck is verified, copy the finished `index.html` to the shared artifacts location and report that link instead:

```bash
mkdir -p /data/artifacts/daily-report/YYYY-MM-DD
cp /home/ubuntu/git/daily-report/presentations/YYYY-MM-DDTHHMMSS/index.html /data/artifacts/daily-report/YYYY-MM-DD/
```

Report: `http://agentic/daily-report/YYYY-MM-DD/index.html` (also reachable at `http://10.26.0.69/...` from `rocky`). This is nginx-served, persists across reboots, and needs no server process running on the user's end — matches the "self-contained, streamable" delivery model the deck itself is built for (see *No interactive-only affordances*). Re-copy on every edit round; the artifacts path is the one link to keep repeating back to the user, not a fresh `localhost` port each time.

#### Playwright QA (optional but recommended for SPA-init verification)

- Serve over `http://` — Playwright's sandbox blocks `file://`
- Navigate with paced delays (~200ms+ between keypresses) to avoid triggering the animation-lock queue unintentionally
- **`playwright-cli eval` gotcha**: any string containing `=>` is treated as an element-handler function. Pass a single `() => <expr>` arrow function. An IIFE like `(()=>{…})()` fails with "result is not a function".
- **`playwright-cli run-code` gotcha**: sandboxed — no `require`, no dynamic `import`. Do not use it for multi-step logic that needs Node APIs.
- **Save-Page-As round-trip**: capture `document.documentElement.outerHTML` via `eval`, write it to a temp file with Python (not `run-code`), serve on a fresh port, reopen — assert: slide resets to slide 0, dots not duplicated, no stale `exit-left` class, lightbox closed.

## Slide HTML Patterns

### Title slide

Logo uses the official Dash wordmark SVG in Dash blue (`#008DE4`), not white. Source: `dash.org/brand-guidelines` (CC BY 4.0).

```html
<section class="slide active" data-index="0">
  <div class="slide-inner slide-title">
    <div class="dash-logo">
      <svg viewBox="0 0 1438.15 421.17" xmlns="http://www.w3.org/2000/svg" style="height:56px;width:auto;fill:#008DE4;">
        <path d="M312.66,26.11H147.23l-13.71,76.63,149.28.18c73.52,0,95.26,26.69,94.64,71-.32,22.7-10.16,61.07-14.41,73.51-11.33,33.16-34.6,71-121.86,70.85l-145.11-.06L82.32,394.88H247.38c58.22,0,83-6.77,109.2-18.89,58.13-26.84,92.72-84.21,106.57-159.08C483.79,105.42,458.09,26.11,312.66,26.11Z"/>
        <path d="M824.28,395.06l11.66-66.63H977.26c11.1,0,18.88-6.67,20.54-17.77,2.22-11.11-3.33-17.77-14.43-17.77H915.8c-53.31,0-78.85-31.09-69.41-84.4s46.09-84.39,99.39-84.39h153.53l-12.21,66.63H951.79c-11.11,0-18.88,6.66-20.55,17.76-2.22,11.11,3.33,17.77,14.44,17.77h67.57c53.3,0,78.84,31.1,69.41,84.4s-45.54,84.4-98.84,84.4Z"/>
        <path d="M609.06,395.05c-87.74,0-127.17-47.76-111.62-135.5,15-87.73,71.64-135.49,159.37-135.49H812.3l-47.76,271ZM710.12,190.7h-40c-47.75,0-76,21.1-84.29,68.85-8.88,47.76,8.89,68.86,56.64,68.86h43.76Z"/>
        <path d="M1292,395.05l27.77-157.71c5-31.1-7.62-44.42-38.72-44.42h-44.22l-35.54,202.13h-90l65.07-368.94h90l-17.32,98h67.4c78.85,0,107.17,34.43,93.29,113.28l-27.77,157.71Z"/>
        <path d="M75.85,172c-43.33,0-49.54,28.24-53.64,45.3-5.37,22.35-7.13,31.4-7.13,31.4H184.46c43.33,0,49.54-28.24,53.64-45.3,5.37-22.35,7.13-31.4,7.13-31.4Z"/>
      </svg>
    </div>
    <h1>My Focus:<br>repo1, repo2</h1>
    <p class="meta">Date Range &middot; Author</p>
    <div class="title-rule"></div>
  </div>
  <div class="slide-credits">Co-authored by <a href="https://github.com/lklimek/claudius" target="_blank">Claudius the Magnificent</a> AI</div>
</section>
```

### Convergence diagram (mandatory slide #2)

The convergence diagram is the deck's strategic frame. Every PR this sprint feeds a single integration target via an umbrella component, broken into 2-4 thematic pillars. The diagram lives at `data-index="1"` (slide #2) — always, no exceptions.

**Color semantics — green and yellow are RESERVED for PR status.** When status-colored pills are used (see below), green (`#3fb950`) and yellow/amber (`#d29922`) must mean only merged / in-progress respectively — never structural decoration. Recolor any structural elements that would collide: Consumer tile stroke amber → purple `#a371f7`; `PILLAR N` labels amber → gray `#8b949e`; Triage tile stroke amber → gray `#6e7681` dashed; Validation band stroke green → Dash blue `#008DE4` dashed. Keep blue (umbrella, pillar titles, flow arrows) and gray (pillar borders) as structural colors. Add a compact status legend in the SVG's top-right: green swatch "merged", yellow swatch "in progress".

**PR-number pills** — render each `#NNNN` reference as a small rounded pill with a light tinted background colored by that PR's status: merged → fill `rgba(63,185,80,0.18)` text `#3fb950`; open/in-progress/draft → fill `rgba(217,153,34,0.18)` text `#d29922`. In SVG use `<rect rx>` sized to fit the number text, keep to one line per tile. The same pill style applies to `pr-links` spans on content slides.

**Required tile types** (top → bottom):

1. **Consumer / integration target** (purple `#a371f7` stroke, solid border) — the downstream app or consumer where everything lands. Typically DET, an SDK release, a wallet app. PR pill for the rewrite/integration PR (if any) sits inside. Keep the description to **one concise line (~≤340px text at 1920px)** inside the ~440px tile — do not inline per-PR action labels; let status pills carry per-PR identity.
2. **Upstream umbrella component** (Dash-blue `#008DE4` stroke, thicker 2.5px border) — the crate/library that owns the work. Audience sees: "this is the seam where all the work converges."
3. **Pillar tiles** (3-4, gray `#30363d` stroke, transparent fill) — one per content slide. Each tile carries: a `PILLAR N` gray `#8b949e` label, a Dash-blue title matching the next slide's heading, 1-2 lines of plain-English summary, the typed crate/symbol/feature in monospace blue `#79c0ff`, and the PR numbers as status-colored pills.
4. **Triage backlog tile (when WIP items exist)** — distinct styling: gray `#6e7681` dashed stroke (1.5px, `stroke-dasharray="4 3"`), subtly tinted fill, three large `…` glyph as visual cue, and either a verified exact count or a qualitative phrase (e.g. *"A few shielded findings still being triaged"*). **Only state a concrete count when it comes from a verified source** — do not fabricate numbers. Branch line from umbrella to this tile is dashed gray, signalling "incoming work, not yet landed."
5. **Validation band** (Dash blue `#008DE4` dashed stroke, optional) — bottom-spanning rect with `VALIDATED BY` label naming the e2e / test campaign. Dashed arrow up to the umbrella component. Blue avoids implying a "passing/green" board.

**Layout math** (viewBox 1180×600):
- Consumer box at top-center, ~400×74 at y=14
- Umbrella box centered below, ~500×78 at y=138, branch arrow above
- 4-tile pillar row at y=290, each tile 250×180, 30px gaps: x=40 / 320 / 600 / 880
- Validation band at y=526, ~740×50

**Audience-anchor rule**: each pillar slide's heading MUST have a `<span style="color:#d29922;font-size:0.55em;font-weight:700;letter-spacing:0.08em;display:block;margin-bottom:4px;">PILLAR N</span>` prefix matching its tile, so the audience never loses the link back to the map.

```html
<section class="slide" data-index="1">
  <div class="slide-inner slide-compact">
    <h2 class="slide-heading">The map: all roads lead to <target></h2>
    <p class="slide-subheading"><strong>Who benefits:</strong> The audience &mdash; here's where every PR this sprint converges</p>
    <svg width="1180" height="600" viewBox="0 0 1180 600" style="display:block;margin:0 auto;">
      <defs>
        <marker id="arrow-blue" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 Z" fill="#008DE4"/></marker>
        <marker id="arrow-muted" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 Z" fill="#8b949e"/></marker>
        <marker id="arrow-amber" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 Z" fill="#d29922"/></marker>
      </defs>
      <!-- Status legend (top-right) -->
      <rect x="1060" y="10" width="12" height="12" rx="2" fill="rgba(63,185,80,0.18)"/>
      <text x="1076" y="20" fill="#3fb950" font-size="10">merged</text>
      <rect x="1060" y="26" width="12" height="12" rx="2" fill="rgba(217,153,34,0.18)"/>
      <text x="1076" y="36" fill="#d29922" font-size="10">in progress</text>
      <!-- Consumer (purple — green/amber reserved for status) -->
      <rect x="390" y="14" width="400" height="74" rx="10" fill="rgba(163,113,247,0.08)" stroke="#a371f7" stroke-width="2"/>
      <text x="590" y="42" text-anchor="middle" fill="#a371f7" font-size="13" font-weight="700" letter-spacing="0.08em"><CONSUMER LABEL></text>
      <!-- Umbrella (blue) -->
      <rect x="340" y="138" width="500" height="78" rx="10" fill="rgba(0,141,228,0.10)" stroke="#008DE4" stroke-width="2.5"/>
      <text x="590" y="166" text-anchor="middle" fill="#008DE4" font-size="13" font-weight="700" letter-spacing="0.08em">UPSTREAM <SUMMARY></text>
      <!-- Pillar 1 (gray border) — repeat for pillar 2, 3 with x=320, x=600 -->
      <rect x="40" y="290" width="250" height="180" rx="8" fill="rgba(13,17,23,0.6)" stroke="#30363d" stroke-width="1.5"/>
      <text x="165" y="312" text-anchor="middle" fill="#8b949e" font-size="11" font-weight="700" letter-spacing="0.08em">PILLAR 1</text>
      <!-- PR pill example (merged) -->
      <rect x="105" y="318" width="48" height="14" rx="4" fill="rgba(63,185,80,0.18)"/>
      <text x="129" y="329" text-anchor="middle" fill="#3fb950" font-size="9" font-weight="600">#1234</text>
      <!-- Triage backlog (dashed gray — amber reserved for status) -->
      <rect x="880" y="290" width="250" height="180" rx="8" fill="rgba(110,118,129,0.06)" stroke="#6e7681" stroke-width="1.5" stroke-dasharray="4 3"/>
      <text x="1005" y="364" text-anchor="middle" fill="#e6edf3" font-size="28" font-weight="800" letter-spacing="0.18em">&hellip;</text>
      <text x="1005" y="394" text-anchor="middle" fill="#9eaab6" font-size="11">A few findings still being triaged</text>
      <!-- Validation band (blue dashed — not green, avoids "passing board" implication) -->
      <rect x="220" y="526" width="740" height="50" rx="8" fill="rgba(0,141,228,0.06)" stroke="#008DE4" stroke-width="1.5" stroke-dasharray="4 3"/>
      <text x="590" y="548" text-anchor="middle" fill="#008DE4" font-size="13" font-weight="700" letter-spacing="0.05em">VALIDATED BY</text>
    </svg>
  </div>
</section>
```

### Stats closer slide

Two-col closing slide pairing a test-coverage or PR/issue chart on the left with a bug-triage donut on the right when bug-pin data is available. Use `class="two-col"` with `grid-template-columns: 1.5fr 1fr` so the chart gets more horizontal room than the donut.

**Bug-triage donut categories (mandatory split):**
- `Fixed prod bug` (green `#3fb950`) — real production-code fixes, count only confirmed production bugs
- `Has PR` (Dash blue `#008DE4`) — bug has an open fix-in-flight PR
- `To triage` (amber `#d29922`) — unimplemented, blocked, or red-by-design
- `False positive` (purple `#a371f7`) — test-framework-only fixes + spec drift; explicitly NOT counted as production bug fixes

Footer must enumerate the specific pin IDs in each bucket so the audience can audit the classification.

**Internal-consistency rule**: when a concrete count appears in the convergence diagram's triage tile (slide #2), it MUST match the same count on the donut (closing slide). Search the file for both occurrences before declaring done. If the triage tile uses a qualitative phrase, no reconciliation is needed.

### Content slide

```html
<section class="slide" data-index="N">
  <div class="slide-inner">
    <h2 class="slide-heading"><span style="color:#d29922;font-size:0.55em;font-weight:700;letter-spacing:0.08em;display:block;margin-bottom:4px;">PILLAR N</span>Theme Title</h2>
    <p class="slide-subheading"><strong>Who benefits:</strong> Audience</p>
    <ul class="bullets">
      <li>
        <span class="bullet-dot"></span>
        <span class="bullet-body">
          <span class="bullet-lead"><span class="repo-label repo-det">DET</span> Large Headline</span>
          <span class="bullet-desc">Description text on a separate line below.</span>
          <span class="pr-links"><a href="URL" target="_blank">#123</a></span>
          <span class="status-badge badge-merged">merged</span>
        </span>
      </li>
    </ul>
  </div>
  <div class="slide-credits">Co-authored by <a href="https://github.com/lklimek/claudius" target="_blank">Claudius the Magnificent</a> AI</div>
</section>
```

Repo label classes: `repo-det` (blue), `repo-platform` (purple), `repo-tenderdash` (green), `repo-dashcore` (amber). The label sits **inside** `bullet-lead` as the first child so it renders inline with the headline, vertical-align middle. For multi-repo bullets, stack two pills (`<span class="repo-label repo-tenderdash">…</span><span class="repo-label repo-platform">…</span>`).

### Content slide — infographic card variant (preferred)

**User-confirmed preference, repeated across reviews: an SVG card row reads far better than the plain `<ul class="bullets">` list above.** Default to this pattern for pillar slides; fall back to the bullet list only when a slide genuinely has no room for a wide SVG (e.g. paired with a screenshot in `slide-split`).

Layout: one `<svg viewBox="0 0 1240 400">` holding N side-by-side cards (2-3 typical, one per bullet-equivalent). For 3 cards: `x = 10, 430, 850`, each `width="380" height="320"` at `y="10"` (40px gaps, symmetric 10px outer margins). For 2 cards, widen to keep the row filled rather than leaving a gap — e.g. `width="560"` at `x="10"`/`x="610"` (viewBox stays 1240 wide). Card shell: `rx="10" fill="rgba(13,17,23,0.55)" stroke="#30363d" stroke-width="1.5"`.

Per-card content, all positioned relative to the card's own `x` (call it `cx`), left padding 16px:

1. **Header row** (`y="24"`, height 20): repo-label pill top-left at `x=cx+16` — `rx="4" fill="#161b22"` with a tinted `stroke` matching the repo accent (e.g. `rgba(121,192,255,0.3)` for DET/blue, `rgba(163,113,247,0.3)` for Platform/purple), text centered, same accent fill, `font-size="12" font-weight="700"`. Status badge pill top-right, right edge at `cx+380-26`: merged → `rx="10" fill="#238636"` white text "MERGED"; in-progress → `fill="#d29922"` dark text "IN PROGRESS"; non-status CTA card → tinted `rgba(0,141,228,0.18)` blue text, custom label (e.g. "FEEDBACK WELCOME").
2. **Headline** — up to 2 lines, `x=cx+16`, `y="80"` and `y="104"`, `fill="#e6edf3" font-size="19" font-weight="800"`.
3. **Description** — up to 3 lines, `x=cx+16`, `y="132"/"152"/"172"`, `fill="#9eaab6" font-size="14"`. This is the audience-benefit sentence from *Bullet voice*, not a mechanics summary.
4. **Evidence** — up to 2 lines, `x=cx+16`, `y="200"/"218"`, `fill="#9eaab6" font-size="12" font-family="ui-monospace,Menlo,Consolas,monospace"` — the hard numbers from Phase 1c (LoC, test counts, pass rates).
5. **PR pills row** — `y="258"` height 20, `rx="4"`, stacked left-to-right from `cx+16` with ~8px gaps, width auto-fit to digit count (~42-58px). Merged → `fill="rgba(63,185,80,0.18)"` text `#3fb950`; in-progress → `fill="rgba(217,153,34,0.18)"` text `#d29922`. Wrap each pill in `<a href="PR_URL" target="_blank">` so it's clickable when the deck is opened directly (even though — see *No interactive-only affordances* — the audience watching a stream can't click it; the affordance is a bonus for direct viewers, not the primary access path).

```html
<rect x="{cx}" y="10" width="380" height="320" rx="10" fill="rgba(13,17,23,0.55)" stroke="#30363d" stroke-width="1.5"/>
<rect x="{cx+16}" y="24" width="40" height="20" rx="4" fill="#161b22" stroke="rgba(121,192,255,0.3)"/>
<text x="{cx+36}" y="38" text-anchor="middle" fill="#79c0ff" font-size="12" font-weight="700">DET</text>
<rect x="{cx+282}" y="24" width="72" height="20" rx="10" fill="#238636"/>
<text x="{cx+318}" y="38" text-anchor="middle" fill="#fff" font-size="12" font-weight="700" letter-spacing="0.04em">MERGED</text>
<text x="{cx+16}" y="80" fill="#e6edf3" font-size="19" font-weight="800">Headline line one</text>
<text x="{cx+16}" y="104" fill="#e6edf3" font-size="19" font-weight="800">Headline line two</text>
<text x="{cx+16}" y="132" fill="#9eaab6" font-size="14">Audience gets the benefit,</text>
<text x="{cx+16}" y="152" fill="#9eaab6" font-size="14">stated plainly across up to</text>
<text x="{cx+16}" y="172" fill="#9eaab6" font-size="14">three short lines.</text>
<text x="{cx+16}" y="200" fill="#9eaab6" font-size="12" font-family="ui-monospace,Menlo,Consolas,monospace">hard evidence line one &middot;</text>
<text x="{cx+16}" y="218" fill="#9eaab6" font-size="12" font-family="ui-monospace,Menlo,Consolas,monospace">hard evidence line two</text>
<a href="PR_URL" target="_blank"><rect x="{cx+16}" y="258" width="52" height="20" rx="4" fill="rgba(63,185,80,0.18)"/><text x="{cx+42}" y="272" text-anchor="middle" fill="#3fb950" font-size="12" font-weight="600" font-family="ui-monospace,Menlo,Consolas,monospace">#123</text></a>
```

A CTA/QR card can replace the third slot on a pillar row using the same 380×320 shell with `stroke="#008DE4"` and `fill="rgba(0,141,228,0.06)"` — see *QR code / scannable-link pattern* above for the QR image itself; place two side-by-side inside one card (`GET THE BUILD` / `REPORT AN ISSUE`) each with an 80×80 QR image in a white 90×90 rounded rect, and the full `github.com/org/repo/path` (two lines, no scheme needed — it's the complete typeable path that matters) printed underneath in muted monospace.

### Content slide with screenshot (split layout)

Use `slide-split` grid for slides with screenshots. Bullets on the left, screenshot on the right. Images can be local file refs (`./filename.png`) or base64-encoded for self-contained HTML.

```html
<section class="slide" data-index="N">
  <div class="slide-inner">
    <h2 class="slide-heading">Theme Title <span class="stage-badge stage-alpha">In Progress</span></h2>
    <p class="slide-subheading"><strong>Who benefits:</strong> Audience</p>
    <div class="slide-split">
      <div>
        <ul class="bullets">
          <!-- bullets here -->
        </ul>
        <!-- optional: tool-grid or other content under bullets -->
      </div>
      <div class="screenshot-wrap">
        <img src="./screenshot.png" alt="Description">
      </div>
    </div>
  </div>
  <div class="slide-credits">Co-authored by <a href="https://github.com/lklimek/claudius" target="_blank">Claudius the Magnificent</a> AI</div>
</section>
```

Screenshot behavior:
- Images scale to fill column width (`width: 100%`, no max-height cap)
- Hover shows blue border + zoom-in cursor
- Click opens fullscreen lightbox overlay (95vw/95vh), close with click or Escape
- Lightbox element + JS must be in template (see `presentation-template.html`)

### Tool grid (for listing available tools/APIs)

Compact 4-column grid, useful on agent/developer-facing slides. Place inside left column of `slide-split`, below bullets.

```html
<div class="tool-grid">
  <div><span class="tool-grid-group">Category</span></div>
  <!-- repeat for each category column -->
  <div><code>tool_name</code></div>
  <!-- repeat for each tool -->
</div>
```

Badge types:
- `badge-merged` — green solid (`#238636`), all PRs in bullet are merged
- `badge-draft` — amber (`#d29922`), text "in progress", at least one PR not merged
- `badge-open` — green outline, open PR
- `badge-waiting` — amber, awaiting review

### Test-coverage chart slide

Horizontal stacked-bar chart showing test coverage by functional area. Use `.slide-compact` on `.slide-inner` to reclaim vertical space.

Layout rules:
- **≤7 rows**; consolidate small all-todo areas into an "Other" row
- Labels right-aligned in a left column (`text-anchor="end"`); bars start ~20px right of longest label
- **Bar widths are absolute, not normalized**: `bar_width = bar_max_w × (row_total / max_total)`. Visual weight reflects scope — small areas read short.
- Stacked segments inside each bar: passing / failing / todo
- Palette: passing `#3fb950`, failing `#da3633`, todo `#8b949e`
- **No `rx` on data bars** (square corners). Legend swatches may use `rx="2"`
- Counts column on right: `<passing> / <failing> / <todo>`
- Footnote ≤2 lines at ~0.68rem; PR-pill credit row single-line via `flex-wrap:nowrap`

```html
<section class="slide" data-index="N">
  <div class="slide-inner slide-compact">
    <h2 class="slide-heading">Test Coverage by Area</h2>
    <p class="slide-subheading"><strong>Who benefits:</strong> QA &amp; release engineering</p>
    <svg width="680" height="220" viewBox="0 0 680 220" style="display:block;margin:0 auto;">
      <!-- label column ends at x=180; bars start at x=200 -->
      <!-- row 0: label -->
      <text x="178" y="22" text-anchor="end" fill="#8b949e" font-size="13">Area Name</text>
      <!-- passing segment (no rx) -->
      <rect x="200" y="8" width="PASS_W" height="18" fill="#3fb950"/>
      <!-- failing segment -->
      <rect x="200+PASS_W" y="8" width="FAIL_W" height="18" fill="#da3633"/>
      <!-- todo segment -->
      <rect x="200+PASS_W+FAIL_W" y="8" width="TODO_W" height="18" fill="#8b949e"/>
      <!-- counts -->
      <text x="650" y="22" text-anchor="end" fill="#8b949e" font-size="11">P / F / T</text>
      <!-- repeat for each row with y += 30 -->
      <!-- legend (rx="2" allowed on swatches) -->
      <rect x="200" y="BOTTOM" width="12" height="12" rx="2" fill="#3fb950"/>
      <text x="216" y="BOTTOM+10" fill="#8b949e" font-size="11">Passing</text>
      <rect x="280" y="BOTTOM" width="12" height="12" rx="2" fill="#da3633"/>
      <text x="296" y="BOTTOM+10" fill="#8b949e" font-size="11">Failing</text>
      <rect x="340" y="BOTTOM" width="12" height="12" rx="2" fill="#8b949e"/>
      <text x="356" y="BOTTOM+10" fill="#8b949e" font-size="11">Todo</text>
    </svg>
    <p style="font-size:0.68rem;color:#8b949e;margin-top:8px;">Footnote line 1. Footnote line 2.</p>
    <div class="pr-links" style="flex-wrap:nowrap">#PR credit</div>
  </div>
  <div class="slide-credits">Co-authored by <a href="https://github.com/lklimek/claudius" target="_blank">Claudius the Magnificent</a> AI</div>
</section>
```

### Closing slide (flexible column layout)

Use `two-col` or `three-col` grid. Mix donut and waterfall charts based on data.

**Donut math:** circumference = 2×π×44 = 276.46. Each segment: `stroke-dasharray="ARC 276.46"`. Rotation = -90 + (cumulative_prior / total) × 360.

**Waterfall math:** Pick a zoomed Y-axis range (e.g., 56–64 for values 60/62/63). Scale = chart_height / range. Bars: Before (baseline→start), +Created (floating green), −Closed (floating amber), After (baseline→end). Connect with dashed lines at transition points.

```html
<!-- Donut card (e.g., PR breakdown) -->
<div class="col-card">
  <h3>Pull Requests</h3>
  <div class="donut-wrap">
    <svg width="180" height="180" viewBox="0 0 120 120">
      <circle cx="60" cy="60" r="44" fill="none" stroke="#21262d" stroke-width="14"/>
      <circle cx="60" cy="60" r="44" fill="none" stroke="#008DE4" stroke-width="14"
        stroke-dasharray="ARC1 276.46" transform="rotate(-90 60 60)"/>
      <!-- more segments... -->
      <text x="60" y="56" text-anchor="middle" fill="#e6edf3" font-size="22" font-weight="800"
        font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">TOTAL</text>
      <text x="60" y="72" text-anchor="middle" fill="#8b949e" font-size="9"
        font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">PRs</text>
    </svg>
    <div class="donut-legend"><!-- legend-row items --></div>
  </div>
</div>

<!-- Waterfall card (e.g., issue tracker) -->
<div class="col-card">
  <h3>dash-evo-tool Issues</h3>
  <div style="display:flex;flex-direction:column;align-items:center;">
    <svg width="220" height="180" viewBox="0 0 220 180">
      <!-- Y-axis: zoomed range, dashed grid line at start value -->
      <!-- Bar 1: Before — blue, opacity 0.7 -->
      <!-- Bar 2: +Created — green, floating on top of Before -->
      <!-- Bar 3: −Closed — amber, floating, hanging from top -->
      <!-- Bar 4: After — blue, solid -->
      <!-- Dashed connectors between bar transitions -->
      <!-- X-axis labels: Before, Created, Closed, After -->
    </svg>
  </div>
  <p class="stat-breakdown" style="margin-top:4px;text-align:center">net <span>+N</span> open issues</p>
</div>
```

**Color palette for charts:**
- `#008DE4` — Dash blue (primary segments)
- `#a371f7` — purple (secondary segments)
- `#3fb950` — green (tertiary)
- `#d29922` — amber (quaternary)
- `#8b949e` — muted gray (catch-all "others")

## Layout Rules

- **Slide content pinned to top**: `justify-content: flex-start` with `padding: 100px 80px 60px` — headings stay at a fixed vertical position across slides (no vertical jumping)
- **Title slide exception**: vertically centered with `justify-content: center`
- **Credits line**: bottom-left of every slide, last child inside `<section>`
- **Page counter**: bottom-right (built into template JS)
- **Three-col grid**: `grid-template-columns: 1fr 1fr 1fr; gap: 24px`
- **Deck-wide font bump**: raising the root body `font-size` (e.g. 18px→20px) cascades proportionally through every `rem`/`em`-based CSS rule for free. It does NOT touch raw SVG `font-size="N"` attributes — those are absolute px and read the same regardless of body size, so a deck-wide legibility pass needs a second find-and-bump across every inline SVG `text` element.

## Design Constraints

- **Self-contained**: all CSS/JS inline in single HTML file. Images can be local file refs (`./`) or base64-encoded
- **Target resolution**: 1920×1080, responsive down to 1280px
- **3 bullets per slide max** — readability over completeness
- **Bullet layout**: large headline (block, accent blue) + description below (block, muted) — NOT inline `lead — description`
- **PR links are subtle** — small monospace pills in muted gray, not the visual focus
- **Theme headlines** describe user outcomes, not technical changes
- **Code/endpoint names** use `<code>` tags for monospace treatment
- **Logo**: official Dash wordmark SVG in **Dash blue** (`#008DE4`), not white
- **Screenshots**: scale to fill column width, open fullscreen lightbox on click
- **Stage badges**: amber "In Progress" / purple "Beta" pills on headings for pre-release features
- **Save-Page-As idempotency**: Chrome serializes post-JS DOM, so a saved deck arrives with stale `.active`/`.exit-left` classes, appended dot buttons, and open lightbox state. The nav init block MUST be idempotent: call `dotsEl.replaceChildren()`, reset every slide's `.active`/`.exit-left` to defaults, clear lightbox state, and sync the counter. Test by round-tripping `document.documentElement.outerHTML` to a temp port.
- **Animation lock queue**: nav JS MUST NOT silently drop keypresses during transitions. Chrome auto-repeat (~50ms) vs animation (~480ms) means rapid presses feel frozen if dropped. Pattern: `let pendingDelta = 0`; when called while `animating`, set `pendingDelta = Math.sign(target - current)`; replay on animation complete; reset.
- **No interactive-only affordances**: this deck is typically delivered via screen-share/video stream, not driven by the viewer. Anything that looks clickable (a "Learn more" button, a CTA link styled as a button) is non-functional for the audience — replace with a **QR code** and/or a **full, plain-text URL** they can type or scan. Never show a shortened/truncated path (e.g. just `releases` or `…/releases`) under a QR code — it reads as a hint, not a typeable address. The scheme (`https://`) can be omitted, but the full `domain/org/repo/path` must be shown — that's the complete, unambiguous, typeable address.

### QR code / scannable-link pattern

For a slide that needs a scannable destination (release page, issue tracker, docs), fetch a QR PNG at **build time** (not at view time — the deck must stay self-contained) and inline it as base64:

```bash
curl -s "https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' 'https://full/url/here')" -o /tmp/qr-name.png
base64 -w0 /tmp/qr-name.png > /tmp/qr-name.b64
```

Then `Read` the `.b64` file and paste its exact content into `<img src="data:image/png;base64,...">` — **never** type/recall base64 image data from memory; always read it from the file you just generated (a hand-typed or "recalled" base64 string decodes as noise or a corrupt/blank image, and this is easy to get wrong silently). Pair every QR code with the full URL in plain, selectable/legible text underneath — some viewers will prefer to type it over scanning.

## Assets

- `assets/presentation-template.html` — HTML/CSS/JS skeleton with `{{TITLE}}` and `{{SLIDES}}` placeholders
- `presentations/assets/` — shared Dash brand SVGs (CC BY 4.0 from dash.org/brand-guidelines)
- `scripts/agent_stats.py` — AI agent usage stats miner (reads `~/.claude/` session data)
