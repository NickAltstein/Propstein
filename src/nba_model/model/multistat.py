"""NBAMultiStatProjector — generalizes NBAPointsProjector to PTS, REB, AST, 3PM, and combos.

Reuses the exact shared ExpectedMinutesModel, TeamContext, and NBAPointsProjector
points path. Projects:
  - points   (Skewed/Discretized Gaussian via PointsDistribution)
  - rebounds (NegativeBinomialDistribution)
  - assists  (NegativeBinomialDistribution)
  - threes   (Zero-inflated NegativeBinomialDistribution)
  - combos   (PRA, PR, PA, RA with empirical within-player covariance shrunk to position prior)

Every emitted row carries fail-closed fields:
  actionable=False, edge_pct=None, kelly_stake=0.0, gate_status="RESEARCH_ONLY_UNVERIFIED".
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..config import MINUTES_FLOOR
from ..util import finite_or
from .distribution import NegativeBinomialDistribution, PointsDistribution
from .projector import NBAPointsProjector, PPM_SHRINK_GAMES

COV_SHRINK_GAMES = 8.0

ATOMIC_STATS = (
    ("rebounds", "rebounds", 0.16, 0.08),
    ("assists", "assists", 0.10, 0.06),
    ("threes", "fg3m", 0.05, 0.05),
)

COMBO_DEFS = (
    ("pra", ("points", "rebounds", "assists")),
    ("pr", ("points", "rebounds")),
    ("pa", ("points", "assists")),
    ("ra", ("rebounds", "assists")),
)


class NBAMultiStatProjector:
    """Shared-minutes multi-market NBA player prop projector."""

    def __init__(self) -> None:
        self.points_projector = NBAPointsProjector()
        self.stat_rates: dict[str, dict[str, dict[str, float]]] = {}
        self.pos_rates: dict[str, dict[str, dict[str, float]]] = {}
        self.player_cov: dict[str, np.ndarray] = {}
        self.pos_cov: dict[str, np.ndarray] = {}
        self.player_zero_3pm: dict[str, float] = {}
        self.pos_zero_3pm: dict[str, float] = {}

    def fit(self, train: pd.DataFrame) -> "NBAMultiStatProjector":
        self.points_projector.fit(train)
        played = train[~train["dnp"]].copy()
        mins = pd.to_numeric(played["minutes"], errors="coerce").clip(lower=1.0)

        # Fit empirical-Bayes shrunk per-minute rates for REB, AST, 3PM
        for stat_name, col, fallback_mean, fallback_sd in ATOMIC_STATS:
            vals = pd.to_numeric(played.get(col, 0.0), errors="coerce").fillna(0.0)
            rate_col = f"_rate_{stat_name}"
            played[rate_col] = vals / mins

            pos_agg = played.groupby("position")[rate_col].agg(["mean", "std"])
            self.pos_rates[stat_name] = {
                str(pos): {
                    "mean": finite_or(float(r["mean"]), fallback_mean),
                    "sd": finite_or(float(r["std"]), fallback_sd),
                }
                for pos, r in pos_agg.iterrows()
            }

            p_map: dict[str, dict[str, float]] = {}
            for pid, grp in played.groupby("player_id"):
                n = len(grp)
                pos = str(grp["position"].iloc[0])
                p_prior = self.pos_rates[stat_name].get(pos, {"mean": fallback_mean, "sd": fallback_sd})
                m = float(grp[rate_col].mean())
                s = finite_or(grp[rate_col].std(ddof=1), p_prior["sd"])
                k = PPM_SHRINK_GAMES
                p_map[str(pid)] = {
                    "mean": (n * m + k * p_prior["mean"]) / (n + k),
                    "sd": max(0.02, (n * s + k * p_prior["sd"]) / (n + k)),
                    "n": n,
                }
            self.stat_rates[stat_name] = p_map

        # Fit shrunk excess zero-mass for 3PM
        fg3m = pd.to_numeric(played.get("fg3m", 0.0), errors="coerce").fillna(0.0)
        played["_is_zero_3pm"] = (fg3m <= 0).astype(float)
        self.pos_zero_3pm = {
            str(pos): float(np.clip(val * 0.35, 0.0, 0.5))
            for pos, val in played.groupby("position")["_is_zero_3pm"].mean().items()
        }
        for pid, grp in played.groupby("player_id"):
            n = len(grp)
            pos = str(grp["position"].iloc[0])
            prior_z = self.pos_zero_3pm.get(pos, 0.15)
            # Excess zero inflation above Poisson baseline e^{-mean}
            obs_z = float(grp["_is_zero_3pm"].mean())
            mu_3 = float(pd.to_numeric(grp.get("fg3m", 0.0), errors="coerce").fillna(0.0).mean())
            excess = max(0.0, obs_z - math.exp(-max(mu_3, 0.0)))
            k = PPM_SHRINK_GAMES
            self.player_zero_3pm[str(pid)] = float(np.clip((n * excess + k * prior_z) / (n + k), 0.0, 0.6))

        # Fit empirical within-player covariance matrix over (pts, rebounds, assists)
        # shrunk toward position-level covariance priors (never assumes independence).
        for c in ("pts", "rebounds", "assists"):
            if c not in played.columns:
                played[c] = 0.0
        self.pos_cov = {}
        for pos, grp in played.groupby("position"):
            mat = grp[["pts", "rebounds", "assists"]].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy()
            if len(mat) >= 5:
                cov = np.cov(mat, rowvar=False)
                self.pos_cov[str(pos)] = np.nan_to_num(cov, nan=0.0)
            else:
                self.pos_cov[str(pos)] = np.zeros((3, 3), dtype=float)

        self.player_cov = {}
        for pid, grp in played.groupby("player_id"):
            pos = str(grp["position"].iloc[0])
            prior_c = self.pos_cov.get(pos, np.zeros((3, 3), dtype=float))
            mat = grp[["pts", "rebounds", "assists"]].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy()
            n = len(mat)
            if n >= 3:
                emp_c = np.nan_to_num(np.cov(mat, rowvar=False), nan=0.0)
                shrunk = (n * emp_c + COV_SHRINK_GAMES * prior_c) / (n + COV_SHRINK_GAMES)
            else:
                shrunk = prior_c.copy()
            self.player_cov[str(pid)] = shrunk

        return self

    def predict(self, test: pd.DataFrame, history: pd.DataFrame | None = None) -> pd.DataFrame:
        pts_df = self.points_projector.predict(test, history=history)
        if pts_df.empty:
            return pts_df

        ctx = self.points_projector.context.apply(test, schedule_df=history)
        ctx_by_key = {
            (str(r["player_id"]), str(r["game_id"])): r
            for _, r in ctx.iterrows()
        }

        enriched_rows: list[dict[str, Any]] = []
        for _, r in pts_df.iterrows():
            pid = str(r["player_id"])
            gid = str(r["game_id"])
            pos = str(r.get("position") or "G")
            c_row = ctx_by_key.get((pid, gid), {})

            min_hat = float(r["minutes_hat"])
            sd_min = float(r["sd_minutes"])
            pace_mult = float(c_row.get("pace_mult", 1.0)) if hasattr(c_row, "get") else 1.0
            opp_mult = float(c_row.get("opp_mult", 1.0)) if hasattr(c_row, "get") else 1.0
            avail_mult = float(c_row.get("availability_mult", 1.0)) if hasattr(c_row, "get") else 1.0
            var_infl = float(c_row.get("var_inflation", 1.0)) if hasattr(c_row, "get") else 1.0

            pts_mean = float(r["mean_pts"])
            pts_sd = float(r["sd_pts"])
            pts_dist = PointsDistribution(mean=pts_mean, sd=pts_sd)
            pts_fair = pts_dist.fair_line()

            row_out: dict[str, Any] = {
                **dict(r),
                "proj_points": round(pts_mean, 2),
                "sd_points": round(pts_sd, 2),
                "fair_line_points": pts_fair,
                "p_over_fair_points": round(pts_dist.p_over(pts_fair), 4),
            }

            atomic_means = {"points": pts_mean}
            atomic_sds = {"points": pts_sd}

            for stat_name, _, fb_m, fb_s in ATOMIC_STATS:
                p_info = self.stat_rates.get(stat_name, {}).get(pid)
                if p_info is None:
                    p_info = self.pos_rates.get(stat_name, {}).get(pos, {"mean": fb_m, "sd": fb_s})
                rate_m = float(p_info["mean"])
                rate_s = float(p_info["sd"])

                # Moderate pace & availability scaling for non-points stats
                mean_s = max(0.0, rate_m * min_hat * (0.5 + 0.5 * pace_mult) * avail_mult)
                sd_s = math.sqrt((rate_s * min_hat) ** 2 + (rate_m * sd_min) ** 2) * var_infl
                sd_s = max(0.6, sd_s)

                z_inf = self.player_zero_3pm.get(pid, self.pos_zero_3pm.get(pos, 0.15)) if stat_name == "threes" else 0.0
                dist = NegativeBinomialDistribution(mean=mean_s, sd=sd_s, zero_inflation=z_inf)
                fair_s = dist.fair_line()

                atomic_means[stat_name] = mean_s
                atomic_sds[stat_name] = sd_s
                row_out[f"proj_{stat_name}"] = round(mean_s, 2)
                row_out[f"sd_{stat_name}"] = round(sd_s, 2)
                row_out[f"fair_line_{stat_name}"] = fair_s
                row_out[f"p_over_fair_{stat_name}"] = round(dist.p_over(fair_s), 4)

            # Covariance-adjusted combos (PRA, PR, PA, RA)
            cov_mat = self.player_cov.get(pid, self.pos_cov.get(pos, np.zeros((3, 3), dtype=float)))
            idx_map = {"points": 0, "rebounds": 1, "assists": 2}
            for combo_name, parts in COMBO_DEFS:
                c_mean = sum(atomic_means[p] for p in parts)
                # Diagonal from model atomic variances + off-diagonal from shrunk empirical covariance
                c_var = sum(atomic_sds[p] ** 2 for p in parts)
                for i in range(len(parts)):
                    for j in range(i + 1, len(parts)):
                        cov_ij = float(cov_mat[idx_map[parts[i]], idx_map[parts[j]]])
                        # Cap covariance magnitude by Cauchy-Schwarz (0.85 * sd_i * sd_j)
                        max_cov = 0.85 * atomic_sds[parts[i]] * atomic_sds[parts[j]]
                        cov_ij = float(np.clip(cov_ij, -max_cov, max_cov))
                        c_var += 2.0 * cov_ij
                c_sd = max(1.0, math.sqrt(max(c_var, 1.0)))
                c_dist = PointsDistribution(mean=c_mean, sd=c_sd)
                c_fair = c_dist.fair_line()
                row_out[f"proj_{combo_name}"] = round(c_mean, 2)
                row_out[f"sd_{combo_name}"] = round(c_sd, 2)
                row_out[f"fair_line_{combo_name}"] = c_fair
                row_out[f"p_over_fair_{combo_name}"] = round(c_dist.p_over(c_fair), 4)

            # Fail-closed invariants
            row_out["actionable"] = False
            row_out["edge_pct"] = None
            row_out["kelly_stake"] = 0.0
            row_out["gate_status"] = "RESEARCH_ONLY_UNVERIFIED"
            enriched_rows.append(row_out)

        return pd.DataFrame(enriched_rows)
