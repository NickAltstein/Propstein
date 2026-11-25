"""NBA nights built from synthetic fixtures must be labelled as fixtures.

Oct 22, 23 and 29 2026 hold only rows from scripts/build_nba_fixtures.py
(_fixture: true, "SYNTHETIC projections"), yet the slate index listed them as
"Historical audit" nights with model rows. autopilot.scan_nba_slates now flags
an all-fixture night `fixture: true` and labels it as research fixtures.
"""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts import autopilot

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")


def _night(viewer: Path, date: str, rows) -> None:
    (viewer / f"nba_games_{date}.json").write_text(json.dumps([{"game_id": f"g-{date}"}]))
    (viewer / f"nba_slate_{date}.json").write_text(json.dumps(rows))


def test_scan_flags_all_fixture_nights_and_only_those(tmp_path, monkeypatch):
    monkeypatch.setattr(autopilot, "VIEWER", tmp_path)
    _night(tmp_path, "2026-10-22", [{"player": "fx-1", "_fixture": True}, {"player": "fx-2", "_fixture": True}])
    _night(tmp_path, "2026-11-20", [{"player": "real", "_fixture": False}])
    _night(tmp_path, "2026-11-21", [{"player": "fx", "_fixture": True}, {"player": "real"}])
    _night(tmp_path, "2026-11-22", [])
    slates = autopilot.scan_nba_slates(datetime(2026, 11, 25, 12, tzinfo=ET))

    fx = slates["2026-10-22"]
    assert fx["fixture"] is True
    assert fx["shortLabel"] == "Oct 22 Fixtures"
    assert fx["state"] == "Research fixtures (synthetic) · 2026-10-22"
    assert "audit" not in (fx["shortLabel"] + fx["state"]).lower()

    assert slates["2026-11-20"]["fixture"] is False
    assert slates["2026-11-20"]["shortLabel"] == "Nov 20 Audit"
    assert slates["2026-11-21"]["fixture"] is False, "a night with any real row is not a fixture night"
    assert slates["2026-11-22"]["fixture"] is False and slates["2026-11-22"]["marketOnly"] is True


def test_committed_index_labels_match_the_slate_files():
    idx = json.loads((ROOT / "viewer/feeds/slate-index.json").read_text())
    nba = idx["sports"]["nba"]["slates"]
    for date, entry in nba.items():
        rows = json.loads((ROOT / "viewer" / f"nba_slate_{date}.json").read_text())
        all_fixture = bool(rows) and all(isinstance(r, dict) and r.get("_fixture") is True for r in rows)
        assert entry.get("fixture") is all_fixture, date
        if all_fixture:
            assert "Fixtures" in entry["shortLabel"] and "fixtures" in entry["state"], date
            assert "audit" not in entry["state"].lower(), date
    for date in ("2026-10-22", "2026-10-23", "2026-10-29"):
        assert nba[date]["fixture"] is True, date


FIXTURE_NIGHTS = ("2026-10-22", "2026-10-23", "2026-10-29")


def test_live_recalc_agent_loads_no_synthetic_nba_baselines():
    """The money-path agent used to load all 169 fx- rows from the three
    fixture nights as NBA baselines (base_mu, base_sd, line)."""
    from src.agents import recalc_agent as ra

    agent = ra.ProjectionRecalcAgent.__new__(ra.ProjectionRecalcAgent)
    agent.baseline_catalog = {}
    agent._load_baseline_catalog()
    nba = {pid: row for pid, row in agent.baseline_catalog.items() if row["sport"] == "nba"}
    assert not [pid for pid in nba if pid.startswith("fx-")]
    fixture_ids = {
        str(r.get("player_id"))
        for d in FIXTURE_NIGHTS
        for r in json.loads((ROOT / "viewer" / f"nba_slate_{d}.json").read_text())
    }
    assert fixture_ids and not (fixture_ids & set(agent.baseline_catalog))


def test_s14_evidence_counts_leave_out_fixture_nights():
    from scripts.diagnostics import s14_evidence as s14

    rows = s14.inspect_field_inventory()
    kalshi = next(r for r in rows if str(r.get("source", r.get("name", ""))).startswith("Kalshi NBA")
                  or "Kalshi NBA" in json.dumps(r))
    real_files = [p for p in sorted((ROOT / "viewer").glob("nba_odds_*.json"))
                  if not p.name.endswith(".manifest.json")
                  and json.loads(p.read_text()).get("_fixture") is not True]
    assert f"({len(real_files)} files)" in json.dumps(kalshi)

    coverage = s14.compute_kalshi_nba_offset_coverage()
    blob = json.dumps(coverage)
    for d in FIXTURE_NIGHTS:
        for g in json.loads((ROOT / "viewer" / f"nba_games_{d}.json").read_text()):
            gid = str(g.get("game_id") or "")
            assert not gid or gid not in blob, f"fixture game {gid} counted in offset coverage"
