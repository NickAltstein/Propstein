# NBA Multi-Stat Prop Model — Walk-Forward Findings

> **Status:** Research-only (`actionable=False`, `gate_status=RESEARCH_ONLY_UNVERIFIED`).
> Expanding-window walk-forward with strict temporal cutoff (`game_date < target_date`).
> Per `EDGE_GATE.md`, no NBA market is actionable until prospective Kalshi CLV gates clear.

## Dataset & Scope

- **Evaluated player-games:** `3948` across `23` game dates (`['2026-10-30', '2026-11-22']`)
- **Markets evaluated:** Points (`PTS`), Rebounds (`REB`), Assists (`AST`), 3-Pointers Made (`3PM`), and covariance-adjusted `PRA`.
- **Naive baseline:** Lagged player season-mean (strictly `game_date < target_date`).

## Walk-Forward Accuracy & Calibration vs. Fair Line

| Market | N | Model MAE | Naive MAE | MAE Diff | Model RMSE | Obs P(Over Fair) | Mean Pred P(Over Fair) | Brier | Log Loss |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| POINTS | 3948 | 4.585 | 4.546 | +0.039 | 6.072 | 0.4633 | 0.4786 | 0.24874 | 0.69063 |
| REBOUNDS | 3948 | 1.852 | 1.867 | -0.014 | 2.453 | 0.4987 | 0.4935 | 0.24709 | 0.68741 |
| ASSISTS | 3948 | 1.328 | 1.325 | +0.004 | 1.826 | 0.4843 | 0.4772 | 0.24360 | 0.68055 |
| THREES | 3948 | 0.913 | 0.889 | +0.024 | 1.267 | 0.4395 | 0.4189 | 0.22195 | 0.63229 |
| PRA | 3948 | 5.946 | 5.935 | +0.011 | 7.758 | 0.4800 | 0.4842 | 0.24984 | 0.69282 |

## Kalshi Closing-Reference Tape Status

- **Captured `nba:points` rungs on `capture-data`:** `4586` (`389` distinct T-5 ladders).
- **Paired CLV status:** Prospective paired closing-line evaluation requires matching live pre-tipoff `nba_slate_{date}.json` publications against `capture-data` T-5 closes (sample size still accumulating; `EDGE_GATE.md` remains CLOSED).

## Honest Limitations

- Early-season 2026–27 sample (`< 1 month` of games) is too small to certify stable edge.
- Negative MAE diff (`Model MAE < Naive MAE`) indicates skill over season mean; positive diff indicates the model underperforms naive season mean and must be tightened before any commercial gate review.
- Non-points Kalshi closing ladders (`REB`, `AST`, `3PM`, `PRA`) are not yet captured on `capture-data`.
