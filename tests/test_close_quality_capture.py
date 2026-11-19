"""Fixture-only unit and schedule-coverage tests for additive close-quality capture."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import src.close_quality_capture as capture_module
from src.close_quality_capture import (
    _last_volume_by_market,
    analysis_eligible_captures,
    append_captures,
    load_captures_jsonl,
    normalize_kalshi_capture,
    normalize_polymarket_capture,
    run_live_capture,
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
    assert rec["volume_window_method"] == "successive_volume_total_delta"
    assert rec["schema_version"] == 2
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
    assert rec["volume_window_method"] == "successive_volume_total_delta"
    assert rec["schema_version"] == 2
    assert rec["open_interest"] == 962.27


def test_offset_computed_correctly_pre_start():
    rec = _pm_fixture(datetime(2026, 11, 20, 0, 30, 0, tzinfo=timezone.utc))
    assert rec["offset_minutes"] == 30.0
    assert rec["pre_start"] is True


def test_post_start_capture_flagged_pre_start_false():
    rec = _kalshi_fixture(datetime(2026, 11, 20, 1, 5, 0, tzinfo=timezone.utc))
    assert rec["offset_minutes"] == -5.0
    assert rec["pre_start"] is False


def test_kalshi_old_quote_does_not_make_post_start_snapshot_pregame():
    captured = datetime(2026, 11, 20, 1, 3, tzinfo=timezone.utc)
    rec = normalize_kalshi_capture(
        market_id="K1", game_id="G1", scheduled_start="2026-11-20T01:00:00Z",
        market_payload={"market": {"updated_time": "2026-11-20T00:10:00Z", "volume_fp": "100"}},
        captured_at=captured,
    )
    assert rec["offset_minutes"] == -3.0
    assert rec["pre_start"] is False
    assert rec["quoted_at"] == "2026-11-20T00:10:00+00:00"
    assert rec["quote_age_minutes"] == 53.0
    assert rec["volume_window"] is None
    assert rec["volume_window_method"] is None


def test_polymarket_old_quote_does_not_make_post_start_snapshot_pregame():
    captured = datetime(2026, 11, 20, 1, 3, tzinfo=timezone.utc)
    rec = normalize_polymarket_capture(
        market_id="tok-1", game_id="G1", scheduled_start="2026-11-20T01:00:00Z",
        book_payload={"timestamp": "2026-11-20T00:10:00Z", "bids": [], "asks": []},
        captured_at=captured,
    )
    assert rec["offset_minutes"] == -3.0
    assert rec["pre_start"] is False
    assert rec["quote_age_minutes"] == 53.0


def test_old_quote_before_start_is_pregame_with_measured_age():
    captured = datetime(2026, 11, 20, 0, 57, tzinfo=timezone.utc)
    rec = normalize_kalshi_capture(
        market_id="K1", game_id="G1", scheduled_start="2026-11-20T01:00:00Z",
        market_payload={"market": {"updated_time": "2026-11-20T00:10:00Z"}},
        captured_at=captured,
    )
    assert rec["offset_minutes"] == 3.0
    assert rec["pre_start"] is True
    assert rec["quote_age_minutes"] == 47.0


def test_capture_exactly_at_start_is_not_pregame():
    rec = _kalshi_fixture(datetime(2026, 11, 20, 1, 0, tzinfo=timezone.utc))
    assert rec["offset_minutes"] == 0.0
    assert rec["pre_start"] is False


def test_missing_start_and_quote_time_remain_unknown():
    rec = normalize_polymarket_capture(
        market_id="tok-1", game_id="G1", scheduled_start=None,
        book_payload={"bids": [], "asks": []},
        captured_at=datetime(2026, 11, 20, 0, 57, tzinfo=timezone.utc),
    )
    assert rec["offset_minutes"] is None
    assert rec["pre_start"] is None
    assert rec["quote_age_minutes"] is None


def test_v1_excluded_from_analysis_and_volume_baseline():
    before = datetime(2026, 11, 20, 0, 57, tzinfo=timezone.utc)
    v1 = {"source": "kalshi", "market_id": "K1", "captured_at": "2026-11-20T00:40:00Z",
          "volume_total": 900, "pre_start": True}
    v2 = {**v1, "schema_version": 2, "captured_at": "2026-11-20T00:50:00Z",
          "volume_total": 920}
    assert analysis_eligible_captures([v1, v2]) == [v2]
    assert _last_volume_by_market([v1], before) == {}
    assert _last_volume_by_market([v2, v1], before) == {("kalshi", "K1"): 920}


def test_volume_reset_is_unknown_not_zero():
    rec = _kalshi_fixture(datetime(2026, 11, 20, 0, 45, tzinfo=timezone.utc), prev_vol=1000)
    assert rec["volume_window"] is None
    assert rec["volume_window_method"] is None


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


def test_unchanged_provider_book_at_new_capture_time_remains_distinct(tmp_path):
    path = tmp_path / "close_quality_captures.jsonl"
    status = tmp_path / "status.json"
    first = _kalshi_fixture(datetime(2026, 11, 20, 0, 40, tzinfo=timezone.utc))
    later = normalize_kalshi_capture(
        market_id="KXNBAPTS-TEST", game_id="nba-test", scheduled_start="2026-11-20T01:00:00Z",
        market_payload={"market": {
            "yes_bid_dollars": "0.4800", "yes_ask_dollars": "0.4900",
            "volume_fp": "972.27", "open_interest_fp": "962.27",
            "updated_time": "2026-11-20T00:40:00+00:00",
        }},
        orderbook_payload={"orderbook_fp": {
            "yes_dollars": [["0.4700", "193.00"], ["0.4800", "690.00"]],
            "no_dollars": [["0.5000", "1100.00"], ["0.5100", "3891.87"]],
        }},
        captured_at=datetime(2026, 11, 20, 0, 50, tzinfo=timezone.utc),
    )
    assert first["raw_payload_sha256"] == later["raw_payload_sha256"]
    assert first["quoted_at"] == later["quoted_at"]
    assert later["quote_age_minutes"] == 10.0
    assert append_captures(jsonl_path=path, status_path=status, captures=[first])["appended_count"] == 1
    assert append_captures(jsonl_path=path, status_path=status, captures=[later])["appended_count"] == 1
    records, _, valid = load_captures_jsonl(path)
    assert valid is True
    assert records[0]["record_id"] != records[1]["record_id"]


def test_v2_append_preserves_v1_bytes_and_hash_chain(tmp_path):
    path = tmp_path / "close_quality_captures.jsonl"
    status = tmp_path / "status.json"
    v1 = _kalshi_fixture(datetime(2026, 11, 20, 0, 40, tzinfo=timezone.utc))
    for key in ("schema_version", "quote_age_minutes", "volume_window_method"):
        v1.pop(key)
    append_captures(jsonl_path=path, status_path=status, captures=[v1])
    original = path.read_bytes()
    v2 = _kalshi_fixture(datetime(2026, 11, 20, 0, 50, tzinfo=timezone.utc))
    assert append_captures(jsonl_path=path, status_path=status, captures=[v2])["appended_count"] == 1
    assert path.read_bytes().startswith(original)
    records, _, valid = load_captures_jsonl(path)
    assert valid is True
    assert analysis_eligible_captures(records) == [records[1]]


def test_live_fetch_stamps_snapshot_after_book_read_across_tip(tmp_path, monkeypatch):
    viewer = tmp_path / "viewer"
    viewer.mkdir()
    (viewer / "nba_odds_2026-11-19.json").write_text(json.dumps({"quotes": [{
        "provider_quote_id": "K1", "game_id": "nba-test",
        "scheduled_start": "2026-11-20T01:00:00Z",
    }]}))
    clock = {"now": datetime(2026, 11, 20, 0, 59, tzinfo=timezone.utc)}

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    def fetch(url):
        if "/orderbook" in url:
            clock["now"] = datetime(2026, 11, 20, 1, 3, tzinfo=timezone.utc)
            return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
        return {"market": {"updated_time": "2026-11-20T00:10:00Z", "volume_fp": "100"}}

    monkeypatch.setattr(capture_module, "datetime", ClockDateTime)
    monkeypatch.setattr(capture_module, "_fetch_json", fetch)
    path = tmp_path / "captures.jsonl"
    result = run_live_capture(viewer_dir=viewer, jsonl_path=path, status_path=tmp_path / "status.json")
    assert result["appended_count"] == 1
    records, _, valid = load_captures_jsonl(path)
    assert valid is True
    assert records[0]["captured_at"] == "2026-11-20T01:03:00+00:00"
    assert records[0]["pre_start"] is False
    assert records[0]["offset_minutes"] == -3.0


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


def test_nominal_cron_pattern_has_t60_t30_t15_t5_targets():
    """The cron expression has targets in each window; run delivery is audited separately."""
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
