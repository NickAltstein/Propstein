"""Expanding-window walk-forward evaluation for NBA PTS / REB / AST / 3PM / PRA.

Strict temporal cutoff: predictions for game_date D are fit ONLY on box scores
with game_date < D. Writes strictly honest results (including sample size and
any negative skill vs. naive season-mean baseline) to docs/NBA_FINDINGS.md.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..model.distribution import NegativeBinomialDistribution, PointsDistribution
from ..model.multistat import NBAMultiStatProjector


MARKETS = (
    ("points", "pts"),
    ("rebounds", "rebounds"),
    ("assists", "assists"),
    ("threes", "fg3m"),
    ("pra", "_pra"),
)


def _brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def _log_loss(y: np.ndarray, p: np.ndarray) -> float:
    pc = np.clip(p, 1e-6, 1.0 - 1e-6)
    return float(-np.mean(y * np.log(pc) + (1.0 - y) * np.log(1.0 - pc)))


def run_multistat_walkforward(
    df: pd.DataFrame,
    *,
    min_train_days: int = 10,
) -> dict[str, Any]:
    """Run expanding-window walk-forward evaluation across game_dates."""
    work = df.copy()
    work["game_date"] = work["game_date"].astype(str)
    for c in ("pts", "rebounds", "assists", "fg3m"):
        work[c] = pd.to_numeric(work.get(c, 0.0), errors="coerce").fillna(0.0)
    work["_pra"] = work["pts"] + work["rebounds"] + work["assists"]

    dates = sorted(work["game_date"].unique())
    eval_frames: list[pd.DataFrame] = []

    for i in range(min_train_days, len(dates)):
        d = dates[i]
        train = work[work["game_date"] < d].copy()
        test = work[(work["game_date"] == d) & (~work["dnp"])].copy()
        if train.empty or test.empty:
            continue

        model = NBAMultiStatProjector().fit(train)
        preds = model.predict(test, history=train)

        # Attach actuals and naive season-mean baseline strictly from train
        played_train = train[~train["dnp"]]
        for m_name, act_col in MARKETS:
            act_map = dict(zip(test["player_id"].astype(str), test[act_col].to_numpy(dtype=float)))
            preds[f"actual_{m_name}"] = preds["player_id"].astype(str).map(act_map)
            naive_map = played_train.groupby("player_id")[act_col].mean().to_dict()
            global_m = float(played_train[act_col].mean())
            preds[f"naive_{m_name}"] = preds["player_id"].map(naive_map).fillna(global_m)

        eval_frames.append(preds)

    if not eval_frames:
        return {"n_eval_rows": 0, "n_eval_days": 0, "date_range": [], "markets": {}}

    full = pd.concat(eval_frames, ignore_index=True)
    market_summary: dict[str, dict[str, Any]] = {}

    for m_name, _ in MARKETS:
        sub = full.dropna(subset=[f"actual_{m_name}", f"proj_{m_name}", f"fair_line_{m_name}"])
        if sub.empty:
            continue
        act = sub[f"actual_{m_name}"].to_numpy(dtype=float)
        proj = sub[f"proj_{m_name}"].to_numpy(dtype=float)
        naive = sub[f"naive_{m_name}"].to_numpy(dtype=float)
        fair = sub[f"fair_line_{m_name}"].to_numpy(dtype=float)
        p_over = sub[f"p_over_fair_{m_name}"].to_numpy(dtype=float)
        y_over = (act > fair).astype(float)

        mae_model = float(np.mean(np.abs(proj - act)))
        mae_naive = float(np.mean(np.abs(naive - act)))
        rmse_model = float(np.sqrt(np.mean((proj - act) ** 2)))
        brier_m = _brier(y_over, p_over)
        brier_const = _brier(y_over, np.full_like(y_over, 0.5))
        ll_m = _log_loss(y_over, p_over)

        market_summary[m_name] = {
            "n": int(len(sub)),
            "mae_model": round(mae_model, 3),
            "mae_naive": round(mae_naive, 3),
            "mae_diff": round(mae_model - mae_naive, 3),
            "rmse_model": round(rmse_model, 3),
            "obs_over_fair_rate": round(float(y_over.mean()), 4),
            "mean_pred_over_fair": round(float(p_over.mean()), 4),
            "brier_over_fair": round(brier_m, 5),
            "brier_vs_50pct": round(brier_m - brier_const, 5),
            "log_loss_over_fair": round(ll_m, 5),
        }

    return {
        "n_eval_rows": int(len(full)),
        "n_eval_days": int(full["game_date"].nunique()),
        "date_range": [str(full["game_date"].min()), str(full["game_date"].max())],
        "markets": market_summary,
    }


def summarize_kalshi_capture_dir(capture_dir: Path | None) -> dict[str, Any]:
    """Summarize Kalshi NBA points closing-reference tape if available."""
    if capture_dir is None or not capture_dir.exists() or not (capture_dir / "index.json").exists():
        return {"available": False, "nba_points_rungs": 0, "t5_ladders": 0}
    idx = json.loads((capture_dir / "index.json").read_text(encoding="utf-8"))
    rungs = 0
    t5_sids: set[str] = set()
    for t in idx.get("ticks") or []:
        fp = capture_dir / t["file_name"]
        if not fp.exists():
            continue
        with gzip.open(fp, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("sport") == "nba" and r.get("stat_type") == "points":
                    rungs += 1
                    off = r.get("offset_minutes")
                    if off is not None and 0.0 <= float(off) < 10.0 and r.get("snapshot_id"):
                        t5_sids.add(str(r["snapshot_id"]))
    return {"available": True, "nba_points_rungs": rungs, "t5_ladders": len(t5_sids)}


def write_nba_findings(
    result: dict[str, Any],
    kalshi_info: dict[str, Any] | None = None,
    out_path: str | Path = "docs/NBA_FINDINGS.md",
) -> str:
    """Write strictly honest NBA multi-stat walk-forward findings report."""
    lines = [
        "# NBA Multi-Stat Prop Model — Walk-Forward Findings",
        "",
        "> **Status:** Research-only (`actionable=False`, `gate_status=RESEARCH_ONLY_UNVERIFIED`).",
        "> Expanding-window walk-forward with strict temporal cutoff (`game_date < target_date`).",
        "> Per `EDGE_GATE.md`, no NBA market is actionable until prospective Kalshi CLV gates clear.",
        "",
        "## Dataset & Scope",
        "",
        f"- **Evaluated player-games:** `{result.get('n_eval_rows', 0)}` across `{result.get('n_eval_days', 0)}` game dates (`{result.get('date_range', [])}`)",
        "- **Markets evaluated:** Points (`PTS`), Rebounds (`REB`), Assists (`AST`), 3-Pointers Made (`3PM`), and covariance-adjusted `PRA`.",
        "- **Naive baseline:** Lagged player season-mean (strictly `game_date < target_date`).",
        "",
        "## Walk-Forward Accuracy & Calibration vs. Fair Line",
        "",
        "| Market | N | Model MAE | Naive MAE | MAE Diff | Model RMSE | Obs P(Over Fair) | Mean Pred P(Over Fair) | Brier | Log Loss |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m_name, _ in MARKETS:
        m = (result.get("markets") or {}).get(m_name)
        if not m:
            continue
        lines.append(
            f"| {m_name.upper()} | {m['n']} | {m['mae_model']:.3f} | {m['mae_naive']:.3f} | "
            f"{m['mae_diff']:+.3f} | {m['rmse_model']:.3f} | {m['obs_over_fair_rate']:.4f} | "
            f"{m['mean_pred_over_fair']:.4f} | {m['brier_over_fair']:.5f} | {m['log_loss_over_fair']:.5f} |"
        )

    lines.extend(["", "## Kalshi Closing-Reference Tape Status", ""])
    if kalshi_info and kalshi_info.get("available"):
        lines.append(
            f"- **Captured `nba:points` rungs on `capture-data`:** `{kalshi_info['nba_points_rungs']}` "
            f"(`{kalshi_info['t5_ladders']}` distinct T-5 ladders)."
        )
        lines.append(
            "- **Paired CLV status:** Prospective paired closing-line evaluation requires matching "
            "live pre-tipoff `nba_slate_{date}.json` publications against `capture-data` T-5 closes "
            "(sample size still accumulating; `EDGE_GATE.md` remains CLOSED)."
        )
    else:
        lines.append("- *No local `capture-data` checkout supplied during this run; Kalshi CLV gate remains CLOSED.*")

    lines.extend([
        "",
        "## Honest Limitations",
        "",
        "- Early-season 2026–27 sample (`< 1 month` of games) is too small to certify stable edge.",
        "- Negative MAE diff (`Model MAE < Naive MAE`) indicates skill over season mean; positive diff indicates the model underperforms naive season mean and must be tightened before any commercial gate review.",
        "- Non-points Kalshi closing ladders (`REB`, `AST`, `3PM`, `PRA`) are not yet captured on `capture-data`.",
    ])

    content = "\n".join(lines) + "\n"
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NBA multi-stat expanding-window walk-forward.")
    parser.add_argument("--dataset", type=Path, required=True, help="Path to nba_player_games.parquet")
    parser.add_argument("--capture-dir", type=Path, default=None, help="Optional capture-data checkout")
    parser.add_argument("--min-train-days", type=int, default=10)
    parser.add_argument("--out", default="docs/NBA_FINDINGS.md")
    args = parser.parse_args()

    df = pd.read_parquet(args.dataset)
    result = run_multistat_walkforward(df, min_train_days=args.min_train_days)
    kalshi_info = summarize_kalshi_capture_dir(args.capture_dir)
    write_nba_findings(result, kalshi_info=kalshi_info, out_path=args.out)
    print(f"Wrote {args.out} (n_eval_rows={result['n_eval_rows']}, days={result['n_eval_days']})")
    print(json.dumps(result["markets"], indent=2))


if __name__ == "__main__":
    main()
