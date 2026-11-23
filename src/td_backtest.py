"""Expanding-window walk-forward backtest for the NFL Anytime Touchdown model.

Strict temporal cutoff: for every evaluation (season S, week W), features are
built solely from prior weeks and the TouchdownModel is fit solely on prior
(season, week) rows. Compares Brier score, log loss, and decile calibration
against a naive baseline (team implied TDs × lagged season position share).
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from src.td_features import (
    DATA_DIR,
    build_td_features,
    extract_pbp_player_weeks,
    fetch_pbp_season,
    load_pbp,
)
from src.td_model import (
    DEFAULT_IMPLIED_TEAM_TOTAL,
    TouchdownModel,
    implied_total_to_expected_tds,
    nb_td_probabilities,
)


def brier_score(y_true: np.ndarray, p_pred: np.ndarray) -> float:
    return float(np.mean((p_pred - y_true) ** 2))


def log_loss(y_true: np.ndarray, p_pred: np.ndarray) -> float:
    p = np.clip(p_pred, 1e-6, 1.0 - 1e-6)
    return float(-np.mean(y_true * np.log(p) + (1.0 - y_true) * np.log(1.0 - p)))


def reliability_deciles(y_true: np.ndarray, p_pred: np.ndarray, n_bins: int = 10) -> list[dict[str, Any]]:
    """Compute reliability calibration table across quantile bins."""
    if len(y_true) == 0:
        return []
    df = pd.DataFrame({"y": y_true, "p": p_pred})
    try:
        df["bin"] = pd.qcut(df["p"], q=min(n_bins, len(df)), duplicates="drop")
    except ValueError:
        df["bin"] = pd.cut(df["p"], bins=min(n_bins, len(df)))
    rows = []
    for i, (_, grp) in enumerate(df.groupby("bin", observed=True), 1):
        mp = float(grp["p"].mean())
        ob = float(grp["y"].mean())
        rows.append({
            "bin": i,
            "n": int(len(grp)),
            "mean_pred": round(mp, 4),
            "obs_rate": round(ob, 4),
            "diff": round(ob - mp, 4),
        })
    return rows


def _attach_game_totals(
    df: pd.DataFrame,
    games: pd.DataFrame | None,
    season: int,
    week: int,
) -> pd.DataFrame:
    out = df.copy()
    if games is None or games.empty:
        out["implied_team_total"] = DEFAULT_IMPLIED_TEAM_TOTAL
        out["implied_opp_total"] = DEFAULT_IMPLIED_TEAM_TOTAL
        return out

    g = games[(games["season"] == season) & (games["week"] == week)]
    team_tot: dict[str, float] = {}
    opp_tot: dict[str, float] = {}
    for _, r in g.iterrows():
        tot = r.get("total_line")
        spr = r.get("spread_line")
        if pd.notna(tot) and pd.notna(spr):
            tot_f, spr_f = float(tot), float(spr)
            # nflverse convention: spread_line > 0 means home favored
            home_imp = (tot_f + spr_f) / 2.0
            away_imp = (tot_f - spr_f) / 2.0
            h, a = str(r["home_team"]), str(r["away_team"])
            team_tot[h] = home_imp
            opp_tot[h] = away_imp
            team_tot[a] = away_imp
            opp_tot[a] = home_imp

    out["implied_team_total"] = out["team"].map(team_tot).fillna(DEFAULT_IMPLIED_TEAM_TOTAL)
    out["implied_opp_total"] = out["team"].map(opp_tot).fillna(DEFAULT_IMPLIED_TEAM_TOTAL)
    return out


def _naive_anytime_probs(
    eval_feats: pd.DataFrame,
    prior_weeks: pd.DataFrame,
) -> np.ndarray:
    """Naive baseline: team expected TDs × lagged position share per player."""
    if prior_weeks.empty:
        return np.full(len(eval_feats), 0.20, dtype=float)

    # Lagged average TDs per player-game by position, normalized to team implied TDs
    pos_td_avg = prior_weeks.groupby("position")["total_tds"].mean().to_dict()
    global_avg = float(prior_weeks["total_tds"].mean()) if len(prior_weeks) else 0.25

    probs = []
    for _, r in eval_feats.iterrows():
        pos = str(r.get("position") or "WR")
        base_lam = float(pos_td_avg.get(pos, global_avg))
        exp_team_tds = implied_total_to_expected_tds(r.get("implied_team_total"))
        lam = max(1e-4, base_lam * (exp_team_tds / (DEFAULT_IMPLIED_TEAM_TOTAL / 7.5)))
        p_any, _ = nb_td_probabilities(lam, alpha=0.0)
        probs.append(p_any)
    return np.array(probs, dtype=float)


def run_walkforward_backtest(
    player_weeks: pd.DataFrame,
    games: pd.DataFrame | None = None,
    seasons: Iterable[int] = (2022, 2023, 2024, 2025),
    min_train_rows: int = 40,
) -> dict[str, Any]:
    """Run expanding-window walk-forward evaluation across target seasons."""
    seasons_set = {int(s) for s in seasons}
    pw = player_weeks.sort_values(["season", "week", "player_id"]).reset_index(drop=True)

    # Pre-build strictly lagged feature snapshots per (season, week)
    all_sw = sorted({(int(s), int(w)) for s, w in zip(pw["season"], pw["week"])})
    labeled_snapshots: list[pd.DataFrame] = []
    eval_preds: list[pd.DataFrame] = []

    for season, week in all_sw:
        actual = pw[(pw["season"] == season) & (pw["week"] == week)][
            ["player_id", "team", "opponent_team", "total_tds"]
        ].copy()
        if actual.empty:
            continue

        matchups = dict(zip(actual["team"].astype(str), actual["opponent_team"].astype(str)))
        feats = build_td_features(
            pw,
            target_season=season,
            target_week=week,
            target_matchups=matchups,
        )
        if feats.empty:
            continue

        feats = _attach_game_totals(feats, games, season, week)
        joined = feats.merge(
            actual[["player_id", "total_tds"]],
            on="player_id",
            how="inner",
        )
        if joined.empty:
            continue

        # Evaluate on target seasons using ONLY strictly prior labeled snapshots
        if season in seasons_set and labeled_snapshots:
            train_df = pd.concat(labeled_snapshots, ignore_index=True)
            if len(train_df) >= min_train_rows:
                model = TouchdownModel().fit(train_df, target_col="total_tds")
                pred = model.predict(joined.drop(columns=["total_tds"]))
                pred["actual_tds"] = joined["total_tds"].to_numpy(dtype=float)
                pred["y_anytime"] = (pred["actual_tds"] >= 1.0).astype(float)
                pred["y_2plus"] = (pred["actual_tds"] >= 2.0).astype(float)

                prior_pw = pw[(pw["season"] < season) | ((pw["season"] == season) & (pw["week"] < week))]
                pred["naive_p_anytime"] = _naive_anytime_probs(joined, prior_pw)
                eval_preds.append(pred)

        # Append AFTER evaluation so current week is never in its own training set
        labeled_snapshots.append(joined)

    if not eval_preds:
        return {
            "n_eval_rows": 0,
            "seasons": sorted(seasons_set),
            "metrics": {},
            "reliability_deciles": [],
            "per_season": {},
            "predictions": pd.DataFrame(),
        }

    full = pd.concat(eval_preds, ignore_index=True)
    y_any = full["y_anytime"].to_numpy(dtype=float)
    p_any = full["p_anytime_td"].to_numpy(dtype=float)
    p_naive = full["naive_p_anytime"].to_numpy(dtype=float)
    y_2p = full["y_2plus"].to_numpy(dtype=float)
    p_2p = full["p_2plus_td"].to_numpy(dtype=float)

    b_model = brier_score(y_any, p_any)
    b_naive = brier_score(y_any, p_naive)
    ll_model = log_loss(y_any, p_any)
    ll_naive = log_loss(y_any, p_naive)

    per_season: dict[int, dict[str, Any]] = {}
    for yr, grp in full.groupby("season"):
        ya = grp["y_anytime"].to_numpy(dtype=float)
        pa = grp["p_anytime_td"].to_numpy(dtype=float)
        pn = grp["naive_p_anytime"].to_numpy(dtype=float)
        per_season[int(yr)] = {
            "n": int(len(grp)),
            "obs_anytime_rate": round(float(ya.mean()), 4),
            "mean_pred_anytime": round(float(pa.mean()), 4),
            "brier_model": round(brier_score(ya, pa), 5),
            "brier_naive": round(brier_score(ya, pn), 5),
            "brier_diff": round(brier_score(ya, pa) - brier_score(ya, pn), 5),
            "log_loss_model": round(log_loss(ya, pa), 5),
            "log_loss_naive": round(log_loss(ya, pn), 5),
        }

    return {
        "n_eval_rows": int(len(full)),
        "seasons": sorted(seasons_set),
        "metrics": {
            "obs_anytime_rate": round(float(y_any.mean()), 4),
            "mean_pred_anytime": round(float(p_any.mean()), 4),
            "brier_anytime_model": round(b_model, 5),
            "brier_anytime_naive": round(b_naive, 5),
            "brier_anytime_diff": round(b_model - b_naive, 5),
            "log_loss_anytime_model": round(ll_model, 5),
            "log_loss_anytime_naive": round(ll_naive, 5),
            "log_loss_anytime_diff": round(ll_model - ll_naive, 5),
            "brier_2plus_model": round(brier_score(y_2p, p_2p), 5),
            "log_loss_2plus_model": round(log_loss(y_2p, p_2p), 5),
        },
        "reliability_deciles": reliability_deciles(y_any, p_any),
        "per_season": per_season,
        "predictions": full,
    }


def write_backtest_report(result: dict[str, Any], out_path: str | Path = "docs/TD_MODEL_BACKTEST.md") -> str:
    """Write an honest, reproducible markdown backtest report."""
    m = result.get("metrics") or {}
    lines = [
        "# NFL Anytime Touchdown Model — Walk-Forward Backtest",
        "",
        "> **Status:** Research-only (`actionable=False`, `gate_status=RESEARCH_ONLY_UNVERIFIED`).",
        "> Expanding-window walk-forward with strict temporal cutoff (`season < S` or `season == S and week < W`).",
        "",
        "## Summary Metrics",
        "",
        f"- **Evaluated player-games:** `{result.get('n_eval_rows', 0)}` across seasons `{result.get('seasons', [])}`",
    ]
    if m:
        lines.extend([
            f"- **Observed Anytime TD rate:** `{m['obs_anytime_rate']:.4f}` vs. **Mean predicted:** `{m['mean_pred_anytime']:.4f}`",
            f"- **Anytime TD Brier:** Model `{m['brier_anytime_model']:.5f}` vs. Naive `{m['brier_anytime_naive']:.5f}` (diff `{m['brier_anytime_diff']:+.5f}`; negative = better)",
            f"- **Anytime TD Log Loss:** Model `{m['log_loss_anytime_model']:.5f}` vs. Naive `{m['log_loss_anytime_naive']:.5f}` (diff `{m['log_loss_anytime_diff']:+.5f}`; negative = better)",
            f"- **2+ TD Brier / Log Loss:** `{m['brier_2plus_model']:.5f}` / `{m['log_loss_2plus_model']:.5f}`",
        ])
    else:
        lines.append("- *No evaluated rows yet — run with local `play_by_play_{2021..2025}.parquet` files.*")

    lines.extend(["", "## Per-Season Breakdown", "", "| Season | N | Obs Rate | Mean Pred | Model Brier | Naive Brier | Brier Diff | Model LogLoss |", "|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for yr, s in sorted((result.get("per_season") or {}).items()):
        lines.append(
            f"| {yr} | {s['n']} | {s['obs_anytime_rate']:.4f} | {s['mean_pred_anytime']:.4f} | "
            f"{s['brier_model']:.5f} | {s['brier_naive']:.5f} | {s['brier_diff']:+.5f} | {s['log_loss_model']:.5f} |"
        )

    lines.extend(["", "## Reliability Deciles (Anytime TD)", "", "| Bin | N | Mean Pred | Obs Rate | Obs - Pred |", "|---:|---:|---:|---:|---:|"])
    for b in result.get("reliability_deciles") or []:
        lines.append(f"| {b['bin']} | {b['n']} | {b['mean_pred']:.4f} | {b['obs_rate']:.4f} | {b['diff']:+.4f} |")

    content = "\n".join(lines) + "\n"
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description="Run expanding-window NFL Anytime TD walk-forward backtest.")
    parser.add_argument("--seasons", nargs="+", type=int, default=[2022, 2023, 2024, 2025])
    parser.add_argument("--warmup-season", type=int, default=2021)
    parser.add_argument("--fetch", action="store_true", help="Download missing nflverse PBP parquet files.")
    parser.add_argument("--out", default="docs/TD_MODEL_BACKTEST.md")
    args = parser.parse_args()

    all_seasons = sorted({args.warmup_season, *args.seasons})
    if args.fetch:
        for yr in all_seasons:
            fetch_pbp_season(yr)

    pbp = load_pbp(all_seasons)
    pw = extract_pbp_player_weeks(pbp)
    games_path = Path(DATA_DIR) / "games.csv"
    games = pd.read_csv(games_path) if games_path.exists() else None

    result = run_walkforward_backtest(pw, games=games, seasons=args.seasons)
    write_backtest_report(result, out_path=args.out)
    print(f"Wrote {args.out} (n_eval_rows={result['n_eval_rows']})")
    if result["metrics"]:
        print(result["metrics"])


if __name__ == "__main__":
    main()
