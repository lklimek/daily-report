# daily-report

Daily GitHub PR report generator. Hybrid local-git + GraphQL architecture (3-phase pipeline).

## Quick Reference

```bash
./run.sh [args]          # run the tool (or: python -m daily_report)
./test.sh [args]         # run tests (or: python3 -m pytest tests/ -v)
```

**Prerequisites:** Python 3.8+, `gh` CLI (authenticated), `pyyaml`

## Project Structure

- `daily_report/` — main package, source code of the app
- `tests/` — `test_date_range.py` (functional, live GitHub), `test_graphql_client.py` (unit, mocked)
- `tests/scenarios/` — test case documentation
- `docs/` — design and research documents

## Architecture

Three phases: **local git discovery** → **GraphQL review search** → **GraphQL batch enrichment**

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
- **Data models:** dataclasses (`RepoInfo`, `GitCommit`, `RepoConfig`, `Config`)

## Known Gotchas

- GraphQL variable named `query` conflicts with `gh api graphql -f query=` — always use `searchQuery`
- Hyphenated repo names lose info via `_safe_alias()` — use index-based aliases instead
- Output ordering must be deterministic — sort authored/reviewed results before output
- `test_date_range.py` hits live GitHub API — requires `gh` auth and network access
