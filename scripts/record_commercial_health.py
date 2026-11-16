#!/usr/bin/env python3
"""Record each degraded slate day for later credit/pause review.

Runs after the 15-minute feed refresh. A day stays flagged even if the feed
recovers later. This is a flag, not an automatic billing credit.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "commercial_degraded_days.json"
spec = importlib.util.spec_from_file_location("monetization_policy", ROOT / "src/services/monetization.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
health = module.readiness()
today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
try:
    state = json.loads(OUTPUT.read_text())
except (OSError, ValueError):
    state = {"schema_version": "commercial-health.v1", "degraded_days": []}
if not health["ready"] and today not in state["degraded_days"]:
    state["degraded_days"].append(today)
    state["degraded_days"] = sorted(set(state["degraded_days"]))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(state, indent=2) + "\n")
print(json.dumps({"day": today, "ready": health["ready"], "credit_review_flagged": today in state["degraded_days"], "checks": health["checks"]}))
