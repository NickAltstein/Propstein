"""Expected-minutes model — the interpretable blend, fully explainable.

    minutes_hat = clip( w_eff * recent5_nongarbage + (1 - w_eff) * season_mean,
                        4, NBA_MINUTES_CAP )
    w_eff       = RECENT_WEIGHT * (n_recent / 5)     # shrinkage while history is thin

Blowout ("garbage") minutes are excluded from the recent window because
one 14-minute blowout can masquerade as a role change. Players absent from
the training window fall back to position means, never to zero.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import (
    GARBAGE_MARGIN,
    NBA_MINUTES_CAP,
    RECENT_WEIGHT,
)
from ..util import finite_or


class ExpectedMinutesModel:
    def __init__(self):
        self.player_params: dict[str, dict] = {}
        self.position_params: dict[str, dict] = {}

    # ── fit (train window only) ─────────────────────────────────────────────
    def fit(self, train: pd.DataFrame) -> "ExpectedMinutesModel":
        played = train[~train["dnp"]]
        clean = played[played["margin"].abs() < GARBAGE_MARGIN]

        self.player_params = {}
        for pid, grp in played.groupby("player_id"):
            season_mean = float(grp["minutes"].mean())
            recent = clean[clean["player_id"] == pid].tail(5)
            self.player_params[pid] = {
                "season_mean": season_mean,
                "recent_mean": float(recent["minutes"].mean()) if len(recent) else season_mean,
                "n_recent": int(len(recent)),
                "n_season": int(len(grp)),
                "sd_recent": finite_or(grp["minutes"].tail(10).std(ddof=1), 0.0),
            }

        self.position_params = {}
        for pos, grp in played.groupby("position"):
            self.position_params[pos] = {
                "season_mean": float(grp["minutes"].mean()),
                "sd": finite_or(grp["minutes"].std(ddof=1), 6.0),
            }
        return self

    # ── predict (row-level, test window) ────────────────────────────────────
    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for _, row in df.iterrows():
            params = self.player_params.get(row["player_id"])
            fallback_sd = 6.0
            if params is None:
                pos_params = self.position_params.get(row["position"], {})
                base = pos_params.get("season_mean", 20.0)
                fallback_sd = pos_params.get("sd", 6.0)
                recent_mean, n_recent, season_mean = base, 0, base
                source = "position_fallback"
            else:
                recent_mean = params["recent_mean"]
                n_recent = params["n_recent"]
                season_mean = params["season_mean"]
                fallback_sd = params["sd_recent"]
                source = "player_history"

            w_eff = RECENT_WEIGHT * (min(n_recent, 5) / 5.0)
            minutes_hat = float(
                np.clip(w_eff * recent_mean + (1 - w_eff) * season_mean, 4.0, NBA_MINUTES_CAP)
            )
            sd_minutes = max(3.0, float(fallback_sd or 3.0))

            # Blowout haircut & wider uncertainty band when |spread| >= 9.5
            spread_val = row.get("spread")
            if spread_val is not None and pd.notna(spread_val):
                abs_spread = abs(float(spread_val))
                if abs_spread >= 9.5:
                    if minutes_hat >= 24.0:
                        haircut = min(3.5, 1.5 + 0.25 * (abs_spread - 9.5))
                        minutes_hat = max(4.0, minutes_hat - haircut)
                    sd_minutes = round(sd_minutes * 1.25, 2)

            # Bounded vacated-minutes boost from availability / injury layer if present
            vacated_boost = float(row.get("vacated_min_boost") or 0.0)
            if vacated_boost > 0.0:
                minutes_hat = float(np.clip(minutes_hat + min(vacated_boost, 8.0), 4.0, NBA_MINUTES_CAP))

            minutes_p10 = float(np.clip(minutes_hat - 1.2816 * sd_minutes, 0.0, minutes_hat))
            minutes_p90 = float(np.clip(minutes_hat + 1.2816 * sd_minutes, minutes_hat, NBA_MINUTES_CAP))

            recent_min_delta = float(recent_mean - season_mean) if n_recent >= 2 else 0.0
            usage_delta = float(row.get("usage_delta") or 0.0)
            reasons: list[str] = []
            if vacated_boost >= 3.5:
                inj_reason = str(row.get("role_surge_reason") or "").strip()
                reasons.append(inj_reason if inj_reason else f"+{vacated_boost:.1f} min vacated")
            if recent_min_delta >= 5.0:
                reasons.append(f"+{recent_min_delta:.1f} min recent surge")
            if usage_delta >= 0.04:
                reasons.append(f"+{usage_delta * 100:.1f}% usage jump")
            role_surge = bool(reasons) or bool(row.get("role_surge"))
            role_surge_reason = " · ".join(reasons) if reasons else str(row.get("role_surge_reason") or "")

            rows.append({
                "player_id": row["player_id"],
                "game_id": row["game_id"],
                "minutes_p10": round(minutes_p10, 2),
                "minutes_hat": round(minutes_hat, 2),
                "minutes_p90": round(minutes_p90, 2),
                "sd_minutes": round(sd_minutes, 2),
                "minutes_source": source,
                "n_recent": n_recent,
                "recent_min_delta": round(recent_min_delta, 2),
                "role_surge": role_surge,
                "role_surge_reason": role_surge_reason,
            })
        return pd.DataFrame(rows)
