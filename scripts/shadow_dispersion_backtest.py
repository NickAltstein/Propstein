"""Shadow dispersion backtest on nflverse receiving data (P12 / P11-D).

Baseline uses the frozen production `ReceivingModel` (`src/model.py`).
`shadow_v1` keeps the same `mu_rec`, `alpha`, `mu_ypr` and conditions only the
Gamma yards-per-catch dispersion `k` on point-in-time `prior_adot` and `prior_ay_share`.

Split (never re-run 2025 holdout):
  - Fit baseline + initial dispersion: 2021-2022
  - Tune shadow_v1 dispersion weights: 2023
  - Validation check: 2024
  - Final holdout (touched once): 2025
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln, gammainc

from src.model import ReceivingModel

TRAIN_SEASONS = (2021, 2022)
TUNE_SEASON = 2023
CHECK_SEASON = 2024
HOLDOUT_SEASON = 2025

MODEL_COLS = ["prior_rec_mean", "prior_ypc_mean", "prior_adot", "prior_ay_share"]
ROLLING_WINDOW = 8
MIN_PRIOR_GAMES = 3
BUCKETS = [
    ("0-20%", 0.0, 0.20),
    ("20-35%", 0.20, 0.35),
    ("35-65%", 0.35, 0.65),
    ("65-80%", 0.65, 0.80),
    ("80-100%", 0.80, 1.01),
]


def _season_url(season: int) -> str:
    if season >= 2025:
        return f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.parquet"
    return f"https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{season}.parquet"


def load_nflverse_receiving(seasons: Sequence[int]) -> tuple[pd.DataFrame, dict[int, dict[str, Any]]]:
    """Load nflverse receiving stats for WR/TE/RB and record missingness per season."""
    frames: list[pd.DataFrame] = []
    missing_report: dict[int, dict[str, Any]] = {}
    for s in seasons:
        df = pd.read_parquet(_season_url(int(s)))
        if "recent_team" not in df.columns and "team" in df.columns:
            df["recent_team"] = df["team"]
        df = df[df["position"].isin(["WR", "TE", "RB"])].copy()
        missing_report[int(s)] = {
            "rows": int(len(df)),
            "receiving_air_yards_na": int(df["receiving_air_yards"].isna().sum()),
            "ngs_air_yards_na": int(df["ngs_air_yards"].isna().sum()) if "ngs_air_yards" in df.columns else -1,
        }
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True)
    return combined, missing_report


def build_point_in_time_features(df: pd.DataFrame, *, window: int = ROLLING_WINDOW) -> pd.DataFrame:
    """Compute strictly point-in-time trailing features (only games strictly before t)."""
    work = df.copy()
    work = work.sort_values(["season", "week", "recent_team", "player_id"]).reset_index(drop=True)

    team_week = (
        work.groupby(["recent_team", "season", "week"], as_index=False)["receiving_air_yards"]
        .sum()
        .rename(columns={"receiving_air_yards": "team_air_yards"})
        .sort_values(["recent_team", "season", "week"])
    )
    team_week["team_ay_rolling"] = (
        team_week.groupby("recent_team")["team_air_yards"]
        .transform(lambda s: s.shift(1).rolling(window, min_periods=1).sum())
    )
    work = work.merge(
        team_week[["recent_team", "season", "week", "team_ay_rolling"]],
        on=["recent_team", "season", "week"],
        how="left",
    )

    work = work.sort_values(["player_id", "season", "week"]).reset_index(drop=True)
    g = work.groupby("player_id", group_keys=False)

    work["prior_games"] = g.cumcount()
    work["prior_rec_mean"] = g["receptions"].transform(lambda s: s.shift(1).rolling(window, min_periods=1).mean())
    work["prior_yards_sum"] = g["receiving_yards"].transform(lambda s: s.shift(1).rolling(window, min_periods=1).sum())
    work["prior_rec_sum"] = g["receptions"].transform(lambda s: s.shift(1).rolling(window, min_periods=1).sum())
    work["prior_targets_sum"] = g["targets"].transform(lambda s: s.shift(1).rolling(window, min_periods=1).sum())
    work["prior_ay_sum"] = g["receiving_air_yards"].transform(lambda s: s.shift(1).rolling(window, min_periods=1).sum())

    work["prior_ypc_mean"] = np.where(
        work["prior_rec_sum"] >= 3.0,
        np.clip(work["prior_yards_sum"] / np.maximum(work["prior_rec_sum"], 1.0), 3.0, 25.0),
        10.5,
    )
    work["prior_adot"] = np.where(
        work["prior_targets_sum"] >= 5.0,
        np.clip(work["prior_ay_sum"] / np.maximum(work["prior_targets_sum"], 1.0), -2.0, 25.0),
        8.5,
    )
    work["prior_ay_share"] = np.where(
        work["team_ay_rolling"] > 0.0,
        np.clip(work["prior_ay_sum"] / np.maximum(work["team_ay_rolling"], 1.0), 0.0, 0.8),
        0.15,
    )
    return work


def _negbin_pmf_grid(mu: np.ndarray, alpha: float, max_r: int = 36) -> np.ndarray:
    r_disp = 1.0 / max(alpha, 1e-6)
    r_vals = np.arange(max_r + 1, dtype=np.float64)[None, :]
    mu_c = np.clip(mu[:, None], 0.05, 25.0)
    p = r_disp / (r_disp + mu_c)
    log_pmf = (
        gammaln(r_vals + r_disp)
        - gammaln(r_disp)
        - gammaln(r_vals + 1.0)
        + r_disp * np.log(p)
        + r_vals * np.log(1.0 - p)
    )
    pmf = np.exp(log_pmf)
    pmf[:, -1] += np.maximum(0.0, 1.0 - pmf.sum(axis=1))
    return pmf


def compound_surv_probs(
    mu_rec: np.ndarray,
    alpha_nb: float,
    mu_ypc: np.ndarray,
    k_ypc: np.ndarray,
    line: np.ndarray,
    max_r: int = 36,
) -> np.ndarray:
    """Exact P(Y > line) under NegBin(mu_rec, alpha_nb) x Gamma(shape=k_ypc, mean=mu_ypc)."""
    pmf = _negbin_pmf_grid(mu_rec, alpha_nb, max_r=max_r)
    surv = np.zeros(len(mu_rec), dtype=np.float64)
    pos_line = np.maximum(line, 0.0)
    for r in range(1, max_r + 1):
        shape = r * k_ypc
        scale = mu_ypc / np.maximum(k_ypc, 0.1)
        cdf_r = gammainc(shape, pos_line / np.maximum(scale, 1e-6))
        surv += pmf[:, r] * (1.0 - cdf_r)
    return np.clip(surv, 1e-6, 1.0 - 1e-6)


def compound_crps(
    mu_rec: np.ndarray,
    alpha_nb: float,
    mu_ypc: np.ndarray,
    k_ypc: np.ndarray,
    y_actual: np.ndarray,
) -> np.ndarray:
    """Compute CRPS via numerical integration over 0..220 yards."""
    y_grid = np.linspace(0.0, 220.0, 111)
    dy = float(y_grid[1] - y_grid[0])
    pmf = _negbin_pmf_grid(mu_rec, alpha_nb, max_r=36)
    cdf = np.broadcast_to(pmf[:, 0:1], (len(mu_rec), len(y_grid))).copy()
    for r in range(1, 37):
        shape = (r * k_ypc)[:, None]
        scale = (mu_ypc / np.maximum(k_ypc, 0.1))[:, None]
        cdf += pmf[:, r : r + 1] * gammainc(shape, y_grid[None, :] / np.maximum(scale, 1e-6))
    indicator = (y_grid[None, :] >= y_actual[:, None]).astype(np.float64)
    return np.sum((cdf - indicator) ** 2, axis=1) * dy


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Holm-Bonferroni step-down multiple-comparison adjustment."""
    m = len(p_values)
    if m == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda kv: kv[1])
    adjusted = [0.0] * m
    running_max = 0.0
    for rank, (orig_idx, pval) in enumerate(indexed):
        adj = min(1.0, (m - rank) * float(pval))
        running_max = max(running_max, adj)
        adjusted[orig_idx] = round(running_max, 6)
    return adjusted


