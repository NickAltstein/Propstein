"""Fail-closed decision boundary for actionable NFL receiving offers.

This module consumes the published quote and verification decision. It does not
upgrade a model projection, receipt match, or database row into a live offer.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from src.contracts.verification import VerificationConfig, VERIFICATION_CLAUSE_IDS
from src.freeze_slate import EDGE_HURDLE, QUALIFICATION_POLICY_VERSION

POLICY_VERSION = QUALIFICATION_POLICY_VERSION
VERIFICATION_ENGINE = "verification.v1"
ALLOWED_MARKETS = frozenset({"receiving_yards", "receptions"})
WINDOWS = VerificationConfig()


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def american_odds(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 100 <= abs(value) <= 50000 else None


def decide_quote(quote: dict[str, Any], now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    """Return an actionable offer or a diagnostic reason, never a guessed field."""
    if quote.get("verified") is not True or quote.get("verification_status") != "VERIFIED":
        return None, "VERIFICATION_NOT_PASSED"
    if quote.get("verification_engine") != VERIFICATION_ENGINE:
        return None, "VERIFICATION_ENGINE_MISSING_OR_INVALID"
    clauses = quote.get("verification_clauses")
    if (not isinstance(clauses, list) or len(clauses) != len(VERIFICATION_CLAUSE_IDS)
            or any(not isinstance(c, dict) or c.get("passed") is not True for c in clauses)
            or {c.get("clause_id") for c in clauses} != VERIFICATION_CLAUSE_IDS):
        return None, "VERIFICATION_CLAUSES_NOT_PASSED"
    if not isinstance(quote.get("verification_receipt_id"), str) or not quote["verification_receipt_id"].strip():
        return None, "VERIFICATION_RECEIPT_MISSING"
    policy = quote.get("qualification_policy")
    if (not isinstance(policy, dict) or policy.get("version") != POLICY_VERSION
            or finite_number(policy.get("edge_hurdle")) != EDGE_HURDLE
            or policy.get("require_positive_ev") is not True):
        return None, "POLICY_MISSING_OR_INVALID"
    evaluation = quote.get("evaluation")
    if not isinstance(evaluation, dict) or evaluation.get("is_qualified") is not True:
        return None, "QUALIFICATION_NOT_PASSED"
    if quote.get("market") not in ALLOWED_MARKETS:
        return None, "MARKET_UNSUPPORTED"
    for key in ("quote_id", "game_id", "player_id", "player_name", "book"):
        if not isinstance(quote.get(key), str) or not quote[key].strip():
            return None, f"{key.upper()}_MISSING"
    side = evaluation.get("best_side")
    if side not in ("OVER", "UNDER"):
        return None, "SIDE_MISSING_OR_INVALID"
    line = finite_number(quote.get("line"))
    odds = american_odds(quote.get("over_odds" if side == "OVER" else "under_odds"))
    if line is None or line <= 0:
        return None, "BOOK_LINE_MISSING_OR_INVALID"
    if odds is None:
        return None, "BOOK_PRICE_MISSING_OR_INVALID"
    quoted_at = parse_utc(quote.get("quoted_at"))
    start = parse_utc(quote.get("scheduled_start"))
    if quoted_at is None:
        return None, "QUOTE_TIME_MISSING_OR_INVALID"
    if start is None:
        return None, "START_TIME_MISSING_OR_INVALID"
    if quote.get("pre_start") is not True or quoted_at >= start or now >= start:
        return None, "EVENT_STARTED_OR_TIMING_UNVERIFIED"
    now = now.astimezone(timezone.utc)
    until_start = (start - now).total_seconds() / 60
    max_age = (WINDOWS.staleness_near_kickoff_minutes if until_start <= WINDOWS.near_kickoff_window_minutes
               else WINDOWS.staleness_pregame_minutes)
    expires_at = min(start, quoted_at + timedelta(minutes=max_age))
    if quoted_at > now or now >= expires_at:
        return None, "QUOTE_STALE_OR_FUTURE"
    expected_return = finite_number(evaluation.get("expected_return"))
    edge = finite_number(evaluation.get("break_even_adv"))
    prob_edge = finite_number(evaluation.get("prob_edge"))
    stake = finite_number(evaluation.get("actionable_stake"))
    if (expected_return is None or expected_return <= 0 or edge is None or edge <= 0
            or prob_edge is None or prob_edge < EDGE_HURDLE or stake is None or stake <= 0):
        return None, "QUALIFICATION_VALUES_MISSING_OR_INVALID"
    return {
        "quote_id": quote["quote_id"], "game_id": quote["game_id"],
        "player_id": quote["player_id"], "player_name": quote["player_name"],
        "market": quote["market"], "book": quote["book"],
        "line": line, "side": side, "odds": odds,
        "edge_pct": round(edge * 100, 4),
        "expected_return_pct": round(expected_return * 100, 4),
        "quoted_at": quoted_at.isoformat(), "expires_at": expires_at.isoformat(),
        "qualification_policy_version": POLICY_VERSION,
        "verification_engine": VERIFICATION_ENGINE,
        "verified": True, "verification_status": "VERIFIED",
        "receipt_id": quote.get("verification_receipt_id"),
    }, None


def decide_feed(feed: dict[str, Any], now: datetime, *, min_edge: float = 5.0,
                max_edge: float = 12.0, slate: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not isinstance(feed, dict) or (slate and feed.get("slate") != slate):
        return [], []
    if not isinstance(feed.get("quotes"), list):
        return [], [{"quote_id": "", "reason": "FEED_QUOTES_MISSING_OR_INVALID"}]
    edges, diagnostics = [], []
    for quote in feed.get("quotes", []):
        if not isinstance(quote, dict):
            continue
        edge, reason = decide_quote(quote, now)
        if edge is None:
            diagnostics.append({"quote_id": str(quote.get("quote_id") or ""), "reason": reason or "UNKNOWN"})
        elif not min_edge <= edge["expected_return_pct"] <= max_edge:
            diagnostics.append({"quote_id": edge["quote_id"], "reason": "EDGE_OUTSIDE_POLICY_RANGE"})
        else:
            edges.append(edge)
    edges.sort(key=lambda edge: edge["expected_return_pct"], reverse=True)
    return edges, diagnostics
