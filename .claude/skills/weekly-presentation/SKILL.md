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

Work dir: `/data/tmp/weekly-<to-date>/` (reports, PR JSON, generator, screenshots — never `/tmp`). Run the tool twice and merge the two markdown reports before designing slides:

```bash
cd /home/ubuntu/git/daily-report

# Primary user (authenticated gh user)
python3 -m daily_report --from YYYY-MM-DD --to YYYY-MM-DD \
  --repos-dir /home/ubuntu/git --org dashpay > /data/tmp/weekly-<to-date>/report-primary.md

# Claudius the Magnificent AI account
python3 -m daily_report --from YYYY-MM-DD --to YYYY-MM-DD \
  --repos-dir /home/ubuntu/git --org dashpay \
  --user Claudius-Maginificent > /data/tmp/weekly-<to-date>/report-claudius.md
```

Then read both files and treat the union as the input for Phase 2. Deduplicate PRs that appear in both (same `#number` in same repo) — a PR co-authored by both identities should only be presented once. Attribute it to whichever identity actually opened it (the `author` field), and note the AI co-authorship if relevant to the slide narrative.

- Default scope: `dashpay` org only. Broaden only if user explicitly asks.
- **Date range derivation** — derive `--from`/`--to` from the previous deck, not a fixed "2 weeks back" formula:
  1. Anchor on the previous deck (reliable): `ls presentations/ | sort | tail -1` gives the most recent `YYYY-MM-DDTHHMMSS` directory. The date prefix is the previous `--to`.
  2. New `--from` = previous_to + 1 day. New `--to` = most recent past Tuesday on or before today (sprint cycle is bi-weekly Wed→Tue). If today IS a Tuesday and previous deck is ≥14 days ago, use today.
  3. Fallback to "last 2 weeks Wed→Tue" only when no `presentations/` directory exists yet.
- **Exclude stale PRs**: Only include PRs that had actual activity (commits, reviews, status changes) within the date range. PRs that were merely open/waiting with no activity during the period must be excluded — they clutter the presentation with non-progress.
- Skip if report output is already provided for both identities.

### Phase 1b: Summary counts (mandatory)

Per focus repo, via GitHub search (`gh api -X GET search/issues -f q='...' --jq .total_count`), range `FROM..TO` inclusive:

- **PRs** — authors `lklimek` + `Claudius-Maginificent`, summed:
  - created: `repo:dashpay/R is:pr author:A created:FROM..TO`
  - merged: `repo:dashpay/R is:pr author:A merged:FROM..TO` (may include PRs opened earlier). The search also counts merges into stacked feature branches — check `baseRefName`: work merged only into a feature branch whose PR is still open is **in progress**, not shipped. A repo whose merges all landed on feature branches gets no Summary block, only an `IN PROGRESS` card.
  - closed (unmerged): `repo:dashpay/R is:pr author:A is:unmerged is:closed closed:FROM..TO`
- **Issues** — whole repo, `is:issue`:
  - opened: `repo:dashpay/R is:issue created:FROM..TO`; closed: `... is:issue closed:FROM..TO`
  - open after: `... is:issue is:open` (confirm nothing opened/closed after TO); open before = after − opened + closed. Cross-check "before" against the previous deck's "after".
  - Verify any `0` by listing (`gh issue list --state closed --search "closed:FROM..TO"`).
- **Releases** in range: `gh release list --repo dashpay/R`.

### Phase 1c: Read Every PR Body (mandatory)

**Do not write a single bullet from PR titles alone.** PR titles compress; bodies have the user-impact, the numbers, and the gotchas. Skipping this step produces generic, undersold slides.

For every PR you intend to put on a slide (authored *and* notable reviews), fetch the body via `gh`:

```bash
gh pr view <NUMBER> --repo dashpay/<REPO> --json title,state,isDraft,additions,deletions,body,author,createdAt,mergedAt,url
```

Batch them with `xargs -P 8` or parallel `Bash` calls — they're independent and cheap. Save raw JSON to `/data/tmp/weekly-<to-date>/pr/<repo>-<num>.json` so reruns don't re-fetch.

For each PR, extract three things and use them when writing the bullet:

1. **What changed (one line)** — the *user-visible* change, not the file diff. Bodies usually have a "What was done" or "Summary" section; lift the lede.
2. **Hard evidence** — concrete numbers ("passes on testnet in 24.76s", "+13.4k LoC", "reduces 100→16", "first end-to-end transfer"). These are what make a slide feel real.
3. **Sibling/dependency relationships** — bodies often reveal that PR-A *contains* PR-B's fix, or that PR-A is a doc-PR for crate-X built in PR-B. Group siblings into the same bullet (or attribute correctly across bullets) instead of double-counting.

If a PR body is empty or thin (dependabot, mechanical bumps, broken-fetch entries), it counts in the summary numbers but gets no card.

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

