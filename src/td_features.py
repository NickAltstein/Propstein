"""Leak-free play-by-play feature pipeline for NFL Anytime Touchdown scoring.

Every feature computed for (player, target_season, target_week) uses ONLY
observations with (season < target_season) or (season == target_season and
week < target_week). No future-week or same-week outcomes ever enter any
rolling share, shrinkage prior, opponent allowance, or injury redistribution.

Fail-closed invariant: every emitted row starts with actionable=False,
edge_pct=None, kelly_stake=0.0, and gate_status="RESEARCH_ONLY_UNVERIFIED".
"""

from __future__ import annotations

import os
import urllib.request
from typing import Iterable

import numpy as np
import pandas as pd

NFLVERSE_PBP_BASE = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp"
)
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

TD_POSITIONS = ("WR", "RB", "TE", "QB")
RZ_EWMA_SPAN = 5
RZ_SHRINK_OPPS = 8.0
OUT_STATUSES = frozenset({"OUT", "DOUBTFUL", "IR", "PUP", "SUSPENDED", "INACTIVE"})

POSITION_RZ_TD_PRIOR = {
    "RB": 0.18,
    "WR": 0.16,
    "TE": 0.17,
    "QB": 0.22,
}

TD_FEATURE_COLUMNS = (
    "rz_tgt_share_5g",
    "rz_carry_share_5g",
    "gl10_opp_share_5g",
    "gl5_opp_share_5g",
    "rz_td_rate_shrunk",
    "role_surge_z",
    "opp_rz_td_rate_pos",
    "vacated_rz_opps_team",
    "teammate_out_flag",
    "games_prior",
    "rz_opps_prior",
)


def fetch_pbp_season(season: int, data_dir: str = DATA_DIR) -> str:
    """Download nflverse play_by_play_{season}.parquet if absent (free, keyless)."""
    os.makedirs(data_dir, exist_ok=True)
    dst = os.path.join(data_dir, f"play_by_play_{season}.parquet")
    if not os.path.exists(dst):
        url = f"{NFLVERSE_PBP_BASE}/play_by_play_{season}.parquet"
        urllib.request.urlretrieve(url, dst)
    return dst


