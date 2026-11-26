# AMEND-005 (DRAFT): Kalshi as the NFL close source

- **Status:** DRAFT for Nick's review. Not approved, not effective, grades nothing.
  It edits nothing in `docs/EDGE_GATE.md`, PINS, the approved plan or any
  published result. If approved, the edits in §4 are made in a separate change.
- **Effective UTC timestamp:** `EFFECTIVE_DATE_SLOT` (not set).
- **Depends on:** AMEND-003 (PR #196) and the §14 values from draft PR #185.
- **Evidence:** `research/kalshi-nfl-close-source/` on branch
  `research/kalshi-nfl-close-source`.

## 1. Why

At a T-5 cutoff with spread ≤ 5.0pp and a $100 buy moving the price ≤ 1.0pp
(the §14 picks in the Nov 26 options sheet), 8.6% of Polymarket NFL closes pass
(21 of 564 pre-start markets, 3.7%). Polymarket is NFL's only registered close
source, so NFL confirmatory accrual all but stops under those floors. This
amendment registers Kalshi for NFL only if Kalshi's NFL player-prop books are
measured, not assumed, to survive the same checks.

## 2. Decision rule (set by Nick on 2026-11-26, before the measurement)

Register Kalshi for NFL only if **most** (more than 50%) Kalshi NFL player-prop
markets survive. A market survives when it has a fresh T-5 close (a capture
within 15 minutes before scheduled start minus 5 minutes) that passes both
floors. If most do not survive, NFL stays out rather than getting a thin close
source.

## 3. Measurement

Method: `research/kalshi-nfl-close-source/analyze_close_quality.py`, applied
unchanged to both sources. Run on `data/close_quality_captures.jsonl` it
reproduces the options sheet (Polymarket NFL 8.6% of closes; Kalshi NBA 91.3%
of closes, 60.1% of markets).

- Price impact: average fill on a $100 buy from the best ask, within the 5
  captured levels, minus the best ask. Unfilled counts as a fail.
- Kalshi books are captured with the same normalizer
  (`src.close_quality_capture.normalize_kalshi_capture`), every 3 minutes from
  T-75 to T+10, in a branch-only research workflow (not the GitHub cron).
- Kalshi series: the headline single-game player props (passing, rushing and
  receiving yards, receptions, passing TDs, completions, attempts, rushing
  attempts, rush+rec yards, interceptions). Ladder, escalator and "protected"
  variants are left out. Anytime TD did not match a game and is not in the
  sample.

### Results

RESULTS_TABLE

### The "115% spread check"

No written definition of a 115% check was found in the repo, the §14 options
sheet or the PRs. The column above uses one reading: YES ask + NO ask ≤ 1.15
(for Polymarket, NO ask = 1 − YES bid, since the two outcome books mirror).
**This reading needs Nick's confirmation.** Under it, 88.5% of Polymarket
closes also pass, so it does not separate the two sources the way the §14
floors do.

## 4. Change (if approved)

RECOMMENDATION

`docs/EDGE_GATE.md` §3, NFL column, for signals registered on or after the
effective timestamp:

| Field | NFL (new) |
|---|---|
| Closing reference provider | `kalshi`. The close must carry that same tag (§13). |
| Approved entry books | `kalshi` only. |
| Source-disagreement tolerance | 5.0pp of de-vigged probability, only between two closes that share the `kalshi` tag. |

Everything else in the NFL column is unchanged. §14 floors apply to Kalshi NFL
closes at the same values as Kalshi NBA.

The entry book has to move too. §13 matches a close to an entry only when both
carry the same `quote_source` tag, so a Polymarket entry against a Kalshi close
is unmatched. NFL therefore moves to Kalshi entries and Kalshi closes, as NBA
did on 2026-11-16. Polymarket NFL rows registered before the effective
timestamp keep their tag and stay in their own source class (§13 never averages
classes).

## 5. Open items for review

1. Confirm what the 115% check is (§3).
2. Sample size: see the results table. Thanksgiving games only, so treat it as
   directional; a re-measure on a normal Sunday slate before the effective date
   is cheap.
3. Whether a source switch inside the current NFL attempt counts as a new
   attempt under §1 ("maximum two prospective attempts"; a second attempt must
   be registered before new observations).
4. NFL entry pricing from Kalshi does not exist yet: `scripts/fetch_kalshi_nba.py`
   covers NBA only, and the close-quality capture reads Polymarket for NFL.
5. Kalshi NFL props are integer "N+" ladders (40+ yards = over 39.5). Exact-line
   matching (§3) is unaffected because entry and close would both be Kalshi.
