"""Fixture-only tests for additive close-quality capture."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from src.close_quality_capture import (
    append_captures,
    load_captures_jsonl,
    normalize_kalshi_capture,
    normalize_polymarket_capture,
)


def test_polymarket_and_kalshi_capture_fields_offsets_and_nulls(tmp_path):
    start = datetime(2026, 11, 20, 1, 0, 0, tzinfo=timezone.utc)
    cap_pre = datetime(2026, 11, 20, 0, 30, 0, tzinfo=timezone.utc)

    pm_book = {
        "timestamp": str(int(cap_pre.timestamp() * 1000)),
        "bids": [{"price": "0.45", "size": "120.0"}, {"price": "0.47", "size": "250.0"}],
        "asks": [{"price": "0.52", "size": "300.0"}, {"price": "0.50", "size": "180.0"}],
    }
    pm_gamma = [{"volumeNum": 1500.0}]
    pm_rec = normalize_polymarket_capture(
        market_id="tok-1",
        game_id="2026_11_IND_HOU",
        scheduled_start=start,
        book_payload=pm_book,
        gamma_payload=pm_gamma,
        captured_at=cap_pre,
        prev_volume_total=1200.0,
    )
    assert pm_rec["pre_start"] is True
    assert pm_rec["offset_minutes"] == 30.0
    assert pm_rec["bid"] == 0.47
    assert pm_rec["ask"] == 0.50
    assert pm_rec["volume_window"] == 300.0
    assert pm_rec["open_interest"] is None  # missing stays null, never estimated
    assert len(pm_rec["depth_levels"]["bids"]) == 2
    assert len(pm_rec["depth_levels"]["asks"]) == 2

    # Kalshi post-start capture -> flagged pre_start=False, null volume_window when prev missing
    cap_post = datetime(2026, 11, 20, 1, 5, 0, tzinfo=timezone.utc)
    k_mkt = {
        "market": {
            "yes_bid_dollars": "0.4800",
            "yes_ask_dollars": "0.4900",
            "volume_fp": "972.27",
            "open_interest_fp": "962.27",
            "updated_time": cap_post.isoformat(),
        }
    }
    k_ob = {
        "orderbook_fp": {
            "yes_dollars": [["0.4700", "193.00"], ["0.4800", "690.00"]],
            "no_dollars": [["0.5000", "1100.00"], ["0.5100", "3891.87"]],
        }
    }
    k_rec = normalize_kalshi_capture(
        market_id="KXNBAPTS-TEST",
        game_id="nba-test",
        scheduled_start=start,
        market_payload=k_mkt,
        orderbook_payload=k_ob,
        captured_at=cap_post,
        prev_volume_total=None,
    )
    assert k_rec["pre_start"] is False
    assert k_rec["offset_minutes"] == -5.0
    assert k_rec["bid"] == 0.48
    assert k_rec["ask"] == 0.49
    assert k_rec["depth_levels"]["asks"][0] == {"price": 0.49, "size": 3891.87}
    assert k_rec["volume_total"] == 972.27
    assert k_rec["volume_window"] is None
    assert k_rec["open_interest"] == 962.27

    jsonl_path = tmp_path / "close_quality_captures.jsonl"
    status_path = tmp_path / "close_quality_capture_status.json"
    st1 = append_captures(jsonl_path=jsonl_path, status_path=status_path, captures=[pm_rec, k_rec], now=cap_post)
    assert st1["appended_count"] == 2
    assert st1["total_records"] == 2

    # Idempotent replay -> 0 new records
    st2 = append_captures(jsonl_path=jsonl_path, status_path=status_path, captures=[pm_rec, k_rec], now=cap_post)
    assert st2["appended_count"] == 0
    assert st2["total_records"] == 2

    # Hash chain valid
    recs, _, chain_ok = load_captures_jsonl(jsonl_path)
    assert chain_ok is True
    assert recs[0]["prev_record_hash"] == "GENESIS"


def test_fetch_failure_keeps_previous_data_and_emits_stale_and_leaves_existing_files_unchanged(tmp_path):
    audit_bytes_before = hashlib.sha256(Path("viewer/clv_audit_report.json").read_bytes()).hexdigest()
    jsonl_path = tmp_path / "close_quality_captures.jsonl"
    status_path = tmp_path / "close_quality_capture_status.json"

    rec = normalize_kalshi_capture(
        market_id="K1",
        game_id="G1",
        scheduled_start="2026-11-20T01:00:00Z",
        market_payload={"market": {"updated_time": "2026-11-20T00:30:00Z"}},
        orderbook_payload=None,
        captured_at=datetime(2026, 11, 20, 0, 30, tzinfo=timezone.utc),
    )
    assert rec["bid"] is None
    assert rec["ask"] is None
    assert rec["depth_levels"] is None
    append_captures(jsonl_path=jsonl_path, status_path=status_path, captures=[rec])

    # Subsequent fetch failure keeps previous JSONL and emits stale_context=True
    st_fail = append_captures(
        jsonl_path=jsonl_path,
        status_path=status_path,
        captures=[],
        fetch_errors=["KALSHI_FETCH_FAILED:K1:HTTP 503"],
    )
    assert st_fail["stale_context"] is True
    assert st_fail["total_records"] == 1
    assert hashlib.sha256(Path("viewer/clv_audit_report.json").read_bytes()).hexdigest() == audit_bytes_before
