"""Unit tests for src.td_features — leakage, PBP aggregation, injury redistribution, fail-closed."""

from __future__ import annotations

import pandas as pd

from src.td_features import (
    build_td_features,
    extract_pbp_player_weeks,
)


def _sample_pbp() -> pd.DataFrame:
    rows = []
    for wk in (1, 2, 3, 4, 5, 6):
        # WR1 gets 2 RZ targets (1 inside 5, 1 TD) per week
        rows.append({
            "season": 2026, "week": wk, "posteam": "DAL", "defteam": "NYG",
            "yardline_100": 4, "play_type": "pass",
            "receiver_player_id": "WR1", "receiver_player_name": "Alpha WR",
            "receiver_position": "WR", "pass_touchdown": 1, "air_yards": 4.0,
        })
        rows.append({
            "season": 2026, "week": wk, "posteam": "DAL", "defteam": "NYG",
            "yardline_100": 14, "play_type": "pass",
            "receiver_player_id": "WR1", "receiver_player_name": "Alpha WR",
            "receiver_position": "WR", "pass_touchdown": 0, "air_yards": 12.0,
        })
        # WR2 gets 1 RZ target (inside 10, 0 TD) per week
        rows.append({
            "season": 2026, "week": wk, "posteam": "DAL", "defteam": "NYG",
            "yardline_100": 9, "play_type": "pass",
            "receiver_player_id": "WR2", "receiver_player_name": "Beta WR",
            "receiver_position": "WR", "pass_touchdown": 0, "air_yards": 9.0,
        })
        # RB1 gets 2 RZ carries (1 inside 5) per week
        rows.append({
            "season": 2026, "week": wk, "posteam": "DAL", "defteam": "NYG",
            "yardline_100": 2, "play_type": "run",
            "rusher_player_id": "RB1", "rusher_player_name": "GoalLine RB",
            "rusher_position": "RB", "rush_touchdown": 1,
        })
        rows.append({
            "season": 2026, "week": wk, "posteam": "DAL", "defteam": "NYG",
            "yardline_100": 15, "play_type": "run",
            "rusher_player_id": "RB1", "rusher_player_name": "GoalLine RB",
            "rusher_position": "RB", "rush_touchdown": 0,
        })
    return pd.DataFrame(rows)


def test_extract_pbp_player_weeks_aggregates_rz_and_gl():
    pw = extract_pbp_player_weeks(_sample_pbp())
    wr1_w1 = pw[(pw["player_id"] == "WR1") & (pw["week"] == 1)].iloc[0]
    assert wr1_w1["rz_targets"] == 2
    assert wr1_w1["rz_carries"] == 0
    assert wr1_w1["gl10_opps"] == 1
    assert wr1_w1["gl5_opps"] == 1
    assert wr1_w1["rz_tds"] == 1
    assert wr1_w1["total_tds"] == 1

    rb1_w1 = pw[(pw["player_id"] == "RB1") & (pw["week"] == 1)].iloc[0]
    assert rb1_w1["rz_carries"] == 2
    assert rb1_w1["gl5_opps"] == 1
    assert rb1_w1["rz_tds"] == 1


def test_build_td_features_zero_leakage_from_same_or_future_week():
    pw = extract_pbp_player_weeks(_sample_pbp())
    clean = build_td_features(pw, target_season=2026, target_week=5)

    # Inject a massive future/same-week TD spike in week 5 and week 6
    poisoned = pw.copy()
    mask = (poisoned["player_id"] == "WR2") & (poisoned["week"].isin([5, 6]))
    poisoned.loc[mask, "rz_targets"] = 25
    poisoned.loc[mask, "rz_opps"] = 25
    poisoned.loc[mask, "gl5_opps"] = 20
    poisoned.loc[mask, "rz_tds"] = 10
    poisoned.loc[mask, "total_tds"] = 10

    after = build_td_features(poisoned, target_season=2026, target_week=5)
    pd.testing.assert_frame_equal(clean, after)


def test_build_td_features_shares_shrinkage_and_fail_closed():
    pw = extract_pbp_player_weeks(_sample_pbp())
    feats = build_td_features(pw, target_season=2026, target_week=7)
    assert len(feats) == 3

    for col in ("rz_tgt_share_5g", "rz_carry_share_5g", "gl10_opp_share_5g", "gl5_opp_share_5g", "rz_td_rate_shrunk"):
        assert (feats[col] >= 0.0).all()
        assert (feats[col] <= 1.0).all()

    wr1 = feats[feats["player_id"] == "WR1"].iloc[0]
    assert abs(wr1["rz_tgt_share_5g"] - (2.0 / 3.0)) < 1e-6
    assert wr1["confidence_tier"] == "HIGH"

    # Fail-closed invariants
    assert (feats["actionable"] == False).all()  # noqa: E712
    assert feats["edge_pct"].isna().all()
    assert (feats["kelly_stake"] == 0.0).all()
    assert (feats["gate_status"] == "RESEARCH_ONLY_UNVERIFIED").all()


def test_build_td_features_injury_vacated_rz_redistribution():
    pw = extract_pbp_player_weeks(_sample_pbp())
    injuries = [
        {"player_id": "WR1", "team": "DAL", "position": "WR", "status": "OUT"},
    ]
    feats = build_td_features(pw, target_season=2026, target_week=7, injuries=injuries)

    wr1 = feats[feats["player_id"] == "WR1"].iloc[0]
    wr2 = feats[feats["player_id"] == "WR2"].iloc[0]
    rb1 = feats[feats["player_id"] == "RB1"].iloc[0]

    # WR1 is OUT so WR1 does not receive its own vacated opps
    assert wr1["vacated_rz_opps_team"] == 0.0
    assert wr1["teammate_out_flag"] == 0

    # Active teammate WR2 receives WR1's lagged avg RZ opps (2.0/game)
    assert abs(wr2["vacated_rz_opps_team"] - 2.0) < 1e-6
    assert wr2["teammate_out_flag"] == 1

    # Different position (RB1) does not receive WR vacated opps
    assert rb1["vacated_rz_opps_team"] == 0.0
    assert rb1["teammate_out_flag"] == 0
