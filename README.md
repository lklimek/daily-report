# daily-report

A CLI tool for daily standups. If your team reports progress every day on Slack or a similar tool, this generates a ready-to-post summary of your GitHub activity — no more manual copy-pasting from pull requests.

- **Automatic PR discovery** — finds authored, contributed, and reviewed PRs across all your repos
- **Post to Slack** *(experimental)* — send a formatted report directly to a Slack channel via webhook
- **Export to slides** — generate a `.pptx` deck for sprint reviews, one slide per project
- **Fast hybrid engine** — combines local git history with GitHub GraphQL API for speed
- **Flexible date ranges** — single day, date range, or default to today

## Prerequisites

- Python 3.8+
- [GitHub CLI](https://cli.github.com/) (`gh`) installed and authenticated
- [PyYAML](https://pypi.org/project/PyYAML/) (`pip install pyyaml`) — required for config file support
- *(optional)* [python-pptx](https://pypi.org/project/python-pptx/) (`pip install python-pptx`) — for `--slides` export
- *(optional)* [anthropic](https://pypi.org/project/anthropic/) (`pip install anthropic`) — for `--consolidate` AI summaries

## Usage

```bash
# Default: all orgs, authenticated user, today
python -m daily_report

# Specific date
python -m daily_report --date 2026-02-10

# Date range (inclusive)
python -m daily_report --from 2026-02-01 --to 2026-02-07

# Filter to a specific org
python -m daily_report --org dashpay

# Different org and user
python -m daily_report --org myorg --user someone

# Use local git repos from a directory (fastest mode)
python -m daily_report --repos-dir ~/git

# Use a custom config file
python -m daily_report --config ~/.config/daily-report/repos.yaml

# Force API-only mode (skip local git)
python -m daily_report --no-local

# Generate a .pptx slide deck (requires: pip install python-pptx)
python -m daily_report --slides --from 2026-01-26 --to 2026-02-06

# Specify a custom output path for the slide deck
python -m daily_report --slides --slides-output ~/presentations/sprint-review.pptx --from 2026-01-26 --to 2026-02-06

# Post report to Slack
python -m daily_report --slack --slack-webhook https://hooks.slack.com/services/T.../B.../xxx

# Post to Slack using env var for the webhook URL
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxx python -m daily_report --slack

# Post to Slack using webhook URL from config file
python -m daily_report --slack

# Consolidate PR lists into AI-generated summaries (requires: pip install anthropic)
python -m daily_report --consolidate

# Consolidate with a specific Claude model
python -m daily_report --consolidate --model claude-sonnet-4-5-20250929

# Consolidate for a date range and export to slides
python -m daily_report --consolidate --slides --from 2026-01-26 --to 2026-02-06

# Consolidate and post to Slack
python -m daily_report --consolidate --slack

# Replace default summary stats with a short AI-generated summary
python -m daily_report --summary

# Combine consolidation and AI summary
python -m daily_report --consolidate --summary

# AI features with a specific model
python -m daily_report --consolidate --summary --model claude-haiku-4-5-20251001
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--org` | *(none — all orgs)* | GitHub organization to report on |
| `--user` | authenticated `gh` user | GitHub username to report for |
| `--date` | today | Single date in `YYYY-MM-DD` format; mutually exclusive with `--from`/`--to` |
| `--from` | *(none)* | Start of date range in `YYYY-MM-DD` format (requires `--to`) |
| `--to` | *(none)* | End of date range in `YYYY-MM-DD` format (requires `--from`) |
| `--config` | `~/.config/daily-report/repos.yaml` | Path to YAML config file |
| `--repos-dir` | *(none)* | Scan directory for git repos, filters by `--org` if given (overrides config repos list) |
| `--git-email` | *(none)* | Additional git author email for commit matching |
| `--no-local` | `false` | Skip local git discovery, use GraphQL-only mode |
| `--slides` | `false` | Generate `.pptx` slide deck instead of Markdown output |
| `--slides-output` | *(auto-generated)* | Custom output path for `.pptx` file (requires `--slides`) |
| `--slack` | `false` | Post report to Slack via Incoming Webhook instead of Markdown output |
| `--slack-webhook` | *(env var or config)* | Slack webhook URL (requires `--slack`); falls back to `SLACK_WEBHOOK_URL` env var or `slack_webhook` in config file |
| `--waiting-days` | `365` | Max age (days) for "Waiting for review" PRs; hides PRs waiting longer than this (minimum: 1) |
| `--consolidate` | `false` | Consolidate PR lists into AI-generated summaries per repository |
| `--summary` | `false` | Replace default summary stats with a short AI-generated summary (<160 chars) |
| `--model` | `claude-sonnet-4-5-20250929` | Claude model for AI features (requires `--consolidate` or `--summary`) |

`--date` and `--from`/`--to` are mutually exclusive. When neither is provided, defaults to today.

Waiting-for-review items are limited to PRs where the reviewer was assigned within the last `--waiting-days` (default 365), so very old open PRs don't clutter daily reports.

## Slides Export

The `--slides` flag generates a `.pptx` (PowerPoint) slide deck instead of the default Markdown output. This is useful for bi-weekly sprint presentations — the generated file can be uploaded directly to Google Slides via Google Drive or "File > Import slides".

**Requires** the optional `python-pptx` dependency:

```bash
pip install python-pptx
```

If `python-pptx` is not installed, using `--slides` prints a clear error message and exits.

The slide deck contains:

1. **Title slide** — "Activity Report" with the GitHub username and date range.
2. **Per-project slides** — one slide per repository with activity, listing authored/contributed PRs, reviewed PRs, and PRs waiting for review under grouped subheadings.
3. **Summary slide** — aggregate metrics (total PRs, repos, merged count, open count, key themes).

By default, the output file is written to the current directory with the name `daily-report-{user}-{date}.pptx` (or `daily-report-{user}-{from}_{to}.pptx` for date ranges). Use `--slides-output` to specify a custom path.

## Slack Integration

The `--slack` flag posts the report to a Slack channel via an [Incoming Webhook](https://api.slack.com/messaging/webhooks) instead of printing Markdown output. The report is formatted using Slack's Block Kit for a clean, readable layout. No additional dependencies are required (uses Python stdlib only).

`--slack` and `--slides` are mutually exclusive.

### Setting up a Slack Incoming Webhook

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**.
2. Choose **From an app manifest** and paste the contents of [`slack-app-manifest.yaml`](slack-app-manifest.yaml) included in this repo.
3. Click **Create** and install the app to your workspace.
4. Go to **Incoming Webhooks** and click **Add New Webhook to Workspace**.
5. Select the channel where reports should be posted and authorize.
6. Copy the webhook URL (starts with `https://hooks.slack.com/services/...`).

### Providing the webhook URL

The webhook URL is resolved in the following order (first non-empty value wins):

1. **CLI flag**: `--slack-webhook <URL>`
2. **Environment variable**: `SLACK_WEBHOOK_URL`
3. **Config file**: `slack_webhook` field in the YAML config

Example config with `slack_webhook`:

```yaml
default_org: dashpay
slack_webhook: https://hooks.slack.com/services/T.../B.../xxx

repos:
  - path: ~/git/platform
```

## AI Features

### Consolidation (`--consolidate`)

Replaces per-PR bullet points with AI-generated summaries that describe the purpose and goals of work per repository. Useful when activity reports become long — the AI distils multiple PRs into 2-5 concise bullet points per repo, referencing PR numbers.

### Summary (`--summary`)

Replaces the default summary stats (PR counts, repo counts, themes) with a single AI-generated sentence (<160 characters) describing the overall work.

Both flags can be combined. Both work with all output formats (Markdown, Slides, Slack).

**Requires** the optional `anthropic` dependency (only when using `ANTHROPIC_API_KEY`; not needed when falling back to the `claude` CLI):

```bash
pip install anthropic
```

### Authentication

Two backends are supported:

1. **`ANTHROPIC_API_KEY`** environment variable — uses the `anthropic` Python SDK directly
2. **Claude CLI** (`claude`) — if no API key is set, falls back to the `claude` CLI which handles authentication natively (subscription, OAuth token, etc.)

For subscription users with Claude Code already installed, AI features work out of the box with no extra configuration.

### Custom prompts

Override the default prompts via the config file:

```yaml
consolidate_prompt: "Summarize each repo's work in 3 bullet points focusing on user-facing impact."
summary_prompt: "Write a one-sentence summary of all work done."
```

## Configuration

Create `~/.config/daily-report/repos.yaml` to enable local git commit discovery:

```yaml
default_org: dashpay

repos:
  - path: ~/git/platform
  - path: ~/git/tenderdash
  - path: ~/git/dash-evo-tool

# Optional: bots to exclude from reviewer lists
excluded_bots:
  - coderabbitai
  - copilot-pull-request-reviewer
  - github-actions
  - copilot-swe-agent
```

The `org` and `name` for each repo are auto-detected from the git remote URL. You can override them explicitly:

```yaml
repos:
  - path: ~/git/platform
    org: dashpay
    name: platform
```

Alternatively, use `--repos-dir ~/git` to auto-discover all repos in a directory, filtered by `--org` if given.

## How it works

The tool uses a four-phase pipeline:

1. **Local git commit discovery** — scans locally cloned repos for commits by the user within the date range, then maps commits to PRs via commit message parsing and GraphQL batch queries. Falls back to GraphQL search for repos not cloned locally.
2. **Review discovery** — a single GraphQL search finds PRs where the user has review or comment activity, with inline date verification.
3. **PR enrichment** — a batch GraphQL query fetches details (state, merged date, additions/deletions) for all discovered PRs in one call.
4. **Content preparation** — groups PRs by repository into renderer-agnostic content blocks. With `--consolidate`, sends PR data to the Claude API for AI-powered summarisation instead of listing individual PRs.

This replaces the previous approach of ~100 individual REST API calls with ~5-7 GraphQL calls, reducing runtime from ~50 seconds to ~7 seconds. Local git discovery also catches PRs that the API search misses (e.g., bot-authored PRs where the user has commits).

Without a config file or `--repos-dir`, the tool runs in GraphQL-only mode — still significantly faster than the old REST approach.
