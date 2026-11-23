"""Offline integration tests for src.td_slate (Track A — Day 3)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.td_slate import (
    TD_BOARD_SCHEMA_VERSION,
    TD_SLATE_ADDITIVE_FIELDS,
    write_td_board_and_enrich_slate,
)


def _synthetic_pw_and_games() -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(7)
    pw_rows = []
    players = [
        ("P_WR1", "CeeDee Lamb", "DAL", "WR", 3, 0.28),
        ("P_RB1", "Tony Pollard", "DAL", "RB", 3, 0.30),
        ("P_WR2", "Garrett Wilson", "NYJ", "WR", 2, 0.22),
        ("P_RB2", "Breece Hall", "NYJ", "RB", 2, 0.25),
        ("P_SF1", "Excluded WR", "SF", "WR", 2, 0.24),
    ]
    for season in (2025, 2026):
        max_wk = 10 if season == 2025 else 11
        for wk in range(1, max_wk):
            for pid, name, team, pos, rz_b, td_p in players:
                opp = "NYJ" if team == "DAL" else ("DAL" if team == "NYJ" else "MIN")
                rz_opps = int(rng.poisson(rz_b))
                tds = int(rng.binomial(max(rz_opps, 1), td_p))
                pw_rows.append({
                    "player_id": pid,
                    "player_name": name,
                    "season": season,
                    "week": wk,
                    "team": team,
                    "opponent_team": opp,
                    "position": pos,
                    "rz_targets": rz_opps if pos != "RB" else 0,
                    "rz_carries": rz_opps if pos == "RB" else 0,
                    "rz_opps": rz_opps,
                    "gl10_opps": min(rz_opps, 1),
                    "gl5_opps": min(rz_opps, 1),
                    "rz_tds": tds,
                    "total_tds": tds,
                    "rz_air_yards": float(rz_opps * 6.0),
                    "total_opps": rz_opps + 5,
                })

    games = pd.DataFrame([
        {
            "game_id": "2026_11_NYJ_DAL",
            "season": 2026,
            "week": 11,
            "away_team": "NYJ",
            "home_team": "DAL",
            "total_line": 47.5,
            "spread_line": 6.5,
        },
        {
            "game_id": "2026_11_MIN_SF",
            "season": 2026,
            "week": 11,
            "away_team": "MIN",
            "home_team": "SF",
            "total_line": 44.0,
            "spread_line": 2.5,
        },
    ])
    return pd.DataFrame(pw_rows), games


def test_td_board_and_slate_enrichment_preserves_receiving_and_fail_closed(tmp_path: Path):
    pw, games = _synthetic_pw_and_games()
    viewer_dir = tmp_path / "viewer"
    viewer_dir.mkdir()

    original_slate = [
        {
            "player_id": "P_WR1",
            "player_display_name": "CeeDee Lamb",
            "team": "DAL",
            "opponent_team": "NYJ",
            "game_id": "2026_11_NYJ_DAL",
            "position": "WR",
            "proj_rec": 8.5,
            "fair_line_rec": 8.5,
            "proj_yards": 112.9,
            "fair_line_yards": 104.0,
            "status": "ACT",
        },
        {
            "player_id": None,
            "player_name": "MIN @ SF (Unpriced)",
            "team": "SF",
            "opponent": "MIN",
            "game_id": "2026_11_MIN_SF",
            "position": "CONTEXT_MISSING",
            "context_status": "CONTEXT_MISSING",
            "proj_rec": None,
            "proj_yards": None,
            "actionable": False,
        },
    ]
    slate_path = viewer_dir / "slate_2026_w11.json"
    slate_path.write_text(json.dumps(original_slate, indent=2), encoding="utf-8")

    res = write_td_board_and_enrich_slate(
        pw,
        season=2026,
        week=11,
        viewer_dir=viewer_dir,
        games=games,
        exclude_games=["2026_11_MIN_SF"],
        min_train_rows=20,
    )

    td_board_path = viewer_dir / "td_board_2026_w11.json"
    assert td_board_path.exists()
    board = json.loads(td_board_path.read_text(encoding="utf-8"))

    assert board["schema_version"] == TD_BOARD_SCHEMA_VERSION
    assert board["gate_status"] == "RESEARCH_ONLY_UNVERIFIED"
    assert board["actionable_count"] == 0
    assert board["excluded_games"] == ["2026_11_MIN_SF"]
    assert board["row_count"] == 4  # DAL + NYJ only; SF excluded
    assert all(r["game_id"] != "2026_11_MIN_SF" for r in board["rows"])

    for r in board["rows"]:
        assert 0.0 <= r["p_2plus_td"] <= r["p_anytime_td"] <= 1.0
        assert 0.0 <= r["p_first_td"] <= r["p_anytime_td"]
        assert r["actionable"] is False
        assert r["edge_pct"] is None
        assert r["kelly_stake"] == 0.0
        assert r["gate_status"] == "RESEARCH_ONLY_UNVERIFIED"

    # Verify slate enrichment preserves EVERY existing receiving field verbatim
    enriched_slate = json.loads(slate_path.read_text(encoding="utf-8"))
    assert len(enriched_slate) == 2
    wr1_after = enriched_slate[0]
    for k, v in original_slate[0].items():
        assert wr1_after[k] == v, f"Receiving field {k} was mutated!"
    for k in TD_SLATE_ADDITIVE_FIELDS:
        assert k in wr1_after, f"Missing additive TD field {k}"
    assert wr1_after["td_gate_status"] == "RESEARCH_ONLY_UNVERIFIED"
    assert res["slate_enriched_rows"] == 1

    # CONTEXT_MISSING row stays unpriced (no TD probabilities attached)
    cm_after = enriched_slate[1]
    assert cm_after["context_status"] == "CONTEXT_MISSING"
    assert "p_anytime_td" not in cm_after

    # Determinism: second identical run produces byte-for-byte identical td_board
    first_bytes = td_board_path.read_bytes()
    slate_path.write_text(json.dumps(original_slate, indent=2), encoding="utf-8")
    write_td_board_and_enrich_slate(
        pw,
        season=2026,
        week=11,
        viewer_dir=viewer_dir,
        games=games,
        exclude_games=["2026_11_MIN_SF"],
        min_train_rows=20,
    )
    assert td_board_path.read_bytes() == first_bytes
