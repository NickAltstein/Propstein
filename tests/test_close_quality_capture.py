"""Fixture-only unit and schedule-coverage tests for additive close-quality capture."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from src.close_quality_capture import (
    append_captures,
    load_captures_jsonl,
    normalize_kalshi_capture,
    normalize_polymarket_capture,
)


def _pm_fixture(cap_dt: datetime, prev_vol: float | None = 1200.0) -> dict:
    return normalize_polymarket_capture(
        market_id="tok-1",
        game_id="2026_11_IND_HOU",
        scheduled_start=datetime(2026, 11, 20, 1, 0, 0, tzinfo=timezone.utc),
        book_payload={
            "timestamp": str(int(cap_dt.timestamp() * 1000)),
            "bids": [{"price": "0.45", "size": "120.0"}, {"price": "0.47", "size": "250.0"}],
            "asks": [{"price": "0.52", "size": "300.0"}, {"price": "0.50", "size": "180.0"}],
        },
        gamma_payload=[{"volumeNum": 1500.0}],
        captured_at=cap_dt,
        prev_volume_total=prev_vol,
    )


def _kalshi_fixture(cap_dt: datetime, prev_vol: float | None = None) -> dict:
    return normalize_kalshi_capture(
        market_id="KXNBAPTS-TEST",
        game_id="nba-test",
        scheduled_start=datetime(2026, 11, 20, 1, 0, 0, tzinfo=timezone.utc),
        market_payload={
            "market": {
                "yes_bid_dollars": "0.4800",
                "yes_ask_dollars": "0.4900",
                "volume_fp": "972.27",
                "open_interest_fp": "962.27",
                "updated_time": cap_dt.isoformat(),
            }
        },
        orderbook_payload={
            "orderbook_fp": {
                "yes_dollars": [["0.4700", "193.00"], ["0.4800", "690.00"]],
                "no_dollars": [["0.5000", "1100.00"], ["0.5100", "3891.87"]],
            }
        },
        captured_at=cap_dt,
        prev_volume_total=prev_vol,
    )


def test_polymarket_normalization_all_fields():
    rec = _pm_fixture(datetime(2026, 11, 20, 0, 30, 0, tzinfo=timezone.utc))
    assert rec["source"] == "polymarket"
    assert rec["bid"] == 0.47
    assert rec["ask"] == 0.50
    assert rec["volume_total"] == 1500.0
    assert rec["volume_window"] == 300.0
    assert len(rec["depth_levels"]["bids"]) == 2
    assert len(rec["depth_levels"]["asks"]) == 2


def test_kalshi_normalization_all_fields():
    rec = _kalshi_fixture(datetime(2026, 11, 20, 0, 45, 0, tzinfo=timezone.utc), prev_vol=900.0)
    assert rec["source"] == "kalshi"
    assert rec["bid"] == 0.48
    assert rec["ask"] == 0.49
    assert rec["depth_levels"]["asks"][0] == {"price": 0.49, "size": 3891.87}
    assert rec["volume_total"] == 972.27
    assert rec["volume_window"] == 72.27
    assert rec["open_interest"] == 962.27


def test_offset_computed_correctly_pre_start():
    rec = _pm_fixture(datetime(2026, 11, 20, 0, 30, 0, tzinfo=timezone.utc))
    assert rec["offset_minutes"] == 30.0
    assert rec["pre_start"] is True


def test_post_start_capture_flagged_pre_start_false():
    rec = _kalshi_fixture(datetime(2026, 11, 20, 1, 5, 0, tzinfo=timezone.utc))
    assert rec["offset_minutes"] == -5.0
    assert rec["pre_start"] is False


def test_missing_fields_stay_null_never_estimated():
    pm_rec = _pm_fixture(datetime(2026, 11, 20, 0, 30, 0, tzinfo=timezone.utc))
    assert pm_rec["open_interest"] is None

    k_empty = normalize_kalshi_capture(
        market_id="K1",
        game_id="G1",
        scheduled_start="2026-11-20T01:00:00Z",
        market_payload={"market": {"updated_time": "2026-11-20T00:30:00Z"}},
        orderbook_payload=None,
        captured_at=datetime(2026, 11, 20, 0, 30, tzinfo=timezone.utc),
    )
    assert k_empty["bid"] is None
    assert k_empty["ask"] is None
    assert k_empty["depth_levels"] is None
    assert k_empty["volume_window"] is None


def test_replay_is_idempotent_and_hash_chained(tmp_path):
    jsonl_path = tmp_path / "close_quality_captures.jsonl"
    status_path = tmp_path / "close_quality_capture_status.json"
    rec = _pm_fixture(datetime(2026, 11, 20, 0, 30, 0, tzinfo=timezone.utc))

    st1 = append_captures(jsonl_path=jsonl_path, status_path=status_path, captures=[rec])
    assert st1["appended_count"] == 1
    assert st1["total_records"] == 1

    st2 = append_captures(jsonl_path=jsonl_path, status_path=status_path, captures=[rec])
    assert st2["appended_count"] == 0
    assert st2["total_records"] == 1

    recs, _, chain_ok = load_captures_jsonl(jsonl_path)
    assert chain_ok is True
    assert recs[0]["prev_record_hash"] == "GENESIS"


def test_fetch_failure_keeps_previous_data_and_emits_stale_signal(tmp_path):
    jsonl_path = tmp_path / "close_quality_captures.jsonl"
    status_path = tmp_path / "close_quality_capture_status.json"
    rec = _kalshi_fixture(datetime(2026, 11, 20, 0, 45, 0, tzinfo=timezone.utc))
    append_captures(jsonl_path=jsonl_path, status_path=status_path, captures=[rec])

    st_fail = append_captures(
        jsonl_path=jsonl_path,
        status_path=status_path,
        captures=[],
        fetch_errors=["KALSHI_FETCH_FAILED:K1:HTTP 503"],
    )
    assert st_fail["stale_context"] is True
    assert st_fail["total_records"] == 1


def test_existing_ledger_and_viewer_files_stay_byte_identical(tmp_path):
    audit_path = Path("viewer/clv_audit_report.json")
    before_sha = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    rec = _pm_fixture(datetime(2026, 11, 20, 0, 30, 0, tzinfo=timezone.utc))
    append_captures(
        jsonl_path=tmp_path / "close_quality_captures.jsonl",
        status_path=tmp_path / "close_quality_capture_status.json",
        captures=[rec],
    )
    assert hashlib.sha256(audit_path.read_bytes()).hexdigest() == before_sha


def test_stored_schedule_coverage_across_t60_t30_t15_t5_windows():
    """Every stored NFL/NBA game start has >=1 cron tick in T-60, T-30, T-15, and T-5."""
    starts: list[tuple[str, datetime]] = []
    for gf in sorted(Path("viewer").glob("nba_games_*.json")):
        for g in json.loads(gf.read_text(encoding="utf-8")):
            if g.get("tipoff_utc"):
                starts.append((g["game_id"], datetime.fromisoformat(g["tipoff_utc"].replace("Z", "+00:00"))))
    for gf in sorted(Path("viewer").glob("games_*.json")):
        for g in json.loads(gf.read_text(encoding="utf-8")):
            if g.get("gameday") and g.get("gametime"):
                dt = datetime.strptime(f"{g['gameday']} {g['gametime']}", "%Y-%m-%d %H:%M").replace(
                    tzinfo=ZoneInfo("America/New_York")
                ).astimezone(timezone.utc)
                starts.append((g["game_id"], dt))

    windows = {"T-60": (45, 70), "T-30": (25, 45), "T-15": (10, 25), "T-5": (1, 10)}
    uncovered = []
    for gid, dt in starts:
        ticks = []
        for d_off in (-1, 0, 1):
            base = dt + timedelta(days=d_off)
            for hr in list(range(12, 24)) + list(range(0, 6)):
                for mn in (3, 13, 23, 33, 43, 53):
                    ticks.append(base.replace(hour=hr, minute=mn, second=0, microsecond=0))
        for wname, (lo, hi) in windows.items():
            if not any(lo <= (dt - t).total_seconds() / 60.0 <= hi for t in ticks):
                uncovered.append((gid, dt.isoformat(), wname))
    assert uncovered == []


def test_workflow_commit_guard_exits_zero_when_capture_file_missing(tmp_path):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    script = (
        "if [ ! -f data/close_quality_captures.jsonl ]; then exit 0; fi; "
        "git add -N data/close_quality_captures.jsonl 2>/dev/null || true; "
        "git diff --quiet data/close_quality_captures.jsonl"
    )
    res = subprocess.run(["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert res.returncode == 0
