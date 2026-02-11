# Test Case 05: Wide Range Covering Full Week (2026-02-03 to 2026-02-09)

## Scenario

Verify that a wide date range spanning a full week correctly captures all known activity
for user `lklimek` in org `dashpay`, including PRs across multiple repositories, while
excluding PRs with activity outside the range.

## Command

```bash
python3 daily_report.py --from 2026-02-03 --to 2026-02-09 --org dashpay --user lklimek
```

## Date Parameters

- `--from 2026-02-03 --to 2026-02-09` (range mode)
- Expected header: `# Daily Report -- 2026-02-03 .. 2026-02-09`

## Known GitHub Data

### Authored PRs (commits within range)

| Repo | PR# | Title | Dates | Status |
|------|------|-------|-------|--------|
| dash-evo-tool | #523 | fix: invalid min amount | 2026-02-03 | MERGED |
| grovestark | #1 | build: disable unused grovedb features | 2026-02-04 | MERGED |
| dash-evo-tool | #527 | fix: build fails for windows | 2026-02-04 | MERGED |
| dash-evo-tool | #532 | fix: connection status not clear | 2026-02-05 | MERGED |
| dash-evo-tool | #531 | build: update all dependencies | 2026-02-05 | MERGED |
| platform | #3056 | fix(dapi): files generated outside sandbox | 2026-02-05 | MERGED |
| tenderdash | #1248 | (commits on 2026-02-06) | 2026-02-06 | MERGED |
| platform | #3068 | build: update js webpack to 5.104.0 | 2026-02-06, 2026-02-09 | MERGED |
| platform | #3067 | (created 2026-02-06) | 2026-02-06 | MERGED |
| platform | #3065 | (created 2026-02-06) | 2026-02-06 | MERGED |
| platform | #3072 | feat(dapi): add method to retrieve all non-banned endpoints | 2026-02-09 | MERGED |
| tenderdash | #1250 | chore: add AI agent instructions | 2026-02-09 | MERGED |

### Reviewed PRs (reviews submitted within range)

| Repo | PR# | Author | Review Date |
|------|------|--------|-------------|
| platform | #3059 | QuantumExplorer | 2026-02-05 |
| platform | #3062 | ZocoLini | 2026-02-06 |
| dash-evo-tool | #534 | PastaPastaPasta | 2026-02-09 |
| dash-evo-tool | #535 | PastaPastaPasta | 2026-02-09 |
| tenderdash | #1252 | Copilot | 2026-02-09 |
| platform | #3073 | shumkov | 2026-02-09 |

### PRs that should NOT appear (activity outside range)

| Repo | PR# | Reason |
|------|------|--------|
| dash-evo-tool | #552 | Commits on 2026-02-10/11 only |
| dash-evo-tool | #554 | Reviewed on 2026-02-11 only |
| tenderdash | #1244 | Commits on 2026-01-28 only |

## Expected Results

1. Header shows range `2026-02-03 .. 2026-02-09`
2. All 12 authored PRs appear in the output
3. All 6 reviewed PRs appear in the output
4. PRs outside the range (#552, #554, #1244) do NOT appear
5. Summary uses "merged" (not "merged today") since this is range mode
6. Multiple repos should be mentioned (platform, tenderdash, dash-evo-tool, grovestark)
