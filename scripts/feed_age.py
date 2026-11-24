#!/usr/bin/env python3
"""Per-sport feed age for the published board.

The pollers record a success only when a fetch returns at least one quote.
An empty or zero-event response is a failure and does not move
last_success_at. This checker does not poll vendors. A separate scheduled
workflow runs it, so stopping the poller still ages the last success and
alerts.

Thresholds live in config/feed_age.json:
  degraded when the feed is older than degraded_after_hours and a slate
  starts inside upcoming_window_hours (or is already in progress);
  critical at critical_after_hours;
  no alert when no slate is pending (NBA off-days, NFL Tue/Wed).

NFL also reports a named slate state (scripts/slate_state.py):
  NO_CURRENT_SLATE  between weeks; suppressed until the next ingest run plus
                    no_current_slate_buffer_hours, then critical;
  SLATE_BLOCKED     context gate excluded every game; suppressed, with the
                    exclusion reasons in the status file;
  NO_SLATE_ROWS     live slate with nothing to price; critical at once.

A SLATE_BLOCKED slate raises a separate BLOCKED SLATE alarm (the feed
level is unchanged) when it has been blocked longer than
blocked_slate_alarm_hours, or as soon as a kickoff in that slate is within
blocked_slate_kickoff_hours: a block that starts 10 hours before a
Thursday game would otherwise never page in time. Its
start time is the poller's heartbeat state_since; if the poller has not
confirmed the block, slate-index.json's generated_at is used as a lower
bound, so a block alarms even when the poller is down.

The NFL poller also records its last fetch counters. skipped_no_book (a
parsed market with no CLOB book) is reported only; skipped_unparsed > 0 (an
in-scope market whose outcome tokens could not be read) raises a parse alarm
on every check until a fetch comes back clean. It does not change the feed
level: the parsed quotes are still good.

Paging uses env secrets and never a URL in the repo:
  ALARM_WEBHOOK_URL    optional POST of {"content", "text"} (Discord or Slack)
  FEED_AGE_UPTIME_URL  optional GET each check. Point an external monitor at
                       the same secret. If this workflow stops, that monitor
                       pages even though nothing here is left to fail.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import slate_state  # noqa: E402

VIEWER = ROOT / "viewer"
ET = ZoneInfo("America/New_York")
HEARTBEAT_NAME = "feeds/feed_heartbeat.json"
DEFAULT_CONFIG = {
    "degraded_after_hours": 2.0,
    "critical_after_hours": 12.0,
    "upcoming_window_hours": 24.0,
    "in_progress_hours": 4.0,
    "schedule_observation_max_hours": 30.0,
    "nfl_off_weekdays": [1, 2],
    "sports": ["nba", "nfl"],
    "nfl_ingest_utc_hour": slate_state.DEFAULT_INGEST_UTC_HOUR,
    "no_current_slate_buffer_hours": slate_state.DEFAULT_NO_CURRENT_SLATE_BUFFER_HOURS,
    "blocked_slate_alarm_hours": 12.0,
    "blocked_slate_kickoff_hours": 4.0,
}
NBA_ODDS_RE = re.compile(r"nba_odds_(\d{4}-\d{2}-\d{2})\.json$")
EMPTY_ERROR = "empty/zero-event response"


def parse_time(value) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_config(path: Path | None = None, override: dict | None = None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    file = path or (ROOT / "config" / "feed_age.json")
    if file.is_file():
        cfg.update(json.loads(file.read_text(encoding="utf-8")))
    if override:
        cfg.update(override)
    cfg["nfl_off_weekdays"] = [int(d) for d in cfg["nfl_off_weekdays"]]
    return cfg


def nba_in_season(day: date) -> bool:
    return (day.month == 10 and day.day >= 20) or day.month in (11, 12, 1, 2, 3, 4, 5) or (
        day.month == 6 and day.day <= 20)


def _blank_sport() -> dict:
    return {
        "last_success_at": None,
        "last_success_quotes": 0,
        "last_error": None,
        "last_error_at": None,
        "last_attempt_at": None,
        "schedule": {"observed_at": None, "state": None, "starts": []},
    }


def load_heartbeat(path: Path) -> dict:
    if not path.is_file():
        return {"schema_version": "feed-heartbeat.v1", "sports": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("schema_version", "feed-heartbeat.v1")
    data.setdefault("sports", {})
    return data


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def note_fetch(sport: str, *, ok: bool, quotes: int = 0, error: str | None = None,
               schedule_state: str | None = None, starts: list[str] | None = None,
               path: Path | None = None, now: datetime | None = None,
               slate: dict | None = None, collisions: int | None = None,
               counters: dict | None = None) -> dict:
    """Record one fetch. Zero quotes are a failure even when ok=True."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    quotes = int(quotes or 0)
    if ok and quotes < 1:
        ok = False
        error = error or EMPTY_ERROR
    hb_path = path or (VIEWER / HEARTBEAT_NAME)
    heartbeat = load_heartbeat(hb_path)
    sport_row = heartbeat["sports"].setdefault(sport, _blank_sport())
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    sport_row["last_attempt_at"] = stamp
    if ok:
        sport_row["last_success_at"] = stamp
        sport_row["last_success_quotes"] = quotes
    elif error or schedule_state != "none":
        sport_row["last_error"] = error or EMPTY_ERROR
        sport_row["last_error_at"] = stamp
    if schedule_state:
        sport_row["schedule"] = {
            "observed_at": stamp,
            "state": schedule_state,
            "starts": list(starts or []),
        }
    if slate:
        # state_since: when this state (for this slate) was first recorded.
        # Kept across notes while the state holds; reset when it changes.
        prev = sport_row.get("slate_state") or {}
        same = (prev.get("state") == slate.get("state")
                and prev.get("slate_key") == slate.get("slate_key")
                and prev.get("state_since"))
        sport_row["slate_state"] = {
            "observed_at": stamp,
            "state_since": prev["state_since"] if same else stamp,
            **{k: slate.get(k) for k in ("state", "detail", "slate_key", "since", "alarm_after",
                                         "overdue", "exclusions", "priced_rows", "markets")},
        }
    if collisions is not None:
        sport_row["line_collisions_excluded"] = int(collisions)
    if counters is not None:
        if counters:
            sport_row["fetch_counters"] = {"observed_at": stamp, **counters}
        else:
            sport_row.pop("fetch_counters", None)
    save_json(hb_path, heartbeat)
    return heartbeat


