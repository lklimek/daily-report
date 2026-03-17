---
name: weekly-presentation
description: "Generate a self-contained HTML slide deck from a daily-report markdown output. Use when the user asks to create a weekly presentation, sprint report slides, or an HTML presentation of their PR activity. Triggers on: 'create presentation', 'make slides', 'weekly presentation', 'sprint report presentation', 'HTML slides'."
allowed-tools: ["Bash", "Read", "Write", "Glob", "Agent"]
---

# Weekly Presentation

Generate a self-contained HTML slide deck summarizing PR activity from `daily-report` output. The presentation uses a Dash-branded dark theme with arrow-key navigation, progress dots, and touch support.

## Workflow

### Phase 1: Generate the Report

Run `daily-report` with `--consolidate` and a date range covering the sprint/week:

```bash
cd /home/ubuntu/git/daily-report
python -m daily_report --from YYYY-MM-DD --to YYYY-MM-DD --consolidate --repos-dir /home/ubuntu/git
```

If the user provides a date range, use it. If not, default to the last 7 days. If a report output is already available (piped or pasted), skip this step.

Capture the markdown output — this is the raw material for slides.

### Phase 2: Design the Slide Content

Analyze the consolidated report and group PRs into **user-facing themes** (not repo-by-repo). Target 4-6 slides total:

1. **Title slide** — headline, date range, author
2. **2-4 content slides** — each themed around a user benefit (e.g., "Your Wallet, Your Way", "A Network That Heals Itself"). Group related PRs from different repos under the same theme when they serve the same user outcome.
3. **Closing slide** — stats (PR counts, repo counts) + "coming soon" items from open/waiting PRs

For each content slide:
- Write a catchy headline that describes the user benefit, not the technical change
- Add "Who benefits:" subtitle identifying the target audience
- Write 3-4 bullets max per slide. Each bullet has a **bold lead-in** in accent blue followed by a plain-language explanation
- PR numbers appear as small muted pill-badges at the end of each bullet — NOT prominent
- Endpoint/API names use `<code>` formatting

### Phase 3: Generate the HTML