@dataclass(frozen=True)
class FittedShadowParams:
    baseline_model: ReceivingModel
    alpha_k: float
    beta_adot: float
    beta_ay_share: float
    adot_mean: float
    adot_std: float
    ays_mean: float
    ays_std: float


def fit_and_tune(train_df: pd.DataFrame, tune_df: pd.DataFrame) -> FittedShadowParams:
    """Fit frozen production ReceivingModel on train_df (2021-2022), tune shadow_v1 k on tune_df (2023)."""
    baseline = ReceivingModel.fit(train_df, cols=MODEL_COLS)

    tune_pred = baseline.predict_params(tune_df)
    mu_rec = tune_pred["mu_rec"].to_numpy()
    mu_ypr = tune_pred["mu_ypr"].to_numpy()
    y_actual = tune_df["receiving_yards"].to_numpy()
    lines = np.clip(np.round(mu_rec * mu_ypr) - 0.5, 4.5, 119.5)
    y_hit = (y_actual > lines).astype(np.float64)

    adot = tune_df["prior_adot"].to_numpy()
    ays = tune_df["prior_ay_share"].to_numpy()
    adot_m, adot_s = float(np.mean(adot)), max(float(np.std(adot)), 1e-3)
    ays_m, ays_s = float(np.mean(ays)), max(float(np.std(ays)), 1e-3)
    z_adot = (adot - adot_m) / adot_s
    z_ays = (ays - ays_m) / ays_s

    def _loss_shadow(theta: np.ndarray) -> float:
        a_k, b1, b2 = float(theta[0]), float(theta[1]), float(theta[2])
        k = np.exp(np.clip(a_k + b1 * z_adot + b2 * z_ays, -1.5, 3.0))
        p = compound_surv_probs(mu_rec, baseline.alpha, mu_ypr, k, lines)
        return float(-np.mean(y_hit * np.log(p) + (1.0 - y_hit) * np.log(1.0 - p)))

    res_sh = minimize(
        _loss_shadow,
        x0=np.array([math.log(baseline.k), 0.0, 0.0]),
        bounds=[(-1.5, 3.0), (-1.5, 1.5), (-1.5, 1.5)],
    )
    return FittedShadowParams(
        baseline_model=baseline,
        alpha_k=float(res_sh.x[0]),
        beta_adot=float(res_sh.x[1]),
        beta_ay_share=float(res_sh.x[2]),
        adot_mean=adot_m,
        adot_std=adot_s,
        ays_mean=ays_m,
        ays_std=ays_s,
    )


