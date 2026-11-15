import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import espn_injuries as ei  # noqa: E402
import slate as slate_mod  # noqa: E402


def payload(*entries):
    return {"injuries": [{"injuries": [
        {"status": st, "date": "2026-11-13T00:39Z", "shortComment": "c",
         "athlete": {"displayName": name, "team": {"abbreviation": team}}}
        for name, team, st in entries]}]}


def test_normalize_maps_teams_and_classifies_status():
    r = ei.normalize(payload(("Puka Nacua", "LAR", "Injured Reserve"), ("Terry McLaurin", "WSH", "Questionable"),
                             ("A B", "KC", "Active")), "nfl")
    by = {p["player_name"]: p for p in r["players"]}
    assert by["Puka Nacua"]["team"] == "LA" and by["Puka Nacua"]["unavailable"]
    assert by["Terry McLaurin"]["team"] == "WAS" and by["Terry McLaurin"]["uncertain"]
    assert not by["A B"]["unavailable"] and not by["A B"]["uncertain"]
    nba = ei.normalize(payload(("X Y", "GS", "Out"), ("Z W", "SA", "Day-To-Day")), "nba")
    assert [p["team"] for p in nba["players"]] == ["GSW", "SAS"]
    assert nba["players"][1]["uncertain"]


def test_match_is_name_and_team_scoped_and_skips_ambiguity():
    report = ei.normalize(payload(("Kenneth Walker III", "KC", "Out"), ("Mike Williams", "LAC", "Out"),
                                  ("Mike Williams", "LAC", "Questionable")), "nfl")
    roster = [{"player_display_name": "Kenneth Walker", "team": "KC"},       # suffix-insensitive
              {"player_display_name": "Kenneth Walker", "team": "SEA"},      # wrong team
              {"player_display_name": "Mike Williams", "team": "LAC"}]       # ambiguous -> skipped
    m = ei.match(report, roster, "player_display_name")
    assert list(m) == [0]


def test_freshness():
    now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
    assert ei.is_fresh({"fetched_at": "2026-11-15T00:00:00Z"}, 36, now)
    assert not ei.is_fresh({"fetched_at": "2026-11-13T00:00:00Z"}, 36, now)
    assert not ei.is_fresh({}, 36, now)


def test_slate_excludes_ir_and_out_but_keeps_questionable(tmp_path, monkeypatch):
    report = ei.normalize(payload(("Puka Nacua", "LAR", "Injured Reserve"), ("Q Guy", "KC", "Questionable")), "nfl")
    (tmp_path / "espn_injuries_nfl.json").write_text(json.dumps(report))
    monkeypatch.setattr(slate_mod, "DATA_DIR", str(tmp_path))
    s = pd.DataFrame([{"player_id": "1", "player_display_name": "Puka Nacua", "team": "LA"},
                      {"player_id": "2", "player_display_name": "Q Guy", "team": "KC"},
                      {"player_id": "3", "player_display_name": "Healthy", "team": "KC"}])
    espn = slate_mod.espn_availability(s)
    r = slate_mod.availability_eligible(s, set(), {"9"}, espn).set_index("player_id").excl_reason
    assert r["1"] == "espn: injured reserve" and r["2"] == "" and r["3"] == ""
    assert espn["2"]["uncertain"]


def test_stale_report_adds_nothing(tmp_path, monkeypatch):
    report = ei.normalize(payload(("Puka Nacua", "LAR", "Injured Reserve")), "nfl")
    report["fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "espn_injuries_nfl.json").write_text(json.dumps(report))
    monkeypatch.setattr(slate_mod, "DATA_DIR", str(tmp_path))
    s = pd.DataFrame([{"player_id": "1", "player_display_name": "Puka Nacua", "team": "LA"}])
    assert slate_mod.espn_availability(s) == {}
