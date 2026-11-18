"""The action feed and alert dispatcher must fail closed together."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.services.actionable_edges import POLICY_VERSION, decide_feed
from scripts import dispatch_edge_alerts as dispatcher


def complete_quote(now: datetime) -> dict:
    return {
        "quote_id": "book-quote-1", "game_id": "2026_11_BAL_LAC",
        "player_id": "player-1", "player_name": "Test Receiver",
        "book": "TestBook", "market": "receiving_yards", "line": 49.5,
        "over_odds": -150, "under_odds": 120,
        "quoted_at": (now - timedelta(seconds=20)).isoformat(),
        "scheduled_start": (now + timedelta(hours=2)).isoformat(),
        "pre_start": True, "verified": True,
        "verification_engine": "verification.v1",
        "verification_status": "VERIFIED", "verification_receipt_id": "receipt-1",
        "verification_clauses": [
            {"clause_id": key, "passed": True}
            for key in ("V1_receipt", "V2_staleness", "V3_conflict")
        ],
        "qualification_policy": {"version": "edge_10pct_v1", "edge_hurdle": 0.10,
                                 "require_positive_ev": True},
        "evaluation": {"is_qualified": True, "best_side": "OVER",
                       "expected_return": 0.1167, "break_even_adv": 0.07,
                       "prob_edge": 0.101,
                       "actionable_stake": 0.01},
    }


def test_dispatcher_policy_version_matches_decision_source():
    assert dispatcher.POLICY_VERSION == POLICY_VERSION


@pytest.fixture
def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def test_complete_quote_produces_one_edge_and_one_alert(now, monkeypatch):
    edges, diagnostics = decide_feed({"slate": "2026-W11", "quotes": [complete_quote(now)]}, now, slate="2026_w11")
    assert len(edges) == 1 and diagnostics == []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    payload = {"status": "success", "source": "verified_feed", "data": edges + edges}
    monkeypatch.setattr(dispatcher, "urlopen", lambda *_args, **_kwargs: Response(json.dumps(payload).encode()))
    alerts = dispatcher.collect_active_edges(edges_url="https://example.test/v1/edges")
    assert len(alerts) == 1
    assert alerts[0]["odds"] == -150


@pytest.mark.parametrize("path", [
    "verified", "verification_status", "verification_engine", "verification_receipt_id",
    "verification_clauses", "qualification_policy", "evaluation", "quote_id",
    "game_id", "player_id", "player_name", "book", "market", "line",
    "over_odds", "quoted_at", "scheduled_start", "pre_start",
])
def test_missing_evidence_produces_zero_edges(now, path):
    quote = complete_quote(now)
    quote.pop(path)
    edges, diagnostics = decide_feed({"slate": "2026_w11", "quotes": [quote]}, now)
    assert edges == []
    assert len(diagnostics) == 1


def test_started_or_stale_quote_produces_zero_edges(now):
    quote = complete_quote(now)
    quote["scheduled_start"] = (now - timedelta(seconds=1)).isoformat()
    assert decide_feed({"quotes": [quote]}, now)[0] == []
    quote = complete_quote(now)
    quote["quoted_at"] = (now - timedelta(hours=2)).isoformat()
    assert decide_feed({"quotes": [quote]}, now)[0] == []


@pytest.mark.parametrize("section,key", [
    ("qualification_policy", "version"), ("qualification_policy", "edge_hurdle"),
    ("qualification_policy", "require_positive_ev"),
    ("evaluation", "is_qualified"), ("evaluation", "best_side"),
    ("evaluation", "expected_return"), ("evaluation", "prob_edge"),
    ("evaluation", "break_even_adv"), ("evaluation", "actionable_stake"),
])
def test_missing_policy_or_evaluation_field_is_not_actionable(now, section, key):
    quote = complete_quote(now)
    quote[section].pop(key)
    assert decide_feed({"quotes": [quote]}, now)[0] == []


def test_actual_book_side_and_expected_return_determine_order(now):
    lower = complete_quote(now)
    lower["quote_id"] = "lower-ev"
    lower["evaluation"]["expected_return"] = 0.06
    higher = complete_quote(now)
    higher["quote_id"] = "higher-ev"
    higher["evaluation"]["expected_return"] = 0.11
    higher["evaluation"]["best_side"] = "UNDER"
    edges, _ = decide_feed({"quotes": [lower, higher]}, now)
    assert [edge["quote_id"] for edge in edges] == ["higher-ev", "lower-ev"]
    assert edges[0]["odds"] == 120


def test_dispatcher_rejects_missing_verification_or_price(now, monkeypatch):
    edges, _ = decide_feed({"quotes": [complete_quote(now)]}, now)

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    for key in ("verified", "verification_status", "verification_engine", "receipt_id",
                "qualification_policy_version", "odds", "quoted_at", "line"):
        malformed = dict(edges[0])
        malformed.pop(key)
        payload = {"status": "success", "source": "verified_feed", "data": [malformed]}
        monkeypatch.setattr(dispatcher, "urlopen", lambda *_args, **_kwargs: Response(json.dumps(payload).encode()))
        assert dispatcher.collect_active_edges(edges_url="https://example.test/v1/edges") == []


def test_receipt_matched_observed_quote_is_not_actionable(now):
    quote = complete_quote(now)
    quote["verified"] = False
    quote["verification_status"] = "OBSERVED"
    edges, _ = decide_feed({"quotes": [quote]}, now)
    assert edges == []


def test_api_edges_and_diagnostics_use_same_quote_decision(now, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app

    feed_path = tmp_path / "live_odds_feed.json"
    quote = complete_quote(now)
    missing_price = complete_quote(now)
    missing_price["quote_id"] = "missing-price"
    missing_price.pop("over_odds")
    feed_path.write_text(json.dumps({"slate": "2026_w11", "quotes": [quote, missing_price]}))
    monkeypatch.setenv("LIVE_ODDS_FEED_PATH", str(feed_path))
    client = TestClient(app)
    response = client.get("/v1/edges", params={"slate": "2026_w11"})
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["data"][0]["odds"] == -150
    diagnostics = client.get("/v1/diagnostics", params={"slate": "2026_w11"})
    assert diagnostics.status_code == 200
    assert diagnostics.json()["data"] == [
        {"quote_id": "missing-price", "reason": "BOOK_PRICE_MISSING_OR_INVALID"}
    ]


def test_dispatcher_unset_url_raises_and_sends_nothing(monkeypatch):
    import pytest
    import sys

    monkeypatch.delenv("PROPSTEIN_EDGES_URL", raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    sent = []
    monkeypatch.setattr(dispatcher, "post_webhook", lambda *args, **kwargs: sent.append(("discord", args)) or True)
    monkeypatch.setattr(dispatcher, "post_telegram", lambda *args, **kwargs: sent.append(("telegram", args)) or True)
    monkeypatch.setattr(sys, "argv", ["dispatch_edge_alerts.py"])
    with pytest.raises(RuntimeError, match="PROPSTEIN_EDGES_URL is required"):
        dispatcher.main()
    assert sent == []


def test_dispatcher_set_url_zero_qualifying_edges_sends_nothing(monkeypatch):
    import sys

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    monkeypatch.setenv("PROPSTEIN_EDGES_URL", "https://example.test/v1/edges")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    payload = {"status": "success", "source": "verified_feed", "data": []}
    monkeypatch.setattr(dispatcher, "urlopen", lambda *_args, **_kwargs: Response(json.dumps(payload).encode()))
    sent = []
    monkeypatch.setattr(dispatcher, "post_webhook", lambda *args, **kwargs: sent.append(("discord", args)) or True)
    monkeypatch.setattr(dispatcher, "post_telegram", lambda *args, **kwargs: sent.append(("telegram", args)) or True)
    monkeypatch.setattr(sys, "argv", ["dispatch_edge_alerts.py"])
    dispatcher.main()
    assert sent == []
