#!/usr/bin/env python3
"""Named NFL slate states shared by the Polymarket poller, the props sweep,
the odds freshness check and the feed-age watcher.

A null default slate is not an error. Every week, from the night after
Monday's game until the next week is ingested, autopilot's default_for()
correctly returns None. Before this module the jobs either raised on that
state (an alarm every week for an expected state) or returned early and
exited 0 (the quiet success that hid an empty slate for nine hours). Each
job now classifies the slate the same way and reports one of:

  NO_CURRENT_SLATE  Between weeks: the last week is done and the next is not
                    ingested yet. Report it. Alarm only once it outlasts the
                    next ingest run plus a buffer (``overdue``).
  SLATE_BLOCKED     The slate exists but the context gate excluded every
                    game. Report it with the exclusion reasons; no alarm.
  NO_SLATE_ROWS     The slate is meant to be live but has no rows to price.
                    That is a pipeline bug: fail and alert immediately.
  OK                The slate has priced rows.

``UNKNOWN`` is returned only when slate-index.json does not exist at all
(a bare test viewer); callers keep their earlier behaviour for it.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NO_CURRENT_SLATE = "NO_CURRENT_SLATE"
SLATE_BLOCKED = "SLATE_BLOCKED"
NO_SLATE_ROWS = "NO_SLATE_ROWS"
OK = "OK"
UNKNOWN = "UNKNOWN"

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
VIEWER = ROOT / "viewer"

#: daily_update.yml runs autopilot (which writes slate-index.json) at 11:00 UTC.
DEFAULT_INGEST_UTC_HOUR = 11
#: How long past that run a missing slate stays an expected state.
DEFAULT_NO_CURRENT_SLATE_BUFFER_HOURS = 6.0

CONTEXT_MISSING = "CONTEXT_MISSING"


def _stamp(when: datetime | None) -> str | None:
    return None if when is None else when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _result(state: str, detail: str, **extra) -> dict:
    out = {
        "state": state,
        "detail": detail,
        "slate_key": None,
        "since": None,
        "alarm_after": None,
        "overdue": False,
        "exclusions": [],
        "priced_rows": 0,
        "excluded_rows": 0,
        "markets": None,
    }
    out.update(extra)
    return out


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _last_gameday(viewer: Path, entry: dict) -> date | None:
    games_file = entry.get("games")
    if not games_file:
        return None
    try:
        games = _read_json(viewer / games_file)
        days = sorted(str(g["gameday"])[:10] for g in games if isinstance(g, dict) and g.get("gameday"))
        return date.fromisoformat(days[-1]) if days else None
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def between_weeks_since(viewer: Path, index: dict) -> datetime | None:
    """When the current between-weeks stretch began.

    autopilot marks a slate historical once ``last_gameday + 1 day`` (ET
    midnight) has passed, so the stretch starts at that instant for the
    newest historical slate. Falls back to the index's generated_at."""
    slates = ((index.get("sports") or {}).get("nfl") or {}).get("slates") or {}
    ends = []
    for entry in slates.values():
        if isinstance(entry, dict) and entry.get("historical"):
            last = _last_gameday(viewer, entry)
            if last is not None:
                ends.append(datetime.combine(last + timedelta(days=1), time(0, 0), tzinfo=ET))
    if ends:
        return max(ends).astimezone(timezone.utc)
    generated = index.get("generated_at")
    if generated:
        try:
            parsed = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def no_current_slate_deadline(since: datetime, ingest_utc_hour: int = DEFAULT_INGEST_UTC_HOUR,
                              buffer_hours: float = DEFAULT_NO_CURRENT_SLATE_BUFFER_HOURS) -> datetime:
    """The first scheduled ingest run at or after ``since``, plus the buffer."""
    since = since.astimezone(timezone.utc)
    run = since.replace(hour=int(ingest_utc_hour), minute=0, second=0, microsecond=0)
    if run < since:
        run += timedelta(days=1)
    return run + timedelta(hours=float(buffer_hours))


def _is_excluded_row(row: dict) -> bool:
    return (row.get("context_status") == CONTEXT_MISSING
            or row.get("position") == CONTEXT_MISSING)


