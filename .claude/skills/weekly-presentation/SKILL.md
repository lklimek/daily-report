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
python -m daily_report --from YYYY-MM-DD --to YYYY-MM-DD \
  --repos-dir /home/ubuntu/git --org dashpay > /tmp/report-primary.md

# Claudius the Magnificent AI account
python -m daily_report --from YYYY-MM-DD --to YYYY-MM-DD \
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

### Phase 2: Design the Slide Content

Analyze the consolidated report and group PRs into **user-facing themes** (not repo-by-repo). Target 4-6 content slides:

1. **Title slide** — "My Focus: repo1, repo2", date range, author
2. **2-4 content slides** — themed around user benefits, grouping related PRs across repos
3. **Closing slide** — two-column layout: PR donut (merged/in-progress/closed) + issue waterfall (before→created→closed→after)

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
Use `class="two-col"` grid with two `col-card` elements:

1. **Pull Requests** — donut chart of PR status (merged, in progress, closed)
2. **Issue Tracker** — waterfall chart showing before → created → closed → after

**Chart type selection:**
- **Donut**: good when segments have comparable proportions (e.g., 60/30/10 split)
- **Waterfall**: better for before/after comparisons with small deltas (e.g., issue counts 60→62 with +3/−1). Donuts fail here because a 95%/5% split is visually meaningless

Each donut card: chart (180×180), legend with colored dots + labels + counts + space-padded percentage.
Each waterfall: 4 bars (Before, +Created, −Closed, After) with zoomed Y-axis range, dashed connectors between bars, colored delta labels.

### Phase 3: Generate the HTML

Read template at `assets/presentation-template.html`. Replace `{{TITLE}}` and `{{SLIDES}}`.

1. First slide: `class="slide active"`, all others: `class="slide"`
2. `data-index` sequential from 0
3. Create timestamped directory: `presentations/YYYY-MM-DDTHHMMSS/`
4. Write as `index.html` inside that directory

### Phase 4: Preview

```bash
python3 -m http.server 8244 --directory /home/ubuntu/git/daily-report/presentations/YYYY-MM-DDTHHMMSS
```

Run in background. Tell user: `http://localhost:8244/`
Only the presentation directory is exposed. Kill and restart on different port if requested.

#### Playwright QA (optional but recommended for SPA-init verification)

- Serve over `http://` — Playwright's sandbox blocks `file://`
- Navigate with paced delays (~200ms+ between keypresses) to avoid triggering the animation-lock queue unintentionally
- Simulate Save-Page-As round-trip: capture `document.documentElement.outerHTML` after navigating to a mid-deck slide, write it to a temp file, serve on a fresh port, reopen — verify slide resets to slide 0 correctly

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

### Content slide

```html
<section class="slide" data-index="N">
  <div class="slide-inner">
    <h2 class="slide-heading">Theme Title</h2>
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

## Assets

- `assets/presentation-template.html` — HTML/CSS/JS skeleton with `{{TITLE}}` and `{{SLIDES}}` placeholders
- `presentations/assets/` — shared Dash brand SVGs (CC BY 4.0 from dash.org/brand-guidelines)
- `scripts/agent_stats.py` — AI agent usage stats miner (reads `~/.claude/` session data)
