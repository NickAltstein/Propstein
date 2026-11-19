"""Tests for P12 / P11-D: point-in-time leakage, common bucketing, Holm correction, and isolation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.shadow_dispersion_backtest import (
    build_point_in_time_features,
    evaluate_split,
    fit_and_tune,
    holm_adjust,
)


def _synthetic_df() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    rows = []
    for season in (2021, 2022, 2023, 2024):
        for week in range(1, 10):
            for pid, team, adot_base in (("p1", "DET", 11.0), ("p2", "DET", 7.0), ("p3", "KC", 9.0)):
                tgt = int(rng.integers(3, 11))
                rec = int(max(1, round(tgt * 0.65)))
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
                    "receiving_yards": max(1.0, yds),
                })
    return pd.DataFrame(rows)


def test_point_in_time_features_have_zero_same_game_leakage():
    df = _synthetic_df()
    base_feat = build_point_in_time_features(df)

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


def test_holm_adjust_monotonic_and_bounded():
    adj = holm_adjust([0.005, 0.02, 0.04, 0.50])
    assert adj == [0.02, 0.06, 0.08, 0.5]


def test_common_bucket_counts_verdicts_and_production_isolation():
    feat = build_point_in_time_features(_synthetic_df())
    elig = feat[feat["prior_games"] >= 3].copy()
    params = fit_and_tune(elig[elig["season"].isin((2021, 2022))], elig[elig["season"] == 2023])
    res = evaluate_split(elig[elig["season"] == 2024], params, n_boot=20)
    assert res["n_games"] > 0
    assert sum(b["n_obs"] for b in res["buckets"]) == res["n_games"] * 4
    assert all(b["verdict"] in {"improves", "no evidence of difference", "worse"} for b in res["buckets"])
    assert all("holm_p" in b and "raw_p" in b for b in res["buckets"])

    for root in (Path("src"), Path("api")):
        for py_file in root.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            assert "shadow_v1" not in text
            assert "shadow_dispersion_backtest" not in text
