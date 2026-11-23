"""Regression test for mid-season roster updates in src.slate.build_slate."""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

_SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import slate as slate_mod


def _make_hist() -> pd.DataFrame:
    rows = []
    for wk in (1, 2, 3, 4):
        rows.append({
            "player_id": "00-0030279",
            "player_display_name": "Keenan Allen",
            "season": 2026,
            "week": wk,
            "team": "IND",
            "opponent_team": "HOU",
            "position": "WR",
            "targets": 7.0,
            "receptions": 5.0,
            "receiving_yards": 55.0,
            "receiving_tds": 0.0,
            "receiving_air_yards": 65.0,
            "receiving_yards_after_catch": 20.0,
            "target_share": 0.22,
            "air_yards_share": 0.24,
        })
    return pd.DataFrame(rows)


def _make_games() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "game_id": "2026_05_IND_HOU",
            "season": 2026,
            "week": 5,
            "away_team": "IND",
            "home_team": "HOU",
            "away_score": float("nan"),
            "home_score": float("nan"),
            "roof": "dome",
            "surface": "fieldturf",
            "wind": 0.0,
            "spread_line": 3.0,
            "total_line": 46.5,
        },
        {
            "game_id": "2026_11_IND_HOU",
            "season": 2026,
            "week": 11,
            "away_team": "IND",
            "home_team": "HOU",
            "away_score": float("nan"),
            "home_score": float("nan"),
            "roof": "dome",
            "surface": "fieldturf",
            "wind": 0.0,
            "spread_line": 3.0,
            "total_line": 46.5,
        },
        {
            "game_id": "2026_11_MIN_SF",
            "season": 2026,
            "week": 11,
            "away_team": "MIN",
            "home_team": "SF",
            "away_score": float("nan"),
            "home_score": float("nan"),
            "roof": "outdoors",
            "surface": "grass",
            "wind": 8.0,
            "spread_line": -2.5,
            "total_line": 45.0,
        },
    ])


def _make_rosters() -> pd.DataFrame:
    return pd.DataFrame([
        {"player_id": "00-0030279", "week": 1, "team": "IND", "position": "WR", "status": "ACT", "years_exp": 13, "full_name": "Keenan Allen"},
        {"player_id": "00-0030279", "week": 5, "team": "IND", "position": "WR", "status": "ACT", "years_exp": 13, "full_name": "Keenan Allen"},
        {"player_id": "00-0030279", "week": 10, "team": "SF", "position": "WR", "status": "ACT", "years_exp": 13, "full_name": "Keenan Allen"},
    ])


def test_build_slate_uses_latest_roster_week_up_to_target(monkeypatch):
    monkeypatch.setattr(slate_mod, "load_player_weeks", _make_hist)
    monkeypatch.setattr(slate_mod, "load_games", _make_games)
    monkeypatch.setattr(slate_mod, "load_rosters", lambda season: _make_rosters())
    monkeypatch.setattr(slate_mod, "attach_game_context", lambda slate, games: slate.assign(
        implied_team_total=22.0, implied_opp_total=23.0, is_home=1, is_dome=0, wind=5.0,
    ))
    monkeypatch.setattr(slate_mod, "add_player_form", lambda df: df.assign(
        f_targets=7.0, f_rec=5.0, f_yards=55.0, f_tgt_share=0.22,
        f_ay_share=0.24, f_adot=9.0, f_catch_rate=0.71, f_ypr=11.0,
        f_tgt_sd=1.5, c_targets=7.0, c_yards=55.0,
        catch_rate_g=0.71, ypr_g=11.0, adot=9.0, games_prior=4,
    ))
    monkeypatch.setattr(slate_mod, "add_defense_adjustment", lambda slate, *a, **kw: slate.assign(
        def_yards_idx=1.0, def_rec_idx=1.0, def_tgt_idx=1.0,
    ))

    w11 = slate_mod.build_slate(2026, 11, include_all=True)
    assert len(w11) == 1
    assert w11.iloc[0]["current_team"] == "SF"
    assert w11.iloc[0]["team"] == "SF"
    assert int(w11.iloc[0]["changed_team"]) == 1
    assert w11.iloc[0]["game_id"] == "2026_11_MIN_SF"

    # Historical Week 5 must not leak the Week 10 trade to SF.
    w5 = slate_mod.build_slate(2026, 5, include_all=True)
    assert len(w5) == 1
    assert w5.iloc[0]["current_team"] == "IND"
    assert w5.iloc[0]["team"] == "IND"
    assert int(w5.iloc[0]["changed_team"]) == 0
