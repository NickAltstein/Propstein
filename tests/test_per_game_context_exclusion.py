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
