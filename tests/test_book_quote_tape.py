"""Tests for BookQuoteTape: raw_payload provenance, evidential isolation, dynamic freshness, and /v1/edges."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from api.main import app, set_book_quote_tape
from src.contracts.book_quote_tape import (
    best_price_view,
    best_price_views_by_class,
    filter_fresh_tape,
    resolve_freshness_limit_seconds,
    sanitize_legacy_edge_rows,
    validate_tape_row,
)


def _now() -> datetime:
    return datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)


def _base_row(now: datetime, payload: bytes, *, book: str = "circa") -> dict:
    return {
        "market_id": "nfl:2026_11_DET_GB:p1:receiving_yards:over:47.5",
        "line": 47.5,
        "book": book,
        "american": -110,
        "devig_p": 0.512,
        "source_ts": (now - timedelta(seconds=45)).isoformat(),
        "observed_at": now.isoformat(),
        "raw_payload": payload,
        "raw_payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_valid_raw_payload_bytes_accepted_with_registry_class():
    now = _now()
    payload = b'{"market":"nfl:2026_11_DET_GB:p1","line":47.5,"american":-110}'
    out = validate_tape_row(_base_row(now, payload, book="circa"), now=now)
    assert out is not None
    assert out["age_seconds"] == 45.0
    assert out["book"] == "circa"
    assert out["evidential_class"] == "shadow"

    poly_out = validate_tape_row(_base_row(now, payload, book="polymarket"), now=now)
    assert poly_out is not None
    assert poly_out["evidential_class"] == "confirmatory"


def test_hash_only_and_signed_receipt_only_rejected():
    now = _now()
    payload = b'{"price":-110}'
    base = _base_row(now, payload)
    del base["raw_payload"]

    # Well-formed 64-hex hash without raw_payload bytes -> rejected
    assert validate_tape_row({**base, "raw_payload_sha256": "a" * 64}, now=now) is None

    # Legacy self-signed receipt dict without raw_payload bytes -> rejected
    assert validate_tape_row(
        {
            **base,
            "signed_receipt": {
                "market_id": base["market_id"],
                "raw_payload_sha256": base["raw_payload_sha256"],
                "receipt_sha256": "b" * 64,
            },
        },
        now=now,
    ) is None


def test_forged_hash_rejected():
    now = _now()
    payload = b'{"price":-110}'
    forged = {**_base_row(now, payload), "raw_payload_sha256": "f" * 64}
    assert validate_tape_row(forged, now=now) is None


def test_wrong_payload_for_correct_hash_rejected():
    now = _now()
    expected_payload = b'{"price":-110}'
    tampered_payload = b'{"price":+150}'
    row = {
        **_base_row(now, expected_payload),
        "raw_payload": tampered_payload,
        "raw_payload_sha256": hashlib.sha256(expected_payload).hexdigest(),
    }
    assert validate_tape_row(row, now=now) is None


def test_missing_naive_timestamp_and_dom_adapter_rejected():
    now = _now()
    payload = b'{"price":-110}'
    base = _base_row(now, payload)
    assert validate_tape_row({**base, "source_ts": ""}, now=now) is None
    assert validate_tape_row({**base, "source_ts": "2026-11-18T11:59:00"}, now=now) is None
    assert validate_tape_row({**base, "book": "dom_adapter_dk", "quote_source": "browser_dom"}, now=now) is None


def test_dynamic_freshness_verification_v1_rules_and_stale_book():
    now = _now()
    payload = b'{"price":-110}'
    base = _base_row(now, payload, book="polymarket")

    # Pregame (90 min): 60 min passes, 95 min fails
    assert validate_tape_row({**base, "game_state": "pregame", "source_ts": (now - timedelta(minutes=60)).isoformat()}, now=now) is not None
    assert validate_tape_row({**base, "game_state": "pregame", "source_ts": (now - timedelta(minutes=95)).isoformat()}, now=now) is None

    # Near start (15 min): 10 min passes, 20 min fails
    kickoff = (now + timedelta(minutes=20)).isoformat()
    assert validate_tape_row({**base, "scheduled_start": kickoff, "source_ts": (now - timedelta(minutes=10)).isoformat()}, now=now) is not None
    assert validate_tape_row({**base, "scheduled_start": kickoff, "source_ts": (now - timedelta(minutes=20)).isoformat()}, now=now) is None

    # In game (2 min): 90s passes, 3 min fails
    assert validate_tape_row({**base, "game_state": "in_game", "source_ts": (now - timedelta(seconds=90)).isoformat()}, now=now) is not None
    assert validate_tape_row({**base, "game_state": "in_game", "source_ts": (now - timedelta(minutes=3)).isoformat()}, now=now) is None

    # Unknown policy_version fails closed
    assert resolve_freshness_limit_seconds(game_state="pregame", policy_version="unknown.v0") is None
    assert validate_tape_row({**base, "game_state": "pregame"}, now=now, policy_version="unknown.v0") is None


def test_evidential_class_split_shadow_never_in_confirmatory_view():
    now = _now()
    shadow_only = _base_row(now, b"circa-only", book="circa")
    shadow_only["market_id"] = "mkt-shadow-only"
    shadow_only["devig_p"] = 0.45

    conf_row = _base_row(now, b"poly-1", book="polymarket")
    conf_row["market_id"] = "mkt-both"
    conf_row["devig_p"] = 0.52

    better_shadow = _base_row(now, b"circa-2", book="circa")
    better_shadow["market_id"] = "mkt-both"
    better_shadow["devig_p"] = 0.48

    rows = [shadow_only, conf_row, better_shadow]
    with pytest.raises(ValueError, match="evidential_class"):
        best_price_view(rows, evidential_class="all", now=now)

    conf_view = best_price_view(rows, evidential_class="confirmatory", now=now)
    assert "mkt-shadow-only" not in conf_view
    assert conf_view["mkt-both"]["book"] == "polymarket"

    by_class = best_price_views_by_class(rows, now=now)
    assert "mkt-shadow-only" not in by_class["confirmatory"]
    assert by_class["shadow"]["mkt-shadow-only"]["book"] == "circa"
    assert by_class["shadow"]["mkt-both"]["book"] == "circa"


def test_v1_edges_sanitizes_unverified_legacy_rows_and_omits_stale_books():
    now = datetime.now(timezone.utc)
    fresh_ts = (now - timedelta(seconds=30)).isoformat()
    stale_ts = (now - timedelta(hours=4)).isoformat()

    rows = [
        {**_base_row(now, b"poly-fresh", book="polymarket"), "market_id": "mkt-1", "source_ts": fresh_ts, "devig_p": 0.52},
        {**_base_row(now, b"circa-fresh", book="circa"), "market_id": "mkt-1", "source_ts": fresh_ts, "devig_p": 0.50},
        {**_base_row(now, b"circa-stale", book="circa"), "market_id": "mkt-stale", "source_ts": stale_ts, "devig_p": 0.42},
    ]
    fresh = filter_fresh_tape(rows, now=now)
    assert len(fresh) == 2

    legacy = [
        {"market_id": "mkt-unverified", "actionable": True, "actionable_stake": 0.02, "stake": 0.02, "qualified": True, "can_execute": True, "edge": 14.0},
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
        assert "mkt-stale" not in body["best_price_by_class"]["shadow"]

        diag = client.get("/api/v1/diagnostics?slate=2026_w7")
        assert diag.status_code == 200
        assert "mkt-stale" not in diag.text
    finally:
        set_book_quote_tape([])
