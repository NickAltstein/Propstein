"""Unit and offline integration tests for Track B Day 1 (NBA minutes, role_surge, slate export)."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

_SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from nba_model.features.injuries import add_availability_adjustments
from nba_model.features.minutes import ExpectedMinutesModel
from nba_model.publish.slate_export import export_nba_slate_and_index


def _sample_train() -> pd.DataFrame:
    rows = []
    # P1: season avg ~26 min, last 5 games ~34 min (recent surge!)
    for i in range(10):
        mins = 20.0 if i < 5 else 34.0
        rows.append({
            "player_id": "P1", "game_id": f"G{i}", "position": "G",
            "minutes": mins, "margin": 4.0, "dnp": False,
        })
    # P2: steady 32 min starter
    for i in range(10):
        rows.append({
            "player_id": "P2", "game_id": f"G{i}", "position": "F",
            "minutes": 32.0 + (i % 3), "margin": 5.0, "dnp": False,
        })
    return pd.DataFrame(rows)


def test_minutes_model_bands_blowout_haircut_and_role_surge():
    model = ExpectedMinutesModel().fit(_sample_train())

    close_df = pd.DataFrame([
        {"player_id": "P1", "game_id": "T1", "position": "G", "spread": 2.5},
        {"player_id": "P2", "game_id": "T1", "position": "F", "spread": 2.5},
    ])
    blowout_df = pd.DataFrame([
        {"player_id": "P2", "game_id": "T2", "position": "F", "spread": -14.5},
    ])

    close_out = model.predict(close_df)
    blow_out = model.predict(blowout_df)

    for _, r in close_out.iterrows():
        assert 0.0 <= r["minutes_p10"] <= r["minutes_hat"] <= r["minutes_p90"] <= 48.0

    p1 = close_out[close_out["player_id"] == "P1"].iloc[0]
    assert bool(p1["role_surge"]) is True
    assert "recent surge" in p1["role_surge_reason"]

    p2_close = close_out[close_out["player_id"] == "P2"].iloc[0]
    p2_blow = blow_out.iloc[0]
    assert p2_blow["minutes_hat"] < p2_close["minutes_hat"]
    assert p2_blow["sd_minutes"] > p2_close["sd_minutes"]


def test_injuries_vacated_minutes_capped_and_role_surge():
    df = pd.DataFrame([
        {"player_id": "STAR", "game_id": "G1", "team": "MIA"},
        {"player_id": "BACKUP", "game_id": "G1", "team": "MIA"},
    ])
    injuries = [
        {"player_id": "STAR", "game_id": "G1", "team": "MIA", "report_status": "Out"},
        {"player_id": "EXTRA1", "game_id": "G1", "team": "MIA", "report_status": "Out"},
        {"player_id": "EXTRA2", "game_id": "G1", "team": "MIA", "report_status": "Doubtful"},
    ]
    out = add_availability_adjustments(df, injuries)
    star = out[out["player_id"] == "STAR"].iloc[0]
    backup = out[out["player_id"] == "BACKUP"].iloc[0]

    assert star["availability_mult"] == 0.0
    assert star["vacated_min_boost"] == 0.0
    assert bool(star["role_surge"]) is False

    # 3 teammates OUT/Doubtful -> capped at 8.0 min
    assert backup["vacated_min_boost"] == 8.0
    assert bool(backup["role_surge"]) is True
    assert "OUT/D" in backup["role_surge_reason"]


def test_export_nba_slate_fail_closed_and_preserves_nfl_index(tmp_path: Path):
    viewer_dir = tmp_path / "viewer"
    feeds_dir = viewer_dir / "feeds"
    feeds_dir.mkdir(parents=True)

    original_nfl = {
        "slates": {
            "2026_w11": {
                "shortLabel": "W11 Live",
                "actionLocked": False,
                "modelContextStatus": "AVAILABLE",
            }
        }
    }
    index_path = feeds_dir / "slate-index.json"
    index_path.write_text(json.dumps({
        "schema_version": "slate-index.v1",
        "sports": {
            "nfl": original_nfl,
            "nba": {"slates": {"2026-11-23": {"shortLabel": "Nov 23 Slate", "marketOnly": True}}},
        },
    }, indent=2), encoding="utf-8")

    research_path = tmp_path / "nba_research_2026-11-23.json"
    research_path.write_text(json.dumps({
        "rows": [
            {
                "player_id": "203999",
                "player_display_name": "Nikola Jokic",
                "team": "DEN",
                "opponent": "CHI",
                "game_id": "nba-20261123-DEN-CHI",
                "position": "C",
                "mean_pts": 27.4,
                "sd_pts": 6.1,
                "fair_line": 27.5,
                "minutes_p10": 31.2,
                "minutes_hat": 35.8,
                "minutes_p90": 40.4,
                "sd_minutes": 3.6,
                "role_surge": True,
                "role_surge_reason": "+3.5 min · 1 teammate OUT/D",
            }
        ]
    }), encoding="utf-8")

    res = export_nba_slate_and_index(research_path, "2026-11-23", viewer_dir)
    assert res["row_count"] == 1
    assert res["index_updated"] is True

    slate = json.loads((viewer_dir / "nba_slate_2026-11-23.json").read_text(encoding="utf-8"))
    assert len(slate) == 1
    row = slate[0]
    assert row["player_display_name"] == "Nikola Jokic"
    assert row["minutes_p10"] == 31.2
    assert row["minutes_hat"] == 35.8
    assert row["minutes_p90"] == 40.4
    assert row["role_surge"] is True
    assert row["actionable"] is False
    assert row["edge_pct"] is None
    assert row["kelly_stake"] == 0.0
    assert row["gate_status"] == "RESEARCH_ONLY_UNVERIFIED"

    idx_after = json.loads(index_path.read_text(encoding="utf-8"))
    assert idx_after["sports"]["nfl"] == original_nfl
    assert idx_after["sports"]["nba"]["slates"]["2026-11-23"]["modelContextStatus"] == "AVAILABLE"
    assert idx_after["sports"]["nba"]["slates"]["2026-11-23"]["marketOnly"] is False
