#!/usr/bin/env python3
"""Count close-capture coverage at the T-5 cutoff from the per-game capture log.

A market has a fresh close when a schema-v2 capture of it was taken in the
`--fresh` minutes before the cutoff (default: between T-20 and T-5). A game
is covered when at least one of its markets has one.

The universe is every game in `data/close_capture_log.jsonl` (a `scheduled`
line is written the first time a session sees a game in the feeds, so a game
that never got a capture round still counts as a miss). Its markets are the
most the log or the captures ever saw for it. `--include-unlogged` adds
games that only appear in the capture file (before the log existed); their
market count is only the markets captured at least once, so treat those
rates as an upper bound.

Usage:
  PYTHONPATH=. python scripts/close_capture_coverage.py [--since 2026-11-27] [--json]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import statistics
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from src.close_quality_capture import _parse_utc, analysis_eligible_captures, load_captures_jsonl


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def coverage(
    *,
    log_entries: Iterable[Mapping[str, Any]],
    captures: Iterable[Mapping[str, Any]],
    cutoff_min: float = 5.0,
    fresh_min: float = 15.0,
    now: datetime | None = None,
    since: datetime | None = None,
    include_unlogged: bool = False,
) -> dict[str, Any]:
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    games: dict[tuple[str, str, str], dict[str, Any]] = {}

    def game(key: tuple[str, str, str], logged: bool) -> dict[str, Any] | None:
        if key not in games:
            if not logged and not include_unlogged:
                return None
            games[key] = {"logged": logged, "markets_known": 0, "seen": set(), "fresh": set(), "rounds": []}
        elif logged:
            games[key]["logged"] = True
        return games[key]

    for entry in log_entries:
        key = (str(entry.get("source")), str(entry.get("game_id")), str(entry.get("scheduled_start")))
        g = game(key, True)
        assert g is not None
        if entry.get("kind") == "scheduled":
            g["markets_known"] = max(g["markets_known"], int(entry.get("markets_in_feed") or 0))
        elif entry.get("kind") == "round":
            g["markets_known"] = max(g["markets_known"], int(entry.get("markets_expected") or 0))
            g["rounds"].append(entry)

    for cap in analysis_eligible_captures(list(captures)):
        start = _parse_utc(cap.get("scheduled_start"))
        captured = _parse_utc(cap.get("captured_at"))
        if start is None or captured is None or captured >= start:
            continue
        key = (str(cap.get("source")), str(cap.get("game_id")), start.isoformat())
        g = game(key, False)
        if g is None:
            continue
        market = str(cap.get("market_id"))
        g["seen"].add(market)
        cut = start - timedelta(minutes=cutoff_min)
        if cut - timedelta(minutes=fresh_min) <= captured <= cut:
            g["fresh"].add(market)

    by_source: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "games": 0, "games_covered": 0, "markets": 0, "markets_fresh": 0, "late_seconds": [], "missed_games": [],
    })
    rows = []
    for (source, game_id, start_iso), g in sorted(games.items(), key=lambda kv: kv[0][2]):
        start = _parse_utc(start_iso)
        if start is None or start - timedelta(minutes=cutoff_min) > now_dt:
            continue  # cutoff not reached yet
        if since and start < since:
            continue
        markets = max(g["markets_known"], len(g["seen"]))
        covered = bool(g["fresh"])
        s = by_source[source]
        s["games"] += 1
        s["games_covered"] += covered
        s["markets"] += markets
        s["markets_fresh"] += len(g["fresh"])
        s["late_seconds"].extend(r["late_seconds"] for r in g["rounds"] if r.get("late_seconds") is not None)
        if not covered:
            s["missed_games"].append(f"{game_id} {start_iso}")
        rows.append({
            "source": source, "game_id": game_id, "scheduled_start": start_iso,
            "logged": g["logged"], "markets": markets, "markets_fresh": len(g["fresh"]),
            "covered": covered, "rounds": len(g["rounds"]),
        })

    summary = {}
    for source, s in sorted(by_source.items()):
        late = s.pop("late_seconds")
        summary[source] = {
            **s,
            "game_coverage_pct": round(100.0 * s["games_covered"] / s["games"], 1) if s["games"] else None,
            "market_coverage_pct": round(100.0 * s["markets_fresh"] / s["markets"], 1) if s["markets"] else None,
            "round_late_seconds_median": round(statistics.median(late), 1) if late else None,
            "round_late_seconds_max": round(max(late), 1) if late else None,
        }
    return {
        "cutoff_minutes": cutoff_min,
        "fresh_window_minutes": fresh_min,
        "generated_at": now_dt.isoformat(),
        "by_source": summary,
        "games": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", type=Path, default=Path("data/close_capture_log.jsonl"))
    parser.add_argument("--captures", type=Path, default=Path("data/close_quality_captures.jsonl"))
    parser.add_argument("--cutoff", type=float, default=5.0, help="minutes before start (default 5)")
    parser.add_argument("--fresh", type=float, default=15.0, help="fresh window before the cutoff (default 15)")
    parser.add_argument("--since", help="only games starting on or after this date (ET, YYYY-MM-DD)")
    parser.add_argument("--include-unlogged", action="store_true", help="also count games only in the capture file")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    since = None
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    records, _, _ = load_captures_jsonl(args.captures)
    result = coverage(
        log_entries=_read_jsonl(args.log),
        captures=records,
        cutoff_min=args.cutoff,
        fresh_min=args.fresh,
        since=since,
        include_unlogged=args.include_unlogged,
    )
    if args.json:
        print(json.dumps(result, indent=2))
        return
    print(f"Fresh close = capture between T-{args.cutoff + args.fresh:g} and T-{args.cutoff:g}")
    for source, s in result["by_source"].items():
        print(
            f"{source}: games {s['games_covered']}/{s['games']} ({s['game_coverage_pct']}%), "
            f"markets {s['markets_fresh']}/{s['markets']} ({s['market_coverage_pct']}%), "
            f"round lateness median {s['round_late_seconds_median']} s, max {s['round_late_seconds_max']} s"
        )
        for missed in s["missed_games"]:
            print(f"  missed: {missed}")


if __name__ == "__main__":
    main()
