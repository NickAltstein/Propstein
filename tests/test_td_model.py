"""Unit and smoke tests for src.td_model and src.td_backtest."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.td_backtest import run_walkforward_backtest, write_backtest_report
from src.td_features import build_td_features
from src.td_model import TouchdownModel, implied_total_to_expected_tds, prob_to_american_odds


def _synthetic_player_weeks() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    rows = []
    players = [
        ("RB1", "Star RB", "DAL", "RB", 3, 2, 1, 0.30),
        ("WR1", "Alpha WR", "DAL", "WR", 3, 1, 1, 0.25),
        ("TE1", "RedZone TE", "DAL", "TE", 2, 1, 0, 0.20),
        ("RB2", "Opp RB", "NYG", "RB", 2, 1, 1, 0.24),
        ("WR2", "Opp WR", "NYG", "WR", 2, 1, 0, 0.18),
        ("TE2", "Opp TE", "NYG", "TE", 1, 0, 0, 0.12),
    ]
    for season in (2022, 2023):
        for wk in range(1, 10):
            for pid, name, team, pos, rz_base, gl10_b, gl5_b, td_p in players:
                opp = "NYG" if team == "DAL" else "DAL"
                rz_opps = int(rng.poisson(rz_base))
                rz_tgt = rz_opps if pos != "RB" else 0
                rz_car = rz_opps if pos == "RB" else 0
                tds = int(rng.binomial(max(rz_opps, 1), td_p))
                rows.append({
                    "player_id": pid,
                    "player_name": name,
                    "season": season,
                    "week": wk,
                    "team": team,
                    "opponent_team": opp,
                    "position": pos,
                    "rz_targets": rz_tgt,
                    "rz_carries": rz_car,
                    "rz_opps": rz_opps,
                    "gl10_opps": min(rz_opps, gl10_b),
                    "gl5_opps": min(rz_opps, gl5_b),
                    "rz_tds": tds,
                    "total_tds": tds,
                    "rz_air_yards": float(rz_tgt * 8.0),
                    "total_opps": rz_opps + 6,
                })
    return pd.DataFrame(rows)


def test_td_model_probability_coherence_team_totals_and_fail_closed():
    pw = _synthetic_player_weeks()
    train_snaps = []
    for wk in range(2, 9):
        f = build_td_features(pw, target_season=2022, target_week=wk)
        act = pw[(pw["season"] == 2022) & (pw["week"] == wk)][["player_id", "total_tds"]]
        f["implied_team_total"] = 24.0
        f["implied_opp_total"] = 21.0
        train_snaps.append(f.merge(act, on="player_id"))
    train_df = pd.concat(train_snaps, ignore_index=True)

    model = TouchdownModel().fit(train_df)
    eval_f = build_td_features(pw, target_season=2023, target_week=5)
    eval_f["implied_team_total"] = np.where(eval_f["team"] == "DAL", 27.0, 20.0)
    eval_f["implied_opp_total"] = np.where(eval_f["team"] == "DAL", 20.0, 27.0)

    preds = model.predict(eval_f)
    assert len(preds) == 6

    # Analytical coherence: 0 <= p_2plus <= p_anytime <= 1 and p_first <= p_anytime
    assert (preds["p_2plus_td"] >= 0.0).all()
    assert (preds["p_2plus_td"] <= preds["p_anytime_td"]).all()
    assert (preds["p_anytime_td"] <= 1.0).all()
    assert (preds["p_first_td"] <= preds["p_anytime_td"]).all()

    # Team-level coherence: sum(td_mean) == expected_team_tds and team_td_share sums to 1.0
    for team, grp in preds.groupby("team"):
        exp_tds = implied_total_to_expected_tds(grp["implied_team_total"].iloc[0])
        assert abs(grp["td_mean"].sum() - exp_tds) < 1e-4
        assert abs(grp["team_td_share"].sum() - 1.0) < 1e-4

    # Across both teams in the matchup, p_first_td sums to ~1.0
    assert abs(preds["p_first_td"].sum() - 1.0) < 1e-3

    # Fair odds sanity
    assert prob_to_american_odds(0.5) == -100
    assert prob_to_american_odds(0.25) == 300
    assert preds["fair_odds_anytime"].notna().all()

    # Fail-closed invariants
    assert (preds["actionable"] == False).all()  # noqa: E712
    assert preds["edge_pct"].isna().all()
    assert (preds["kelly_stake"] == 0.0).all()
    assert (preds["gate_status"] == "RESEARCH_ONLY_UNVERIFIED").all()


def test_walkforward_backtest_strict_no_future_leakage():
    pw = _synthetic_player_weeks()
    res_clean = run_walkforward_backtest(pw, seasons=(2023,), min_train_rows=20)
    p_clean = res_clean["predictions"]
    w3_clean = p_clean[p_clean["week"] == 3].sort_values("player_id").reset_index(drop=True)

    # Poison future weeks (2023 weeks 4..9) with extreme TDs
    poisoned = pw.copy()
    mask = (poisoned["season"] == 2023) & (poisoned["week"] >= 4)
    poisoned.loc[mask, "total_tds"] = 10
    poisoned.loc[mask, "rz_tds"] = 10
    poisoned.loc[mask, "rz_opps"] = 20

    res_poison = run_walkforward_backtest(poisoned, seasons=(2023,), min_train_rows=20)
    p_poison = res_poison["predictions"]
    w3_poison = p_poison[p_poison["week"] == 3].sort_values("player_id").reset_index(drop=True)

    pd.testing.assert_frame_equal(w3_clean, w3_poison)


def test_walkforward_backtest_offline_smoke_and_report(tmp_path: Path):
    pw = _synthetic_player_weeks()
    res = run_walkforward_backtest(pw, seasons=(2023,), min_train_rows=20)
    assert res["n_eval_rows"] > 0
    assert "brier_anytime_model" in res["metrics"]
    assert "brier_anytime_naive" in res["metrics"]
    assert len(res["reliability_deciles"]) >= 2
    assert 2023 in res["per_season"]

    report_path = tmp_path / "TD_MODEL_BACKTEST.md"
    txt = write_backtest_report(res, out_path=report_path)
    assert report_path.exists()
    assert "RESEARCH_ONLY_UNVERIFIED" in txt
    assert "Reliability Deciles" in txt
