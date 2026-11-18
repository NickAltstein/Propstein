"""Capture scheduler and pre-start timing utilities.

Determines pre-start status (quoted_at < scheduled_start) and generates
capture plans that ensure every game on a slate has a scheduled capture
prior to its start.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence


def parse_utc_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def compute_pre_start_attribute(
    quoted_at: Any, scheduled_start: Any
) -> tuple[bool | str, float | None]:
    """Compute pre_start status and offset in seconds to scheduled start.

    pre_start:
      true    = quoted_at < scheduled_start (both known, UTC)
      false   = quoted_at >= scheduled_start
      unknown = either timestamp missing or unparseable
    offset_to_scheduled_start_sec:
      quoted_at - scheduled_start in seconds (negative = pre-start)
    """
    q_dt = parse_utc_dt(quoted_at)
    s_dt = parse_utc_dt(scheduled_start)
    if q_dt is None or s_dt is None:
        return "unknown", None

    offset_sec = (q_dt - s_dt).total_seconds()
    if offset_sec < 0:
        return True, offset_sec
    return False, offset_sec


def timing_diagnostics(quoted_at: Any, scheduled_start: Any) -> tuple[str, ...]:
    """Explain timing evidence without changing ledger reason codes."""
    quote_time = parse_utc_dt(quoted_at)
    start_time = parse_utc_dt(scheduled_start)
    reasons = []
    if quote_time is None:
        reasons.append("QUOTE_TIME_MISSING_OR_INVALID")
    if start_time is None:
        reasons.append("START_TIME_MISSING_OR_INVALID")
    if not reasons and quote_time >= start_time:
        reasons.append("AFTER_START")
    return tuple(reasons)


def generate_capture_plan(
    games: Sequence[dict[str, Any]],
    lead_minutes: int = 15,
) -> list[dict[str, Any]]:
    """Produce a capture plan ensuring every game on the slate is covered before start.

    Groups games by start time and schedules a capture run at least `lead_minutes`
    prior to each tipoff/kickoff window.
    """
    by_start: dict[str, list[dict[str, Any]]] = {}
    unmatched: list[dict[str, Any]] = []

    for g in games:
        start_str = g.get("tipoff_utc") or g.get("scheduled_start") or g.get("kickoff_utc")
        s_dt = parse_utc_dt(start_str)
        if s_dt is None:
            # Fallback to gameday + gametime if present
            if g.get("gameday") and g.get("gametime"):
                from zoneinfo import ZoneInfo
                et_dt = datetime.strptime(
                    f"{g['gameday']} {g['gametime']}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=ZoneInfo("America/New_York"))
                s_dt = et_dt.astimezone(timezone.utc)

        if s_dt is not None:
            key = s_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            by_start.setdefault(key, []).append(g)
        else:
            unmatched.append(g)

    plan = []
    for start_iso in sorted(by_start.keys()):
        s_dt = parse_utc_dt(start_iso)
        planned_dt = s_dt - timedelta(minutes=lead_minutes)
        game_list = by_start[start_iso]
        game_ids = [g.get("game_id") for g in game_list if g.get("game_id")]
        plan.append({
            "planned_capture_utc": planned_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "target_start_utc": start_iso,
            "lead_minutes": lead_minutes,
            "games_covered": game_ids,
            "game_count": len(game_ids),
        })

    return plan