def _exclusions(rows: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for row in rows:
        if not _is_excluded_row(row):
            continue
        gid = str(row.get("game_id") or row.get("matchup") or "?")
        seen.setdefault(gid, {
            "game_id": gid,
            "matchup": row.get("matchup") or "",
            "reason": row.get("excl_reason") or row.get("reason") or CONTEXT_MISSING,
        })
    return list(seen.values())


def classify(viewer: Path = VIEWER, *, now: datetime | None = None, markets: int | None = None,
             ingest_utc_hour: int = DEFAULT_INGEST_UTC_HOUR,
             buffer_hours: float = DEFAULT_NO_CURRENT_SLATE_BUFFER_HOURS) -> dict:
    """Classify the default NFL slate. Never raises.

    ``markets`` is the number of live prop markets the caller saw (the
    poller passes its Polymarket event count); it only enriches the detail.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    index_path = viewer / "feeds" / "slate-index.json"
    if not index_path.is_file():
        return _result(UNKNOWN, "slate-index.json not found", markets=markets)
    try:
        index = _read_json(index_path)
        nfl = (index.get("sports") or {}).get("nfl") or {}
        slates = nfl.get("slates") or {}
    except (OSError, ValueError, AttributeError) as exc:
        return _result(NO_SLATE_ROWS, f"slate-index.json unreadable: {exc}", markets=markets)

    default_key = nfl.get("default_slate")
    if not default_key:
        since = between_weeks_since(viewer, index)
        if since is None:
            return _result(NO_CURRENT_SLATE,
                           "no current NFL slate and no record of when the last one ended; "
                           "treating as overdue", overdue=True, markets=markets)
        deadline = no_current_slate_deadline(since, ingest_utc_hour, buffer_hours)
        overdue = now > deadline
        hours = (now - since).total_seconds() / 3600
        detail = (f"between weeks for {max(0.0, hours):.1f}h (since {_stamp(since)}); "
                  f"next slate expected by {_stamp(deadline)}")
        if overdue:
            detail += " and is overdue"
        return _result(NO_CURRENT_SLATE, detail, since=_stamp(since), alarm_after=_stamp(deadline),
                       overdue=overdue, markets=markets)

    entry = slates.get(default_key)
    if not isinstance(entry, dict):
        return _result(NO_SLATE_ROWS, f"default slate {default_key!r} is not listed in slate-index.json",
                       slate_key=default_key, markets=markets)

    if entry.get("actionLocked") or entry.get("modelContextStatus") == "BLOCKED_UNTIMESTAMPED":
        reasons = [f"modelContextStatus={entry.get('modelContextStatus')}"] if entry.get("modelContextStatus") else []
        if entry.get("actionLocked"):
            reasons.append("actionLocked")
        return _result(SLATE_BLOCKED,
                       f"slate {default_key!r} locked by the context gate ({', '.join(reasons)})",
                       slate_key=default_key, markets=markets,
                       exclusions=[{"game_id": "*", "matchup": "whole slate",
                                    "reason": entry.get("state") or ", ".join(reasons)}])

    slate_file = entry.get("slate")
    if not slate_file:
        return _result(NO_SLATE_ROWS, f"slate {default_key!r} has no slate file",
                       slate_key=default_key, markets=markets)
    try:
        rows = _read_json(viewer / slate_file)
    except (OSError, ValueError) as exc:
        return _result(NO_SLATE_ROWS, f"slate file {slate_file!r} unreadable: {exc}",
                       slate_key=default_key, markets=markets)
    if not isinstance(rows, list):
        return _result(NO_SLATE_ROWS, f"slate file {slate_file!r} is not a list of rows",
                       slate_key=default_key, markets=markets)

    rows = [r for r in rows if isinstance(r, dict)]
    excluded = [r for r in rows if _is_excluded_row(r)]
    priced = [r for r in rows if not _is_excluded_row(r) and r.get("player_id")]
    exclusions = _exclusions(excluded)
    counts = dict(slate_key=default_key, markets=markets, exclusions=exclusions,
                  priced_rows=len(priced), excluded_rows=len(excluded))
    if not priced and exclusions:
        reasons = "; ".join(f"{e['matchup'] or e['game_id']}: {e['reason']}" for e in exclusions)
        return _result(SLATE_BLOCKED,
                       f"slate {default_key!r}: context gate excluded all {len(exclusions)} game(s) ({reasons})",
                       **counts)
    if not priced:
        market_note = "" if markets is None else f" while {markets} live prop market(s) exist"
        return _result(NO_SLATE_ROWS, f"slate {default_key!r} has 0 priced rows{market_note}", **counts)
    detail = f"slate {default_key!r} has {len(priced)} priced row(s)"
    if exclusions:
        detail += f"; {len(exclusions)} game(s) excluded by the context gate"
    return _result(OK, detail, **counts)


def summary(status: dict) -> str:
    """One log line: STATE: detail."""
    return f"{status['state']}: {status['detail']}"