def load_pbp(seasons: Iterable[int], data_dir: str = DATA_DIR) -> pd.DataFrame:
    """Load local nflverse play-by-play parquet files for the given seasons."""
    frames: list[pd.DataFrame] = []
    for yr in seasons:
        path = os.path.join(data_dir, f"play_by_play_{int(yr)}.parquet")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing PBP file: {path} (run fetch_pbp_season({yr}))")
        frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def extract_pbp_player_weeks(pbp: pd.DataFrame) -> pd.DataFrame:
    """Aggregate nflverse play-by-play rows into player-week TD opportunity rows.

    Expects standard nflverse PBP columns (season, week, posteam, defteam,
    yardline_100, play_type, rusher_player_id, rusher_player_name,
    receiver_player_id, receiver_player_name, rush_touchdown, pass_touchdown,
    air_yards) plus optional position columns.
    """
    if pbp.empty:
        return pd.DataFrame(columns=[
            "player_id", "player_name", "season", "week", "team",
            "opponent_team", "position", "rz_targets", "rz_carries", "rz_opps",
            "gl10_opps", "gl5_opps", "rz_tds", "total_tds", "rz_air_yards",
            "total_opps",
        ])

    df = pbp.copy()
    yl = pd.to_numeric(df.get("yardline_100"), errors="coerce")
    is_rz = (yl <= 20).fillna(False)
    is_gl10 = (yl <= 10).fillna(False)
    is_gl5 = (yl <= 5).fillna(False)
    air_y = pd.to_numeric(df.get("air_yards", 0.0), errors="coerce").fillna(0.0)

    records: list[pd.DataFrame] = []

    # Rushing opportunities
    rush_mask = df.get("rusher_player_id").notna() if "rusher_player_id" in df.columns else pd.Series(False, index=df.index)
    if rush_mask.any():
        r = df.loc[rush_mask].copy()
        r_td = pd.to_numeric(r.get("rush_touchdown", 0), errors="coerce").fillna(0).astype(int)
        r_pos = r["rusher_position"].fillna("RB").astype(str) if "rusher_position" in r.columns else "RB"
        r_name = r["rusher_player_name"].fillna("").astype(str) if "rusher_player_name" in r.columns else r["rusher_player_id"].astype(str)
        records.append(pd.DataFrame({
            "player_id": r["rusher_player_id"].astype(str),
            "player_name": r_name,
            "season": r["season"].astype(int),
            "week": r["week"].astype(int),
            "team": r["posteam"].astype(str),
            "opponent_team": r["defteam"].astype(str),
            "position": r_pos,
            "rz_targets": 0,
            "rz_carries": is_rz.loc[rush_mask].astype(int).values,
            "rz_opps": is_rz.loc[rush_mask].astype(int).values,
            "gl10_opps": is_gl10.loc[rush_mask].astype(int).values,
            "gl5_opps": is_gl5.loc[rush_mask].astype(int).values,
            "rz_tds": (r_td.values * is_rz.loc[rush_mask].astype(int).values),
            "total_tds": r_td.values,
            "rz_air_yards": 0.0,
            "total_opps": 1,
        }))

    # Receiving opportunities
    rec_mask = df.get("receiver_player_id").notna() if "receiver_player_id" in df.columns else pd.Series(False, index=df.index)
    if rec_mask.any():
        p = df.loc[rec_mask].copy()
        p_td = pd.to_numeric(p.get("pass_touchdown", 0), errors="coerce").fillna(0).astype(int)
        rz_m = is_rz.loc[rec_mask].astype(int).values
        p_pos = p["receiver_position"].fillna("WR").astype(str) if "receiver_position" in p.columns else "WR"
        p_name = p["receiver_player_name"].fillna("").astype(str) if "receiver_player_name" in p.columns else p["receiver_player_id"].astype(str)
        records.append(pd.DataFrame({
            "player_id": p["receiver_player_id"].astype(str),
            "player_name": p_name,
            "season": p["season"].astype(int),
            "week": p["week"].astype(int),
            "team": p["posteam"].astype(str),
            "opponent_team": p["defteam"].astype(str),
            "position": p_pos,
            "rz_targets": rz_m,
            "rz_carries": 0,
            "rz_opps": rz_m,
            "gl10_opps": is_gl10.loc[rec_mask].astype(int).values,
            "gl5_opps": is_gl5.loc[rec_mask].astype(int).values,
            "rz_tds": (p_td.values * rz_m),
            "total_tds": p_td.values,
            "rz_air_yards": air_y.loc[rec_mask].values * rz_m,
            "total_opps": 1,
        }))

    if not records:
        return pd.DataFrame()

    combined = pd.concat(records, ignore_index=True)
    grouped = (
        combined.groupby(
            ["player_id", "season", "week", "team", "opponent_team"],
            as_index=False,
        )
        .agg({
            "player_name": "first",
            "position": "first",
            "rz_targets": "sum",
            "rz_carries": "sum",
            "rz_opps": "sum",
            "gl10_opps": "sum",
            "gl5_opps": "sum",
            "rz_tds": "sum",
            "total_tds": "sum",
            "rz_air_yards": "sum",
            "total_opps": "sum",
        })
    )
    return grouped.sort_values(["season", "week", "player_id"]).reset_index(drop=True)


def enforce_td_fail_closed(df: pd.DataFrame) -> pd.DataFrame:
    """Enforce fail-closed research defaults on every TD feature/board row."""
    out = df.copy()
    out["actionable"] = False
    out["edge_pct"] = None
    out["kelly_stake"] = 0.0
    out["gate_status"] = "RESEARCH_ONLY_UNVERIFIED"
    return out


def _ewma_last(values: pd.Series, span: int = RZ_EWMA_SPAN) -> float:
    if values.empty:
        return 0.0
    return float(values.astype(float).ewm(span=span, adjust=False).mean().iloc[-1])


