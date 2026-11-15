"""Pregame bridge must not attach a FanDuel quote to the wrong player/game."""

import importlib.util
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_nba_pregame_research.py"
spec = importlib.util.spec_from_file_location("nba_pregame_research", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def test_quote_identity_requires_current_game_team():
    history = pd.DataFrame([
        {"player_id": "nba-1", "player_display_name": "A. Player", "team": "BOS",
         "game_date": "2026-11-03", "game_id": "old-1", "dnp": False,
         "ppm": 0.5, "position": "G"},
        {"player_id": "nba-2", "player_display_name": "A. Player", "team": "NYK",
         "game_date": "2026-11-03", "game_id": "old-2", "dnp": False,
         "ppm": 0.6, "position": "G"},
    ])
    games = [{"game_id": "today-1", "gameday": "2026-11-04",
              "home_team": "NYK", "away_team": "MIA"}]
    quotes = [{"player_name": "A. Player", "player_id": "odds-name",
               "game_id": "today-1", "market": "points", "line": 12.5}]
    rows, mapped, excluded = bridge.pregame_rows(history, games, quotes, "2026-11-04")
    assert len(rows) == 1
    assert rows.iloc[0]["player_id"] == "nba-2"
    assert mapped[0]["player_id"] == "nba-2"
    assert excluded["ambiguous_identity"] == 0


def test_quote_identity_refuses_unknown_or_ambiguous_players():
    history = pd.DataFrame([
        {"player_id": f"nba-{n}", "player_display_name": "Same Name", "team": "NYK",
         "game_date": "2026-11-03", "game_id": f"old-{n}", "dnp": False,
         "ppm": 0.5, "position": "G"}
        for n in (1, 2)
    ])
    games = [{"game_id": "today-1", "gameday": "2026-11-04",
              "home_team": "NYK", "away_team": "MIA"}]
    quotes = [
        {"player_name": "Same Name", "game_id": "today-1", "market": "points", "line": 12.5},
        {"player_name": "Unknown", "game_id": "today-1", "market": "points", "line": 12.5},
    ]
    rows, mapped, excluded = bridge.pregame_rows(history, games, quotes, "2026-11-04")
    assert rows.empty and not mapped
    assert excluded["ambiguous_identity"] == 1
    assert excluded["unmatched_identity"] == 1


# ── ESPN availability (injury feed) ─────────────────────────────────────────
def _history():
    rows = []
    for d, gid in (("2026-11-10", "g1"), ("2026-11-12", "g2"), ("2026-11-13", "g3")):
        for pid, name in (("p1", "Star Guard"), ("p2", "Role Wing")):
            rows.append({"player_id": pid, "player_display_name": name, "team": "AAA",
                         "game_id": gid, "game_date": d, "dnp": False})
    # long-term absentee: last played two weeks ago
    rows.append({"player_id": "p3", "player_display_name": "Hurt Big", "team": "AAA",
                 "game_id": "g0", "game_date": "2026-10-30", "dnp": False})
    return pd.DataFrame(rows)


GAMES = [{"game_id": "nba-20261115-AAA-BBB", "gameday": "2026-11-15", "home_team": "BBB", "away_team": "AAA"}]


def test_availability_rows_split_recent_and_long_term_outs():
    report = {"injuries": [
        {"player_name": "Star Guard", "team": "AAA", "report_status": "Out"},
        {"player_name": "Hurt Big", "team": "AAA", "report_status": "Out"},
        {"player_name": "Role Wing", "team": "AAA", "report_status": "Day-To-Day"},
        {"player_name": "Nobody", "team": "AAA", "report_status": "Out"},       # unmatched
        {"player_name": "Star Guard", "team": "ZZZ", "report_status": "Out"},   # team not on slate
    ]}
    rows, stats = bridge.availability_rows(report, _history(), GAMES, "2026-11-15")
    by = {r["player_id"]: r["report_status"] for r in rows}
    assert by == {"p1": "out", "p3": "out_long_term", "p2": "questionable"}
    assert stats == {"entries": 4, "matched": 3, "out_recent": 1, "out_long_term": 1, "uncertain": 1}
    assert all(r["game_id"] == "nba-20261115-AAA-BBB" for r in rows)


def test_availability_rows_absent_report_is_neutral():
    assert bridge.availability_rows(None, _history(), GAMES, "2026-11-15") == (None, {
        "entries": 0, "matched": 0, "out_recent": 0, "out_long_term": 0, "uncertain": 0})
