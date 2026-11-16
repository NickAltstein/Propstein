"""API checks that the commercial switch cannot open paid access alone."""

import sqlite3

from fastapi.testclient import TestClient

from api.main import app


def test_paid_checkout_frozen_even_with_legacy_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("PROPSTEIN_PAID_SIGNUPS_ENABLED", "1")
    missing_feed = tmp_path / "missing-feed.json"
    monkeypatch.setenv("PROPSTEIN_LIVE_FEED_PATH", str(missing_feed))
    client = TestClient(app)
    status = client.get("/api/billing/readiness")
    assert status.status_code == 200
    assert status.json()["ready"] is False
    assert status.json()["checks"]["feed_fresh"] is False
    checkout = client.post("/api/billing/create-checkout-session",
                           headers={"X-PropStein-API-Key": "test-user"},
                           json={"plan": "pro_monthly"})
    assert checkout.status_code == 503
    assert "frozen" in checkout.json()["detail"].lower()


def test_product_events_store_only_aggregates(monkeypatch, tmp_path):
    db = tmp_path / "events.sqlite3"
    monkeypatch.setenv("PROPSTEIN_PRODUCT_EVENTS_DB", str(db))
    client = TestClient(app)
    assert client.post("/api/events", json={"event": "widget_load", "tier": "research"}).status_code == 204
    assert client.post("/api/events", json={"event": "widget_load", "tier": "research"}).status_code == 204
    assert client.post("/api/events", json={"event": "widget_load", "email": "private@example.com"}).status_code == 400
    assert client.post("/api/events", json={"event": "price_rail_click", "operator": "unknown"}).status_code == 400
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT event, tier, operator, count FROM daily_counts").fetchall() == [
            ("widget_load", "research", "", 2)
        ]
