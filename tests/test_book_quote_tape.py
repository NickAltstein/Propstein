"""Tests for Commit 1: BookQuoteTape timestamped provenance and stale-is-absence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

from api.main import app, set_book_quote_tape
from src.contracts.book_quote_tape import (
    BookQuoteTapeRow,
    best_price_view,
    filter_fresh_tape,
    validate_tape_row,
)


def _now() -> datetime:
    return datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)


def test_tape_rejects_missing_source_ts_dom_adapters_and_missing_hash():
    now = _now()
    fresh_ts = (now - timedelta(seconds=45)).isoformat()
    valid = BookQuoteTapeRow(
        market_id="nfl:2026_11_DET_GB:p1:receiving_yards:over:47.5",
        line=47.5,
        book="circa",
        american=-110,
        devig_p=0.512,
        source_ts=fresh_ts,
        observed_at=now.isoformat(),
        raw_payload_sha256="a" * 64,
    )
    out = validate_tape_row(valid, now=now)
    assert out is not None
    assert out["age_seconds"] == 45.0
    assert out["book"] == "circa"

    # Missing source_ts -> no printed edge
    no_ts = {**valid.as_dict(), "source_ts": ""}
    assert validate_tape_row(no_ts, now=now) is None

    # Naive source_ts -> rejected
    naive_ts = {**valid.as_dict(), "source_ts": "2026-11-18T11:59:00"}
    assert validate_tape_row(naive_ts, now=now) is None

    # DOM adapter / scraped -> rejected
    dom_row = {**valid.as_dict(), "book": "dom_adapter_dk", "quote_source": "browser_dom"}
    assert validate_tape_row(dom_row, now=now) is None

    # Missing provenance hash -> rejected
    no_hash = {**valid.as_dict(), "raw_payload_sha256": "not-a-hash"}
    assert validate_tape_row(no_hash, now=now) is None

    # Stale (> max_age_seconds) -> absent
    stale = {**valid.as_dict(), "source_ts": (now - timedelta(hours=3)).isoformat()}
    assert validate_tape_row(stale, now=now, max_age_seconds=5400) is None


def test_best_price_view_and_v1_edges_stale_is_absence():
    now = datetime.now(timezone.utc)
    fresh_ts = (now - timedelta(seconds=30)).isoformat()
    stale_ts = (now - timedelta(hours=4)).isoformat()

    rows = [
        {
            "market_id": "mkt-1",
            "line": 47.5,
            "book": "circa",
            "american": -108,
            "devig_p": 0.505,
            "source_ts": fresh_ts,
            "observed_at": now.isoformat(),
            "raw_payload_sha256": "b" * 64,
        },
        {
            "market_id": "mkt-1",
            "line": 47.5,
            "book": "pinnacle",
            "american": -115,
            "devig_p": 0.520,
            "source_ts": fresh_ts,
            "observed_at": now.isoformat(),
            "raw_payload_sha256": "c" * 64,
        },
        {
            "market_id": "mkt-stale",
            "line": 47.5,
            "book": "circa",
            "american": 130,
            "devig_p": 0.420,
            "source_ts": stale_ts,
            "observed_at": now.isoformat(),
            "raw_payload_sha256": "d" * 64,
        },
    ]
    fresh = filter_fresh_tape(rows, now=now)
    assert len(fresh) == 2
    assert all(r["market_id"] != "mkt-stale" for r in fresh)

    best = best_price_view(rows, now=now)
    assert best["mkt-1"]["book"] == "circa"
    assert "mkt-stale" not in best

    try:
        set_book_quote_tape(rows)
        client = TestClient(app)
        resp = client.get("/v1/edges?slate=2026_w7")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["tape"]) == 2
        assert body["best_price"]["mkt-1"]["book"] == "circa"
        assert "mkt-stale" not in body["best_price"]

        # Stale tape rows must NOT appear in /api/v1/diagnostics
        diag = client.get("/api/v1/diagnostics?slate=2026_w7")
        assert diag.status_code == 200
        assert "mkt-stale" not in diag.text
    finally:
        set_book_quote_tape([])
