# Test Case 07: PR Updated After Range but Has Commit Within Range

## Scenario

Verify that a PR which has commits within the date range appears correctly,
even if the PR's updatedAt timestamp is after the end of the range.
This tests that the tool uses commit dates (not just PR updated dates) for filtering.

## Command

```bash
python3 daily_report.py --date 2026-02-03 --org dashpay --user lklimek
```

## Known GitHub Data

- `dash-evo-tool#523`: authored by lklimek
  - Commits by lklimek on: 2026-02-03 (INSIDE range)
  - updatedAt: 2026-02-04 (OUTSIDE the single-day range)
  - state: merged
  - Title: "fix: invalid min amount when transferring funds"

The PR was updated on 2026-02-04 (merged), but has commits on 2026-02-03.
Since `gh search prs --created=2026-02-03` should find it (created on 2026-02-03),
and commits match the date, it should appear.

## Expected Results

1. `dash-evo-tool#523` SHOULD appear in "Authored / Contributed" section
2. Header shows single-day format `2026-02-03`
3. Summary uses "merged today"
