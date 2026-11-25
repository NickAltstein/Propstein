"""Read-only §14 close-quality evidence report generator.

Inspects stored quote, receipt, snapshot, and CLV ledger files without modifying
any data, ledger row, receipt, threshold, or enforcement logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
OFFSETS_MIN = (5, 15, 30, 60, 120)
NOTIONALS_USD = (50, 100, 250, 500)


def _parse_utc(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_game_start_utc(g: dict[str, Any]) -> datetime | None:
    dt = _parse_utc(g.get("kickoff_utc") or g.get("commence_time") or g.get("tipoff_utc") or g.get("scheduled_start"))
    if dt is not None:
        return dt
    gd = g.get("gameday")
    gt = g.get("gametime")
    if gd and gt:
        try:
            naive = datetime.strptime(f"{gd} {gt}", "%Y-%m-%d %H:%M")
            return naive.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _quartiles(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "min": None, "q1": None, "median": None, "q3": None, "max": None}
    s = sorted(values)
    n = len(s)
    med = statistics.median(s)
    if n >= 4:
        qs = statistics.quantiles(s, n=4, method="inclusive")
        q1, q3 = qs[0], qs[2]
    else:
        q1, q3 = s[0], s[-1]
    return {
        "n": n,
        "min": round(s[0], 4),
        "q1": round(q1, 4),
        "median": round(med, 4),
        "q3": round(q3, 4),
        "max": round(s[-1], 4),
    }


def inspect_field_inventory() -> list[dict[str, Any]]:
    """Inventory stored fields across all quote, snapshot, and ledger artifacts."""
    rows: list[dict[str, Any]] = []

    def add_row(source: str, path_glob: str, count: int, keys: set[str], note: str = "") -> None:
        rows.append({
            "source": source,
            "files": path_glob,
            "rows": count,
            "bid": any(k in keys for k in ("bid", "over_bid_price", "under_bid_price", "yes_bid")),
            "ask": any(k in keys for k in ("ask", "over_ask_price", "under_ask_price", "yes_ask")),
            "last": any(k in keys for k in ("last", "last_price")),
            "quoted_at": "quoted_at" in keys,
            "observed_at": any(k in keys for k in ("observed_at", "captured_at", "close_time")),
            "depth": any("depth" in k for k in keys),
            "size": any("size" in k for k in keys),
            "volume": any("volume" in k for k in keys),
            "book_snapshot": any(k in keys for k in ("book_snapshot", "bids", "asks", "orderbook")),
            "event_start": any(k in keys for k in ("scheduled_start", "commence_time", "kickoff_utc")),
            "note": note,
        })

    # 1. Polymarket NFL live_odds_feed.json
    pm_path = ROOT / "viewer" / "live_odds_feed.json"
    if pm_path.exists():
        pm = json.loads(pm_path.read_text(encoding="utf-8"))
        qs = pm.get("quotes") or []
        keys = set().union(*(q.keys() for q in qs if isinstance(q, dict))) if qs else set()
        add_row(
            "Polymarket NFL (live_odds_feed)",
            "viewer/live_odds_feed.json",
            len(qs),
            keys,
            "Has over_ask_price/under_ask_price & top-of-book ask depth_usd; no bid, no multi-level book, no volume, no quoted_at.",
        )

    # 2. Kalshi NBA nba_odds_*.json
    nba_files = sorted((ROOT / "viewer").glob("nba_odds_*.json"))
    nba_files = [p for p in nba_files if not p.name.endswith(".manifest.json")]
    nba_keys: set[str] = set()
    nba_count = 0
    # Fixture nights (scripts/build_nba_fixtures.py) are synthetic sample data,
    # not stored Kalshi evidence: leave them out of every count.
    nba_files = [p for p in nba_files if json.loads(p.read_text(encoding="utf-8")).get("_fixture") is not True]
    for p in nba_files:
        d = json.loads(p.read_text(encoding="utf-8"))
        qs = [q for q in (d.get("quotes") or []) if not (isinstance(q, dict) and q.get("_fixture") is True)]
        nba_count += len(qs)
        for q in qs:
            if isinstance(q, dict):
                nba_keys.update(q.keys())
    add_row(
        "Kalshi NBA (nba_odds_*.json)",
        f"viewer/nba_odds_*.json ({len(nba_files)} files)",
        nba_count,
        nba_keys,
        "Has quoted_at, observed_at, scheduled_start, offset_to_scheduled_start_sec; no bid/ask/depth/volume.",
    )

    # 3. Polymarket receipt log
    rc_path = ROOT / "data" / "polymarket_receipt_log.json"
    if rc_path.exists():
        rcs = json.loads(rc_path.read_text(encoding="utf-8"))
        keys = set(rcs[0].keys()) if rcs else set()
        add_row("Polymarket Receipt Log", "data/polymarket_receipt_log.json", len(rcs), keys, "Hash/provenance log only; no prices or depth.")

    # 4. CLV audit report (8 rows)
    clv_a_path = ROOT / "viewer" / "clv_audit_report.json"
    if clv_a_path.exists():
        clv_a = json.loads(clv_a_path.read_text(encoding="utf-8"))
        recs = clv_a.get("records") or []
        keys = set(recs[0].keys()) if recs else set()
        add_row("CLV Audit Report (n=8)", "viewer/clv_audit_report.json", len(recs), keys, "No close_time, bid, ask, depth, or volume stored.")

    # 5. CLV report W7 (37 rows)
    clv_w7_path = ROOT / "viewer" / "clv_report_2026_w7.json"
    if clv_w7_path.exists():
        clv_w7 = json.loads(clv_w7_path.read_text(encoding="utf-8"))
        recs = clv_w7.get("ledger") or []
        keys = set(recs[0].keys()) if recs else set()
        add_row("CLV Report W7 (n=37)", "viewer/clv_report_2026_w7.json", len(recs), keys, "Has close_time; no bid, ask, depth, or volume.")

    return rows


def compute_kalshi_nba_offset_coverage() -> dict[str, Any]:
    """Compute per-game offset coverage for all stored Kalshi NBA slates."""
    game_files = sorted((ROOT / "viewer").glob("nba_games_*.json"))
    total_games = 0
    per_game: list[dict[str, Any]] = []
    offset_hits = {m: 0 for m in OFFSETS_MIN}

    for gf in game_files:
        day = gf.stem.replace("nba_games_", "")
        of = ROOT / "viewer" / f"nba_odds_{day}.json"
        games = json.loads(gf.read_text(encoding="utf-8"))
        odds = json.loads(of.read_text(encoding="utf-8")) if of.exists() else {}
        if odds.get("_fixture") is True or (games and all(isinstance(g, dict) and g.get("_fixture") is True for g in games)):
            continue  # synthetic fixture night, not a stored Kalshi slate
        quotes = [q for q in (odds.get("quotes") or [])
                  if isinstance(q, dict) and q.get("_fixture") is not True]

        quotes_by_gid: dict[str, list[dict[str, Any]]] = {}
        for q in quotes:
            if isinstance(q, dict) and q.get("game_id"):
                quotes_by_gid.setdefault(str(q["game_id"]), []).append(q)

        for g in games:
            if not isinstance(g, dict) or g.get("_fixture") is True:
                continue
            gid = str(g.get("game_id") or "")
            tip = _parse_utc(g.get("tipoff_utc") or g.get("scheduled_start"))
            if not gid or tip is None:
                continue
            total_games += 1
            pre_offsets_min: list[tuple[float, str]] = []
            for q in quotes_by_gid.get(gid, []):
                q_dt = _parse_utc(q.get("quoted_at")) or _parse_utc(q.get("observed_at"))
                if q_dt is None:
                    continue
                off_min = (tip - q_dt).total_seconds() / 60.0
                if off_min > 0:
                    pre_offsets_min.append((off_min, q_dt.isoformat()))

            for m in OFFSETS_MIN:
                if any(off >= m for off, _ in pre_offsets_min):
                    offset_hits[m] += 1

            last_pre = min(pre_offsets_min, key=lambda x: x[0]) if pre_offsets_min else None
            per_game.append({
                "date": day,
                "game_id": gid,
                "matchup": f"{g.get('away_team', '?')} @ {g.get('home_team', '?')}",
                "scheduled_start_utc": tip.isoformat(),
                "pre_start_quotes": len(pre_offsets_min),
                "last_pre_start_ts": last_pre[1] if last_pre else None,
                "last_pre_start_offset_min": round(last_pre[0], 2) if last_pre else None,
            })

    return {
        "total_games": total_games,
        "offset_coverage": {
            m: {
                "games": offset_hits[m],
                "share": round(offset_hits[m] / total_games, 4) if total_games else 0.0,
            }
            for m in OFFSETS_MIN
        },
        "games_with_zero_pre_start_quotes": [g for g in per_game if g["pre_start_quotes"] == 0],
        "per_game": per_game,
    }


def compute_polymarket_nfl_stats() -> dict[str, Any]:
    """Compute offset coverage and top-of-book ask depth stats for Polymarket NFL."""
    pm_path = ROOT / "viewer" / "live_odds_feed.json"
    if not pm_path.exists():
        return {"total_quotes": 0, "games": [], "ask_depth_usd": {}}
    pm = json.loads(pm_path.read_text(encoding="utf-8"))
    quotes = pm.get("quotes") or []

    # Load game schedules across viewer/games_*.json
    starts: dict[str, datetime] = {}
    for gf in sorted((ROOT / "viewer").glob("games_*.json")):
        for g in json.loads(gf.read_text(encoding="utf-8")):
            if isinstance(g, dict) and g.get("game_id"):
                dt = _parse_game_start_utc(g)
                if dt is not None:
                    starts[str(g["game_id"])] = dt

    ask_depths: list[float] = []
    game_rows: dict[str, list[float]] = {}
    post_start_count = 0
    for q in quotes:
        if not isinstance(q, dict):
            continue
        for k in ("over_ask_depth_usd", "under_ask_depth_usd"):
            v = q.get(k)
            if v is not None:
                ask_depths.append(float(v))
        gid = str(q.get("game_id") or "")
        obs_dt = _parse_utc(q.get("observed_at"))
        start_dt = starts.get(gid) or _parse_utc(q.get("end_date"))
        if start_dt is not None and obs_dt is not None:
            off_min = (start_dt - obs_dt).total_seconds() / 60.0
            if off_min > 0:
                game_rows.setdefault(gid, []).append(off_min)
            else:
                post_start_count += 1
    return {
        "total_quotes": len(quotes),
        "ask_depth_dist": _quartiles(ask_depths),
        "ask_depth_floor_removal": {
            n: {
                "below_count": sum(1 for d in ask_depths if d < n),
                "total": len(ask_depths),
                "removed_share": round(sum(1 for d in ask_depths if d < n) / len(ask_depths), 4) if ask_depths else 0.0,
            }
            for n in NOTIONALS_USD
        },
        "games_with_pre_start_offsets": len(game_rows),
        "post_start_quotes_count": post_start_count,
    }


def compute_stale_close_sensitivity() -> dict[str, Any]:
    """Compute how many W7 closes change status under each candidate cutoff offset."""
    w7_clv = json.loads((ROOT / "viewer" / "clv_report_2026_w7.json").read_text(encoding="utf-8"))
    w7_games = json.loads((ROOT / "viewer" / "games_2026_w7.json").read_text(encoding="utf-8"))

    team_kickoff: dict[str, datetime] = {}
    for g in w7_games:
        dt = _parse_game_start_utc(g)
        if dt is None:
            continue
        for k in ("home_team", "away_team"):
            if g.get(k):
                team_kickoff[str(g[k]).upper()] = dt

    offsets: list[float] = []
    missing_ts = 0
    for rec in w7_clv.get("ledger") or ():
        close_dt = _parse_utc(rec.get("close_time"))
        ko_dt = team_kickoff.get(str(rec.get("team") or "").upper())
        if close_dt is None or ko_dt is None:
            missing_ts += 1
            continue
        offsets.append((ko_dt - close_dt).total_seconds() / 60.0)

    sensitivity = {}
    for m in OFFSETS_MIN:
        outside = sum(1 for o in offsets if o < m)
        sensitivity[m] = {
            "outside_cutoff": outside,
            "total_with_ts": len(offsets),
            "share_changed": round(outside / len(offsets), 4) if offsets else 0.0,
        }
    return {
        "w7_total_closes": len(w7_clv.get("ledger") or ()),
        "w7_with_timestamps": len(offsets),
        "w7_missing_timestamps": missing_ts,
        "offset_dist_min": _quartiles(offsets),
        "sensitivity": sensitivity,
    }


def main() -> None:
    report = {
        "field_inventory": inspect_field_inventory(),
        "kalshi_nba_offsets": compute_kalshi_nba_offset_coverage(),
        "polymarket_nfl": compute_polymarket_nfl_stats(),
        "stale_close_sensitivity": compute_stale_close_sensitivity(),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
