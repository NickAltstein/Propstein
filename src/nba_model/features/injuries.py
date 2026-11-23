"""Availability / rotation adjustments from the project's injury feed.

Consumes the existing PropWire availability schema (viewer/nba_injuries_*.json
shape): rows with player_id, team, game_id, report_status, as_of, and — when
the upstream report supplies them — vacated_minutes / redistribution_plan.

Adjustments (all explicit, all explainable):
  own status "Out"                    -> availability_mult = 0 (not projected)
  own status Questionable/GTD/Doubtful -> variance inflation
  teammate(s) Out                     -> capped minutes boost, share-based
  missing/stale feed                  -> neutral 1.0 (recorded, never guessed)
"""

from __future__ import annotations

import pandas as pd

from ..config import (
    QUESTIONABLE_VAR_INFLATION,
    TEAMMATE_OUT_BOOST_CAP,
    TEAMMATE_OUT_MINUTES_BOOST,
)

BOOST_STATUSES = {"out", "out (rehab assignment)", "inactive", "suspended"}
REDISTRIBUTION_STATUSES = BOOST_STATUSES | {"doubtful"}
VAR_STATUSES = {"questionable", "game time decision", "gtd", "doubtful"}
VACATED_MIN_PER_OUT = 3.5
VACATED_MIN_CAP = 8.0


def add_availability_adjustments(
    df: pd.DataFrame,
    injuries: list[dict] | None,
) -> pd.DataFrame:
    df = df.copy()
    df["availability_mult"] = 1.0
    df["var_inflation"] = 1.0
    df["teammates_out"] = 0
    df["vacated_min_boost"] = 0.0
    df["role_surge"] = False
    df["role_surge_reason"] = ""

    if not injuries:
        df["availability_source"] = "none"
        df["own_status"] = ""
        return df
    df["availability_source"] = "project_feed"

    inj_df = pd.DataFrame(injuries)
    inj_df["status_key"] = inj_df["report_status"].fillna("").str.lower()

    # Own status per (player_id, game_id). The merge supplies the column —
    # do NOT pre-create own_status above (pandas suffix collision).
    own = inj_df[["player_id", "game_id", "status_key"]].rename(
        columns={"status_key": "own_status"}
    )
    df = df.merge(
        own.drop_duplicates(subset=["player_id", "game_id"]),
        on=["player_id", "game_id"], how="left",
    )
    df["own_status"] = df["own_status"].fillna("")

    # Teammate-out map per (game_id, team): OUT rows for availability_mult,
    # OUT/DOUBTFUL rows for bounded vacated_min_boost / role_surge.
    out_map = (
        inj_df[inj_df["status_key"].isin(BOOST_STATUSES)]
        .groupby(["game_id", "team"])["player_id"]
        .apply(set)
        .to_dict()
    )
    redist_map = (
        inj_df[inj_df["status_key"].isin(REDISTRIBUTION_STATUSES)]
        .groupby(["game_id", "team"])["player_id"]
        .apply(set)
        .to_dict()
    )

    boost_values, var_values, n_out_values = [], [], []
    vacated_min_values, surge_flags, surge_reasons = [], [], []
    for _, row in df.iterrows():
        var = QUESTIONABLE_VAR_INFLATION if row["own_status"] in VAR_STATUSES else 1.0
        var_values.append(var)

        out_ids = out_map.get((row["game_id"], row["team"]), set())
        boost, n_out = 0.0, 0
        for out_id in out_ids:
            if out_id == row["player_id"]:
                continue
            boost += TEAMMATE_OUT_MINUTES_BOOST
            n_out += 1
        boost_values.append(round(min(boost, TEAMMATE_OUT_BOOST_CAP), 4))
        n_out_values.append(n_out)

        redist_ids = {pid for pid in redist_map.get((row["game_id"], row["team"]), set()) if pid != row["player_id"]}
        if row["own_status"] in BOOST_STATUSES or not redist_ids:
            vacated_min_values.append(0.0)
            surge_flags.append(False)
            surge_reasons.append("")
        else:
            v_min = round(min(len(redist_ids) * VACATED_MIN_PER_OUT, VACATED_MIN_CAP), 2)
            vacated_min_values.append(v_min)
            is_surge = v_min >= 3.5
            surge_flags.append(is_surge)
            surge_reasons.append(
                f"+{v_min:.1f} min · {len(redist_ids)} teammate{'s' if len(redist_ids) != 1 else ''} OUT/D"
                if is_surge else ""
            )

    df["availability_mult"] = 1.0 + pd.Series(boost_values, index=df.index)
    df["var_inflation"] = pd.Series(var_values, index=df.index)
    df["teammates_out"] = pd.Series(n_out_values, index=df.index)
    df["vacated_min_boost"] = pd.Series(vacated_min_values, index=df.index)
    df["role_surge"] = pd.Series(surge_flags, index=df.index)
    df["role_surge_reason"] = pd.Series(surge_reasons, index=df.index)
    df.loc[df["own_status"].isin(BOOST_STATUSES), "availability_mult"] = 0.0
    return df
