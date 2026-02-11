# TC02: Date range spanning a weekend (2026-02-06 to 2026-02-09)

## Purpose
Verify that `--from 2026-02-06 --to 2026-02-09` correctly returns PRs with
activity strictly within the given date range, spanning a weekend.

## Command
```bash
python3 daily_report.py --from 2026-02-06 --to 2026-02-09 --org dashpay --user lklimek
```

## Expected behaviour

### Header
- Report title must show range format: `Daily Report — 2026-02-06 .. 2026-02-09`

### Authored / Contributed PRs (activity within range)
| Repo | PR | Reason |
|---|---|---|
| tenderdash | #1248 | commits on 2026-02-06 |
| platform | #3068 | commits on 2026-02-06 and 2026-02-09 |
| platform | #3072 | commits on 2026-02-09 |
| tenderdash | #1250 | commits on 2026-02-09 (and 2026-02-10, but 2026-02-09 is in range) |
| platform | #3067 | created 2026-02-06 |
| platform | #3065 | created 2026-02-06 |

### Reviewed / Approved PRs (review activity within range)
| Repo | PR | Review date |
|---|---|---|
| platform | #3062 | APPROVED 2026-02-06 |
| dash-evo-tool | #534 | COMMENTED + APPROVED 2026-02-09 |
| dash-evo-tool | #535 | APPROVED 2026-02-09 |
| tenderdash | #1252 | CHANGES_REQUESTED + APPROVED 2026-02-09 |
| platform | #3073 | APPROVED 2026-02-09 |

### Should NOT appear (activity outside range)
| Repo | PR | Reason |
|---|---|---|
| dash-evo-tool | #552 | commits only on 2026-02-10 and 2026-02-11 |
| dash-evo-tool | #556 | commits only on 2026-02-11 |
| platform | #3059 | reviewed on 2026-02-05 |

### Summary line
- Must say "merged" (not "merged today") because it is a range report.
