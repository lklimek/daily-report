# daily_report.py

Daily GitHub PR report generator. Uses `gh` CLI to gather authored, contributed, reviewed PRs and pending review requests across a GitHub organization.

## Prerequisites

- Python 3.8+
- [GitHub CLI](https://cli.github.com/) (`gh`) installed and authenticated

## Usage

```bash
# Default: dashpay org, authenticated user, today
./daily_report.py

# Specific date
./daily_report.py --date 2026-02-10

# Different org or user
./daily_report.py --org myorg --user someone
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--org` | `dashpay` | GitHub organization |
| `--user` | authenticated user | GitHub username |
| `--date` | today | Date in YYYY-MM-DD format |
