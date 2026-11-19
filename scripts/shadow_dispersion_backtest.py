"""Shadow dispersion backtest on nflverse receiving data (P11 Part D).

Compares frozen NegBin(receptions) x Gamma(yards-per-catch) baseline with pooled
dispersion against `shadow_v1` (dispersion conditioned on point-in-time prior_adot
and prior_ay_share). Never called by production code; no swap.
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

TRAIN_SEASONS = (2021, 2022)
TEST_SEASONS = (2023, 2024)
ROLLING_WINDOW = 8
MIN_PRIOR_GAMES = 3
BUCKETS = [
    ("0-20%", 0.0, 0.20),
    ("20-35%", 0.20, 0.35),
    ("35-65%", 0.35, 0.65),
    ("65-80%", 0.65, 0.80),
    ("80-100%", 0.80, 1.01),
]


def load_nflverse_receiving(seasons: Sequence[int]) -> tuple[pd.DataFrame, dict[int, dict[str, int]]]:
    """Load nflverse player_stats for WR/TE/RB and record missingness per season."""
    frames: list[pd.DataFrame] = []
    missing_report: dict[int, dict[str, int]] = {}
    for s in seasons:
        url = f"https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{s}.parquet"
        df = pd.read_parquet(url)
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

    # Team-week total receiving_air_yards, shifted by 1 week per team so game t never sees week t
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


def _negbin_pmf_grid(mu: np.ndarray, r_disp: float, max_r: int = 22) -> np.ndarray:
    """Exact NegBin PMF for r = 0..max_r; shape (N, max_r + 1)."""
    r_vals = np.arange(max_r + 1, dtype=np.float64)[None, :]
    mu_c = np.clip(mu[:, None], 0.05, 20.0)
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
    r_rec: float,
    mu_ypc: np.ndarray,
    k_ypc: np.ndarray,
    line: np.ndarray,
    max_r: int = 22,
) -> np.ndarray:
    """Exact P(Y > line) under NegBin(mu_rec, r_rec) x Gamma(shape=k_ypc, mean=mu_ypc)."""
    pmf = _negbin_pmf_grid(mu_rec, r_rec, max_r=max_r)
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
    r_rec: float,
    mu_ypc: np.ndarray,
    k_ypc: np.ndarray,
    y_actual: np.ndarray,
    y_grid: np.ndarray | None = None,
) -> np.ndarray:
    """Compute CRPS via numerical integration over y_grid (0..220 yards)."""
    if y_grid is None:
        y_grid = np.linspace(0.0, 220.0, 111)
    dy = float(y_grid[1] - y_grid[0])
    pmf = _negbin_pmf_grid(mu_rec, r_rec, max_r=22)

    # F(y) shape: (N, G)
    cdf = np.broadcast_to(pmf[:, 0:1], (len(mu_rec), len(y_grid))).copy()
    for r in range(1, 23):
        shape = (r * k_ypc)[:, None]
        scale = (mu_ypc / np.maximum(k_ypc, 0.1))[:, None]
        cdf += pmf[:, r : r + 1] * gammainc(shape, y_grid[None, :] / np.maximum(scale, 1e-6))

    indicator = (y_grid[None, :] >= y_actual[:, None]).astype(np.float64)
    return np.sum((cdf - indicator) ** 2, axis=1) * dy


@dataclass(frozen=True)
class FittedShadowParams:
    r_rec: float
    k_baseline: float
    alpha: float
    beta_adot: float
    beta_ay_share: float
    adot_mean: float
    adot_std: float
    ays_mean: float
    ays_std: float


def fit_models(train_df: pd.DataFrame) -> FittedShadowParams:
    """Fit baseline pooled k and shadow_v1 k(adot, ay_share) strictly on train_df."""
    mu_rec = np.clip(train_df["prior_rec_mean"].to_numpy(), 0.2, 15.0)
    y_rec = train_df["receptions"].to_numpy()
    # Method-of-moments estimate for NegBin receptions dispersion r_rec
    var_rec = float(np.mean((y_rec - mu_rec) ** 2))
    mean_mu = float(np.mean(mu_rec))
    excess = max(0.25, var_rec - mean_mu)
    r_rec = float(np.clip((mean_mu ** 2) / excess, 1.5, 25.0))

    mu_ypc = np.clip(train_df["prior_ypc_mean"].to_numpy(), 4.0, 22.0)
    y_actual = train_df["receiving_yards"].to_numpy()
    # Evaluate binary log-loss across standard yardage lines to fit dispersion k
    lines = np.clip(np.round(mu_rec * mu_ypc) - 0.5, 4.5, 119.5)
    y_hit = (y_actual > lines).astype(np.float64)

    def _loss_base(log_k: np.ndarray) -> float:
        k = np.full(len(train_df), math.exp(float(log_k[0])))
        p = compound_surv_probs(mu_rec, r_rec, mu_ypc, k, lines)
        return float(-np.mean(y_hit * np.log(p) + (1.0 - y_hit) * np.log(1.0 - p)))

    res_base = minimize(_loss_base, x0=np.array([math.log(2.0)]), bounds=[(-1.0, 3.0)])
    k_base = float(math.exp(res_base.x[0]))

    adot = train_df["prior_adot"].to_numpy()
    ays = train_df["prior_ay_share"].to_numpy()
    adot_m, adot_s = float(np.mean(adot)), max(float(np.std(adot)), 1e-3)
    ays_m, ays_s = float(np.mean(ays)), max(float(np.std(ays)), 1e-3)
    z_adot = (adot - adot_m) / adot_s
    z_ays = (ays - ays_m) / ays_s

    def _loss_shadow(theta: np.ndarray) -> float:
        alpha, b1, b2 = float(theta[0]), float(theta[1]), float(theta[2])
        k = np.exp(np.clip(alpha + b1 * z_adot + b2 * z_ays, -1.5, 3.5))
        p = compound_surv_probs(mu_rec, r_rec, mu_ypc, k, lines)
        return float(-np.mean(y_hit * np.log(p) + (1.0 - y_hit) * np.log(1.0 - p)))

    res_sh = minimize(
        _loss_shadow,
        x0=np.array([math.log(k_base), 0.0, 0.0]),
        bounds=[(-1.5, 3.5), (-1.5, 1.5), (-1.5, 1.5)],
    )
    return FittedShadowParams(
        r_rec=r_rec,
        k_baseline=k_base,
        alpha=float(res_sh.x[0]),
        beta_adot=float(res_sh.x[1]),
        beta_ay_share=float(res_sh.x[2]),
        adot_mean=adot_m,
        adot_std=adot_s,
        ays_mean=ays_m,
        ays_std=ays_s,
    )


def evaluate_oos(
    test_df: pd.DataFrame,
    params: FittedShadowParams,
    *,
    n_boot: int = 300,
    seed: int = 20261119,
) -> dict[str, Any]:
    """Evaluate baseline vs shadow_v1 out-of-sample with player-clustered bootstrap CIs."""
    # Evaluate across 4 standard lines per game so all 5 probability buckets are populated
    eval_lines = np.array([24.5, 39.5, 59.5, 79.5])
    mu_rec = np.clip(test_df["prior_rec_mean"].to_numpy(), 0.2, 15.0)
    mu_ypc = np.clip(test_df["prior_ypc_mean"].to_numpy(), 4.0, 22.0)
    y_actual = test_df["receiving_yards"].to_numpy()
    players = test_df["player_id"].to_numpy()

    z_adot = (test_df["prior_adot"].to_numpy() - params.adot_mean) / params.adot_std
    z_ays = (test_df["prior_ay_share"].to_numpy() - params.ays_mean) / params.ays_std
    k_base = np.full(len(test_df), params.k_baseline)
    k_sh = np.exp(np.clip(params.alpha + params.beta_adot * z_adot + params.beta_ay_share * z_ays, -1.5, 3.5))

    crps_base = compound_crps(mu_rec, params.r_rec, mu_ypc, k_base, y_actual)
    crps_sh = compound_crps(mu_rec, params.r_rec, mu_ypc, k_sh, y_actual)

    # Stack across the 4 evaluation lines for log score & probability-bucket calibration
    all_p_base, all_p_sh, all_y, all_players = [], [], [], []
    for L in eval_lines:
        line_vec = np.full(len(test_df), L)
        all_p_base.append(compound_surv_probs(mu_rec, params.r_rec, mu_ypc, k_base, line_vec))
        all_p_sh.append(compound_surv_probs(mu_rec, params.r_rec, mu_ypc, k_sh, line_vec))
        all_y.append((y_actual > L).astype(np.float64))
        all_players.append(players)

    p_b = np.concatenate(all_p_base)
    p_s = np.concatenate(all_p_sh)
    y_h = np.concatenate(all_y)
    pl_all = np.concatenate(all_players)

    ll_b = -(y_h * np.log(p_b) + (1.0 - y_h) * np.log(1.0 - p_b))
    ll_s = -(y_h * np.log(p_s) + (1.0 - y_h) * np.log(1.0 - p_s))

    # Player-clustered bootstrap CI on delta log-score (shadow - baseline; negative = shadow better)
    # and delta CRPS
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

    bucket_rows: list[dict[str, Any]] = []
    for b_name, lo, hi in BUCKETS:
        mask_b = (p_b >= lo) & (p_b < hi)
        mask_s = (p_s >= lo) & (p_s < hi)
        n_b = int(mask_b.sum())
        n_s = int(mask_s.sum())
        pred_b = float(np.mean(p_b[mask_b])) if n_b else 0.0
        act_b = float(np.mean(y_h[mask_b])) if n_b else 0.0
        pred_s = float(np.mean(p_s[mask_s])) if n_s else 0.0
        act_s = float(np.mean(y_h[mask_s])) if n_s else 0.0
        ll_diff_b = float(np.mean(ll_s[mask_b] - ll_b[mask_b])) if n_b else 0.0

        # Clustered CI for bucket log-score delta (grouped by baseline bucket membership)
        b_boot = []
        if n_b > 0:
            b_players = np.unique(pl_all[mask_b])
            bp_idx = {p: np.where((pl_all == p) & mask_b)[0] for p in b_players}
            for _ in range(n_boot):
                sp = rng.choice(b_players, size=len(b_players), replace=True)
                idx = np.concatenate([bp_idx[p] for p in sp])
                b_boot.append(float(np.mean(ll_s[idx] - ll_b[idx])))
        ci_lo = float(np.percentile(b_boot, 2.5)) if b_boot else 0.0
        ci_hi = float(np.percentile(b_boot, 97.5)) if b_boot else 0.0

        bucket_rows.append({
            "bucket": b_name,
            "n_base": n_b,
            "n_shadow": n_s,
            "base_pred": round(pred_b, 4),
            "base_actual": round(act_b, 4),
            "base_cal_err": round(abs(pred_b - act_b), 4),
            "shadow_pred": round(pred_s, 4),
            "shadow_actual": round(act_s, 4),
            "shadow_cal_err": round(abs(pred_s - act_s), 4),
            "delta_log_score": round(ll_diff_b, 6),
            "delta_log_score_ci95": [round(ci_lo, 6), round(ci_hi, 6)],
        })

    return {
        "n_test_games": int(len(test_df)),
        "n_test_players": int(len(unique_players)),
        "baseline_log_score": round(float(np.mean(ll_b)), 6),
        "shadow_v1_log_score": round(float(np.mean(ll_s)), 6),
        "delta_log_score": round(float(np.mean(ll_s - ll_b)), 6),
        "delta_log_score_ci95": [round(float(np.percentile(boot_dl, 2.5)), 6), round(float(np.percentile(boot_dl, 97.5)), 6)],
        "baseline_crps": round(float(np.mean(crps_base)), 4),
        "shadow_v1_crps": round(float(np.mean(crps_sh)), 4),
        "delta_crps": round(float(np.mean(crps_sh - crps_base)), 4),
        "delta_crps_ci95": [round(float(np.percentile(boot_dc, 2.5)), 4), round(float(np.percentile(boot_dc, 97.5)), 4)],
        "buckets": bucket_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run nflverse shadow dispersion backtest.")
    parser.add_argument("--out-json", type=Path, default=Path("reports/shadow_dispersion_backtest.json"))
    args = parser.parse_args()

    raw, missing = load_nflverse_receiving((*TRAIN_SEASONS, *TEST_SEASONS))
    feat = build_point_in_time_features(raw)
    eligible = feat[feat["prior_games"] >= MIN_PRIOR_GAMES].copy()

    train_df = eligible[eligible["season"].isin(TRAIN_SEASONS)].copy()
    test_df = eligible[eligible["season"].isin(TEST_SEASONS)].copy()
    params = fit_models(train_df)
    summary = evaluate_oos(test_df, params)

    out = {
        "train_seasons": list(TRAIN_SEASONS),
        "test_seasons": list(TEST_SEASONS),
        "missing_by_season": missing,
        "params": {
            "r_rec": round(params.r_rec, 4),
            "k_baseline": round(params.k_baseline, 4),
            "alpha": round(params.alpha, 4),
            "beta_adot": round(params.beta_adot, 4),
            "beta_ay_share": round(params.beta_ay_share, 4),
        },
        "evaluation": summary,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
