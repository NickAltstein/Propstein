"""Research-only capture of Kalshi NFL player-prop order books.

Measures whether Kalshi's NFL books could serve as an NFL close source before
any amendment registers them. Writes only under research/kalshi-nfl-close-source/
and reuses src.close_quality_capture.normalize_kalshi_capture so records have the
same v2 schema as data/close_quality_captures.jsonl. It touches no ledger,
EDGE_GATE value, PINS, approved plan, or published result.

Phases:
  snapshot  one pass over every open KXNFL* market tied to a scheduled game
  loop      every --interval minutes, capture markets for games whose scheduled
            start is within [-10, +75] minutes, until --until (UTC ISO)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.close_quality_capture import normalize_kalshi_capture  # noqa: E402

API = "https://api.elections.kalshi.com/trade-api/v2"
OUT_DIR = Path(__file__).resolve().parent / "data"
CAPTURES = OUT_DIR / "kalshi_nfl_captures.jsonl"
SERIES_OUT = OUT_DIR / "kalshi_nfl_series.json"
LOG = OUT_DIR / "capture_log.jsonl"
ET = ZoneInfo("America/New_York")
MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
ALIASES = {"LA": {"LA", "LAR"}, "JAX": {"JAX", "JAC"}, "WAS": {"WAS", "WSH"}, "LV": {"LV", "LVR"}}
GAME_LEVEL = {"KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL"}


def get_json(path: str, params: dict | None = None, tries: int = 5):
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json", "User-Agent": "propstein-research/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                time.sleep(0.12)
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(2 * (i + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"gave up: {url}")


def load_schedule() -> list[dict]:
    games = []
    for p in sorted((ROOT / "viewer").glob("games_2026_w*.json")):
        try:
            rows = json.loads(p.read_text())
        except Exception:
            continue
        for g in rows if isinstance(rows, list) else rows.get("games", []):
            try:
                local = datetime.fromisoformat(f"{g['gameday']}T{g['gametime']}").replace(tzinfo=ET)
            except Exception:
                continue
            games.append({"game_id": g["game_id"], "away": g["away_team"], "home": g["home_team"],
                          "start": local.astimezone(timezone.utc)})
    return games


def codes(team: str) -> set[str]:
    return ALIASES.get(team, {team})


def match_game(event_ticker: str, games: list[dict]) -> dict | None:
    m = re.match(r"^[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)", event_ticker)
    if not m:
        return None
    yy, mon, dd, teams = m.groups()
    if mon not in MONTHS:
        return None
    day = f"20{yy}-{MONTHS[mon]:02d}-{dd}"
    for g in games:
        if g["start"].astimezone(ET).strftime("%Y-%m-%d") != day:
            continue
        if any(teams == a + h for a in codes(g["away"]) for h in codes(g["home"])):
            return g
    return None


def nfl_series() -> list[dict]:
    data = get_json("/series", {"category": "Sports"})
    out = [s for s in data.get("series") or [] if str(s.get("ticker", "")).startswith("KXNFL")]
    return out


def open_markets(series_ticker: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(20):
        params = {"series_ticker": series_ticker, "status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        page = get_json("/markets", params)
        out += page.get("markets") or []
        cursor = page.get("cursor")
        if not cursor:
            break
    return out


def discover(games: list[dict], horizon_days: float) -> list[dict]:
    now = datetime.now(timezone.utc)
    series = nfl_series()
    SERIES_OUT.write_text(json.dumps(
        [{k: s.get(k) for k in ("ticker", "title", "category", "tags", "frequency")} for s in series],
        indent=1, sort_keys=True) + "\n")
    targets = []
    for s in series:
        st = s["ticker"]
        try:
            mkts = open_markets(st)
        except Exception as exc:  # keep going; logged
            log({"event": "series_error", "series": st, "error": str(exc)[:200]})
            continue
        for m in mkts:
            g = match_game(str(m.get("event_ticker") or ""), games)
            if not g or not (now - timedelta(minutes=15) <= g["start"] <= now + timedelta(days=horizon_days)):
                continue
            targets.append({"series_ticker": st, "series_title": s.get("title"), "market": m, "game": g})
    log({"event": "discover", "series": len(series), "targets": len(targets),
         "games": sorted({t["game"]["game_id"] for t in targets})})
    return targets


def log(obj: dict) -> None:
    obj = {"at": datetime.now(timezone.utc).isoformat(), **obj}
    with LOG.open("a") as fh:
        fh.write(json.dumps(obj, sort_keys=True) + "\n")
    print(json.dumps(obj), flush=True)


def capture(targets: list[dict], phase: str) -> int:
    n = 0
    with CAPTURES.open("a") as fh:
        for t in targets:
            m = t["market"]
            tk = m["ticker"]
            try:
                mkt = get_json(f"/markets/{tk}")
                ob = get_json(f"/markets/{tk}/orderbook", {"depth": 5})
            except Exception as exc:
                log({"event": "fetch_error", "ticker": tk, "error": str(exc)[:200]})
                continue
            rec = normalize_kalshi_capture(
                market_id=tk, game_id=t["game"]["game_id"], scheduled_start=t["game"]["start"],
                market_payload=mkt, orderbook_payload=ob, captured_at=datetime.now(timezone.utc))
            mm = mkt.get("market", mkt)
            no_bids = (ob.get("orderbook_fp") or {}).get("yes_dollars") or []
            rec.update({
                "phase": phase, "series_ticker": t["series_ticker"], "series_title": t["series_title"],
                "event_ticker": mm.get("event_ticker"), "market_title": mm.get("title"),
                "yes_sub_title": mm.get("yes_sub_title"), "strike_type": mm.get("strike_type"),
                "floor_strike": mm.get("floor_strike"), "no_ask": mm.get("no_ask_dollars"),
                "no_bid": mm.get("no_bid_dollars"), "status": mm.get("status"),
                "no_ask_levels": sorted(
                    ({"price": round(1 - float(p), 4), "size": round(float(s), 4)} for p, s in no_bids),
                    key=lambda x: x["price"])[:5],
            })
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
    return n


def commit(msg: str) -> None:
    if subprocess.run(["git", "status", "--porcelain", str(OUT_DIR)], capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "add", str(OUT_DIR)], check=True)
        subprocess.run(["git", "commit", "-q", "-m", msg], check=True)
        for _ in range(3):
            if subprocess.run(["git", "push", "-q", "origin", "HEAD"]).returncode == 0:
                return
            subprocess.run(["git", "pull", "-q", "--rebase", "origin", "HEAD"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", required=True)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--snapshot-days", type=float, default=4.0)
    ap.add_argument("--no-commit", action="store_true")
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    until = datetime.fromisoformat(a.until.replace("Z", "+00:00"))
    games = load_schedule()

    targets = discover(games, a.snapshot_days)
    snapshot_done = False
    it = 0
    last_discover = datetime.now(timezone.utc)
    while datetime.now(timezone.utc) < until:
        t0 = time.time()
        now = datetime.now(timezone.utc)
        if now - last_discover > timedelta(minutes=30):
            targets = discover(games, a.snapshot_days)
            last_discover = now
        mins = lambda t: (t["game"]["start"] - now).total_seconds() / 60
        window = [t for t in targets if -10 <= mins(t) <= 75]
        if window:
            n = capture(window, "close_window")
            log({"event": "window_pass", "captured": n,
                 "games": sorted({t["game"]["game_id"] for t in window})})
            it += 1
            if it % 5 == 0 and not a.no_commit:
                commit("research(data): Kalshi NFL close-window captures")
        elif not snapshot_done and all(mins(t) > 115 or mins(t) < -10 for t in targets):
            later = [t for t in targets if mins(t) > 115]
            n = capture(later, "snapshot")
            log({"event": "snapshot_done", "captured": n,
                 "games": sorted({t["game"]["game_id"] for t in later})})
            snapshot_done = True
            if not a.no_commit:
                commit("research(data): Kalshi NFL book snapshot")
            continue
        time.sleep(max(5.0, a.interval * 60 - (time.time() - t0)))
    if not a.no_commit:
        commit("research(data): Kalshi NFL close-window captures (final)")


if __name__ == "__main__":
    main()
