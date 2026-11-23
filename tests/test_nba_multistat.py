"""Unit tests for NBA Phase 2 — NBAMultiStatProjector, NegativeBinomialDistribution, and covariance combos."""

from __future__ import annotations

import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

_SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from nba_model.model.distribution import NegativeBinomialDistribution
from nba_model.model.multistat import NBAMultiStatProjector


def _synthetic_nba_games() -> pd.DataFrame:
    rows = []
    # P1: strongly positively correlated PTS, REB, AST (high-minute star)
    for i in range(15):
        scale = 1.0 if i % 2 == 0 else 1.5
        mins = 28.0 if i % 2 == 0 else 38.0
        pts = round(18.0 * scale, 1)
        reb = round(6.0 * scale, 1)
        ast = round(5.0 * scale, 1)
        fg3m = 0 if i % 3 == 0 else 3
        rows.append({
            "player_id": "P1", "player_display_name": "Star Guard",
            "game_id": f"G{i}", "game_date": f"2026-10-{10 + i:02d}",
            "team": "DEN", "opponent": "CHI", "position": "G",
            "is_home": i % 2, "dnp": False, "minutes": mins, "margin": 4.0,
            "pts": pts, "ppm": pts / mins, "rebounds": reb, "assists": ast,
            "fg3m": fg3m, "fga": 16.0, "poss": 98.0, "pace_ratio": 1.0,
            "opp_pace_ratio": 1.0,
        })
    # P2: negatively correlated PTS vs AST (scoring burst vs playmaking)
    for i in range(15):
        mins = 32.0
        pts = 26.0 if i % 2 == 0 else 12.0
        ast = 2.0 if i % 2 == 0 else 11.0
        reb = 5.0
        rows.append({
            "player_id": "P2", "player_display_name": "Toggle Guard",
            "game_id": f"G{i}", "game_date": f"2026-10-{10 + i:02d}",
            "team": "CHI", "opponent": "DEN", "position": "G",
            "is_home": 1 - (i % 2), "dnp": False, "minutes": mins, "margin": -4.0,
            "pts": pts, "ppm": pts / mins, "rebounds": reb, "assists": ast,
            "fg3m": 2, "fga": 15.0, "poss": 98.0, "pace_ratio": 1.0,
            "opp_pace_ratio": 1.0,
        })
    return pd.DataFrame(rows)


def test_negative_binomial_distribution_coherence_and_zero_inflation():
    d = NegativeBinomialDistribution(mean=6.5, sd=3.2, zero_inflation=0.0)
    # Complementarity on integer and half-integer lines
    for line in (0.5, 4.5, 6.0, 6.5, 10.0):
        tot = d.p_over(line) + d.p_push(line) + d.p_under(line)
        assert abs(tot - 1.0) < 1e-6

    # Monotonic in line
    assert d.p_over(2.5) > d.p_over(6.5) > d.p_over(12.5)

    # Zero-inflation increases mass at 0 (lowers P(over 0.5))
    d_plain = NegativeBinomialDistribution(mean=2.0, sd=1.8, zero_inflation=0.0)
    d_zi = NegativeBinomialDistribution(mean=2.0, sd=1.8, zero_inflation=0.35)
    assert d_zi.p_over(0.5) < d_plain.p_over(0.5)


def test_multistat_projector_covariance_combos_and_fail_closed():
    df = _synthetic_nba_games()
    model = NBAMultiStatProjector().fit(df)

    test_df = pd.DataFrame([
        {
            "player_id": "P1", "player_display_name": "Star Guard",
            "game_id": "NEXT1", "game_date": "2026-11-23",
            "team": "DEN", "opponent": "CHI", "position": "G",
            "is_home": 1, "dnp": False, "minutes": 33.0, "ppm": 0.65,
        },
        {
            "player_id": "P2", "player_display_name": "Toggle Guard",
            "game_id": "NEXT1", "game_date": "2026-11-23",
            "team": "CHI", "opponent": "DEN", "position": "G",
            "is_home": 0, "dnp": False, "minutes": 32.0, "ppm": 0.59,
        },
    ])

    preds = model.predict(test_df, history=df)
    assert len(preds) == 2

    for _, r in preds.iterrows():
        for m in ("points", "rebounds", "assists", "threes", "pra", "pr", "pa", "ra"):
            assert r[f"proj_{m}"] >= 0.0
            assert r[f"sd_{m}"] > 0.0
            assert r[f"fair_line_{m}"] >= 0.5
            assert 0.0 <= r[f"p_over_fair_{m}"] <= 1.0
        # Fail-closed invariants
        assert r["actionable"] is False
        assert r["edge_pct"] is None
        assert r["kelly_stake"] == 0.0
        assert r["gate_status"] == "RESEARCH_ONLY_UNVERIFIED"

    # Covariance check: P1 has strong positive covariance -> sd_pra > independent sum
    p1 = preds[preds["player_id"] == "P1"].iloc[0]
    indep_sd_p1 = math.sqrt(p1["sd_points"] ** 2 + p1["sd_rebounds"] ** 2 + p1["sd_assists"] ** 2)
    assert p1["sd_pra"] > indep_sd_p1

    # P2 has strong negative PTS-AST covariance -> sd_pa < independent sum
    p2 = preds[preds["player_id"] == "P2"].iloc[0]
    indep_sd_pa_p2 = math.sqrt(p2["sd_points"] ** 2 + p2["sd_assists"] ** 2)
    assert p2["sd_pa"] < indep_sd_pa_p2
