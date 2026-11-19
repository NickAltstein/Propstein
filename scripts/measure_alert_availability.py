#!/usr/bin/env python3
"""At a pilot alert open, re-read its exact Polymarket CLOB side.

Run with --quote-id when the recipient opens the alert. A network failure is
unknown, never an exact match. No operator link or wager action is performed.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.dispatch_edge_alerts import CACHE_DIR, PILOT_LOG_PATH, _american_odds, _finite_number, _parse_utc
from scripts.fetch_polymarket_props import cents_to_american, fetch_token_book

OPEN_LOG_PATH = CACHE_DIR / "alert_pilot_opens.jsonl"


def find_dispatch(quote_id: str, path: Path = PILOT_LOG_PATH) -> dict | None:
    if not path.is_file():
        return None
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("quote_id") == quote_id:
            found = row
    return found


def classify_open(dispatch: dict, fetch_book=fetch_token_book) -> dict:
    """Return exact/moved/suspended/unknown against the alerted terms."""
    result = {key: dispatch.get(key) for key in (
        "quote_id", "book", "game_id", "player_id", "market", "side",
        "line", "odds", "quoted_at", "scheduled_start", "sent_at",
    )}
    opened_at = datetime.now(timezone.utc)
    result["opened_at"] = opened_at.isoformat()
    result["status"] = "unknown"
    result["opened_odds"] = None
    if (dispatch.get("book") != "Polymarket" or dispatch.get("market") not in ("receiving_yards", "receptions")
            or dispatch.get("side") not in ("OVER", "UNDER")
            or not isinstance(dispatch.get("side_token_id"), str) or not dispatch["side_token_id"].strip()
            or _finite_number(dispatch.get("line")) is None
            or _american_odds(dispatch.get("odds")) is None
            or _parse_utc(dispatch.get("scheduled_start")) is None):
        return result
    if opened_at >= _parse_utc(dispatch["scheduled_start"]):
        result["status"] = "suspended"
        return result
    try:
        book = fetch_book(dispatch["side_token_id"])
        ask = _finite_number(book.get("ask_price")) if isinstance(book, dict) else None
        if ask is None or not 0 < ask < 1:
            return result
        result["opened_odds"] = cents_to_american(ask)
        result["status"] = "exact" if result["opened_odds"] == dispatch["odds"] else "moved"
    except ValueError as exc:
        # The provider returned an empty or non-tradeable book for this token.
        if "no valid ask" in str(exc):
            result["status"] = "suspended"
    except Exception:
        # Provider/network errors do not count as an available quote.
        pass
    return result


def append_open(result: dict, path: Path = OPEN_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(result, sort_keys=True) + "\n")


def summarize(path: Path = OPEN_LOG_PATH, dispatch_path: Path = PILOT_LOG_PATH) -> dict:
    counts = {status: 0 for status in ("exact", "moved", "suspended", "unknown")}
    opened = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                status = row.get("status")
            except (ValueError, AttributeError):
                continue
            identity = (row.get("quote_id"), row.get("sent_at"))
            if status in counts and identity not in opened:
                counts[status] += 1
                opened.add(identity)
    if dispatch_path.is_file():
        for line in dispatch_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and (row.get("quote_id"), row.get("sent_at")) not in opened:
                counts["unknown"] += 1
    denominator = counts["exact"] + counts["moved"] + counts["suspended"]
    return {**counts, "denominator": denominator,
            "exact_rate": counts["exact"] / denominator if denominator else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quote-id", help="Alert quote ID opened by a pilot recipient")
    parser.add_argument("--report", action="store_true", help="Summarize observed opens")
    args = parser.parse_args()
    if args.report:
        print(json.dumps(summarize(), sort_keys=True))
        return
    if not args.quote_id:
        parser.error("--quote-id is required unless --report is used")
    dispatch = find_dispatch(args.quote_id)
    if dispatch is None:
        parser.error("quote ID was not dispatched in this pilot")
    result = classify_open(dispatch)
    append_open(result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
