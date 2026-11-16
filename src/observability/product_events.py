"""Aggregate product events without users, IPs, URLs or raw payload storage."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

EVENTS = frozenset({"tier_upgrade", "paywall_encounter", "alert_engagement",
                    "price_rail_open", "price_rail_click", "widget_load",
                    "share_graphic_generated", "fail_closed_share"})
TIERS = frozenset({"research", "pro", "quant", "none"})


def record_event(event: str, tier: str = "none", operator: str = "") -> None:
    if event not in EVENTS or tier not in TIERS:
        raise ValueError("Unknown event or tier")
    if operator:
        if event != "price_rail_click":
            raise ValueError("Operator is only allowed on price clicks")
        import json
        root = Path(__file__).resolve().parents[2]
        config = json.loads((root / "viewer/operator-jurisdictions.json").read_text())
        if operator not in config.get("operators", {}):
            raise ValueError("Operator has no approved ID")
    db = Path(os.environ.get("PROPSTEIN_PRODUCT_EVENTS_DB", "/data/product_events.sqlite3"))
    db.parent.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).date().isoformat()
    with sqlite3.connect(db, timeout=5) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS daily_counts (day TEXT NOT NULL, event TEXT NOT NULL, tier TEXT NOT NULL, operator TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(day,event,tier,operator))")
        connection.execute("INSERT INTO daily_counts VALUES (?,?,?,?,1) ON CONFLICT(day,event,tier,operator) DO UPDATE SET count=count+1", (day, event, tier, operator))
