import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nba = load("grade_nba_research")
nfl = load("grade_nfl_week")


# ── NBA ───────────────────────────────────────────────────────────────────────
def rec(player, line, over, under, p_over, team="AAA"):
    return {"player": player, "team": team, "line": line, "over_odds": over, "under_odds": under,
            "model_p_over_uncalibrated": p_over}


def test_nba_grade_picks_the_bigger_gap_side_and_settles_at_quoted_price():
    r = nba.grade_record(rec("A", 14.5, 150, -150, 0.27), actual=18.0)   # market ~40% over, model 27%
    assert (r["side"], r["result"], r["units"]) == ("UNDER", "LOSS", -1.0)
    r = nba.grade_record(rec("B", 19.5, 133, -138, 0.74), actual=24.0)
    assert (r["side"], r["result"], r["units"]) == ("OVER", "WIN", 1.33)
    assert r["brier_model"] == pytest.approx((0.74 - 1) ** 2, abs=1e-4)
    assert nba.grade_record(rec("C", 9.5, -110, -110, 0.5), actual=None)["result"] == "VOID"


def test_nba_summary_top10_and_brier():
    rows = [nba.grade_record(rec(f"P{i}", 10.5, -110, -110, 0.5 + i / 40), actual=12.0) for i in range(12)]
    rows.append(nba.grade_record(rec("Gone", 10.5, -110, -110, 0.99), actual=None))
    s = nba.summarize(rows)
    assert s["graded"] == 12 and s["void"] == 1
    assert s["top10_by_gap"]["void"] == 1 and s["top10_by_gap"]["n"] == 9
    assert s["brier_market"] == pytest.approx(0.25)


def test_nba_history_upsert_is_idempotent(tmp_path):
    p = tmp_path / "h.jsonl"
    nba.upsert_history(p, {"date": "2026-11-14", "graded": 1})
    rows = nba.upsert_history(p, {"date": "2026-11-14", "graded": 2})
    assert rows == [{"date": "2026-11-14", "graded": 2}]


# ── NFL ───────────────────────────────────────────────────────────────────────
def test_nfl_grade_uses_last_pregame_quote_and_voids_missing_players(tmp_path):
    v = tmp_path / "viewer"
    (v / "api" / "lines" / "p1").mkdir(parents=True)
    (v / "slate_2026_w9.json").write_text(json.dumps([
        {"player_id": "p1", "player_display_name": "Wide Out", "team": "AAA", "game_id": "g",
         "mu_rec": 5.0, "mu_ypr": 12.0, "fair_line_rec": 4.5, "fair_line_yards": 55.5, "excl_reason": ""},
        {"player_id": "p2", "player_display_name": "Did Not Play", "team": "AAA", "game_id": "g",
         "mu_rec": 3.0, "mu_ypr": 10.0, "fair_line_rec": 2.5, "fair_line_yards": 25.5, "excl_reason": ""},
        {"player_id": "p3", "player_display_name": "Excluded", "team": "AAA", "game_id": "g",
         "mu_rec": 3.0, "mu_ypr": 10.0, "excl_reason": "espn: out"}]))
    (v / "games_2026_w9.json").write_text(json.dumps([{"game_id": "g", "gameday": "2026-11-09", "gametime": "13:00"}]))
    (v / "api" / "lines" / "p1" / "receiving-yards.json").write_text(json.dumps({"series": {"polymarket": [
        {"t": "2026-11-08T12:00:00Z", "line": 50.5, "over": -110, "under": -110},
        {"t": "2026-11-09T17:00:00Z", "line": 54.5, "over": -120, "under": 100},   # last before 18:00Z kickoff
        {"t": "2026-11-09T20:00:00Z", "line": 80.5, "over": 300, "under": -400},   # in-game: ignored
    ]}}))
    stats = pd.DataFrame([{"player_id": "p1", "season": 2026, "week": 9, "receptions": 6, "receiving_yards": 71}])
    report = nfl.grade(2026, 9, stats, root=tmp_path)
    yards = [r for r in report["rows"] if r["market"] == "receiving_yards"]
    by = {r["player"]: r for r in yards}
    assert by["Did Not Play"]["result"] == "VOID" and "Excluded" not in by
    w = by["Wide Out"]
    assert w["quote"]["line"] == 54.5 and w["actual"] == 71 and w["over_fair"] is True
    assert w["result"] in ("WIN", "LOSS") and 0 < w["p_model_over"] < 1
    rec_rows = [r for r in report["rows"] if r["market"] == "receptions" and r["player"] == "Wide Out"]
    assert rec_rows[0]["result"] == "NO_QUOTE"
    assert report["markets"]["receiving_yards"]["quoted"] == 1
