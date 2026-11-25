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