def _bootstrap_pval(deltas: np.ndarray) -> float:
    n = len(deltas)
    if n == 0:
        return 1.0
    p_pos = (float(np.sum(deltas >= 0.0)) + 1.0) / (n + 1.0)
    p_neg = (float(np.sum(deltas <= 0.0)) + 1.0) / (n + 1.0)
    return min(1.0, 2.0 * min(p_pos, p_neg))


def evaluate_split(
    eval_df: pd.DataFrame,
    params: FittedShadowParams,
    *,
    n_boot: int = 300,
    seed: int = 20261119,
) -> dict[str, Any]:
    """Evaluate baseline vs shadow_v1 using baseline probability as the common bucket key."""
    eval_lines = np.array([24.5, 39.5, 59.5, 79.5])
    pred = params.baseline_model.predict_params(eval_df)
    mu_rec = pred["mu_rec"].to_numpy()
    mu_ypr = pred["mu_ypr"].to_numpy()
    y_actual = eval_df["receiving_yards"].to_numpy()
    players = eval_df["player_id"].to_numpy()

    z_adot = (eval_df["prior_adot"].to_numpy() - params.adot_mean) / params.adot_std
    z_ays = (eval_df["prior_ay_share"].to_numpy() - params.ays_mean) / params.ays_std
    k_base = np.full(len(eval_df), params.baseline_model.k)
    k_sh = np.exp(np.clip(params.alpha_k + params.beta_adot * z_adot + params.beta_ay_share * z_ays, -1.5, 3.0))

    crps_base = compound_crps(mu_rec, params.baseline_model.alpha, mu_ypr, k_base, y_actual)
    crps_sh = compound_crps(mu_rec, params.baseline_model.alpha, mu_ypr, k_sh, y_actual)

    all_p_base, all_p_sh, all_y, all_players = [], [], [], []
    for L in eval_lines:
        line_vec = np.full(len(eval_df), L)
        all_p_base.append(np.clip(params.baseline_model.p_yards_over_vec(mu_rec, mu_ypr, line_vec), 1e-6, 1.0 - 1e-6))
        all_p_sh.append(compound_surv_probs(mu_rec, params.baseline_model.alpha, mu_ypr, k_sh, line_vec))
        all_y.append((y_actual > L).astype(np.float64))
        all_players.append(players)

    p_b = np.concatenate(all_p_base)
    p_s = np.concatenate(all_p_sh)
    y_h = np.concatenate(all_y)
    pl_all = np.concatenate(all_players)

    ll_b = -(y_h * np.log(p_b) + (1.0 - y_h) * np.log(1.0 - p_b))
    ll_s = -(y_h * np.log(p_s) + (1.0 - y_h) * np.log(1.0 - p_s))

    unique_players = np.unique(players)
    rng = np.random.default_rng(seed)
    p_to_idx_game = {p: np.where(players == p)[0] for p in unique_players}
    p_to_idx_line = {p: np.where(pl_all == p)[0] for p in unique_players}

    boot_dl, boot_dc = [], []
    for _ in range(n_boot):
        samp_p = rng.choice(unique_players, size=len(unique_players), replace=True)
        g_idx = np.concatenate([p_to_idx_game[p] for p in samp_p])
        l_idx = np.concatenate([p_to_idx_line[p] for p in samp_p])
        boot_dc.append(float(np.mean(crps_sh[g_idx] - crps_base[g_idx])))
        boot_dl.append(float(np.mean(ll_s[l_idx] - ll_b[l_idx])))

    boot_dl_arr = np.array(boot_dl)
    boot_dc_arr = np.array(boot_dc)
    raw_p_overall_ll = _bootstrap_pval(boot_dl_arr)
    raw_p_overall_crps = _bootstrap_pval(boot_dc_arr)

    # Common bucket key: baseline probability p_b so both models compare identical rows
    bucket_rows: list[dict[str, Any]] = []
    bucket_raw_ps: list[float] = []
    for b_name, lo, hi in BUCKETS:
        mask = (p_b >= lo) & (p_b < hi)
        n_obs = int(mask.sum())
        pred_b = float(np.mean(p_b[mask])) if n_obs else 0.0
        pred_s = float(np.mean(p_s[mask])) if n_obs else 0.0
        act = float(np.mean(y_h[mask])) if n_obs else 0.0
        ll_diff = float(np.mean(ll_s[mask] - ll_b[mask])) if n_obs else 0.0

        b_boot = []
        if n_obs > 0:
            b_players = np.unique(pl_all[mask])
            bp_idx = {p: np.where((pl_all == p) & mask)[0] for p in b_players}
            for _ in range(n_boot):
                sp = rng.choice(b_players, size=len(b_players), replace=True)
                idx = np.concatenate([bp_idx[p] for p in sp])
                b_boot.append(float(np.mean(ll_s[idx] - ll_b[idx])))
        b_arr = np.array(b_boot)
        raw_p = _bootstrap_pval(b_arr)
        bucket_raw_ps.append(raw_p)
        ci_lo = float(np.percentile(b_arr, 2.5)) if len(b_arr) else 0.0
        ci_hi = float(np.percentile(b_arr, 97.5)) if len(b_arr) else 0.0

        bucket_rows.append({
            "bucket": b_name,
            "n_obs": n_obs,
            "base_pred": round(pred_b, 4),
            "shadow_pred": round(pred_s, 4),
            "actual_rate": round(act, 4),
            "base_cal_err": round(abs(pred_b - act), 4),
            "shadow_cal_err": round(abs(pred_s - act), 4),
            "delta_log_score": round(ll_diff, 6),
            "delta_log_score_ci95": [round(ci_lo, 6), round(ci_hi, 6)],
            "raw_p": round(raw_p, 6),
        })

    # Holm correction across 5 bucket log-score tests + 2 overall tests (7 tests total)
    all_raw_p = [*bucket_raw_ps, raw_p_overall_ll, raw_p_overall_crps]
    all_holm_p = holm_adjust(all_raw_p)
    for idx, row in enumerate(bucket_rows):
        hp = all_holm_p[idx]
        row["holm_p"] = hp
        if hp < 0.05:
            row["verdict"] = "improves" if row["delta_log_score"] < 0 else "worse"
        else:
            row["verdict"] = "no evidence of difference"

    return {
        "n_games": int(len(eval_df)),
        "n_players": int(len(unique_players)),
        "baseline_log_score": round(float(np.mean(ll_b)), 6),
        "shadow_v1_log_score": round(float(np.mean(ll_s)), 6),
        "delta_log_score": round(float(np.mean(ll_s - ll_b)), 6),
        "delta_log_score_ci95": [round(float(np.percentile(boot_dl_arr, 2.5)), 6), round(float(np.percentile(boot_dl_arr, 97.5)), 6)],
        "raw_p_log_score": round(raw_p_overall_ll, 6),
        "holm_p_log_score": all_holm_p[5],
        "baseline_crps": round(float(np.mean(crps_base)), 4),
        "shadow_v1_crps": round(float(np.mean(crps_sh)), 4),
        "delta_crps": round(float(np.mean(crps_sh - crps_base)), 4),
        "delta_crps_ci95": [round(float(np.percentile(boot_dc_arr, 2.5)), 4), round(float(np.percentile(boot_dc_arr, 97.5)), 4)],
        "raw_p_crps": round(raw_p_overall_crps, 6),
        "holm_p_crps": all_holm_p[6],
        "buckets": bucket_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run nflverse shadow dispersion backtest.")
    parser.add_argument("--include-2025-holdout", action="store_true", help="Run 2025 final holdout once.")
    parser.add_argument("--out-json", type=Path, default=Path("reports/shadow_dispersion_backtest.json"))
    args = parser.parse_args()

    seasons = [*TRAIN_SEASONS, TUNE_SEASON, CHECK_SEASON]
    if args.include_2025_holdout:
        seasons.append(HOLDOUT_SEASON)

    raw, missing = load_nflverse_receiving(seasons)
    feat = build_point_in_time_features(raw)
    elig = feat[feat["prior_games"] >= MIN_PRIOR_GAMES].copy()

    train_df = elig[elig["season"].isin(TRAIN_SEASONS)].copy()
    tune_df = elig[elig["season"] == TUNE_SEASON].copy()
    check_df = elig[elig["season"] == CHECK_SEASON].copy()

    params = fit_and_tune(train_df, tune_df)
    results: dict[str, Any] = {
        "split": {
            "fit": list(TRAIN_SEASONS),
            "tune": TUNE_SEASON,
            "check": CHECK_SEASON,
            "holdout": HOLDOUT_SEASON if args.include_2025_holdout else None,
        },
        "missing_by_season": missing,
        "params": {
            "baseline_alpha_nb": round(params.baseline_model.alpha, 4),
            "baseline_k": round(params.baseline_model.k, 4),
            "shadow_alpha_k": round(params.alpha_k, 4),
            "shadow_beta_adot": round(params.beta_adot, 4),
            "shadow_beta_ay_share": round(params.beta_ay_share, 4),
        },
        "check_2024": evaluate_split(check_df, params),
    }
    if args.include_2025_holdout:
        holdout_df = elig[elig["season"] == HOLDOUT_SEASON].copy()
        results["holdout_2025"] = evaluate_split(holdout_df, params)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
