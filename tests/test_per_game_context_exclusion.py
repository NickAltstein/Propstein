"""Tests for P15: per-game CONTEXT_MISSING exclusion, cap, allowlist, and downstream safety."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.ingest_espn_game_context import (
    ET,
    apply_context_exclusion_to_slate_rows,
    evaluate_pricing_context,
    format_provenance_report,
)


def _make_slate(n_games: int = 13, *, missing_indices: set[int] = frozenset(), stale_idx: int | None = None):
    now = datetime(2026, 11, 19, 14, 0, tzinfo=ET)
    kickoff = (now + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    obs = (now - timedelta(hours=1)).astimezone(timezone.utc).isoformat()
    stale_obs = (now - timedelta(hours=72)).astimezone(timezone.utc).isoformat()

    schedule = []
    journal = []
    game_ids = [
        "2026_11_MIA_BUF" if i == 0 else ("2026_11_MIN_SF" if i == 1 else f"2026_11_T{i}A_T{i}B")
        for i in range(n_games)
    ]
    for i, gid in enumerate(game_ids):
        schedule.append({
            "game_id": gid,
            "season": "2026",
            "week": "11",
            "game_type": "REG",
            "gameday": "2026-11-22",
            "gametime": "13:00",
            "roof": "outdoors",
            "surface": "grass",
            "away_team": f"A{i}",
            "home_team": f"H{i}",
            "spread_line": "-3.5",
            "total_line": "44.5",
            "temp": "" if i in missing_indices else "52",
            "wind": "" if i in missing_indices else "8",
        })
        used_obs = stale_obs if i == stale_idx else obs
        journal.append({
            "game_id": gid,
            "season": 2026,
            "week": 11,
            "gameday": "2026-11-22",
            "gametime": "13:00",
            "roof": "outdoors",
            "spread_line": -3.5,
            "total_line": 44.5,
            "spread_observed_at": used_obs,
            "total_observed_at": used_obs,
            "weather_observed_at": None if i in missing_indices else used_obs,
            "temp": None if i in missing_indices else 52.0,
            "wind": None if i in missing_indices else 8.0,
            "capture_batch_utc": used_obs,
        })
    return schedule, journal, now, game_ids


def test_one_missing_weather_game_among_13_excluded_while_12_proceed():
    sched, journal, now, gids = _make_slate(13, missing_indices={0})
    rep = evaluate_pricing_context(
        sched, journal, now, allowed_exclusions={gids[0]}, max_excluded_games=3, block_on_stale=True
    )
    assert rep["ready_for_pricing"] is True
    assert rep["games_ready"] == 12
    assert len(rep["excluded"]) == 1
    assert rep["excluded"][0]["game_id"] == gids[0]
    assert rep["excluded"][0]["status"] == "CONTEXT_MISSING"
    assert rep["per_game_readiness"][gids[0]]["ready_for_pricing"] is False
    assert rep["per_game_readiness"][gids[2]]["ready_for_pricing"] is True


def test_two_missing_weather_games_among_13_and_unallowed_game_blocks():
    sched, journal, now, gids = _make_slate(13, missing_indices={0, 1})
    rep = evaluate_pricing_context(
        sched, journal, now, allowed_exclusions={gids[0], gids[1]}, max_excluded_games=3, block_on_stale=True
    )
    assert rep["ready_for_pricing"] is True
    assert rep["games_ready"] == 11
    assert {e["game_id"] for e in rep["excluded"]} == {gids[0], gids[1]}

    # Unallowed game fails closed
    rep_unallowed = evaluate_pricing_context(
        sched, journal, now, allowed_exclusions={gids[0]}, max_excluded_games=3, block_on_stale=True
    )
    assert rep_unallowed["ready_for_pricing"] is False
    assert rep_unallowed["unallowed_excluded"] == [gids[1]]


def test_more_than_n_excluded_games_blocks_whole_slate():
    sched, journal, now, gids = _make_slate(13, missing_indices={0, 1, 2, 3})
    rep = evaluate_pricing_context(
        sched,
        journal,
        now,
        allowed_exclusions=set(gids[:4]),
        max_excluded_games=3,
        block_on_stale=True,
    )
    assert rep["cap_exceeded"] is True
    assert rep["ready_for_pricing"] is False


def test_stale_capture_and_failed_fetch_block_whole_slate():
    sched, journal, now, gids = _make_slate(13, stale_idx=2)
    stale_rep = evaluate_pricing_context(
        sched, journal, now, allowed_exclusions=set(), max_excluded_games=3, block_on_stale=True
    )
    assert len(stale_rep["stale_captures"]) == 1
    assert stale_rep["ready_for_pricing"] is False

    sched2, journal2, now2, _ = _make_slate(13)
    fail_rep = evaluate_pricing_context(
        sched2, journal2, now2, failed_fetches=["espn timeout"], block_on_stale=True
    )
    assert fail_rep["ready_for_pricing"] is False


def test_excluded_game_never_receives_price_or_action_field_and_states_reason():
    sched, journal, now, gids = _make_slate(13, missing_indices={0, 1})
    rep = evaluate_pricing_context(
        sched, journal, now, allowed_exclusions={gids[0], gids[1]}, max_excluded_games=3, block_on_stale=True
    )
    rows = [
        {
            "game_id": gids[0],
            "player_name": "Tyreek Hill",
            "model_prob": 0.62,
            "fair_line": 78.5,
            "edge": 11.2,
            "edge_pp": 11.2,
            "actionable": True,
            "actionable_stake": 0.02,
            "stake": 0.02,
            "qualified": True,
            "can_execute": True,
        },
        {
            "game_id": gids[2],
            "player_name": "Ready Player",
            "model_prob": 0.58,
            "fair_line": 65.5,
            "edge": 10.5,
            "edge_pp": 10.5,
            "actionable": True,
            "actionable_stake": 0.01,
            "stake": 0.01,
            "qualified": True,
            "can_execute": True,
        },
    ]
    out = apply_context_exclusion_to_slate_rows(rows, rep)
    assert out[0]["context_status"] == "CONTEXT_MISSING"
    assert "CONTEXT_MISSING:" in out[0]["reason"]
    assert "weather_observed_at" in out[0]["reason"]
    assert out[0]["model_prob"] is None
    assert out[0]["fair_line"] is None
    assert out[0]["edge"] is None
    assert out[0]["actionable"] is False
    assert out[0]["actionable_stake"] == 0.0
    assert out[0]["stake"] == 0.0
    assert out[0]["can_execute"] is False

    assert out[1]["context_status"] == "READY"
    assert out[1]["model_prob"] == 0.58

    text = format_provenance_report(rep)
    assert "excluded (CONTEXT_MISSING): 2026_11_MIA_BUF" in text


def test_allowed_exclusion_clears_when_weather_arrives_and_expires_after_kickoff(tmp_path):
    import csv
    from scripts.ingest_espn_game_context import CONTEXT_FIELDS, csv_pricing_context_status

    sched, journal, now, gids = _make_slate(13, missing_indices={0, 1})
    obs = (now - timedelta(hours=1)).astimezone(timezone.utc).isoformat()
    csv_path = tmp_path / "games.csv"
    fieldnames = [
        "game_id", "season", "week", "game_type", "gameday", "gametime", "roof",
        "away_team", "home_team", "spread_line", "total_line", "temp", "wind", *CONTEXT_FIELDS,
    ]

    def _write_csv(rows):
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

    base_rows = []
    for i, gid in enumerate(gids):
        base_rows.append({
            "game_id": gid, "season": "2026", "week": "11", "game_type": "REG",
            "gameday": "2026-11-22", "gametime": "13:00", "roof": "outdoors",
            "away_team": f"A{i}", "home_team": f"H{i}",
            "spread_line": "-3.5", "total_line": "44.5",
            "temp": "" if i in {0, 1} else "52",
            "wind": "" if i in {0, 1} else "8",
            "spread_observed_at": obs, "total_observed_at": obs,
            "weather_observed_at": "" if i in {0, 1} else obs,
        })
    _write_csv(base_rows)
    st1 = csv_pricing_context_status(csv_path, now, allowed_exclusions={gids[0], gids[1]}, max_excluded_games=3)
    assert st1["ready_for_pricing"] is True
    assert {e["game_id"] for e in st1["excluded"]} == {gids[0], gids[1]}

    # 1. Weather arrives for gids[0] -> exclusion clears automatically
    base_rows[0]["temp"] = "50"
    base_rows[0]["wind"] = "7"
    base_rows[0]["weather_observed_at"] = obs
    _write_csv(base_rows)
    st2 = csv_pricing_context_status(csv_path, now, allowed_exclusions={gids[0], gids[1]}, max_excluded_games=3)
    assert [e["game_id"] for e in st2["excluded"]] == [gids[1]]
    assert st2["games_ready"] == 12

    # 2. gids[1] has already started -> exclusion expires automatically
    base_rows[1]["gameday"] = "2026-11-18"
    _write_csv(base_rows)
    st3 = csv_pricing_context_status(csv_path, now, allowed_exclusions={gids[0], gids[1]}, max_excluded_games=3)
    assert st3["excluded"] == []
    assert gids[1] not in st3["per_game_readiness"]


def test_model_input_excludes_context_missing_games_before_predict():
    import sys
    from pathlib import Path
    import pandas as pd
    from unittest.mock import MagicMock, patch
    src_path = str(Path("src").resolve())
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    import week1

    fake_slate = pd.DataFrame([
        {"game_id": "2026_11_MIA_BUF", "player_id": "p1", "player_name": "MIA WR", "team": "MIA", "opponent": "BUF", "position": "WR", "status": "ACT", "prior_rec_mean": 5.0, "prior_ypc_mean": 12.0},
        {"game_id": "2026_11_KC_DEN", "player_id": "p2", "player_name": "KC WR", "team": "KC", "opponent": "DEN", "position": "WR", "status": "ACT", "prior_rec_mean": 6.0, "prior_ypc_mean": 11.0},
    ])
    fake_panel = pd.DataFrame([
        {"season": 2025, "week": 1, "receptions": 5, "receiving_yards": 60, "prior_rec_mean": 5.0, "prior_ypc_mean": 12.0}
    ])
    fake_model = MagicMock()
    fake_model.alpha = 0.2
    seen_player_ids = []

    def _fake_predict(df):
        seen_player_ids.extend(df["player_id"].tolist())
        return pd.DataFrame({"mu_rec": [6.0] * len(df), "mu_ypr": [11.0] * len(df)}, index=df.index)

    fake_model.predict_params.side_effect = _fake_predict
    import numpy as np
    fake_model.p_yards_over_vec.return_value = np.array([0.5])
    fake_model.p_rec_over_vec.return_value = np.array([0.5])
    fake_model.yards_quantile_vec.return_value = np.array([60.0])

    with (
        patch.object(week1, "build_panel", return_value=fake_panel),
        patch.object(week1.ReceivingModel, "fit", return_value=fake_model),
        patch.object(week1, "build_slate", return_value=fake_slate),
        patch.object(week1, "load_injuries", return_value=pd.DataFrame()),
        patch.object(week1, "injury_exclusions", return_value=set()),
        patch.object(week1, "questionable_flags", return_value=set()),
        patch.object(week1, "confirmed_inactive_ids", return_value=set()),
        patch.object(week1, "espn_availability", return_value={}),
        patch.object(week1, "availability_eligible", side_effect=lambda df, *a, **kw: df.assign(excl_reason="")),
    ):
        out_df, _ = week1.project(2026, 11, exclude_games=["2026_11_MIA_BUF"])

    assert seen_player_ids == ["p2"]
    assert "2026_11_MIA_BUF" not in set(out_df["game_id"])


def test_autopilot_sync_week_files_marks_excluded_games_unpriced(tmp_path, monkeypatch):
    import json
    from scripts import autopilot

    src_dir = tmp_path / "src"
    viewer_dir = tmp_path / "viewer"
    root_dir = tmp_path / "root"
    src_dir.mkdir()
    viewer_dir.mkdir()
    root_dir.mkdir()

    (src_dir / "slate_projections.json").write_text(json.dumps([
        {"game_id": "2026_11_KC_DEN", "player_name": "KC WR", "proj_yards": 66.0, "fair_line_yards": 64.5}
    ]), encoding="utf-8")
    (src_dir / "current_picks.json").write_text(json.dumps({
        "picks": [
            {"game_id": "2026_11_MIA_BUF", "player_name": "MIA WR"},
            {"game_id": "2026_11_KC_DEN", "player_name": "KC WR"},
        ]
    }), encoding="utf-8")

    monkeypatch.setattr(autopilot, "SRC", src_dir)
    monkeypatch.setattr(autopilot, "VIEWER", viewer_dir)
    monkeypatch.setattr(autopilot, "ROOT", root_dir)

    ctx = {
        "ready_for_pricing": True,
        "excluded": [{"game_id": "2026_11_MIA_BUF", "status": "CONTEXT_MISSING", "missing_fields": ["weather_observed_at", "weather_values"]}],
    }
    assert autopilot.sync_week_files(2026, 11, context_report=ctx) is True
    slate = json.loads((viewer_dir / "slate_2026_w11.json").read_text(encoding="utf-8"))
    excl_rows = [r for r in slate if r.get("game_id") == "2026_11_MIA_BUF"]
    assert len(excl_rows) == 1
    assert excl_rows[0]["context_status"] == "CONTEXT_MISSING"
    assert excl_rows[0]["proj_yards"] is None
    assert excl_rows[0]["actionable"] is False

    picks = json.loads((viewer_dir / "current_picks_2026_w11.json").read_text(encoding="utf-8"))
    assert [p["game_id"] for p in picks["picks"]] == ["2026_11_KC_DEN"]


def test_15_ready_1_missing_yields_partial_slate_with_counts_and_reason():
    """15 READY + 1 missing -> slate_status=PARTIAL, 15 priced, 1 excluded with explicit reason."""
    sched, journal, now, gids = _make_slate(16, missing_indices={0})
    rep = evaluate_pricing_context(
        sched, journal, now, allowed_exclusions={gids[0]}, max_excluded_games=3, block_on_stale=True
    )
    assert rep["ready_for_pricing"] is True
    assert rep["slate_status"] == "PARTIAL"
    assert rep["games_priced"] == 15
    assert rep["games_excluded"] == 1
    assert len(rep["excluded"]) == 1
    assert rep["excluded"][0]["game_id"] == gids[0]
    assert "weather_observed_at" in rep["excluded"][0]["reason"]
    assert "weather_values" in rep["excluded"][0]["reason"]


def test_all_games_missing_yields_zero_priced_blocked_slate_with_all_listed():
    """All 16 games missing -> BLOCKED, 0 priced, all 16 listed with reasons."""
    sched, journal, now, gids = _make_slate(16, missing_indices=set(range(16)))
    rep = evaluate_pricing_context(
        sched, journal, now, allowed_exclusions=set(gids), max_excluded_games=20, block_on_stale=True
    )
    assert rep["ready_for_pricing"] is False
    assert rep["slate_status"] == "BLOCKED"
    assert rep["games_priced"] == 0
    assert rep["games_excluded"] == 16
    assert len(rep["excluded"]) == 16
    assert all("CONTEXT_MISSING:" in e["reason"] for e in rep["excluded"])


def test_unknown_or_empty_exclusion_reason_fails_whole_slate_closed():
    """An unknown reason or an empty/missing reason must fail the entire slate closed."""
    ready_row = {
        "game_id": "2026_11_T2A_T2B",
        "player_name": "Ready WR",
        "model_prob": 0.59,
        "fair_line": 62.5,
        "edge": 9.5,
        "actionable": True,
        "can_execute": True,
    }
    # 1. Unknown exclusion reason string
    unknown_report = {
        "ready_for_pricing": True,
        "excluded": [
            {"game_id": "2026_11_MIA_BUF", "status": "CONTEXT_MISSING", "missing_fields": ["unrecognized_future_reason"]}
        ],
    }
    out_unknown = apply_context_exclusion_to_slate_rows([ready_row], unknown_report)
    assert out_unknown[0]["context_status"] == "CONTEXT_MISSING"
    assert out_unknown[0]["model_prob"] is None
    assert out_unknown[0]["fair_line"] is None
    assert out_unknown[0]["actionable"] is False

    # 2. Empty missing_fields list
    empty_report = {
        "ready_for_pricing": True,
        "excluded": [{"game_id": "2026_11_MIA_BUF", "status": "CONTEXT_MISSING", "missing_fields": []}],
    }
    out_empty = apply_context_exclusion_to_slate_rows([ready_row], empty_report)
    assert out_empty[0]["context_status"] == "CONTEXT_MISSING"
    assert out_empty[0]["model_prob"] is None
    assert out_empty[0]["actionable"] is False

    # 3. Missing missing_fields key altogether
    missing_key_report = {
        "ready_for_pricing": True,
        "excluded": [{"game_id": "2026_11_MIA_BUF", "status": "CONTEXT_MISSING"}],
    }
    out_missing_key = apply_context_exclusion_to_slate_rows([ready_row], missing_key_report)
    assert out_missing_key[0]["context_status"] == "CONTEXT_MISSING"
    assert out_missing_key[0]["model_prob"] is None
    assert out_missing_key[0]["actionable"] is False


def test_excluded_game_strips_player_props_and_multi_game_combos():
    """Player props and multi-game combos referencing an excluded game show no price anywhere."""
    sched, journal, now, gids = _make_slate(16, missing_indices={0})
    rep = evaluate_pricing_context(
        sched, journal, now, allowed_exclusions={gids[0]}, max_excluded_games=3, block_on_stale=True
    )
    rows = [
        # Single-game player prop on excluded game
        {"game_id": gids[0], "player_name": "Excluded WR", "model_prob": 0.61, "fair_line": 71.5, "proj_yards": 73.0, "actionable": True},
        # Multi-game combo via legs[] (1 excluded leg + 1 ready leg)
        {
            "combo_id": "combo-1",
            "legs": [{"game_id": gids[0], "player_name": "Excluded WR"}, {"game_id": gids[2], "player_name": "Ready WR"}],
            "model_prob": 0.35,
            "fair_line": 2.5,
            "actionable": True,
        },
        # Multi-game combo via game_ids[]
        {
            "combo_id": "combo-2",
            "game_ids": [gids[2], gids[0]],
            "model_prob": 0.31,
            "fair_line": 3.5,
            "actionable": True,
        },
        # Clean single-game prop on ready game
        {"game_id": gids[2], "player_name": "Ready WR", "model_prob": 0.58, "fair_line": 65.5, "proj_yards": 67.0, "actionable": True},
    ]
    out = apply_context_exclusion_to_slate_rows(rows, rep)
    for idx in (0, 1, 2):
        assert out[idx]["context_status"] == "CONTEXT_MISSING"
        assert out[idx]["model_prob"] is None
        assert out[idx]["fair_line"] is None
        assert out[idx]["proj_yards"] is None
        assert out[idx]["actionable"] is False
    assert out[3]["context_status"] == "READY"
    assert out[3]["model_prob"] == 0.58
    assert out[3]["proj_yards"] == 67.0


def test_retractable_roof_unknown_is_excluded_and_never_priced_as_outdoor():
    """A retractable stadium with unknown ESPN indoor status is excluded with retractable_roof_unknown."""
    sched, journal, now, gids = _make_slate(16, missing_indices=set())
    journal[0]["roof"] = "open"
    journal[0]["weather_source"] = "nws_hourly_forecast_retractable_unknown"
    rep = evaluate_pricing_context(
        sched, journal, now, allowed_exclusions={gids[0]}, max_excluded_games=3, block_on_stale=True
    )
    assert rep["ready_for_pricing"] is True
    assert rep["slate_status"] == "PARTIAL"
    assert rep["games_priced"] == 15
    assert rep["games_excluded"] == 1
    assert rep["excluded"][0]["game_id"] == gids[0]
    assert "retractable_roof_unknown" in rep["excluded"][0]["missing_fields"]
    assert "retractable_roof_unknown" in rep["excluded"][0]["reason"]
