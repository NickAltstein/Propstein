#!/usr/bin/env python3
"""Fail-closed alert dispatcher for PropStein verified edge decisions.

Reads only /v1/edges and dispatches verified sportsbook offers to Discord
and/or Telegram with deduplication and state tracking.

Usage:
    python3 scripts/dispatch_edge_alerts.py [--dry-run] [--selftest] [--threshold 5.0]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
SENT_LOG_PATH = CACHE_DIR / "sent_edge_alerts.json"
PILOT_LOG_PATH = CACHE_DIR / "alert_pilot_dispatches.jsonl"
POLICY_VERSION = "edge_10pct_v1"  # parity-checked against src.freeze_slate


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _american_odds(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 100 <= abs(value) <= 50000 else None



def load_sent_alerts() -> dict:
    if SENT_LOG_PATH.is_file():
        try:
            return json.loads(SENT_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_sent_alerts(alerts: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SENT_LOG_PATH.write_text(json.dumps(alerts, indent=2), encoding="utf-8")


def pilot_scope() -> tuple[str, str, str, str] | None:
    """A URL alone cannot enable broad alerting; one market must be named."""
    values = tuple(os.environ.get(key, "").strip() for key in (
        "PROPSTEIN_PILOT_GAME_ID", "PROPSTEIN_PILOT_PLAYER_ID",
        "PROPSTEIN_PILOT_MARKET", "PROPSTEIN_PILOT_BOOK",
    ))
    return values if all(values) and values[2:] == ("receiving_yards", "Polymarket") else None


def record_pilot_dispatch(edge: dict, sent_at: datetime) -> None:
    """Keep the offered terms for an eventual provider re-read at user open."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    row = {key: edge.get(key) for key in (
        "quote_id", "game_id", "player_id", "market", "book", "side",
        "line", "odds", "quoted_at", "scheduled_start", "side_token_id", "source_url",
    )}
    row["sent_at"] = sent_at.isoformat()
    with PILOT_LOG_PATH.open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, sort_keys=True) + "\n")


def collect_active_edges(min_edge_pct: float = 5.0, edges_url: str | None = None) -> list[dict]:
    """Read only the canonical edge decision API; never infer a quote from picks."""
    url = edges_url or os.environ.get("PROPSTEIN_EDGES_URL")
    if not url:
        raise RuntimeError("PROPSTEIN_EDGES_URL is required; alert dispatch is disabled")
    scope = pilot_scope()
    if scope is None:
        raise RuntimeError("One PROPSTEIN_PILOT_GAME_ID/PLAYER_ID/MARKET/BOOK scope is required")
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
        raise ValueError("PROPSTEIN_EDGES_URL must use HTTPS (localhost may use HTTP)")
    from urllib.parse import parse_qsl, urlencode, urlunparse
    query = dict(parse_qsl(parsed.query))
    query["min_edge"] = str(min_edge_pct)
    url = urlunparse(parsed._replace(query=urlencode(query)))
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "PropStein-AlertBot/2.0"})
    with urlopen(req, timeout=10) as response:
        payload = json.load(response)
    if payload.get("status") != "success" or payload.get("source") != "verified_feed" or not isinstance(payload.get("data"), list):
        return []
    now = datetime.now(timezone.utc)
    edges = []
    seen = set()
    for edge in payload["data"]:
        if (not isinstance(edge, dict) or edge.get("verified") is not True
                or edge.get("verification_status") != "VERIFIED"
                or edge.get("verification_engine") != "verification.v1"
                or not isinstance(edge.get("receipt_id"), str) or not edge["receipt_id"].strip()):
            continue
        if edge.get("qualification_policy_version") != POLICY_VERSION:
            continue
        if tuple(edge.get(key) for key in ("game_id", "player_id", "market", "book")) != scope:
            continue
        if (not isinstance(edge.get("side_token_id"), str) or not edge["side_token_id"].strip()
                or _parse_utc(edge.get("scheduled_start")) is None):
            continue
        if not isinstance(edge.get("quote_id"), str) or not edge["quote_id"].strip():
            continue
        line = _finite_number(edge.get("line"))
        odds = _american_odds(edge.get("odds"))
        quoted_at = _parse_utc(edge.get("quoted_at"))
        expires_at = _parse_utc(edge.get("expires_at"))
        expected_return = _finite_number(edge.get("expected_return_pct"))
        edge_pct = _finite_number(edge.get("edge_pct"))
        if (line is None or line <= 0 or odds is None or quoted_at is None or expires_at is None
                or quoted_at > now or expires_at <= now or expected_return is None or expected_return <= 0
                or edge_pct is None or edge_pct <= 0 or expected_return < min_edge_pct or expected_return > 12.0):
            continue
        if edge.get("side") not in ("OVER", "UNDER") or not all(edge.get(k) for k in ("book", "player_name", "market", "game_id")):
            continue
        edge_id = f"{edge['quote_id']}:{edge['side']}:{line}:{odds}"
        if edge_id in seen:
            continue
        seen.add(edge_id)
        edges.append({
            "id": edge_id, "quote_id": edge["quote_id"], "player": edge["player_name"],
            "team": edge.get("team", ""), "market": edge["market"], "line": line,
            "side": edge["side"], "odds": odds, "book": edge["book"],
            "ev_pct": expected_return, "edge_pct": edge_pct,
            "expires_at": edge["expires_at"], "quoted_at": edge["quoted_at"],
            "scheduled_start": edge.get("scheduled_start"),
            "game_id": edge["game_id"], "player_id": edge["player_id"],
            "side_token_id": edge.get("side_token_id"),
            "source_url": edge.get("source_url"),
        })
    return sorted(edges, key=lambda x: x["ev_pct"], reverse=True)


