# Test Case 01: Single Day with Known Activity (2026-02-09)

## Scenario

Verify that running the daily report for a single day (2026-02-09) for user `lklimek`
in org `dashpay` correctly shows all PRs with activity on that specific day and excludes
PRs with activity only on other days.

## Command

```bash
python3 daily_report.py --date 2026-02-09 --org dashpay --user lklimek
```

## Date Parameters

- `--date 2026-02-09` (single day mode)
- Expected header: `# Daily Report -- 2026-02-09`

## Known GitHub Data for 2026-02-09

### Authored PRs (commits on 2026-02-09)

| Repo | PR# | Title | Status |
|------|------|-------|--------|
| platform | #3072 | feat(dapi): add method to retrieve all non-banned endpoints | MERGED |
| tenderdash | #1250 | chore: add AI agent instructions | MERGED |
| platform | #3068 | build: update js webpack to 5.104.0 | MERGED |

### Reviewed PRs (reviews submitted on 2026-02-09, not authored by lklimek)

| Repo | PR# | Author | Review Type |
|------|------|--------|-------------|
| tenderdash | #1252 | Copilot | CHANGES_REQUESTED + APPROVED |
| dash-evo-tool | #534 | PastaPastaPasta | COMMENTED + APPROVED |
| dash-evo-tool | #535 | PastaPastaPasta | APPROVED |
| platform | #3073 | shumkov | APPROVED |

### PRs that should NOT appear

| Repo | PR# | Reason |
|------|------|--------|
| tenderdash | #1248 | Commits only on 2026-02-06, no activity on 2026-02-09 |

## Expected Results

1. Header shows single date `2026-02-09`
2. Authored section includes platform#3072, tenderdash#1250, platform#3068
3. Reviewed section includes tenderdash#1252, dash-evo-tool#534, dash-evo-tool#535, platform#3073
4. tenderdash#1248 does NOT appear in the output
5. Summary line uses "merged today" (not just "merged") since this is single-day mode
