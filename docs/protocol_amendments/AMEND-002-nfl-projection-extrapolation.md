# AMEND-002 — NFL projection extrapolation guards

Status: **protocol amendment.** Effective for model runs **on or after the merge of this change**. Prospective only.

This amendment does not edit `docs/EDGE_GATE.md`. The confirmatory size, checkpoints, hurdles, clustering procedure and cross-sport rules are unchanged. It changes how the NFL receiving model turns features into projections, so it ships as a new model version: **`negbin-gamma-v3.4`** (`src/freeze_slate.py`). Records produced under v3.3 keep their version and are never re-priced.

## Problem

`ReceivingModel` predicts receptions and yards per catch with log-link GLMs (`exp(X·β)`). A feature far outside the training range is amplified exponentially. Two examples:

- Week 10, 2026: one 19-target game pushed Puka Nacua's target forecast to 14.2. The model projected **14.6 receptions on 14.2 forecast targets** (catch rate above 100%), 175.7 yards, and a 165.0-yard fair line.
- The graded slates (`viewer/feeds/grades/nfl_*.json`) show outcomes landing over the model's fair line 31–44% of the time in Weeks 7–9 (55–59% in Week 1). The calibrated target is about 50%.

A walk-forward refit (train on every week before *w*, predict *w*) shows no overall bias. The top decile of projections, though, is over-projected:

| Season (weeks) | Receptions, top-10% bias | Yards, top-10% bias |
|---|---|---|
| 2025 (3–18), n = 3,088 | −0.79 | −11.0 |
| 2026 (3–9), n = 1,339 | −0.39 | −6.7 |

## Change (`src/model.py`, `ReceivingModel.predict_params`, prediction time only)

1. **Feature clip.** Standardized features are clipped to ±`FEATURE_Z_CLIP` = **2.5** before the linear predictor. Fitting is unchanged.
2. **Catch-rate cap.** For players with forecast targets `f_targets` ≥ `CAP_MIN_TARGETS` = **3**, `mu_rec` ≤ `MAX_CATCH_RATE` (**0.80**) × `f_targets`. Below 3 forecast targets no cap applies, because low-volume backs often catch every target. Rows without a target forecast are not capped.

Nothing else changes: the dispersion (alpha = 0.1097), the Gamma shape (k = 1.555), the fair-line construction, the edge hurdle and the qualification policy.

## Selection and disclosure

Candidates were scored by walk-forward refits:
- feature clip at ±3 / ±2.5 / ±2.0
- catch-rate cap at 0.85 / 0.80
- combinations of the two

**Selection used 2025 only.** 2026 Weeks 3–9 served as a check. Those weeks had **already been observed** (and partly graded) when this was written, so the 2026 figures below are confirmatory, not blind. The ±2.0 clip was rejected because it over-corrected in 2026 (top-decile receptions bias +0.35) and lowered the log-likelihood.

The committed version, measured against the original:

| Season | Rec bias (top 10%) | Yards bias (top 10%) | Rec MAE | Yards MAE | NB log-lik (receptions) |
|---|---|---|---|---|---|
| 2025, v3.3 | −0.10 (−0.79) | −1.2 (−11.0) | 1.490 | 20.05 | −1.9269 |
| 2025, v3.4 | −0.07 (−0.52) | −0.8 (−7.3) | 1.483 | 19.92 | −1.9245 |
| 2026, v3.3 | +0.05 (−0.39) | +0.8 (−6.7) | 1.510 | 20.80 | −1.9587 |
| 2026, v3.4 | +0.08 (−0.05) | +1.1 (−2.5) | 1.504 | 20.71 | −1.9571 |

Week 10 effect on the highest projections (same inputs):

| Player | Receptions | Yards | Fair line (yds) |
|---|---|---|---|
| Puka Nacua | 14.6 → 8.0 | 175.7 → 99.9 | 165.0 → 92.0 |
| CeeDee Lamb | 10.7 → 7.8 | 144.7 → 103.1 | 135.0 → 95.0 |
| Ja'Marr Chase | 9.1 → 8.1 | 117.2 → 104.5 | 108.5 → 96.0 |
| Amon-Ra St. Brown | 8.6 → 7.6 | 104.2 → 92.8 | 96.0 → 85.0 |

`src/week1.py`'s regression assertion (mean P(over fair receptions line) in [0.46, 0.54]) passes: 0.474.

## Forward only

- v3.4 applies to model runs after merge. Earlier slates, snapshots, journals and ledger rows keep their v3.3 values and version.
- The weekly grader (`scripts/grade_nfl_week.py`) settles each week with the projections that were published for it, so v3.3 and v3.4 weeks stay separately attributable.
- No already-collected signal is re-qualified under this amendment.