def _nba_files(viewer: Path):
    best = None
    newest_empty = None
    for path in viewer.glob("nba_odds_*.json"):
        if not NBA_ODDS_RE.fullmatch(path.name):
            continue
        try:
            feed = json.loads(path.read_text(encoding="utf-8"))
            observed = parse_time(feed["updated_at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if feed.get("_fixture") is not False:
            continue
        quotes = int(feed.get("total_quotes") or 0)
        if quotes >= 1 and (best is None or observed > best[0]):
            best = (observed, quotes)
        elif quotes < 1 and (newest_empty is None or observed > newest_empty):
            newest_empty = observed
    return best, newest_empty


def _nfl_file(viewer: Path):
    path = viewer / "live_odds_feed.json"
    if not path.is_file():
        return None, None
    try:
        feed = json.loads(path.read_text(encoding="utf-8"))
        observed = parse_time(feed["updated_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None
    count = int(feed.get("polymarket_quotes_count") or 0)
    if count < 1:
        count = len(feed.get("quotes") or [])
    if count >= 1:
        return (observed, count), None
    return None, observed


def _game_starts(viewer: Path, sport: str) -> list[datetime]:
    starts = []
    if sport == "nba":
        for path in viewer.glob("nba_games_*.json"):
            try:
                games = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(games, list):
                continue
            for game in games:
                tip = game.get("tipoff_utc")
                if tip:
                    try:
                        starts.append(parse_time(tip))
                    except ValueError:
                        continue
        return starts
    index_path = viewer / "feeds" / "slate-index.json"
    if not index_path.is_file():
        return starts
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        nfl = index.get("sports", {}).get("nfl", {})
        default_key = nfl.get("default_slate")
        if not default_key:
            return starts
        slates = nfl.get("slates", {})
        if default_key not in slates:
            return starts
        slate = slates[default_key]
        games_file = slate.get("games")
        if not games_file or not (viewer / games_file).is_file():
            return starts
        games = json.loads((viewer / games_file).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        return starts
    for game in games:
        day = str(game.get("gameday") or "")[:10]
        clock = str(game.get("gametime") or "12:00")
        if len(clock) == 5:
            clock += ":00"
        try:
            local = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=ET)
        except ValueError:
            continue
        starts.append(local.astimezone(timezone.utc))
    return starts


def _heartbeat_starts(row: dict) -> list[datetime]:
    starts = []
    for value in (row.get("schedule") or {}).get("starts") or []:
        try:
            starts.append(parse_time(value))
        except ValueError:
            continue
    return starts


def slate_disposition(sport: str, now: datetime, starts: list[datetime],
                      schedule: dict, cfg: dict) -> str:
    window = timedelta(hours=float(cfg["upcoming_window_hours"]))
    grace = timedelta(hours=float(cfg["in_progress_hours"]))
    if any(now - grace < start <= now + window for start in starts):
        return "pending"
    observed = schedule.get("observed_at") if schedule else None
    fresh = False
    if observed:
        try:
            age_h = (now - parse_time(observed)).total_seconds() / 3600
            fresh = age_h <= float(cfg["schedule_observation_max_hours"])
        except ValueError:
            fresh = False
    if fresh and schedule.get("state") == "none":
        return "none"
    et = now.astimezone(ET)
    if sport == "nfl" and et.weekday() in cfg["nfl_off_weekdays"]:
        return "none"
    if sport == "nba" and not nba_in_season(et.date()):
        return "none"
    return "pending"


def _later(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return left if left >= right else right


def evaluate_sport(sport: str, viewer: Path, heartbeat: dict, now: datetime, cfg: dict) -> dict:
    row = (heartbeat.get("sports") or {}).get(sport) or _blank_sport()
    if sport == "nba":
        file_ok, file_empty = _nba_files(viewer)
    else:
        file_ok, file_empty = _nfl_file(viewer)
    hb_ok = None
    if row.get("last_success_at") and int(row.get("last_success_quotes") or 0) >= 1:
        try:
            hb_ok = parse_time(row["last_success_at"])
        except ValueError:
            hb_ok = None
    file_time = file_ok[0] if file_ok else None
    success = _later(hb_ok, file_time)
    quotes = int(row.get("last_success_quotes") or 0)
    if file_ok and (file_time == success):
        quotes = file_ok[1]
    last_error = row.get("last_error")
    if file_empty is not None and (success is None or file_empty > success) and not last_error:
        last_error = EMPTY_ERROR
    starts = _game_starts(viewer, sport) + _heartbeat_starts(row)
    disposition = slate_disposition(sport, now, starts, row.get("schedule") or {}, cfg)
    age = None if success is None else (now - success).total_seconds() / 3600
    state = None
    if sport == "nfl":
        state = slate_state.classify(viewer, now=now,
                                     ingest_utc_hour=int(cfg["nfl_ingest_utc_hour"]),
                                     buffer_hours=float(cfg["no_current_slate_buffer_hours"]))
    named = state["state"] if state else None
    if named == slate_state.NO_SLATE_ROWS or (named == slate_state.NO_CURRENT_SLATE and state["overdue"]):
        level = "critical"
        last_error = slate_state.summary(state)
    elif named in (slate_state.NO_CURRENT_SLATE, slate_state.SLATE_BLOCKED):
        level = "suppressed"
    elif disposition != "pending":
        level = "suppressed"
    elif age is None or age >= float(cfg["critical_after_hours"]):
        level = "critical"
    elif age >= float(cfg["degraded_after_hours"]):
        level = "degraded"
    else:
        level = "ok"
    stale_since = None if success is None else success.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {
        "sport": sport,
        "level": level,
        "fail_closed": level == "critical",
        "show_edges": level != "critical",
        "stale_since": stale_since,
        "age_hours": None if age is None else round(age, 2),
        "last_error": last_error,
        "quotes": quotes,
        "slate": disposition,
    }
    if state:
        out["slate_state"] = {k: state[k] for k in ("state", "detail", "slate_key", "since",
                                                    "alarm_after", "overdue", "exclusions",
                                                    "priced_rows")}
        if named == slate_state.SLATE_BLOCKED:
            since, source = blocked_since(viewer, row, state)
            limit = float(cfg["blocked_slate_alarm_hours"])
            hours = None if since is None else (now - since).total_seconds() / 3600
            # Kickoffs come from the blocked slate's own games file.
            window = timedelta(hours=float(cfg["blocked_slate_kickoff_hours"]))
            upcoming = sorted(t for t in _game_starts(viewer, "nfl") if now <= t <= now + window)
            kickoff = upcoming[0] if upcoming else None
            too_long = hours is not None and hours > limit
            out["slate_state"].update({
                "blocked_since": None if since is None else since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "blocked_since_source": source,
                "blocked_hours": None if hours is None else round(hours, 1),
                "blocked_limit_hours": limit,
                "blocked_next_kickoff": None if kickoff is None else kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "blocked_overdue": too_long or kickoff is not None,
                "blocked_reasons": [r for r, hit in (("duration", too_long), ("kickoff", kickoff is not None)) if hit],
            })
        if "line_collisions_excluded" in row:
            out["line_collisions_excluded"] = row["line_collisions_excluded"]
    if row.get("fetch_counters"):
        out["fetch_counters"] = row["fetch_counters"]
    return out


def alert_message(row: dict) -> str:
    age = "unknown" if row["age_hours"] is None else f"{row['age_hours']:.1f} hours"
    err = row["last_error"] or "(none recorded)"
    state = (row.get("slate_state") or {}).get("state")
    tag = f" [{state}]" if state and state not in (slate_state.OK, slate_state.UNKNOWN) else ""
    return (f"PropStein feed age {row['level'].upper()}: {row['sport'].upper()}{tag} "
            f"data age {age}; last error: {err}")


def blocked_since(viewer: Path, row: dict, state: dict) -> tuple[datetime | None, str]:
    """When the current SLATE_BLOCKED stretch began, and where that came from."""
    hb = row.get("slate_state") or {}
    if (hb.get("state") == slate_state.SLATE_BLOCKED and hb.get("slate_key") == state.get("slate_key")
            and hb.get("state_since")):
        try:
            return parse_time(hb["state_since"]), "poller heartbeat"
        except ValueError:
            pass
    try:
        index = json.loads((viewer / "feeds" / "slate-index.json").read_text(encoding="utf-8"))
        return parse_time(index["generated_at"]), "slate-index generated_at (poller has not confirmed the block)"
    except (OSError, ValueError, KeyError, TypeError):
        return None, "unknown"


def blocked_alarm(row: dict) -> str | None:
    """Alert line when a slate has been SLATE_BLOCKED longer than the limit."""
    st = row.get("slate_state") or {}
    if st.get("state") != slate_state.SLATE_BLOCKED or not st.get("blocked_overdue"):
        return None
    reasons = "; ".join(f"{e.get('matchup') or e.get('game_id')}: {e.get('reason')}"
                        for e in (st.get("exclusions") or [])) or "(no reasons recorded)"
    kickoff = (f"; next kickoff {st['blocked_next_kickoff']} is inside the warning window"
               if st.get("blocked_next_kickoff") else "")
    return (f"PropStein BLOCKED SLATE: {row['sport'].upper()} slate {st.get('slate_key')!r} has been "
            f"SLATE_BLOCKED for {st.get('blocked_hours')}h (since {st.get('blocked_since')}, "
            f"from {st.get('blocked_since_source')}; limit {st.get('blocked_limit_hours'):g}h){kickoff}; "
            f"exclusions: {reasons}")


def parse_alarm(row: dict) -> str | None:
    """Alert line when the last fetch could not read an in-scope market."""
    counters = row.get("fetch_counters") or {}
    unparsed = int(counters.get("skipped_unparsed") or 0)
    if unparsed < 1:
        return None
    ids = ", ".join(counters.get("unparsed_market_ids") or []) or "(ids not recorded)"
    return (f"PropStein PARSE ALARM: {row['sport'].upper()} last fetch "
            f"({counters.get('observed_at') or 'time unknown'}) skipped {unparsed} in-scope "
            f"market(s) with unreadable outcome tokens; market ids: {ids}")


def check(viewer: Path, heartbeat: dict | None = None, now: datetime | None = None,
          cfg: dict | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cfg = cfg or load_config()
    heartbeat = heartbeat if heartbeat is not None else load_heartbeat(viewer / HEARTBEAT_NAME)
    sports = [evaluate_sport(sport, viewer, heartbeat, now, cfg) for sport in cfg["sports"]]
    alerts = [alert_message(row) for row in sports if row["level"] in ("degraded", "critical")]
    alerts += [line for line in (parse_alarm(row) for row in sports) if line]
    alerts += [line for line in (blocked_alarm(row) for row in sports) if line]
    return {
        "schema_version": "feed-status.v1",
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sports": {row["sport"]: {k: v for k, v in row.items() if k != "sport"} for row in sports},
        "alerts": alerts,
    }


def _request(url: str, data: bytes | None = None, headers: dict | None = None) -> None:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def deliver(alerts: list[str], webhook: str | None, uptime: str | None,
            opener=_request) -> list[str]:
    """Page and ping. Returns warnings. URLs are never included in warnings."""
    warnings = []
    if webhook:
        for message in alerts:
            body = json.dumps({"content": message, "text": message}).encode()
            try:
                opener(webhook, body, {"Content-Type": "application/json"})
            except Exception as exc:
                warnings.append(f"webhook post failed: {type(exc).__name__}")
    elif alerts:
        warnings.append("ALARM_WEBHOOK_URL is not set; alert is in the job log only")
    if uptime:
        try:
            opener(uptime)
        except Exception as exc:
            warnings.append(f"uptime ping failed: {type(exc).__name__}")
    return warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--viewer", type=Path, default=VIEWER)
    parser.add_argument("--now")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--status-out", type=Path)
    parser.add_argument("--heartbeat", type=Path)
    args = parser.parse_args(argv)
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    cfg = load_config(args.config)
    hb_path = args.heartbeat or (args.viewer / HEARTBEAT_NAME)
    report = check(args.viewer, load_heartbeat(hb_path), now, cfg)
    status = {k: v for k, v in report.items() if k != "alerts"}
    if args.status_out:
        save_json(args.status_out, status)
    for line in report["alerts"]:
        print(line)
    warnings = deliver(report["alerts"], os.environ.get("ALARM_WEBHOOK_URL", "").strip() or None,
                       os.environ.get("FEED_AGE_UPTIME_URL", "").strip() or None)
    for warning in warnings:
        print(warning, file=sys.stderr)
    return 1 if report["alerts"] else 0


if __name__ == "__main__":
    sys.exit(main())
