# TC04: Single day as range (--from X --to X same day)

## Purpose
Verify that `--from 2026-02-06 --to 2026-02-06` produces **identical** output
to `--date 2026-02-06`. When from and to are the same date, the script should
treat it as a single-day report (not a range report).

## Commands
```bash
# Command A: single-date mode
python3 daily_report.py --date 2026-02-06 --org dashpay --user lklimek

# Command B: same-day range mode
python3 daily_report.py --from 2026-02-06 --to 2026-02-06 --org dashpay --user lklimek
```

## Expected behaviour

### Output equivalence
- Command A and Command B must produce **identical** output (diff returns 0).

### Header
- Must show single-day format: `Daily Report — 2026-02-06`
- Must NOT show range format (no `..`).

### Authored PRs expected on 2026-02-06
| Repo | PR | Reason |
|---|---|---|
| tenderdash | #1248 | commits on 2026-02-06 |
| platform | #3068 | commit on 2026-02-06 |
| platform | #3067 | created 2026-02-06 |
| platform | #3065 | created 2026-02-06 |

### Reviewed PRs expected on 2026-02-06
| Repo | PR | Review date |
|---|---|---|
| platform | #3062 | APPROVED 2026-02-06 |

### Summary line
- Must say "merged today" (not just "merged") because it is a single-day report.
