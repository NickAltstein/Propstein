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


def test_real_populated_kalshi_orderbook_fixture_with_fractional_depth():
    # Redacted fixture matching live GET /trade-api/v2/markets/{ticker}/orderbook?depth=5
    now = _now()
    snap_id = make_snapshot_id(market_id="KXNBAPTS-26NOV19GSWMEM-PLAYER1", captured_at=now)
    live_ob = {
        "orderbook_fp": {
            "no_dollars": [
                ["0.6400", "318.00"],
                ["0.6500", "220.00"],
                ["0.6600", "64.77"],
                ["0.6700", "100.00"],
                ["0.6800", "1.00"],
            ],
            "yes_dollars": [
                ["0.2700", "41.00"],
                ["0.2800", "52.00"],
                ["0.2900", "21.00"],
                ["0.3000", "20.00"],
                ["0.3100", "67.30"],
            ],
        }
    }
    row = normalize_kalshi_ladder_rung(
        market_id="KXNBAPTS-26NOV19GSWMEM-PLAYER1",
        ticker="KXNBAPTS-26NOV19GSWMEM-PLAYER1-20",
        rung_threshold=19.5,
        orderbook_payload=live_ob,
        snapshot_id=snap_id,
        captured_at=now,
        quoted_at="2026-11-19T13:01:54.030392+00:00",
    )
    assert row["status"] == "OK"
    assert row["yes_bid"] == "0.3100"
    assert row["yes_ask"] == "0.3200"
    assert row["yes_bid_depth"] == "67.3000"
    assert row["no_bid_depth"] == "1.0000"


def test_weekly_manifest_continues_hash_chain_across_files(tmp_path):
    from scripts.verify_ladder_manifest import build_manifest_entry, verify_manifest

    t1 = _now()
    t2 = t1 + timedelta(days=7)
    w1_path = tmp_path / "ladder_2026_w46.jsonl"
    w2_path = tmp_path / "ladder_2026_w47.jsonl"

    s1 = make_snapshot_id(market_id="MKT-1", captured_at=t1)
    r1 = normalize_kalshi_ladder_rung(
        market_id="MKT-1",
        ticker="MKT-1-15",
        rung_threshold=14.5,
        orderbook_payload={"orderbook_fp": {"yes_dollars": [["0.5000", "10.00"]], "no_dollars": [["0.4800", "10.00"]]}},
        snapshot_id=s1,
        captured_at=t1,
    )
    res1 = append_ladder_captures(jsonl_path=w1_path, captures=[r1])
    e1 = build_manifest_entry(week_id="2026-W46", jsonl_path=w1_path)

    s2 = make_snapshot_id(market_id="MKT-1", captured_at=t2)
    r2 = normalize_kalshi_ladder_rung(
        market_id="MKT-1",
        ticker="MKT-1-15",
        rung_threshold=14.5,
        orderbook_payload={"orderbook_fp": {"yes_dollars": [["0.5200", "12.00"]], "no_dollars": [["0.4600", "12.00"]]}},
        snapshot_id=s2,
        captured_at=t2,
    )
    append_ladder_captures(
        jsonl_path=w2_path,
        captures=[r2],
        expected_initial_prev_hash=res1["last_record_hash"],
    )
    e2 = build_manifest_entry(
        week_id="2026-W47",
        jsonl_path=w2_path,
        expected_initial_prev_hash=e1["last_record_hash"],
    )

    manifest = {"schema": "propwire.ladder_archives_manifest.v1", "archives": [e1, e2]}
    verified = verify_manifest(manifest, archive_dir=tmp_path)
    assert verified["valid"] is True
    assert verified["weeks_checked"] == 2
    assert verified["files_verified"] == 2

    # Tampered SHA-256 fails verification
    bad_manifest = {"schema": "propwire.ladder_archives_manifest.v1", "archives": [e1, {**e2, "sha256": "0" * 64}]}
    assert verify_manifest(bad_manifest, archive_dir=tmp_path)["valid"] is False
