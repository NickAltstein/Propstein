#!/usr/bin/env python3
"""Alarm when the odds feed the live site serves is stale.

check_odds_freshness.py and feed_age.py read the repository. When deploys
stall (a Vercel rate limit, a broken deploy hook, the deploy gate skipping)
the repo can be fresh while propstein.com still serves hours-old odds. This
check fetches live_odds_feed.json from the production URL and alarms when
its updated_at is older than --max-age-hours during an active NFL slate.
It also reports how far the live feed lags the repo, which points at the
deploy path rather than the poller.

Whether to check comes only from the named slate states
(scripts/slate_state.py): NO_CURRENT_SLATE and SLATE_BLOCKED skip, every
other state is checked.

Env: ALARM_WEBHOOK_URL (optional) receives {"content","text"} on an alarm.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import slate_state  # noqa: E402

VIEWER = ROOT / "viewer"
LIVE_URL = "https://propstein.com/live_odds_feed.json"


def parse_time(value) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def slate_active(viewer: Path, now: datetime) -> tuple[bool, str]:
    """(check?, reason), from the named slate states alone (scripts/slate_state.py).

    NO_CURRENT_SLATE (between weeks) and SLATE_BLOCKED (context gate) skip:
    in both the poller deliberately does not refresh odds, so an old live
    feed is expected and PR 258 defines neither as an alarm. Every other
    state (OK, NO_SLATE_ROWS, UNKNOWN) is checked."""
    status = slate_state.classify(viewer, now=now)
    if status["state"] in (slate_state.NO_CURRENT_SLATE, slate_state.SLATE_BLOCKED):
        return False, slate_state.summary(status)
    return True, slate_state.summary(status)


def fetch_live(url: str, opener=urllib.request.urlopen) -> dict:
    # Cache-buster: the feed is served no-store, but a stale CDN copy is
    # exactly what this check exists to catch, so ask for a fresh one.
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}t={int(time.time())}",
                                 headers={"User-Agent": "propstein-live-feed-check",
                                          "Cache-Control": "no-cache"})
    with opener(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def evaluate(live: dict, repo: dict | None, now: datetime, max_age_hours: float) -> tuple[bool, str]:
    """(alarm?, message). Pure."""
    live_at = parse_time(live["updated_at"])
    age_h = (now - live_at).total_seconds() / 3600
    lag = ""
    if repo and repo.get("updated_at"):
        try:
            lag_h = (parse_time(repo["updated_at"]) - live_at).total_seconds() / 3600
            if lag_h > 0.25:
                lag = f"; the repo feed is {lag_h:.1f}h newer, so the deploy path is behind"
        except ValueError:
            pass
    if age_h > max_age_hours:
        return True, (f"PropStein LIVE FEED STALE: propstein.com serves odds {age_h:.1f}h old "
                      f"(limit {max_age_hours:g}h, updated_at {live['updated_at']}){lag}")
    return False, f"live feed age {max(0.0, age_h):.2f}h (limit {max_age_hours:g}h){lag}"


def send_alarm(message: str, url: str | None, opener=urllib.request.urlopen) -> None:
    if not url:
        return
    body = json.dumps({"content": message, "text": message}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with opener(req, timeout=15) as resp:
            resp.read()
    except Exception as exc:
        print(f"alarm webhook failed: {type(exc).__name__}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=LIVE_URL)
    ap.add_argument("--viewer", type=Path, default=VIEWER)
    ap.add_argument("--max-age-hours", type=float, default=6.0)
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    alarm_url = os.environ.get("ALARM_WEBHOOK_URL", "").strip() or None

    active, reason = slate_active(args.viewer, now)
    if not active:
        print(f"live feed check skipped: {reason}")
        return 0
    try:
        live = fetch_live(args.url)
        repo = json.loads((args.viewer / "live_odds_feed.json").read_text(encoding="utf-8"))
        alarm, message = evaluate(live, repo, now, args.max_age_hours)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        alarm, message = True, f"PropStein LIVE FEED CHECK FAILED: could not read the live odds feed ({type(exc).__name__}: {exc})"
    print(message)
    if alarm:
        send_alarm(message, alarm_url)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
