# Kalshi NBA & Polymarket Capture Timing Diagnosis

Dated: 2026-11-17  
Author: Nick Altstein <nickaltstein@gmail.com>

## 1. Executive Summary

An audit of stored Kalshi NBA quote files (`viewer/nba_odds_2026-11-13.json` through `2026-11-16.json`) and Polymarket NFL line-history entries (`viewer/api/lines/*/*.json`) established that:
- **237 of 311 stored Kalshi NBA quotes (76.2%) were captured after the game's scheduled tipoff**.
- **28 Polymarket line-history rows on `2026_09_BUF_MIN` were captured 1.54 hours after kickoff**.
- **Destructive Overwrites Confirmed**: A later post-tip execution completely replaces the stored day's odds file (`viewer/nba_odds_<date>.json`). Earlier, valid pre-tip quotes were overwritten and erased by post-tip prices.

## 2. Evidence & Root Cause

### A. The Workflow & Schedule Trigger
- **Workflow File**: `.github/workflows/nba_daily.yml` (lines 9–15)
- **Schedule Crons**:
  ```yaml
  - cron: '7 16 * * *'   # 11:07 AM EST / 12:07 PM EDT
  - cron: '23 17 * * *'  # 12:23 PM EST /  1:23 PM EDT
  - cron: '37 20 * * *'  #  3:37 PM EST /  4:37 PM EDT
  - cron: '53 22 * * *'  #  5:53 PM EST /  6:53 PM EDT
  ```
  *(Prior to commit `fbd4a5c5`, crons were `0 16`, `0 17`, `30 20`, and `30 22`.)*
- **Unconditional Refresh Guard**: `.github/workflows/nba_daily.yml` (lines 61–62):
  ```python
  if odds.get('source') == 'kalshi':
      raise SystemExit(1)  # Kalshi quotes move; refresh on every tick
  ```
  Because the deduplication guard exits with 1 whenever `source == 'kalshi'`, every tick triggers a fetch and completely overwrites `viewer/nba_odds_<date>.json`.

### B. Why Captures Land After Tip
1. **GitHub Actions Scheduling Jitter & Queue Latency**:
   GitHub scheduled workflows do not guarantee execution at the scheduled second. Run history shows delayed execution landing well after scheduled cron:
   - Run `43791193762`: scheduled for `22:30 UTC`, executed at `2026-11-16T02:16:50Z` (+3h 46m queue delay).
   - Run `43665694791`: scheduled for `22:30 UTC`, executed at `2026-11-15T02:00:23Z` (+3h 30m queue delay).
   - Run `435469...`: executed at `2026-11-14T02:33:21Z` (commit `861373f2`).
2. **Slate Time Span**:
   NBA slates have tipoffs spanning from 19:00 ET (00:00 UTC) to 22:30 ET (03:30 UTC). A single global cron capture executed between 23:00 UTC and 02:30 UTC captures East Coast games (19:00 ET) while they are in-game or finished.
3. **Absence of Fetch-Side Guard**:
   In `scripts/fetch_kalshi_nba.py` (`build_quotes()`, lines 168–207), all open Kalshi markets for the slate are matched against games without checking whether `tipoff_utc` has already passed. The script assigns `observed_at = now` to whatever quotes it receives.

## 3. Offset Table Across Capture Runs

Offsets computed as $\Delta t = \text{quoted\_at} - \text{scheduled\_start}$ in UTC:

| Source | Slate / Run | Run Time (UTC) | Games Covered | Pre-Start | Post-Start | Unknown | Offset Range (Hours) |
|---|---|---|:---:|:---:|:---:|:---:|:---:|
| Kalshi | 2026-11-13 | 2026-11-14T02:33:21Z | 8 | 30 | 85 | 0 | `[-0.94, +1.06, +2.56]` |
| Kalshi | 2026-11-14 | 2026-11-15T02:00:42Z | 5 | 0 | 62 | 0 | `[+1.01, +1.01, +2.01]` |
| Kalshi | 2026-11-15 | 2026-11-16T02:17:05Z | 3 | 0 | 24 | 0 | `[+0.28, +1.28, +2.28]` |
| Kalshi | 2026-11-16 | 2026-11-17T00:43:14Z | 8 | 44 | 66 | 0 | `[-1.28, +0.22, +0.72]` |
| Polymarket | 2026_09_BUF_MIN | 2026-11-10T02:47:16Z | 1 | 27 | 28 | 0 | `[-23.17, +1.54, +1.54]` |

**Total Stored Kalshi Quotes**: 311  
- Pre-Start Quotes: 74 (23.8%)  
- Post-Start Quotes: 237 (76.2%)  

**Polymarket Quotes (2026_09_BUF_MIN window)**: 55  
- Post-Kickoff Quotes: 28 (50.9%)  

## 4. Overwrite Audit: Good Closes Destroyed

Git commit history on `viewer/nba_odds_2026-11-13.json` proves that valid pre-start quotes were previously captured, but subsequently destroyed by post-tip runs:

1. **Commit `3a2aba71` (2026-11-13T22:26:49Z)**:
   - Capture completed at `22:26:48Z` (1h 33m **before** 00:00Z tipoff of `MIL-ORL`).
   - Anthony Black points line was **14.5** (over -150, under +144).
   - This was a valid pre-start quote.
2. **Commit `64036830` (2026-11-14T00:57:23Z)**:
   - Run executed 57 minutes **after** tipoff.
   - It overwrote `viewer/nba_odds_2026-11-13.json`.
   - Anthony Black's line was overwritten with in-game line **9.5** (over +133, under -150).
3. **Commit `861373f2` (2026-11-14T02:33:21Z)**:
   - Run executed 2h 33m **after** tipoff.
   - It overwrote the entire file again.
   - Anthony Black's market settled on Kalshi, so he was omitted entirely from the final stored file.

**Conclusion**: The late cron does not merely append bad quotes; it actively erases valid pre-tip quotes.

## 5. Architectural Remediation Plan

1. **Fetch-side Guard (Primary Fix)**:
   In `scripts/fetch_kalshi_nba.py`:
   - Compare current UTC time against game `tipoff_utc`.
   - Never ingest or stamp an in-game quote as pre-game.
   - If an existing file exists on disk, **preserve previously captured pre-start quotes** rather than clobbering them when a subsequent run occurs after tip.
2. **Pre-Start Enforcement**:
   - Explicitly compute and store `pre_start: true | false | unknown` per quote based on `quoted_at < scheduled_start`.
   - Enforce `pre_start == true` for qualification and close evaluation.
3. **Cron Refinement**:
   - Refine schedule to complete well ahead of the earliest tipoffs, while relying on the fetch-side guard to guarantee integrity regardless of queue delay.
