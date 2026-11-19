"""Tests for BookQuoteTape: provenance verification, evidential isolation, dynamic freshness, and /v1/edges."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from api.main import app, set_book_quote_tape
from src.contracts.book_quote_tape import (
    BookQuoteTapeRow,
    best_price_view,
    best_price_views_by_class,
    filter_fresh_tape,
    resolve_freshness_limit_seconds,
    sanitize_legacy_edge_rows,
    validate_tape_row,
)
from src.crypto_utils import canonical_json_sha256


def _now() -> datetime:
    return datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)


def _with_raw_payload(row: dict, payload: bytes) -> dict:
    return {
        **row,
        "raw_payload": payload,
        "raw_payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _signed_receipt(row: dict, payload: bytes) -> dict:
    payload_hash = hashlib.sha256(payload).hexdigest()
    body = {
        "market_id": row["market_id"],
        "book": row["book"],
        "source_ts": row["source_ts"],
        "raw_payload_sha256": payload_hash,
    }
    return {
        **row,
        "raw_payload_sha256": payload_hash,
        "signed_receipt": {**body, "receipt_sha256": canonical_json_sha256(body)},
    }


def test_tape_verifies_raw_payload_sha256_and_rejects_hash_only_or_forged():
    now = _now()
    fresh_ts = (now - timedelta(seconds=45)).isoformat()
    payload = b'{"market":"nfl:2026_11_DET_GB:p1","line":47.5,"american":-110}'
    base = {
        "market_id": "nfl:2026_11_DET_GB:p1:receiving_yards:over:47.5",
        "line": 47.5,
        "book": "circa",
        "american": -110,
        "devig_p": 0.512,
        "source_ts": fresh_ts,
        "observed_at": now.isoformat(),
        "raw_payload_sha256": hashlib.sha256(payload).hexdigest(),
    }

    # Valid raw_payload bytes -> accepted with evidential_class='shadow'
    out = validate_tape_row({**base, "raw_payload": payload}, now=now)
    assert out is not None
    assert out["age_seconds"] == 45.0
    assert out["book"] == "circa"
    assert out["evidential_class"] == "shadow"

    # Valid signed adapter receipt -> accepted
    assert validate_tape_row(_signed_receipt(base, payload), now=now) is not None

    # Well-formed 64-hex hash WITHOUT raw_payload or signed_receipt -> rejected
    hash_only = {**base, "raw_payload_sha256": "a" * 64}
    assert validate_tape_row(hash_only, now=now) is None

    # Forged / mismatched hash -> rejected
    forged = {**base, "raw_payload": payload, "raw_payload_sha256": "f" * 64}
    assert validate_tape_row(forged, now=now) is None

    # Missing source_ts, naive source_ts, or DOM adapter -> rejected
    assert validate_tape_row({**base, "raw_payload": payload, "source_ts": ""}, now=now) is None
    assert validate_tape_row({**base, "raw_payload": payload, "source_ts": "2026-11-18T11:59:00"}, now=now) is None
    dom_row = {**base, "raw_payload": payload, "book": "dom_adapter_dk", "quote_source": "browser_dom"}
    assert validate_tape_row(dom_row, now=now) is None


def test_dynamic_freshness_verification_v1_rules():
    now = _now()
    payload = b'{"price":-110}'
    base = {
        "market_id": "mkt-dyn",
        "line": 47.5,
        "book": "polymarket",
        "american": -110,
        "devig_p": 0.51,
        "observed_at": now.isoformat(),
        "raw_payload": payload,
        "raw_payload_sha256": hashlib.sha256(payload).hexdigest(),
    }

    # Pregame (90 min limit): 60 min passes, 95 min fails
    assert validate_tape_row({**base, "game_state": "pregame", "source_ts": (now - timedelta(minutes=60)).isoformat()}, now=now) is not None
    assert validate_tape_row({**base, "game_state": "pregame", "source_ts": (now - timedelta(minutes=95)).isoformat()}, now=now) is None

    # Near start (15 min limit): 10 min passes, 20 min fails
    near_start_kickoff = (now + timedelta(minutes=20)).isoformat()
    assert validate_tape_row({**base, "scheduled_start": near_start_kickoff, "source_ts": (now - timedelta(minutes=10)).isoformat()}, now=now) is not None
    assert validate_tape_row({**base, "scheduled_start": near_start_kickoff, "source_ts": (now - timedelta(minutes=20)).isoformat()}, now=now) is None

    # In game (2 min limit): 90s passes, 3 min fails
    assert validate_tape_row({**base, "game_state": "in_game", "source_ts": (now - timedelta(seconds=90)).isoformat()}, now=now) is not None
    assert validate_tape_row({**base, "game_state": "in_game", "source_ts": (now - timedelta(minutes=3)).isoformat()}, now=now) is None

    # Unknown policy_version fails closed
    assert resolve_freshness_limit_seconds(game_state="pregame", policy_version="unknown.v0") is None
    assert validate_tape_row({**base, "game_state": "pregame", "source_ts": now.isoformat()}, now=now, policy_version="unknown.v0") is None


def test_evidential_class_isolation_and_v1_edges_sanitization():
    now = datetime.now(timezone.utc)
    fresh_ts = (now - timedelta(seconds=30)).isoformat()
    stale_ts = (now - timedelta(hours=4)).isoformat()

    rows = [
        _with_raw_payload(
            {
                "market_id": "mkt-1",
                "line": 47.5,
                "book": "circa",
                "american": -108,
                "devig_p": 0.505,
                "source_ts": fresh_ts,
                "observed_at": now.isoformat(),
            },
            b"circa-mkt-1",
        ),
        _with_raw_payload(
            {
                "market_id": "mkt-1",
                "line": 47.5,
                "book": "polymarket",
                "american": -115,
                "devig_p": 0.520,
                "source_ts": fresh_ts,
                "observed_at": now.isoformat(),
            },
            b"poly-mkt-1",
        ),
        _with_raw_payload(
            {
                "market_id": "mkt-stale",
                "line": 47.5,
                "book": "circa",
                "american": 130,
                "devig_p": 0.420,
                "source_ts": stale_ts,
                "observed_at": now.isoformat(),
            },
            b"circa-stale",
        ),
        {
            "market_id": "mkt-forged",
            "line": 47.5,
            "book": "polymarket",
            "american": 120,
            "devig_p": 0.450,
            "source_ts": fresh_ts,
            "observed_at": now.isoformat(),
            "raw_payload": b"real-bytes",
            "raw_payload_sha256": "0" * 64,
        },
    ]

    fresh = filter_fresh_tape(rows, now=now)
    assert len(fresh) == 2
    assert {r["evidential_class"] for r in fresh} == {"confirmatory", "shadow"}
    assert all(r["market_id"] not in {"mkt-stale", "mkt-forged"} for r in fresh)

    # best_price_view never pools confirmatory and shadow
    with pytest.raises(ValueError, match="evidential_class"):
        best_price_view(rows, evidential_class="all", now=now)

    best_conf = best_price_view(rows, evidential_class="confirmatory", now=now)
    best_shad = best_price_view(rows, evidential_class="shadow", now=now)
    assert best_conf["mkt-1"]["book"] == "polymarket"
    assert best_shad["mkt-1"]["book"] == "circa"
    assert "mkt-stale" not in best_conf and "mkt-stale" not in best_shad

    by_class = best_price_views_by_class(rows, now=now)
    assert by_class["confirmatory"]["mkt-1"]["book"] == "polymarket"
    assert by_class["shadow"]["mkt-1"]["book"] == "circa"

    # Legacy DB rows without a verified fresh tape row are non_actionable_reference with zeroed action fields
    legacy = [
        {"market_id": "mkt-legacy-unverified", "actionable": True, "actionable_stake": 0.02, "stake": 0.02, "qualified": True, "can_execute": True, "edge": 14.0},
        {"market_id": "mkt-1", "actionable": True, "actionable_stake": 0.02, "stake": 0.02, "qualified": True, "can_execute": True, "edge": 11.0},
    ]
    sanitized = sanitize_legacy_edge_rows(legacy, fresh)
    assert sanitized[0]["reference_status"] == "non_actionable_reference"
    assert sanitized[0]["non_actionable_reference"] is True
    assert sanitized[0]["actionable"] is False
    assert sanitized[0]["actionable_stake"] == 0.0
    assert sanitized[0]["stake"] == 0.0
    assert sanitized[0]["qualified"] is False
    assert sanitized[0]["can_execute"] is False
    assert sanitized[0]["edge"] == 0.0
    assert sanitized[1]["actionable"] is True

    try:
        set_book_quote_tape(rows)
        client = TestClient(app)
        resp = client.get("/v1/edges?slate=2026_w7")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["tape"]) == 2
        assert body["best_price"]["mkt-1"]["book"] == "polymarket"
        assert body["best_price_by_class"]["shadow"]["mkt-1"]["book"] == "circa"
        assert "mkt-stale" not in body["best_price"]
        assert "mkt-forged" not in body["best_price"]

        # Stale tape rows must NOT appear in /api/v1/diagnostics
        diag = client.get("/api/v1/diagnostics?slate=2026_w7")
        assert diag.status_code == 200
        assert "mkt-stale" not in diag.text
    finally:
        set_book_quote_tape([])