Fixed structure:

1. **Title** — "My Focus: repo1 & repo2", date range, author.
2. **Summary** — one block per focus repo: PRs created / merged / closed, one plain line on what the period was about, releases in period ("2 releases: 1.8.0 and 1.8.1"), issues open-before → +opened → −closed → open-after. All numbers one font size, all labels one size. Replaces any stats closer.
3. **One slide per focus repo** — each theme is one card (3 max), no `PILLAR N` labels. Infra repos (e.g. Tenderdash) add a release timeline above the cards. A repo with 2–3 big themes gets **one slide per theme** instead (see *Theme slides*). Smaller supporting repos may share one "Foundations" card slide.

No convergence map, no separate closing stats slide, **no agenda/overview slide**.

#### Theme slides (user-confirmed, presented well)
An overview slide that lists the themes before the theme slides is a third repetition of the same content — the user rejects it. Instead:
- The Summary one-liner names the themes using **exactly** the theme-slide headings (it is the advance organizer; the presenter says it once).
- Each theme slide carries an eyebrow strip above the heading: `DASH EVO TOOL  1 Theme A / 2 Theme B / 3 Theme C`, current theme highlighted Dash blue, others muted. Orients the viewer without spoken signposting.
- One theme per slide; each detail appears once in the deck.
- A call to action (e.g. `GET THE BUILD`) goes on the **last** slide of the section, after the audience has seen what they get.
- Summary for a repo with no release in the period: "Latest release: X".

#### Plain language (strict)
Mixed audience (engineering, PM, leadership). Every heading and card: short plain outcome, few words, no jargon, no clever or enigmatic phrasing, no hedging.

- "Steadier sync, bounded resources" → **"Hardening and performance"**; "Nodes finish syncing without a restart" → **"Faster initial node sync"**.
- Card = title (2–4 words) + one 2–3 line sentence + optional one short extra line. Name the audience only in the slide's "Who benefits" line.
- No type/crate/constant names, no internal version constants — use public release labels the user confirms (e.g. "Platform v4.2 beta").
- Positive framing: say what now exists, not that the old state was broken. Build gates are not QA sign-off.
- Drop immature/unvalidated work entirely — don't soften it. Omit security-sensitive in-review fixes; fold into a general "hardening" line.

#### Cards
- Group related PRs (bodies reveal siblings) into one card; PR numbers as small status-coloured pills.
- Status: `MERGED` only when every PR in the card is merged; otherwise `IN PROGRESS`.

### Phase 3: Generate the HTML

Write a Python generator `/data/tmp/weekly-<to-date>/gen.py` that fills `assets/presentation-template.html` (`{{TITLE}}`, `{{SLIDES}}`) — reruns after feedback are then cheap. First slide `class="slide active"`, `data-index` sequential from 0. Output `presentations/YYYY-MM-DDTHHMMSS/index.html`; regenerate into the same dir on revisions. Start from the previous deck's generator if one exists (`/data/artifacts/daily-report/<prev-date>/src/gen.py`); when done, persist `gen.py` and its inputs (reports, digests, counts) to `/data/artifacts/daily-report/<to-date>/src/` — `/data/tmp` is wiped on reboot.

**Variants**: keep alternates side by side as `index-<variant>.html` in both the presentations dir and the artifacts dir (e.g. `index-no-weekly.html`), each with its own generator copy (`gen-<variant>.py`).

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

Report: `http://agentic/artifacts/daily-report/YYYY-MM-DD/index.html` (nginx redirects the shorter `http://agentic/daily-report/...` there with a 302; verify with `curl -sL -o /dev/null -w '%{http_code} %{url_effective}'`; also reachable at `http://10.26.0.69/...` from `rocky`). This is nginx-served, persists across reboots, and needs no server process running on the user's end — matches the "self-contained, streamable" delivery model the deck itself is built for (see *No interactive-only affordances*). Copy variants the same way (`index-<variant>.html`). Re-copy on every edit round; the artifacts path is the one link to keep repeating back to the user, not a fresh `localhost` port each time.

#### Playwright QA (optional but recommended for SPA-init verification)