def _confidence_tier(games_prior: int, rz_opps_prior: float) -> str:
    if games_prior >= 6 and rz_opps_prior >= 8:
        return "HIGH"
    if games_prior >= 3 and rz_opps_prior >= 3:
        return "MED"
    return "THIN_SAMPLE"


def build_td_features(
    player_weeks: pd.DataFrame,
    target_season: int,
    target_week: int,
    *,
    target_matchups: dict[str, str] | None = None,
    injuries: list[dict] | None = None,
) -> pd.DataFrame:
    """Build strictly lagged TD scoring features for (target_season, target_week).

    Parameters
    ----------
    player_weeks:
        Historical player-week table (e.g., from extract_pbp_player_weeks).
    target_season, target_week:
        Cutoff week. Strictly only rows with (season < target_season) or
        (season == target_season and week < target_week) are read.
    target_matchups:
        Optional mapping {team: opponent_team} for the target week so opponent
        lagged RZ TD allowance is joined for the upcoming opponent.
    injuries:
        Optional list of injury dicts with keys player_id/player_name, team,
        position, status (e.g. "OUT", "DOUBTFUL").
    """
    if player_weeks.empty:
        return enforce_td_fail_closed(pd.DataFrame())

    # HARD ZERO-LEAKAGE CUTOFF
    hist = player_weeks[
        (player_weeks["season"] < target_season)
        | ((player_weeks["season"] == target_season) & (player_weeks["week"] < target_week))
    ].copy()
    if hist.empty:
        return enforce_td_fail_closed(pd.DataFrame())

    hist = hist.sort_values(["season", "week", "player_id"]).reset_index(drop=True)

    # Team-week totals for share denominators (strictly within hist)
    team_wk = (
        hist.groupby(["season", "week", "team"], as_index=False)[
            ["rz_targets", "rz_carries", "rz_opps", "gl10_opps", "gl5_opps"]
        ]
        .sum()
        .rename(columns={
            "rz_targets": "team_rz_targets",
            "rz_carries": "team_rz_carries",
            "rz_opps": "team_rz_opps",
            "gl10_opps": "team_gl10_opps",
            "gl5_opps": "team_gl5_opps",
        })
    )
    hist = hist.merge(team_wk, on=["season", "week", "team"], how="left")

    hist["rz_tgt_share"] = np.where(hist["team_rz_targets"] > 0, hist["rz_targets"] / hist["team_rz_targets"], 0.0)
    hist["rz_carry_share"] = np.where(hist["team_rz_carries"] > 0, hist["rz_carries"] / hist["team_rz_carries"], 0.0)
    hist["gl10_opp_share"] = np.where(hist["team_gl10_opps"] > 0, hist["gl10_opps"] / hist["team_gl10_opps"], 0.0)
    hist["gl5_opp_share"] = np.where(hist["team_gl5_opps"] > 0, hist["gl5_opps"] / hist["team_gl5_opps"], 0.0)

    # Lagged opponent RZ TDs allowed per game by position
    opp_wk = (
        hist.groupby(["season", "week", "opponent_team", "position"], as_index=False)["rz_tds"]
        .sum()
    )
    opp_rate = (
        opp_wk.groupby(["opponent_team", "position"])["rz_tds"]
        .mean()
        .to_dict()
    )
    pos_default_opp = opp_wk.groupby("position")["rz_tds"].mean().to_dict()

    # Position empirical priors for RZ TD rate shrinkage
    pos_rz_totals = hist.groupby("position")[["rz_tds", "rz_opps"]].sum()
    pos_priors: dict[str, float] = {}
    for pos in TD_POSITIONS:
        if pos in pos_rz_totals.index and pos_rz_totals.loc[pos, "rz_opps"] > 0:
            pos_priors[pos] = float(pos_rz_totals.loc[pos, "rz_tds"] / pos_rz_totals.loc[pos, "rz_opps"])
        else:
            pos_priors[pos] = POSITION_RZ_TD_PRIOR.get(pos, 0.16)

    # Per-player summary strictly from hist
    rows: list[dict] = []
    player_rz_opps_avg: dict[str, float] = {}

    for pid, grp in hist.groupby("player_id", sort=False):
        grp = grp.sort_values(["season", "week"])
        last = grp.iloc[-1]
        pos = str(last["position"])
        team = str(last["team"])
        games_prior = int(len(grp))
        rz_opps_prior = float(grp["rz_opps"].sum())
        rz_tds_prior = float(grp["rz_tds"].sum())
        player_rz_opps_avg[str(pid)] = rz_opps_prior / max(games_prior, 1)

        prior = pos_priors.get(pos, 0.16)
        rz_td_rate_shrunk = (rz_tds_prior + RZ_SHRINK_OPPS * prior) / (rz_opps_prior + RZ_SHRINK_OPPS)

        # Role surge z-score: recent 3-game avg total_opps vs full prior avg
        opps_s = grp["total_opps"].astype(float)
        if games_prior >= 4 and float(opps_s.std(ddof=1)) > 1e-6:
            role_surge_z = float((opps_s.tail(3).mean() - opps_s.mean()) / opps_s.std(ddof=1))
        else:
            role_surge_z = 0.0

        opp_team = (target_matchups or {}).get(team, str(last["opponent_team"]))
        opp_rz_td = float(opp_rate.get((opp_team, pos), pos_default_opp.get(pos, 0.5)))

        rows.append({
            "player_id": str(pid),
            "player_name": str(last["player_name"]),
            "season": int(target_season),
            "week": int(target_week),
            "team": team,
            "opponent_team": opp_team,
            "position": pos,
            "rz_tgt_share_5g": float(np.clip(_ewma_last(grp["rz_tgt_share"]), 0.0, 1.0)),
            "rz_carry_share_5g": float(np.clip(_ewma_last(grp["rz_carry_share"]), 0.0, 1.0)),
            "gl10_opp_share_5g": float(np.clip(_ewma_last(grp["gl10_opp_share"]), 0.0, 1.0)),
            "gl5_opp_share_5g": float(np.clip(_ewma_last(grp["gl5_opp_share"]), 0.0, 1.0)),
            "rz_td_rate_shrunk": float(np.clip(rz_td_rate_shrunk, 0.0, 1.0)),
            "role_surge_z": round(role_surge_z, 4),
            "opp_rz_td_rate_pos": round(opp_rz_td, 4),
            "vacated_rz_opps_team": 0.0,
            "teammate_out_flag": 0,
            "games_prior": games_prior,
            "rz_opps_prior": rz_opps_prior,
            "confidence_tier": _confidence_tier(games_prior, rz_opps_prior),
        })

    out = pd.DataFrame(rows)

    # Injury vacated-RZ redistribution (strictly using prior-week averages)
    if injuries and not out.empty:
        out_ids: set[str] = set()
        vacated_by_team_pos: dict[tuple[str, str], float] = {}
        for inj in injuries:
            status = str(inj.get("status") or "").strip().upper()
            if status not in OUT_STATUSES:
                continue
            pid = str(inj.get("player_id") or "")
            team = str(inj.get("team") or "")
            pos = str(inj.get("position") or "")
            if pid and pid in player_rz_opps_avg:
                out_ids.add(pid)
                key = (team, pos)
                vacated_by_team_pos[key] = vacated_by_team_pos.get(key, 0.0) + player_rz_opps_avg[pid]

        if vacated_by_team_pos:
            for idx, r in out.iterrows():
                if str(r["player_id"]) in out_ids:
                    continue
                vac = vacated_by_team_pos.get((str(r["team"]), str(r["position"])), 0.0)
                if vac > 0.0:
                    out.at[idx, "vacated_rz_opps_team"] = round(vac, 4)
                    out.at[idx, "teammate_out_flag"] = 1

    return enforce_td_fail_closed(out)
