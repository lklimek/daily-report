# Test Case 03: Narrow Range Excluding Boundary Activity (2026-02-07 to 2026-02-08)

## Scenario

Verify that the report correctly excludes activity from adjacent days when the queried
range (Feb 7-8) falls between days with known activity (Feb 6 and Feb 9). This is a
"quiet period" test to ensure date filtering is strict.

## Command

```bash
python3 daily_report.py --from 2026-02-07 --to 2026-02-08 --org dashpay --user lklimek
```

## Date Parameters

- `--from 2026-02-07 --to 2026-02-08` (range mode)
- Expected header: `# Daily Report -- 2026-02-07 .. 2026-02-08`

## Known GitHub Data

User `lklimek` had activity on 2026-02-06 and 2026-02-09, but minimal or no direct
authored commit activity between 2026-02-07 and 2026-02-08.

### PRs that should NOT appear (activity outside range)

| Repo | PR# | Reason |
|------|------|--------|
| tenderdash | #1248 | Commits only on 2026-02-06 |
| platform | #3068 | Commits on 2026-02-06 and 2026-02-09, none in 07-08 range |
| platform | #3072 | Commits on 2026-02-09 only |
| dash-evo-tool | #534 | lklimek review was on 2026-02-09 |
| dash-evo-tool | #535 | lklimek review was on 2026-02-09 |

## Expected Results

1. Header shows range `2026-02-07 .. 2026-02-08`
2. Few or no PRs should appear in the authored section
3. Few or no PRs should appear in the reviewed section
4. PRs with verified activity only on 2026-02-06 should NOT appear
5. PRs with verified activity only on 2026-02-09 should NOT appear
6. Summary should use "merged" (not "merged today") since this is range mode
