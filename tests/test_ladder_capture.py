"""Tests for P11 Part A: multi-rung Kalshi ladder capture."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.ladder_capture import (
    append_ladder_captures,
    load_ladder_captures_jsonl,
    make_snapshot_id,
    normalize_kalshi_ladder_rung,
)


def _now() -> datetime:
    return datetime(2026, 11, 19, 23, 0, 0, tzinfo=timezone.utc)


def test_bid_only_book_to_yes_ask_exact_decimal_cents():
    now = _now()
    snap_id = make_snapshot_id(market_id="KXNBAPTS-26NOV19GSWMEM-P1", captured_at=now)
    # Kalshi orderbook_fp is sorted ascending (best bid last)
    ob = {
        "orderbook_fp": {
            "yes_dollars": [["0.1500", "50.00"], ["0.4100", "25.00"]],
            "no_dollars": [["0.2000", "40.00"], ["0.5700", "18.00"]],
        }
    }
    row = normalize_kalshi_ladder_rung(
        market_id="KXNBAPTS-26NOV19GSWMEM-P1",
        ticker="KXNBAPTS-26NOV19GSWMEM-P1-15",
        rung_threshold=15,
        orderbook_payload=ob,
        snapshot_id=snap_id,
        captured_at=now,
    )
    assert row["status"] == "OK"
    assert row["executable"] is True
    assert row["yes_bid"] == "0.4100"
    # 1.0000 - 0.5700 = 0.4300 exact (no float drift)
    assert row["yes_ask"] == "0.4300"
    assert Decimal(row["yes_ask"]) == Decimal("1.0000") - Decimal("0.5700")
    assert row["yes_bid_depth"] == "25.0000"
    assert row["no_bid_depth"] == "18.0000"


def test_empty_bids_produces_no_book_with_null_prices():
    now = _now()
    snap_id = make_snapshot_id(market_id="KXNBAPTS-26NOV19GSWMEM-P1", captured_at=now)
    row = normalize_kalshi_ladder_rung(
        market_id="KXNBAPTS-26NOV19GSWMEM-P1",
        ticker="KXNBAPTS-26NOV19GSWMEM-P1-20",
        rung_threshold=20,
        orderbook_payload={"orderbook_fp": {"yes_dollars": [], "no_dollars": []}},
        snapshot_id=snap_id,
        captured_at=now,
    )
    assert row["status"] == "NO_BOOK"
    assert row["executable"] is False
    assert row["yes_bid"] is None
    assert row["yes_ask"] is None


def test_one_sided_book_sets_null_for_missing_side():
    now = _now()
    snap_id = make_snapshot_id(market_id="KXNBAPTS-26NOV19GSWMEM-P1", captured_at=now)
    yes_only = normalize_kalshi_ladder_rung(
        market_id="KXNBAPTS-26NOV19GSWMEM-P1",
        ticker="KXNBAPTS-26NOV19GSWMEM-P1-10",
        rung_threshold=10,
        orderbook_payload={"orderbook_fp": {"yes_dollars": [["0.7200", "10.00"]], "no_dollars": []}},
        snapshot_id=snap_id,
        captured_at=now,
    )
    assert yes_only["status"] == "ONE_SIDED_YES_ONLY"
    assert yes_only["executable"] is False
    assert yes_only["yes_bid"] == "0.7200"
    assert yes_only["yes_ask"] is None

    no_only = normalize_kalshi_ladder_rung(
        market_id="KXNBAPTS-26NOV19GSWMEM-P1",
        ticker="KXNBAPTS-26NOV19GSWMEM-P1-25",
        rung_threshold=25,
        orderbook_payload={"orderbook_fp": {"yes_dollars": [], "no_dollars": [["0.8100", "12.00"]]}},
        snapshot_id=snap_id,
        captured_at=now,
    )
    assert no_only["status"] == "ONE_SIDED_NO_ONLY"
    assert no_only["executable"] is False
    assert no_only["yes_bid"] is None
    assert no_only["yes_ask"] == "0.1900"


def test_one_snapshot_id_per_ladder_and_hash_chain_intact(tmp_path):
    now = _now()
    jsonl_path = tmp_path / "ladder_captures.jsonl"
    snap_id = make_snapshot_id(market_id="KXNBAPTS-26NOV19GSWMEM-P1", captured_at=now)
    rungs = [
        normalize_kalshi_ladder_rung(
            market_id="KXNBAPTS-26NOV19GSWMEM-P1",
            ticker=f"KXNBAPTS-26NOV19GSWMEM-P1-{thr}",
            rung_threshold=thr,
            orderbook_payload={"orderbook_fp": {"yes_dollars": [["0.5000", "10.00"]], "no_dollars": [["0.4800", "10.00"]]}},
            snapshot_id=snap_id,
            captured_at=now,
        )
        for thr in (10, 15, 20)
    ]
    res = append_ladder_captures(jsonl_path=jsonl_path, captures=rungs)
    assert res["appended"] == 3
    assert res["chain_valid"] is True

    rows, _, chain_valid = load_ladder_captures_jsonl(jsonl_path)
    assert chain_valid is True
    assert len(rows) == 3
    assert {r["snapshot_id"] for r in rows} == {snap_id}


def test_different_captured_at_cannot_share_snapshot_id(tmp_path):
    now = _now()
    later = now + timedelta(seconds=15)
    jsonl_path = tmp_path / "ladder_captures.jsonl"
    snap_id = make_snapshot_id(market_id="KXNBAPTS-26NOV19GSWMEM-P1", captured_at=now)

    r1 = normalize_kalshi_ladder_rung(
        market_id="KXNBAPTS-26NOV19GSWMEM-P1",
        ticker="KXNBAPTS-26NOV19GSWMEM-P1-10",
        rung_threshold=10,
        orderbook_payload={"orderbook_fp": {"yes_dollars": [["0.5000", "10.00"]], "no_dollars": [["0.4800", "10.00"]]}},
        snapshot_id=snap_id,
        captured_at=now,
    )
    r2 = normalize_kalshi_ladder_rung(
        market_id="KXNBAPTS-26NOV19GSWMEM-P1",
        ticker="KXNBAPTS-26NOV19GSWMEM-P1-15",
        rung_threshold=15,
        orderbook_payload={"orderbook_fp": {"yes_dollars": [["0.4000", "10.00"]], "no_dollars": [["0.5800", "10.00"]]}},
        snapshot_id=snap_id,
        captured_at=later,
    )
    with pytest.raises(ValueError, match="cannot span different captured_at"):
        append_ladder_captures(jsonl_path=jsonl_path, captures=[r1, r2])
