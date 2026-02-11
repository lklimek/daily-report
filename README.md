# daily_report

Daily GitHub PR report generator. Uses local git repos and GitHub GraphQL API to gather authored, contributed, reviewed PRs and pending review requests across a GitHub organization.

## Prerequisites

- Python 3.8+
- [GitHub CLI](https://cli.github.com/) (`gh`) installed and authenticated
- [PyYAML](https://pypi.org/project/PyYAML/) (`pip install pyyaml`) — required for config file support

## Usage

```bash
# Default: dashpay org, authenticated user, today
python -m daily_report

# Specific date
python -m daily_report --date 2026-02-10

# Date range (inclusive)
python -m daily_report --from 2026-02-01 --to 2026-02-07

# Different org or user
python -m daily_report --org myorg --user someone

# Use local git repos from a directory (fastest mode)
python -m daily_report --repos-dir ~/git

# Use a custom config file
python -m daily_report --config ~/.config/daily-report/repos.yaml

# Force API-only mode (skip local git)
python -m daily_report --no-local
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--org` | `dashpay` | GitHub organization |
| `--user` | authenticated user | GitHub username |
| `--date` | today | Single date in YYYY-MM-DD format |
| `--from` | — | Start date for range mode (requires `--to`) |
| `--to` | — | End date for range mode (requires `--from`) |
| `--config` | `~/.config/daily-report/repos.yaml` | Path to config file |
| `--repos-dir` | — | Scan directory for git repos matching the org |
| `--git-email` | — | Additional git email for author matching |
| `--no-local` | off | Force API-only mode (skip local git operations) |

`--date` and `--from`/`--to` are mutually exclusive. When neither is provided, defaults to today.

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

Alternatively, use `--repos-dir ~/git` to auto-discover all repos in a directory that match the target `--org`.

## How it works

The tool uses a three-phase pipeline:

1. **Local git commit discovery** — scans locally cloned repos for commits by the user within the date range, then maps commits to PRs via commit message parsing and GraphQL batch queries. Falls back to GraphQL search for repos not cloned locally.
2. **Review discovery** — a single GraphQL search finds PRs where the user has review or comment activity, with inline date verification.
3. **PR enrichment** — a batch GraphQL query fetches details (state, merged date, additions/deletions) for all discovered PRs in one call.

This replaces the previous approach of ~100 individual REST API calls with ~5-7 GraphQL calls, reducing runtime from ~50 seconds to ~7 seconds. Local git discovery also catches PRs that the API search misses (e.g., bot-authored PRs where the user has commits).

Without a config file or `--repos-dir`, the tool runs in GraphQL-only mode — still significantly faster than the old REST approach.