Read the template at `assets/presentation-template.html` (relative to this skill's directory). The template contains:
- Complete CSS styling (dark theme, Dash blue `#008DE4` accent)
- Navigation JS (arrow keys, dots, touch swipe, slide counter)
- HTML comments documenting the three slide types: TITLE, CONTENT, and STATS

To generate the presentation:
1. Read the template file
2. Replace `{{TITLE}}` with the presentation title (for the browser tab)
3. Replace `{{SLIDES}}` with the generated slide `<section>` elements
4. First slide gets `class="slide active"`, all others get `class="slide"`
5. `data-index` attributes must be sequential starting at 0
6. Create a timestamped directory: `presentations/YYYY-MM-DDTHHMMSS/`
7. Write the final HTML as `index.html` inside that directory

### Slide HTML Patterns

**Title slide:**
```html
<section class="slide active" data-index="0">
  <div class="slide-inner slide-title">
    <div class="dash-logo">
      <div class="dash-logo-mark">
        <svg viewBox="0 0 550 550" xmlns="http://www.w3.org/2000/svg">
          <path d="M354.55,66.65H167.65l-15.5,86.6,168.7.2c83.1,0,107.6,30.2,106.9,80.2-.4,25.6-11.5,69-16.3,83.1-12.8,37.5-39.1,80.2-137.7,80.1l-164-.1-15.5,86.7h186.5c65.8,0,93.7-7.7,123.4-21.3,65.7-30.5,104.8-95.3,120.5-179.9,23.3-126-5.7-215.6-170.1-215.6"/>
          <path d="M87,231.55c-49,0-56,31.9-60.6,51.2-6.1,25.2-8.1,35.5-8.1,35.5h191.4c49,0,56-31.9,60.6-51.2,6.1-25.2,8.1-35.5,8.1-35.5Z"/>
        </svg>
      </div>
      <span class="dash-logo-text">DASH</span>
    </div>
    <h1>Headline</h1>
    <p class="meta">Date Range &middot; Author</p>
    <div class="title-rule"></div>
  </div>
</section>
```

**Content slide (bullets with PR links and status badges):**
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
</section>
```

Badge types: `badge-merged` (green solid), `badge-open` (green outline), `badge-waiting` (amber), `badge-draft` (gray). Choose based on PR status — merged PRs get `badge-merged`, PRs still awaiting review get `badge-waiting`, in-progress/draft get `badge-draft`.

**Stats/closing slide (two-column cards with SVG donut chart):**

Donut math: circumference = 2*pi*44 = 276.46. Each segment: `stroke-dasharray="ARC 276.46"`. Rotation = -90 + (cumulative_prior / total) * 360.

```html
<section class="slide" data-index="N">
  <div class="slide-inner">
    <h2 class="slide-heading">By the Numbers &amp; What's Coming</h2>
    <div class="two-col">
      <div class="col-card">
        <h3>Shipped this sprint</h3>
        <div class="donut-wrap">
          <svg width="120" height="120" viewBox="0 0 120 120" aria-label="PR breakdown donut chart">
            <circle cx="60" cy="60" r="44" fill="none" stroke="#21262d" stroke-width="14"/>
            <!-- Segment 1 (direct, blue): ARC = N1/total*276.46, starts at -90deg -->
            <circle cx="60" cy="60" r="44" fill="none" stroke="#008DE4" stroke-width="14"
              stroke-dasharray="ARC1 276.46" transform="rotate(-90 60 60)"/>
            <!-- Segment 2 (AI agents, purple): ARC = N2/total*276.46 -->
            <circle cx="60" cy="60" r="44" fill="none" stroke="#a371f7" stroke-width="14"
              stroke-dasharray="ARC2 276.46" transform="rotate(DEG2 60 60)"/>
            <!-- Segment 3 (deps, gray): ARC = N3/total*276.46 -->
            <circle cx="60" cy="60" r="44" fill="none" stroke="#8b949e" stroke-width="14"
              stroke-dasharray="ARC3 276.46" transform="rotate(DEG3 60 60)"/>
            <text x="60" y="56" text-anchor="middle" fill="#e6edf3" font-size="22" font-weight="800"
              font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">TOTAL</text>
            <text x="60" y="72" text-anchor="middle" fill="#8b949e" font-size="9"
              font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">PRs</text>
          </svg>
          <div class="donut-legend">
            <div class="legend-row">
              <span class="legend-dot" style="background:#008DE4"></span>
              <span>Direct contributions</span>
              <span class="legend-count">N1</span>
            </div>
            <div class="legend-row">
              <span class="legend-dot" style="background:#a371f7"></span>
              <span>AI agent PRs</span>
              <span class="legend-count">N2</span>
            </div>
            <div class="legend-row">
              <span class="legend-dot" style="background:#8b949e"></span>
              <span>Dependency updates</span>
              <span class="legend-count">N3</span>
            </div>
          </div>
        </div>
        <p class="stat-breakdown" style="margin-top:0">across <span>N</span> repos</p>
      </div>
      <div class="col-card">
        <h3>Coming soon</h3>
        <ul class="coming-list">
          <li><span class="coming-dot"></span> Item</li>
        </ul>
        <div class="roadmap-placeholder">Roadmap teaser — placeholder</div>
      </div>
    </div>
  </div>
</section>
```

### Phase 4: Preview

After writing `index.html`, start a local HTTP server serving only the presentation directory:

```bash
python3 -m http.server 8244 --directory /home/ubuntu/git/daily-report/presentations/YYYY-MM-DDTHHMMSS
```

Run this in the background. Tell the user the URL: `http://localhost:8244/`

This ensures only the presentation file is exposed — not the rest of the project.

If the user requests a different port, kill the old server and restart on the requested port.

## Design Constraints

- **Self-contained**: zero external dependencies — all CSS/JS inline in single HTML file
- **Target resolution**: 1920x1080, responsive down to 1280px
- **3-4 bullets per slide max** — readability over completeness
- **PR links are subtle** — small monospace pills in muted gray, not the visual focus
- **Theme headlines** describe user outcomes, not technical changes
- **Code/endpoint names** use `<code>` tags for monospace treatment

## Assets

- `assets/presentation-template.html` — complete HTML/CSS/JS skeleton with `{{TITLE}}` and `{{SLIDES}}` placeholders. The Dash "D" logo uses official SVG paths from dash.org/brand-guidelines (CC BY 4.0).

### Shared presentation assets

The `presentations/assets/` directory (in the project root) contains official Dash brand assets shared across all generated presentations:
- `dash-d-white.svg` — white Dash "D" mark
- `dash-d-blue.svg` — blue Dash "D" mark
- `dash-logo-full.svg` — full Dash wordmark

Source: https://www.dash.org/brand-guidelines/ (Creative Commons Attribution 4.0 International)
