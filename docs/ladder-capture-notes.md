# Ladder Capture Notes (`capture-data` Orphan Branch)

## Manual Test Ticks (Not Window Evidence)
The following two ticks on `capture-data` were manual `workflow_dispatch` test runs using `window_before=1440.0` / `window_after=-1440.0` to verify orphan-branch creation, gzipped tick output, and cross-tick hash-chain continuity before enabling the schedule:

- `ticks/2026-11-19/tick_20261119T190620Z.jsonl.gz` (run `44594778223`, 197 records, `last_record_hash = 4adb4d009829dd0531ef9ea298a21fc0910d1cdac84066f140021fa2997662eb`)
- `ticks/2026-11-19/tick_20261119T190722Z.jsonl.gz` (run `44594966140`, 197 records, `last_record_hash = d49d6be2478d821d647acf3443259af6c9977eada3aa3a80aaed02f2bcbfcb9e`)

**Exclude both timestamps from window-coverage and T-60/T-30/T-15/T-5 convergence analyses.**

## Day-One Scheduled Coverage Check
After 24 hours of scheduled runs on `main`, generate the coverage summary against `capture-data` with:

```bash
PYTHONPATH=. python scripts/ladder_day1_report.py --capture-dir _capture_data
```
