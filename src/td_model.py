"""Poisson–Gamma (Negative Binomial) Anytime Touchdown scoring model.

Produces analytically coherent, monotonic player-game touchdown probabilities
from the strictly lagged feature frame emitted by `src.td_features.build_td_features`:
  - td_mean (λ): expected touchdowns, team-constrained to implied team TDs
  - p_anytime_td = 1 - P(Y = 0)
  - p_2plus_td   = 1 - P(Y = 0) - P(Y = 1)
  - p_first_td   = P(team scores first) * (λ_i / sum_{j in team} λ_j)
  - fair_odds_anytime, fair_odds_2plus, fair_odds_first

Every emitted row passes through `enforce_td_fail_closed()`.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from src.td_features import TD_POSITIONS, enforce_td_fail_closed

POINTS_PER_TD = 7.5
PRIOR_TDS_PER_IMPLIED_PT = 1.0 / 9.5
TEAM_TD_PRIOR_GAMES = 20.0
DEFAULT_IMPLIED_TEAM_TOTAL = 21.0
DEFAULT_DISPERSION = 0.15
MIN_LAMBDA = 1e-4
MAX_LAMBDA = 4.0

MODEL_NUMERIC_FEATURES = (
    "rz_tgt_share_5g",
    "rz_carry_share_5g",
    "gl10_opp_share_5g",
    "gl5_opp_share_5g",
    "rz_td_rate_shrunk",
    "role_surge_z",
    "opp_rz_td_rate_pos",
    "vacated_rz_opps_team",
    "teammate_out_flag",
    "expected_team_tds",
)


def nb_pmf_0_and_1(lam: float, alpha: float) -> tuple[float, float]:
    """Return (P(Y=0), P(Y=1)) for a Negative Binomial with mean lam and dispersion alpha.

    Var(Y) = lam + alpha * lam^2. Recovers Poisson as alpha -> 0.
    """
    lam = max(float(lam), 0.0)
    alpha = max(float(alpha), 0.0)
    if lam == 0.0:
        return 1.0, 0.0
    if alpha <= 1e-9:
        p0 = math.exp(-lam)
        p1 = lam * p0
        return p0, p1
    r = 1.0 / alpha
    p = r / (r + lam)
    # Use log-space for numerical stability
    log_p0 = r * math.log(max(p, 1e-15))
    p0 = math.exp(log_p0)
    p1 = r * p0 * (1.0 - p)
    return p0, p1


def nb_td_probabilities(lam: float, alpha: float) -> tuple[float, float]:
    """Return analytically coherent (p_anytime_td, p_2plus_td)."""
    p0, p1 = nb_pmf_0_and_1(lam, alpha)
    p_any = float(np.clip(1.0 - p0, 0.0, 1.0))
    p_2p = float(np.clip(1.0 - p0 - p1, 0.0, p_any))
    return p_any, p_2p


def prob_to_american_odds(prob: float) -> int | None:
    """Convert probability in (0, 1) to fair American odds."""
    if prob is None or not math.isfinite(prob) or prob <= 1e-6 or prob >= 1.0 - 1e-6:
        return None
    if prob >= 0.5:
        return -int(round(100.0 * prob / (1.0 - prob)))
    return int(round(100.0 * (1.0 - prob) / prob))


def implied_total_to_expected_tds(implied_team_total: float | None) -> float:
    """Convert team implied point total to expected team touchdowns."""
    if implied_team_total is None or not math.isfinite(float(implied_team_total)):
        return DEFAULT_IMPLIED_TEAM_TOTAL / POINTS_PER_TD
    return max(0.25, float(implied_team_total) / POINTS_PER_TD)


class TouchdownModel:
    """L2-regularized Poisson log-link intensity model with position-level NB dispersion."""

    def __init__(self, l2_reg: float = 2.0) -> None:
        self.l2_reg = float(l2_reg)
        self.coef_: np.ndarray | None = None
        self.intercept_: float = -1.8
        self.feature_means_: np.ndarray | None = None
        self.feature_stds_: np.ndarray | None = None
        self.position_alpha_: dict[str, float] = {p: DEFAULT_DISPERSION for p in TD_POSITIONS}
        self.tds_per_implied_point_: float = PRIOR_TDS_PER_IMPLIED_PT
        self.points_per_td_: float = 1.0 / PRIOR_TDS_PER_IMPLIED_PT
        self.is_fitted_: bool = False

    def _design_matrix(self, df: pd.DataFrame) -> np.ndarray:
        work = df.copy()
        if "expected_team_tds" not in work.columns:
            if "implied_team_total" in work.columns:
                work["expected_team_tds"] = work["implied_team_total"].apply(implied_total_to_expected_tds)
            else:
                work["expected_team_tds"] = DEFAULT_IMPLIED_TEAM_TOTAL / POINTS_PER_TD

        cols = []
        for col in MODEL_NUMERIC_FEATURES:
            if col in work.columns:
                vals = pd.to_numeric(work[col], errors="coerce").fillna(0.0).to_numpy(dtype=float)
            else:
                vals = np.zeros(len(work), dtype=float)
            cols.append(vals)

        pos_s = work.get("position", pd.Series("WR", index=work.index)).fillna("WR").astype(str)
        for pos in ("RB", "TE", "QB"):
            cols.append((pos_s == pos).astype(float).to_numpy())

        return np.column_stack(cols)

    def fit(self, train_df: pd.DataFrame, target_col: str = "total_tds") -> "TouchdownModel":
        """Fit log-link Poisson intensity weights and position NB dispersions on training rows."""
        if train_df.empty:
            raise ValueError("Cannot fit TouchdownModel on empty training frame.")

        X_raw = self._design_matrix(train_df)
        y = pd.to_numeric(train_df[target_col], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)

        means = X_raw.mean(axis=0)
        stds = X_raw.std(axis=0)
        stds = np.where(stds > 1e-6, stds, 1.0)
        X = (X_raw - means) / stds

        n, p = X.shape
        init_intercept = math.log(max(float(y.mean()), 0.05))
        w0 = np.zeros(p + 1, dtype=float)
        w0[0] = init_intercept

        reg = self.l2_reg

        def loss_and_grad(w: np.ndarray) -> tuple[float, np.ndarray]:
            eta = np.clip(w[0] + X @ w[1:], -8.0, 3.0)
            mu = np.exp(eta)
            # Negative log-likelihood (up to constant) + L2 penalty on slopes
            nll = float(np.sum(mu - y * eta) / n + 0.5 * reg * np.sum(w[1:] ** 2) / n)
            diff = (mu - y) / n
            g0 = float(np.sum(diff))
            gw = X.T @ diff + (reg / n) * w[1:]
            return nll, np.concatenate(([g0], gw))

        res = minimize(loss_and_grad, w0, jac=True, method="L-BFGS-B")
        self.intercept_ = float(res.x[0])
        self.coef_ = res.x[1:].copy()
        self.feature_means_ = means
        self.feature_stds_ = stds

        # Estimate position-level NB overdispersion alpha via method of moments
        eta_hat = np.clip(self.intercept_ + X @ self.coef_, -8.0, 3.0)
        mu_hat = np.exp(eta_hat)
        pos_s = train_df.get("position", pd.Series("WR", index=train_df.index)).fillna("WR").astype(str)
        self.position_alpha_ = {}
        for pos in TD_POSITIONS:
            mask = (pos_s == pos).to_numpy()
            if mask.sum() >= 10:
                y_p = y[mask]
                mu_p = mu_hat[mask]
                denom = float(np.sum(mu_p ** 2))
                num = float(np.sum((y_p - mu_p) ** 2 - mu_p))
                alpha = float(np.clip(num / max(denom, 1e-6), 0.0, 2.0))
                self.position_alpha_[pos] = alpha
            else:
                self.position_alpha_[pos] = DEFAULT_DISPERSION

        # Empirical team-total-to-offensive-TD ratio (strictly on training window)
        group_cols = [c for c in ("season", "week", "team") if c in train_df.columns]
        if group_cols:
            work_t = train_df[group_cols].copy()
            work_t["_y"] = y
            if "implied_team_total" in train_df.columns:
                work_t["_imp"] = pd.to_numeric(train_df["implied_team_total"], errors="coerce").fillna(
                    DEFAULT_IMPLIED_TEAM_TOTAL
                )
            else:
                work_t["_imp"] = DEFAULT_IMPLIED_TEAM_TOTAL
            tg = work_t.groupby(group_cols, as_index=False).agg(_y=("_y", "sum"), _imp=("_imp", "mean"))
            obs_tds_sum = float(tg["_y"].sum())
            imp_pts_sum = float(tg["_imp"].sum())
            prior_pts = TEAM_TD_PRIOR_GAMES * DEFAULT_IMPLIED_TEAM_TOTAL
            prior_tds = prior_pts * PRIOR_TDS_PER_IMPLIED_PT
            ratio = (obs_tds_sum + prior_tds) / max(imp_pts_sum + prior_pts, 1e-6)
            self.tds_per_implied_point_ = float(np.clip(ratio, 0.04, 0.25))
        else:
            self.tds_per_implied_point_ = PRIOR_TDS_PER_IMPLIED_PT
        self.points_per_td_ = 1.0 / self.tds_per_implied_point_

        self.is_fitted_ = True
        return self

    def predict(
        self,
        feat_df: pd.DataFrame,
        *,
        constrain_team_totals: bool = True,
    ) -> pd.DataFrame:
        """Predict analytically coherent TD probabilities for each player-game row."""
        if not self.is_fitted_ or self.coef_ is None or self.feature_means_ is None or self.feature_stds_ is None:
            raise RuntimeError("TouchdownModel.fit() must be called before predict().")
        if feat_df.empty:
            return enforce_td_fail_closed(feat_df.copy())

        out = feat_df.copy().reset_index(drop=True)
        if "expected_team_tds" not in out.columns:
            if "implied_team_total" in out.columns:
                out["expected_team_tds"] = out["implied_team_total"].apply(implied_total_to_expected_tds)
            else:
                out["expected_team_tds"] = DEFAULT_IMPLIED_TEAM_TOTAL / POINTS_PER_TD

        X_raw = self._design_matrix(out)
        X = (X_raw - self.feature_means_) / self.feature_stds_
        eta = np.clip(self.intercept_ + X @ self.coef_, -8.0, 2.0)
        raw_lam = np.clip(np.exp(eta), MIN_LAMBDA, MAX_LAMBDA)
        out["_raw_lam"] = raw_lam

        # Team-level coherence constraint: rescale team sum of λ to empirically fitted team TDs
        if constrain_team_totals and "team" in out.columns:
            group_cols = [c for c in ("season", "week", "team") if c in out.columns]
            team_sums = out.groupby(group_cols)["_raw_lam"].transform("sum")
            if "implied_team_total" in out.columns:
                imp_tot = pd.to_numeric(out["implied_team_total"], errors="coerce").fillna(
                    DEFAULT_IMPLIED_TEAM_TOTAL
                )
                target_tds = np.maximum(0.25, imp_tot * self.tds_per_implied_point_)
            else:
                target_tds = np.maximum(0.25, DEFAULT_IMPLIED_TEAM_TOTAL * self.tds_per_implied_point_)
            scale = np.where(team_sums > 1e-6, target_tds / team_sums, 1.0)
            out["td_mean"] = np.clip(out["_raw_lam"] * scale, MIN_LAMBDA, MAX_LAMBDA)
        else:
            out["td_mean"] = out["_raw_lam"]

        # Analytical Negative Binomial probabilities
        p_any_list: list[float] = []
        p_2p_list: list[float] = []
        alpha_list: list[float] = []
        for _, r in out.iterrows():
            pos = str(r.get("position") or "WR")
            alpha = self.position_alpha_.get(pos, DEFAULT_DISPERSION)
            p_any, p_2p = nb_td_probabilities(float(r["td_mean"]), alpha)
            p_any_list.append(round(p_any, 6))
            p_2p_list.append(round(p_2p, 6))
            alpha_list.append(round(alpha, 4))

        out["td_dispersion"] = alpha_list
        out["p_anytime_td"] = p_any_list
        out["p_2plus_td"] = p_2p_list

        # Team-normalized first-TD share × P(team scores first)
        group_cols = [c for c in ("season", "week", "team") if c in out.columns]
        if group_cols:
            team_lam_sum = out.groupby(group_cols)["td_mean"].transform("sum")
            team_share = np.where(team_lam_sum > 1e-9, out["td_mean"] / team_lam_sum, 0.0)
        else:
            total_lam = float(out["td_mean"].sum())
            team_share = out["td_mean"] / total_lam if total_lam > 1e-9 else 0.0

        if "implied_opp_total" in out.columns:
            opp_tds = out["implied_opp_total"].apply(implied_total_to_expected_tds)
            p_team_first = out["expected_team_tds"] / (out["expected_team_tds"] + opp_tds)
        else:
            p_team_first = 0.5

        out["team_td_share"] = np.round(team_share, 6)
        out["p_first_td"] = np.round(np.clip(team_share * p_team_first, 0.0, out["p_anytime_td"]), 6)

        out["fair_odds_anytime"] = [prob_to_american_odds(p) for p in out["p_anytime_td"]]
        out["fair_odds_2plus"] = [prob_to_american_odds(p) for p in out["p_2plus_td"]]
        out["fair_odds_first"] = [prob_to_american_odds(p) for p in out["p_first_td"]]

        out = out.drop(columns=["_raw_lam"])
        return enforce_td_fail_closed(out)