- Serve over `http://` — Playwright's sandbox blocks `file://`
- Navigate with paced delays (~200ms+ between keypresses) to avoid triggering the animation-lock queue unintentionally
- **`playwright-cli eval` gotcha**: any string containing `=>` is treated as an element-handler function. Pass a single `() => <expr>` arrow function. An IIFE like `(()=>{…})()` fails with "result is not a function".
- **`playwright-cli run-code` gotcha**: sandboxed — no `require`, no dynamic `import`. Do not use it for multi-step logic that needs Node APIs.
- **Fit check**: at 1920×1080, 1920×950, 1536×864 and 1280×720, `eval` each `.slide-inner` bottom (`getBoundingClientRect().bottom`) and `scrollHeight > clientHeight`; content must end above the progress dots (~viewport height − 60px). Look at a screenshot of every screenshot-bearing slide.
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
</section>
```

### Summary slide

`two-col` of `col-card`s, one per repo. Top row: three numbers (PRs created / merged / closed) in flex `space-around`; then the plain one-liner + muted releases line; then a divider and an `ISSUES` row `before → +opened → −closed → after` with arrows. Use one number size (2.2rem, weight 800) and one label size (0.95rem, muted) for both rows. Colors: created/opened `#58a6ff`, merged/closed-issues `#3fb950`, closed PRs `#8b949e`.

### Release timeline (infra repo slide)

SVG `viewBox="0 0 1240 112"` above the cards: released milestones = solid Dash-blue line + filled `#008DE4` circles, date above, label + short sub-line below, PR pills under. Next planned version = dashed gray line (`stroke-dasharray="4 4"`) + hollow dashed marker, date "Next" unless a verified target date exists (milestone/release PR).

### Content slide

```html
<section class="slide" data-index="N">
  <div class="slide-inner">
    <h2 class="slide-heading">Repo Name</h2>
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
</section>
```

Repo label classes: `repo-det` (blue), `repo-platform` (purple), `repo-tenderdash` (green), `repo-dashcore` (amber). The label sits **inside** `bullet-lead` as the first child so it renders inline with the headline, vertical-align middle. For multi-repo bullets, stack two pills (`<span class="repo-label repo-tenderdash">…</span><span class="repo-label repo-platform">…</span>`).

### Content slide — infographic card variant (preferred)

**User-confirmed preference, repeated across reviews: an SVG card row reads far better than the plain `<ul class="bullets">` list above.** Default to this pattern for repo slides without screenshots (with screenshots, use *Theme slide — points + screenshots*); fall back to the bullet list only when a slide genuinely has no room for a wide SVG (e.g. paired with a screenshot in `slide-split`).

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

A CTA card can replace the third slot on a card row using the same 380×320 shell with `stroke="#008DE4"` and `fill="rgba(0,141,228,0.06)"`: label (e.g. `GET THE BUILD`) plus the full `github.com/org/repo/path` (two lines if needed, no scheme) in muted monospace — no QR code.

### Theme slide — points + screenshots (preferred when screenshots exist)

Eyebrow strip, heading with status badge, "Who benefits", a row of 3 points, then a screenshot row on the **same grid** (same columns and gap) so each image sits under the point it illustrates. Use the same layout on every theme slide of a section; a theme with only 2 images uses a 2-column image row; the last slide may pair one image (`1.9fr`) with the CTA box (`1fr`). A theme with no usable screenshot yet uses the SVG card row until captures arrive.

```html
<div style="font-size:0.8rem;letter-spacing:0.04em;margin-bottom:10px;white-space:nowrap;">
  <span style="color:#8b949e;font-weight:700;letter-spacing:0.08em;margin-right:18px;">DASH EVO TOOL</span>
  <span style="color:#008DE4;font-weight:700;">1&nbsp;&nbsp;Theme A</span>
  <span style="color:#30363d;margin:0 14px;">/</span>
  <span style="color:#6e7681;">2&nbsp;&nbsp;Theme B</span>
</div>
<h2 class="slide-heading">Theme A <span class="stage-badge" style="background:#238636;color:#fff;">Merged</span></h2>
<p class="slide-subheading" style="margin-bottom:26px;"><strong>Who benefits:</strong> Audience</p>
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:36px;margin-bottom:28px;">
  <div>
    <div style="font-size:1.25rem;font-weight:700;color:#008DE4;line-height:1.3;margin-bottom:6px;">Point title</div>
    <div style="font-size:0.95rem;color:#c9d1d9;line-height:1.5;margin-bottom:8px;">One or two plain sentences.</div>
    <span class="pr-links"><a href="PR_URL" target="_blank">#123</a></span>
  </div>
  <!-- 2 more points -->
</div>
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:36px;align-items:start;">
  <div class="screenshot-wrap" style="min-height:0;flex-direction:column;">
    <img src="data:image/png;base64,..." alt="Screen name" style="width:auto;max-width:100%;max-height:max(160px, calc(100vh - 540px));">
    <div style="font-size:0.8rem;color:#8b949e;margin-top:8px;">Screen: what to look at</div>
  </div>
  <!-- 2 more images -->
</div>
```

