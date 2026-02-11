# Test Case 06: PR Updated in Range but No User Commit in Range

## Scenario

Verify that a PR which was updated (by others or bots) within the date range
but has NO commits from the user within that range does NOT appear in the
"Authored / Contributed" section.

## Command

```bash
python3 daily_report.py --from 2026-02-08 --to 2026-02-10 --org dashpay --user lklimek
```

## Known GitHub Data

- `dash-evo-tool#521`: authored by lklimek
  - Commits by lklimek on: 2026-02-02, 2026-02-03 (OUTSIDE range)
  - updatedAt: 2026-02-10 (INSIDE range, but update was not a commit by lklimek)
  - state: open
- `dash-evo-tool#514`: authored by lklimek
  - Commits by lklimek on: 2026-02-02 (OUTSIDE range)
  - updatedAt: 2026-02-10 (INSIDE range)
  - state: open

These PRs will be returned by `gh search prs --author=lklimek --updated=2026-02-08..2026-02-10`
but `check_commits_for_user` should filter them out since no commits fall within the range.

## Expected Results

1. `dash-evo-tool#521` should NOT appear in "Authored / Contributed" section
   (it might appear in reviewed section if there's review activity, but not as authored)
2. `dash-evo-tool#514` should NOT appear in "Authored / Contributed" section
3. Header shows range format `2026-02-08 .. 2026-02-10`