def format_discord_payload(edge: dict) -> dict:
    ev_val = f"+{edge['ev_pct']:.1f}%" if edge['ev_pct'] > 0 else f"{edge['ev_pct']:.1f}%"
    odds_str = f"+{edge['odds']}" if edge['odds'] > 0 else str(edge['odds'])

    return {
        "username": "PropStein Live Edge",
        "avatar_url": "https://propstein.com/brand/p-logo-2027.png",
        "embeds": [{
            "title": f"🚨 QUANT EDGE DETECTED: {edge['player']} ({edge['team']})",
            "description": f"**Pick**: {edge['side']} {edge['line']} {edge['market'].title()}\n**Book**: {edge['book']} ({odds_str})",
            "color": 0x80C6F6,  # PropStein Blue
            "fields": [
                {"name": "Expected return", "value": ev_val, "inline": True},
                {"name": "Quote ID", "value": edge['quote_id'], "inline": False},
                {"name": "Expires", "value": edge['expires_at'], "inline": False},
            ],
            "footer": {
                "text": "PropStein Terminal · Recheck line and price at the book before acting"
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }]
    }


def format_telegram_message(edge: dict) -> str:
    ev_val = f"+{edge['ev_pct']:.1f}%" if edge['ev_pct'] > 0 else f"{edge['ev_pct']:.1f}%"
    odds_str = f"+{edge['odds']}" if edge['odds'] > 0 else str(edge['odds'])

    return (
        f"🚨 <b>QUANT EDGE DETECTED</b>\n\n"
        f"<b>Player</b>: {edge['player']} ({edge['team']})\n"
        f"<b>Market</b>: {edge['side']} {edge['line']} {edge['market'].title()}\n"
        f"<b>Book</b>: {edge['book']} ({odds_str})\n\n"
        f"📊 <b>Expected return</b>: <code>{ev_val}</code>\n"
        f"<b>Quote ID</b>: <code>{edge['quote_id']}</code>\n"
        f"<b>Expires</b>: <code>{edge['expires_at']}</code>\n\n"
        f"<a href=\"https://propstein.com\">Open PropStein Terminal</a> · "
        f"Recheck line and price at the book before acting"
    )


def post_webhook(url: str, payload: dict) -> bool:
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "PropStein-AlertBot/1.0"})
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except (HTTPError, URLError) as err:
        sys.stderr.write(f"Webhook post error: {err}\n")
        return False


def post_telegram(token: str, chat_id: str, message_text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    return post_webhook(url, payload)


def run_selftest() -> None:
    sample_edge = {
        "id": "jaxon_smith-njigba:rec:8.5:consensus",
        "player": "Jaxon Smith-Njigba",
        "team": "SEA",
        "market": "Receptions",
        "line": 8.5,
        "side": "OVER",
        "odds": -110,
        "book": "FanDuel",
        "ev_pct": 8.4,
        "quote_id": "selftest-quote",
        "expires_at": "2026-11-18T05:00:00+00:00",
        "source": "selftest",
    }
    discord = format_discord_payload(sample_edge)
    telegram = format_telegram_message(sample_edge)

    assert "QUANT EDGE DETECTED" in discord["embeds"][0]["title"]
    assert discord["embeds"][0]["fields"][0]["value"] == "+8.4%"
    assert "Jaxon Smith-Njigba" in telegram
    assert "selftest-quote" in telegram

    print("✅ Selftest passed: alert payload structures verified.")


def main() -> None:
    parser = argparse.ArgumentParser(description="PropStein Edge Alert Dispatcher")
    parser.add_argument("--edges-url", type=str, default=None, help="Canonical /v1/edges endpoint")
    parser.add_argument("--min-ev", "--threshold", dest="threshold", type=float, default=5.0, help="Minimum edge percent hurdle")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads without firing webhooks")
    parser.add_argument("--selftest", action="store_true", help="Execute built-in test suite and exit")
    args = parser.parse_args()

    if args.selftest:
        run_selftest()
        return

    discord_url = os.environ.get("DISCORD_WEBHOOK_URL") or os.environ.get("DISCORD_EDGE_WEBHOOK")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID")

    edges = collect_active_edges(min_edge_pct=args.threshold, edges_url=args.edges_url)
    sent_log = load_sent_alerts()

    new_dispatches = 0
    now = time.time()

    print(f"PropStein Dispatcher: {len(edges)} qualified edges discovered above {args.threshold}% threshold.")

    for edge in edges:
        edge_id = edge["id"]
        # Throttle alert repeat within 12 hours
        last_sent = sent_log.get(edge_id, 0)
        if (now - last_sent) < 43200:
            continue

        if args.dry_run or not (discord_url or (tg_token and tg_chat)):
            print(f"[DRY RUN / NO WEBHOOK] Alert queued: {edge['player']} ({edge['team']}) - {edge['market']} EV: {edge['ev_pct']:.1f}%")
            if not (discord_url or (tg_token and tg_chat)):
                print("  Note: Set DISCORD_EDGE_WEBHOOK or TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID to dispatch live.")
            continue

        success = False
        if discord_url:
            discord_payload = format_discord_payload(edge)
            success = post_webhook(discord_url, discord_payload) or success

        if tg_token and tg_chat:
            tg_msg = format_telegram_message(edge)
            success = post_telegram(tg_token, tg_chat, tg_msg) or success

        if success:
            sent_log[edge_id] = now
            new_dispatches += 1
            record_pilot_dispatch(edge, datetime.fromtimestamp(now, timezone.utc))
            print(f"[DISPATCHED] {edge['player']} - {edge['market']} (+{edge['ev_pct']:.1f}% EV)")

    if new_dispatches:
        save_sent_alerts(sent_log)
    print(f"Summary: {new_dispatches} new alerts processed.")


if __name__ == "__main__":
    main()
