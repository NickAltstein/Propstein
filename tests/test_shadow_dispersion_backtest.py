"""Tests for P11 Part D: point-in-time leakage guard and shadow_v1 isolation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.shadow_dispersion_backtest import (
    build_point_in_time_features,
    evaluate_oos,
    fit_models,
)


def _synthetic_df() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    rows = []
    for season in (2021, 2022, 2023):
        for week in range(1, 10):
            for pid, team, adot_base in (("p1", "DET", 11.0), ("p2", "DET", 7.0), ("p3", "KC", 9.0)):
                tgt = int(rng.integers(3, 11))
                rec = int(max(0, round(tgt * 0.65)))
                ay = float(tgt * adot_base)
                yds = float(rec * (adot_base * 0.9 + rng.normal(0, 2)))
                rows.append({
                    "player_id": pid,
                    "recent_team": team,
                    "position": "WR",
                    "season": season,
                    "week": week,
                    "targets": tgt,
                    "receptions": rec,
                    "receiving_air_yards": ay,
                    "receiving_yards": max(0.0, yds),
                })
    return pd.DataFrame(rows)


def test_point_in_time_features_have_zero_same_game_leakage():
    df = _synthetic_df()
    base_feat = build_point_in_time_features(df)

    # Mutate week 8 of 2023 for p1 to extreme values; its own features for week 8 must not change
    mutated = df.copy()
    mask = (mutated["player_id"] == "p1") & (mutated["season"] == 2023) & (mutated["week"] == 8)
    mutated.loc[mask, "receiving_air_yards"] = 9999.0
    mutated.loc[mask, "targets"] = 99.0
    mutated.loc[mask, "receiving_yards"] = 999.0
    mut_feat = build_point_in_time_features(mutated)

    row_a = base_feat[(base_feat["player_id"] == "p1") & (base_feat["season"] == 2023) & (base_feat["week"] == 8)].iloc[0]
    row_b = mut_feat[(mut_feat["player_id"] == "p1") & (mut_feat["season"] == 2023) & (mut_feat["week"] == 8)].iloc[0]
    for col in ("prior_adot", "prior_ay_share", "prior_rec_mean", "prior_ypc_mean"):
        assert row_a[col] == row_b[col]


def test_shadow_backtest_runs_oos_and_is_isolated_from_production():
    feat = build_point_in_time_features(_synthetic_df())
    elig = feat[feat["prior_games"] >= 3].copy()
    params = fit_models(elig[elig["season"].isin((2021, 2022))])
    res = evaluate_oos(elig[elig["season"] == 2023], params, n_boot=20)
    assert res["n_test_games"] > 0
    assert len(res["buckets"]) == 5

    # No production file may reference shadow_v1
    for root in (Path("src"), Path("api")):
        for py_file in root.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            assert "shadow_v1" not in text
            assert "shadow_dispersion_backtest" not in text