The `max-height` keeps the slide inside the viewport down to 1280×720. Caption = screen name + what to look at, in plain words.

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
</section>
```

Screenshot behavior:
- Images scale to fill column width (`width: 100%`, no max-height cap in this split layout)
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
- **Page counter**: bottom-right (built into template JS)
- **Three-col grid**: `grid-template-columns: 1fr 1fr 1fr; gap: 24px`
- **Deck-wide font bump**: raising the root body `font-size` (e.g. 18px→20px) cascades proportionally through every `rem`/`em`-based CSS rule for free. It does NOT touch raw SVG `font-size="N"` attributes — those are absolute px and read the same regardless of body size, so a deck-wide legibility pass needs a second find-and-bump across every inline SVG `text` element.

## Design Constraints

- **Self-contained**: all CSS/JS inline in single HTML file. Images can be local file refs (`./`) or base64-encoded
- **Target resolution**: 1920×1080, responsive down to 1280px
- **3 cards per slide max** — readability over completeness
- **Bullet layout**: large headline (block, accent blue) + description below (block, muted) — NOT inline `lead — description`
- **PR links are subtle** — small monospace pills in muted gray, not the visual focus
- **Theme headlines** describe user outcomes, not technical changes
- **Code/endpoint names** use `<code>` tags for monospace treatment
- **Logo**: official Dash wordmark SVG in **Dash blue** (`#008DE4`), not white
- **Screenshots**: scale to fill column width, open fullscreen lightbox on click
- **Stage badges**: amber "In Progress" / purple "Beta" pills on headings for pre-release features
- **Save-Page-As idempotency**: Chrome serializes post-JS DOM, so a saved deck arrives with stale `.active`/`.exit-left` classes, appended dot buttons, and open lightbox state. The nav init block MUST be idempotent: call `dotsEl.replaceChildren()`, reset every slide's `.active`/`.exit-left` to defaults, clear lightbox state, and sync the counter. Test by round-tripping `document.documentElement.outerHTML` to a temp port.
- **Animation lock queue**: nav JS MUST NOT silently drop keypresses during transitions. Chrome auto-repeat (~50ms) vs animation (~480ms) means rapid presses feel frozen if dropped. Pattern: `let pendingDelta = 0`; when called while `animating`, set `pendingDelta = Math.sign(target - current)`; replay on animation complete; reset.
- **No interactive-only affordances**: this deck is typically delivered via screen-share/video stream, not driven by the viewer. Anything that looks clickable (a "Learn more" button, a CTA link styled as a button) is non-functional for the audience — show the **full, plain-text URL** instead. Never a shortened/truncated path (e.g. just `releases` or `…/releases`); the scheme (`https://`) can be omitted, but the full `domain/org/repo/path` must be shown.
- **No QR codes** — security bad practice (trains the audience to scan opaque codes; destination not visible). Plain-text URL only.

### Links to builds / releases

- **Release builds**: once published (`gh release view <tag> --repo dashpay/R`), point at the exact tag URL `github.com/dashpay/R/releases/tag/<tag>`; print the full path (may wrap to 2 lines); list platforms from the release assets ("Windows · macOS · Linux").
- Presenting a release that isn't published yet: keep a variant without it (e.g. `index-no-weekly.html`) and tell the user the deck depends on the publish.

## Screenshots

Check PR bodies/comments for attached images first, then a DET visual-diff gallery if one exists (`/data/artifacts/dash-evo-tool/<date>/visual-diff/`: `new/` plain captures, `annotated/` 2× with changes outlined, `README.md` lists what was not reproducible). Fresh DET capture: rebuild the binary (find the target dir via `cargo metadata`), run with a throwaway config dir (never `~/.config/dash-evo-tool`), on a 1920×1080 Xvfb display. Screens needing a funded testnet identity or masternode need user-supplied data. Ask the user before capturing.

- **Real data beats empty states.** If only empty screens exist, ask the user for recaptures with loaded wallets/identities and list exactly which screens, in priority order.
- **Don't wait for captures**: build with what exists, keep screenshot slots swappable, regenerate when new ones land. In `gen.py`, keep an `IMAGES` dict of `key → (path, crop box or None)`; crop and base64-embed with PIL at generation time.
- **Crop to the region of interest** — full 1280×800 windows are unreadable in a 3-column row. Keep the same aspect within one image row.
- **Plain vs annotated**: a whole new screen → plain capture; a small change in an existing screen → annotated (outlined) crop. Crop out "Removed" boxes and old-build insets — they confuse a live audience.
- **Check every image** for recovery phrases, private keys, and real people's photos (profile pictures); flag the latter to the user.
- **Text must not claim what the screenshot or the visual-diff README contradicts** (e.g. a behaviour marked "not reproducible") — drop the claim.

## Assets

- `assets/presentation-template.html` — HTML/CSS/JS skeleton with `{{TITLE}}` and `{{SLIDES}}` placeholders
- `presentations/assets/` — shared Dash brand SVGs (CC BY 4.0 from dash.org/brand-guidelines)
- `scripts/agent_stats.py` — AI agent usage stats miner (reads `~/.claude/` session data)
