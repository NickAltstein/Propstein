"""Fail-closed commercial readiness and feature access policy.

The commercial switch is intentionally not sufficient to open checkout.
Prospective evidence approval and a live, receipt-verified slate are separate
requirements. Both are checked on each checkout attempt.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config" / "monetization.json"
FEED_PATH = ROOT / "viewer" / "live_odds_feed.json"
_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_RANK = {"research": 0, "pro": 1, "quant": 2}


def policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def allowed(feature: str, tier: str, config: dict[str, Any] | None = None) -> bool:
    rules = (config or policy())["tiers"]
    rank = _RANK.get(tier, -1)
    return any(feature in features and rank >= _RANK[name] for name, features in rules.items())


def require_feature(feature: str, tier: str) -> None:
    if not allowed(feature, tier):
        from fastapi import HTTPException
        raise HTTPException(403, f"{feature} requires a higher tier")


def _age_seconds(value: Any, now: datetime) -> float | None:
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return None
        age = (now - observed.astimezone(timezone.utc)).total_seconds()
        return age if age >= 0 else None
    except (TypeError, ValueError):
        return None


def readiness(feed: dict[str, Any] | None = None, *, now: datetime | None = None,
              config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = (config or policy())["readiness"]
    now = now or datetime.now(timezone.utc)
    if feed is None:
        try:
            feed = json.loads(Path(os.environ.get("PROPSTEIN_LIVE_FEED_PATH", FEED_PATH)).read_text())
        except (OSError, ValueError):
            feed = {}
    if not isinstance(feed, dict):
        feed = {}
    quotes = feed.get("quotes") if isinstance(feed.get("quotes"), list) else []
    max_age = cfg["max_feed_age_seconds"]
    feed_age = _age_seconds(feed.get("updated_at"), now)
    feed_ok = str(feed.get("feed_status", "")).upper() in {"LIVE_VERIFIED", "VERIFIED"}
    verified = [q for q in quotes if isinstance(q, dict)
                and str(q.get("verification_status", "")).upper() == "VERIFIED"
                and _HASH.fullmatch(str(q.get("content_hash", "")))
                and _age_seconds(q.get("observed_at"), now) is not None
                and _age_seconds(q.get("observed_at"), now) < max_age]
    qualified = []
    for q in verified:
        evaluation = q.get("evaluation")
        if not isinstance(evaluation, dict) or evaluation.get("is_qualified") is not True:
            continue
        try:
            edge = float(evaluation.get("prob_edge"))
        except (TypeError, ValueError):
            continue
        if edge >= cfg["min_edge_fraction"]:
            qualified.append(q)
    checks = {
        "feed_healthy": feed_ok,
        "feed_fresh": feed_age is not None and feed_age < max_age,
        "verified_books": len({str(q.get("book", "")).lower() for q in verified if q.get("book")}) >= cfg["min_verified_books"],
        "verified_qualified_plays": len(qualified) >= cfg["min_verified_qualified_plays"],
    }
    return {"ready": all(checks.values()), "checks": checks,
            "feed_age_seconds": round(feed_age) if feed_age is not None else None,
            "verified_qualified_count": len(qualified), "checked_at": now.isoformat()}


def checkout_ready() -> dict[str, Any]:
    result = readiness()
    # A machine-readable prospective evidence report is required. The
    # environment variable only locates it; a switch alone cannot open sales.
    evidence_path = os.environ.get("PROPSTEIN_PROSPECTIVE_EVIDENCE_PATH")
    evidence = {}
    if evidence_path:
        try:
            evidence = json.loads(Path(evidence_path).read_text())
        except (OSError, ValueError):
            pass
    sports = evidence.get("sports") if isinstance(evidence, dict) else None
    if not isinstance(sports, dict):
        sports = {}
    def sport_pass(sport: Any) -> bool:
        if not isinstance(sport, dict):
            return False
        try:
            return (int(sport.get("independent_signals")) >= 600
                    and float(sport.get("mean_price_clv_pp")) >= 1.0
                    and float(sport.get("clustered_lower_bound_pp")) > 0)
        except (TypeError, ValueError):
            return False
    result["checks"]["prospective_evidence"] = (
        evidence.get("schema_version") == "prospective-gate.v1"
        and evidence.get("approved") is True
        and evidence.get("compliance_approved") is True
        and all(sport_pass(s) for s in sports.values())
        and {"nfl", "nba"}.issubset(sports.keys())
    )
    result["checks"]["checkout_configured"] = policy().get("checkout_enabled") is True
    result["ready"] = all(result["checks"].values())
    return result
