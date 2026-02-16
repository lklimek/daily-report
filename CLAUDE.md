# daily-report

Daily GitHub PR report generator. Hybrid local-git + GraphQL architecture (4-phase pipeline).

## Quick Reference

```bash
./run.sh [args]          # run the tool (or: python -m daily_report)
./test.sh [args]         # run tests (or: python3 -m pytest tests/ -v)
```

**Prerequisites:** Python 3.8+, `gh` CLI (authenticated), `pyyaml`, optional `python-pptx` (for `--slides`), optional `anthropic` (for `--consolidate`)

## Project Structure

- `daily_report/` — main package, source code of the app
  - `report_data.py` — structured data model (`ReportData`, `AuthoredPR`, `ReviewedPR`, `WaitingPR`, `SummaryStats`, `ContentItem`, `ContentBlock`, `RepoContent`)
  - `content.py` — content preparation layer (default grouping + AI consolidation via Claude API)
  - `format_markdown.py` — Markdown formatter (pure function, returns string)
  - `format_slides.py` — PPTX slide deck formatter (requires `python-pptx`, writes file)
  - `format_slack.py` — Slack Block Kit formatter and webhook poster (stdlib only, no extra dependencies)
- `tests/` — `test_date_range.py` (functional, live GitHub), `test_graphql_client.py` (unit, mocked), `test_consolidate.py` (content preparation), `test_formatters.py` (markdown + slides), `test_format_slack.py` (Slack formatter)
- `tests/scenarios/` — test case documentation
- `docs/` — design and research documents

## Architecture

Four phases: **local git discovery** → **GraphQL review search** → **GraphQL batch enrichment** → **content preparation** (default grouping or AI consolidation)

- GraphQL queries use index-based aliases (`pr_0`, `pr_1`, `c0`, `c1`)
- `parse_pr_details_response(data, prs)` requires the original prs list for index correlation
- `build_waiting_for_review_query` uses `$searchQuery` variable (not `$query` — that conflicts with `gh api graphql -f query=`)
- Parallel git fetch via `ThreadPoolExecutor`
- Date ranges expanded ±1 day for timezone handling, precise filtering in Python

## Conventions

- **Commits:** conventional commits (`feat:`, `fix:`, `refactor:`, `chore:`, `docs:`), signed if possible
- **Before committing:** always run `./test.sh` — all tests must pass
- **Mock paths:** use full package prefix, e.g. `daily_report.graphql_client.subprocess.run`
- **External commands:** via `subprocess.run()` with `capture_output=True`
- **Data models:** dataclasses (`RepoInfo`, `GitCommit`, `RepoConfig`, `Config`, `ReportData`, `AuthoredPR`, `ReviewedPR`, `WaitingPR`, `SummaryStats`, `ContentItem`, `ContentBlock`, `RepoContent`)
- **Config options:** `slack_webhook` in YAML config (also via `SLACK_WEBHOOK_URL` env var or `--slack-webhook` CLI flag); `consolidate_prompt` and `summary_prompt` for custom AI prompt overrides
- **Optional dependencies:** use lazy import pattern (import inside conditional block in `__main__.py`, not at module top level) — see `format_slides` and `content.py` (anthropic) for examples
- **Formatters:** consume `report.content` (`List[RepoContent]`) for per-repo rendering, not raw PR lists directly

## Known Gotchas

- GraphQL variable named `query` conflicts with `gh api graphql -f query=` — always use `searchQuery`
- Hyphenated repo names lose info with naive aliasing — use index-based aliases instead
- Output ordering must be deterministic — sort authored/reviewed results before output
- `test_date_range.py` hits live GitHub API — requires `gh` auth and network access
