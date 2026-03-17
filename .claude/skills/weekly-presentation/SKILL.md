---
name: weekly-presentation
description: "Generate a self-contained HTML slide deck from a daily-report markdown output. Use when the user asks to create a weekly presentation, sprint report slides, or an HTML presentation of their PR activity. Triggers on: 'create presentation', 'make slides', 'weekly presentation', 'sprint report presentation', 'HTML slides'."
allowed-tools: ["Bash", "Read", "Write", "Glob", "Agent"]
---

# Weekly Presentation

Generate a self-contained HTML slide deck summarizing PR activity from `daily-report` output. Dash-branded dark theme, arrow-key navigation, progress dots, touch support.

## Workflow

### Phase 1: Generate the Report

```bash
cd /home/ubuntu/git/daily-report
python -m daily_report --from YYYY-MM-DD --to YYYY-MM-DD --repos-dir /home/ubuntu/git --org dashpay
```

- Default scope: `dashpay` org only. Broaden only if user explicitly asks.
- Default date range: last 2 weeks, **Wednesday to Tuesday** (sprint cycle). Calculate the most recent past Tuesday as `--to`, two Wednesdays back as `--from`.
- **Exclude stale PRs**: Only include PRs that had actual activity (commits, reviews, status changes) within the date range. PRs that were merely open/waiting with no activity during the period must be excluded — they clutter the presentation with non-progress.
- Skip if report output is already provided.

### Phase 1b: Gather AI Agent Stats

```bash
python3 scripts/agent_stats.py --from YYYY-MM-DD --to YYYY-MM-DD --projects "dash-evo-tool|platform|tenderdash|rust-dashcore"
```

- Default scope: dashpay projects only (same filter as report).
- Token totals include cache tokens (cache_creation + cache_read) — these are the bulk.
- Use named agent identities in output: "Claudius the Magnificent" (orchestrator), "Bilby the Developer" (developer-bilby), "Explorer scouts" (Explore), etc.
- Present with grandeur — these are proof of magnificence.

### Phase 2: Design the Slide Content

Analyze the consolidated report and group PRs into **user-facing themes** (not repo-by-repo). Target 4-6 content slides:

1. **Title slide** — "My Progress: repo1, repo2", date range, author
2. **2-4 content slides** — themed around user benefits, grouping related PRs across repos
3. **Closing slide** — three donut charts side by side: PR breakdown, AI agent activity, model usage

#### Content slide rules
- Catchy headline describing user benefit, not technical change
- "Who benefits:" subtitle identifying target audience
- 3-4 bullets max per slide
- Each bullet: **bold lead-in** (accent blue) + plain-language explanation
- PR numbers as small muted pill-badges at end of bullet — NOT prominent
- Endpoint/API names in `<code>` tags

#### Status badge rules
- Each bullet gets exactly one status badge after the PR links
- Badge types: `badge-merged` (green), `badge-draft` (amber, for in-progress)
- **If a bullet references multiple PRs and at least one is not merged, mark the whole bullet as "in progress"** — the most conservative status wins
- Only mark as `merged` when ALL referenced PRs are merged

#### Closing slide: three-column layout
Use `class="three-col"` grid with three `col-card` elements:
1. **Pull Requests** — donut chart of PR categories (direct, AI agent, dependency updates)
2. **AI Agent Activity** — donut chart of token usage by agent type, with named identities
3. **Models** — donut chart of token usage by model (Opus, Sonnet, Haiku)

Each card: donut (180×180), legend with colored dots + labels + counts + space-padded percentage (e.g. `799M (29%)`, `2.3B (82%)`, `224M ( 8%)`), subtitle below.

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
    <h1>My Progress:<br>repo1, repo2</h1>
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
          <span class="bullet-lead">Bold lead</span> — Description.
          <span class="pr-links"><a href="URL" target="_blank">#123</a></span>
          <span class="status-badge badge-merged">merged</span>
        </span>
      </li>
    </ul>
  </div>
  <div class="slide-credits">Co-authored by <a href="https://github.com/lklimek/claudius" target="_blank">Claudius the Magnificent</a> AI</div>
</section>
```

Badge types:
- `badge-merged` — green solid (`#238636`), all PRs in bullet are merged
- `badge-draft` — amber (`#d29922`), text "in progress", at least one PR not merged
- `badge-open` — green outline, open PR
- `badge-waiting` — amber, awaiting review

### Closing slide (three-column with donut charts)

Donut math: circumference = 2×π×44 = 276.46. Each segment: `stroke-dasharray="ARC 276.46"`. Rotation = -90 + (cumulative_prior / total) × 360.

```html
<section class="slide" data-index="N">
  <div class="slide-inner">
    <h2 class="slide-heading">By the Numbers</h2>
    <div class="three-col">
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
      <div class="col-card">
        <h3>AI Agent Activity</h3>
        <!-- Same donut pattern, tokens in center, agent names in legend -->
      </div>
      <div class="col-card">
        <h3>Models</h3>
        <!-- Same donut pattern, model names in legend -->
      </div>
    </div>
  </div>
  <div class="slide-credits">Co-authored by <a href="https://github.com/lklimek/claudius" target="_blank">Claudius the Magnificent</a> AI</div>
</section>
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

- **Self-contained**: zero external dependencies — all CSS/JS inline in single HTML file
- **Target resolution**: 1920×1080, responsive down to 1280px
- **3-4 bullets per slide max** — readability over completeness
- **PR links are subtle** — small monospace pills in muted gray, not the visual focus
- **Theme headlines** describe user outcomes, not technical changes
- **Code/endpoint names** use `<code>` tags for monospace treatment
- **Logo**: official Dash wordmark SVG in **Dash blue** (`#008DE4`), not white

## Assets

- `assets/presentation-template.html` — HTML/CSS/JS skeleton with `{{TITLE}}` and `{{SLIDES}}` placeholders
- `presentations/assets/` — shared Dash brand SVGs (CC BY 4.0 from dash.org/brand-guidelines)
- `scripts/agent_stats.py` — AI agent usage stats miner (reads `~/.claude/` session data)
