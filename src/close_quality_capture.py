"""Additive close-quality capture targeting T-60/-30/-15/-5m windows.

Stores hash-chained capture records in `data/close_quality_captures.jsonl`
(outside `viewer/`) without touching any existing quote file, viewer JSON,
ledger row, EDGE_GATE threshold, or qualification policy.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Collection, Mapping, Sequence
import urllib.request
from zoneinfo import ZoneInfo

from src.crypto_utils import canonical_json_sha256

POLYMARKET_BOOK_URL = "https://clob.polymarket.com/book?token_id={token_id}"
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}"
KALSHI_MARKET_URL = "https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}"
KALSHI_ORDERBOOK_URL = "https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}/orderbook?depth=5"
SCHEMA_VERSION = 2
VOLUME_WINDOW_METHOD = "successive_volume_total_delta"


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        # Polymarket CLOB timestamp is milliseconds since epoch
        sec = float(value) / 1000.0 if float(value) > 1e11 else float(value)
        return datetime.fromtimestamp(sec, tz=timezone.utc)
    elif isinstance(value, str) and value.strip():
        s = value.strip()
        if s.isdigit():
            sec = float(s) / 1000.0 if float(s) > 1e11 else float(s)
            return datetime.fromtimestamp(sec, tz=timezone.utc)
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _line_sha256(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def load_captures_jsonl(jsonl_path: Path) -> tuple[list[dict[str, Any]], str, bool]:
    """Load existing capture JSONL, returning `(records, last_line_hash, chain_valid)`."""
    if not jsonl_path.exists():
        return [], "GENESIS", True
    records: list[dict[str, Any]] = []
    prev_hash = "GENESIS"
    chain_valid = True
    for raw_line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("prev_record_hash") != prev_hash:
            chain_valid = False
        prev_hash = _line_sha256(line)
        records.append(rec)
    return records, prev_hash, chain_valid


def analysis_eligible_captures(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Exclude implicit v1 records: their pre_start used the provider's old quote time."""
    return [record for record in records if record.get("schema_version") == SCHEMA_VERSION]


def _capture_timing(
    captured_at: datetime, scheduled_start: datetime | None, quoted_at: datetime | None
) -> tuple[float | None, bool | None, float | None]:
    offset = round((scheduled_start - captured_at).total_seconds() / 60.0, 2) if scheduled_start else None
    pre_start = captured_at < scheduled_start if scheduled_start else None
    quote_age = round((captured_at - quoted_at).total_seconds() / 60.0, 2) if quoted_at else None
    return offset, pre_start, quote_age


def _volume_window(volume_total: float | None, previous_total: float | None) -> tuple[float | None, str | None]:
    if volume_total is None or previous_total is None or volume_total < previous_total:
        return None, None
    return round(volume_total - previous_total, 4), VOLUME_WINDOW_METHOD


def normalize_polymarket_capture(
    *,
    market_id: str,
    game_id: str,
    scheduled_start: datetime | str | None,
    book_payload: Mapping[str, Any] | None,
    gamma_payload: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    captured_at: datetime | None = None,
    prev_volume_total: float | None = None,
) -> dict[str, Any]:
    """Normalize Polymarket CLOB `/book` + Gamma `/markets` into one additive capture."""
    cap_dt = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start_dt = _parse_utc(scheduled_start)

    gamma_obj: Mapping[str, Any] = {}
    if isinstance(gamma_payload, Sequence) and not isinstance(gamma_payload, (str, bytes)):
        gamma_obj = gamma_payload[0] if gamma_payload else {}
    elif isinstance(gamma_payload, Mapping):
        gamma_obj = gamma_payload

    bids_raw = (book_payload or {}).get("bids") or []
    asks_raw = (book_payload or {}).get("asks") or []
    # Polymarket CLOB /book returns bids ascending and asks descending; sort best-first
    bids_sorted = sorted(
        (
            {"price": float(b["price"]), "size": float(b["size"])}
            for b in bids_raw
            if isinstance(b, Mapping) and b.get("price") is not None and b.get("size") is not None
        ),
        key=lambda x: x["price"],
        reverse=True,
    )[:5]
    asks_sorted = sorted(
        (
            {"price": float(a["price"]), "size": float(a["size"])}
            for a in asks_raw
            if isinstance(a, Mapping) and a.get("price") is not None and a.get("size") is not None
        ),
        key=lambda x: x["price"],
    )[:5]

    bid = bids_sorted[0]["price"] if bids_sorted else _float_or_none(gamma_obj.get("bestBid"))
    ask = asks_sorted[0]["price"] if asks_sorted else _float_or_none(gamma_obj.get("bestAsk"))
    depth_levels = {"bids": bids_sorted, "asks": asks_sorted} if (bids_sorted or asks_sorted) else None

    quoted_dt = _parse_utc((book_payload or {}).get("timestamp"))
    offset_minutes, pre_start, quote_age_minutes = _capture_timing(cap_dt, start_dt, quoted_dt)

    vol_total = _float_or_none(gamma_obj.get("volumeNum") or gamma_obj.get("volume"))
    vol_window, vol_method = _volume_window(vol_total, prev_volume_total)

    raw_hash = canonical_json_sha256({"book": book_payload, "gamma": gamma_obj})
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "polymarket",
        "market_id": str(market_id),
        "game_id": str(game_id),
        "quoted_at": quoted_dt.isoformat() if quoted_dt else None,
        "quote_age_minutes": quote_age_minutes,
        "captured_at": cap_dt.isoformat(),
        "scheduled_start": start_dt.isoformat() if start_dt else None,
        "offset_minutes": offset_minutes,
        "pre_start": pre_start,
        "bid": bid,
        "ask": ask,
        "depth_levels": depth_levels,
        "volume_total": vol_total,
        "volume_window": vol_window,
        "volume_window_method": vol_method,
        "open_interest": None,  # Polymarket CLOB/Gamma do not expose open_interest
        "raw_payload_sha256": raw_hash,
    }


def normalize_kalshi_capture(
    *,
    market_id: str,
    game_id: str,
    scheduled_start: datetime | str | None,
    market_payload: Mapping[str, Any] | None,
    orderbook_payload: Mapping[str, Any] | None = None,
    captured_at: datetime | None = None,
    prev_volume_total: float | None = None,
) -> dict[str, Any]:
    """Normalize Kalshi `/markets/{ticker}` + `/orderbook?depth=5` into one additive capture."""
    cap_dt = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start_dt = _parse_utc(scheduled_start)

    mkt = (market_payload or {}).get("market", market_payload or {})
    ob_fp = (orderbook_payload or {}).get("orderbook_fp") or {}
    yes_raw = ob_fp.get("yes_dollars") or []
    no_raw = ob_fp.get("no_dollars") or []

    # Kalshi orderbook returns YES bids and NO bids; NO bid at p == YES ask at (1 - p)
    bids_sorted = sorted(
        ({"price": round(float(p), 4), "size": round(float(s), 4)} for p, s in yes_raw),
        key=lambda x: x["price"],
        reverse=True,
    )[:5]
    asks_sorted = sorted(
        ({"price": round(1.0 - float(p), 4), "size": round(float(s), 4)} for p, s in no_raw),
        key=lambda x: x["price"],
    )[:5]

    bid = _float_or_none(mkt.get("yes_bid_dollars"))
    if bid is None and bids_sorted:
        bid = bids_sorted[0]["price"]
    ask = _float_or_none(mkt.get("yes_ask_dollars"))
    if ask is None and asks_sorted:
        ask = asks_sorted[0]["price"]
    depth_levels = {"bids": bids_sorted, "asks": asks_sorted} if (bids_sorted or asks_sorted) else None

    quoted_dt = _parse_utc(mkt.get("updated_time"))
    offset_minutes, pre_start, quote_age_minutes = _capture_timing(cap_dt, start_dt, quoted_dt)

    vol_total = _float_or_none(mkt.get("volume_fp") or mkt.get("volume"))
    vol_window, vol_method = _volume_window(vol_total, prev_volume_total)
    open_interest = _float_or_none(mkt.get("open_interest_fp") or mkt.get("open_interest"))

    raw_hash = canonical_json_sha256({"market": mkt, "orderbook": orderbook_payload})
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "kalshi",
        "market_id": str(market_id),
        "game_id": str(game_id),
        "quoted_at": quoted_dt.isoformat() if quoted_dt else None,
        "quote_age_minutes": quote_age_minutes,
        "captured_at": cap_dt.isoformat(),
        "scheduled_start": start_dt.isoformat() if start_dt else None,
        "offset_minutes": offset_minutes,
        "pre_start": pre_start,
        "bid": bid,
        "ask": ask,
        "depth_levels": depth_levels,
        "volume_total": vol_total,
        "volume_window": vol_window,
        "volume_window_method": vol_method,
        "open_interest": open_interest,
        "raw_payload_sha256": raw_hash,
    }


def capture_record_id(cap: Mapping[str, Any]) -> str:
    """Stable id of a capture record; replaying the same capture never appends twice."""
    identity = {
        "source": cap.get("source"),
        "market_id": cap.get("market_id"),
        "quoted_at": cap.get("quoted_at"),
        "raw_payload_sha256": cap.get("raw_payload_sha256"),
    }
    # Each v2 observation is a distinct snapshot, even when the provider's
    # last-update timestamp and book payload have not changed.
    if cap.get("schema_version") == SCHEMA_VERSION:
        identity["captured_at"] = cap.get("captured_at")
    return canonical_json_sha256(identity)[:24]


def append_captures(
    *,
    jsonl_path: Path,
    status_path: Path,
    captures: Sequence[Mapping[str, Any]],
    fetch_errors: Sequence[str] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append idempotent, hash-chained capture records and write status summary."""
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    existing, prev_hash, chain_valid = load_captures_jsonl(jsonl_path)
    seen_ids = {str(r.get("record_id")) for r in existing}

    new_lines: list[str] = []
    appended = 0
    for cap in captures:
        rec = dict(cap)
        rec_id = capture_record_id(rec)
        if rec_id in seen_ids:
            continue
        seen_ids.add(rec_id)
        rec["record_id"] = rec_id
        rec["prev_record_hash"] = prev_hash
        line = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        prev_hash = _line_sha256(line)
        new_lines.append(line)
        appended += 1

    if new_lines:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as fh:
            for line in new_lines:
                fh.write(line + "\n")

    stale_reasons = list(fetch_errors)
    if not chain_valid:
        stale_reasons.append("CAPTURE_CHAIN_BROKEN")

    status = {
        "schema": "propwire.close_quality_capture_status.v1",
        "updated_at": now_dt.isoformat(),
        "stale_context": bool(stale_reasons),
        "stale_reasons": stale_reasons,
        "appended_count": appended,
        "total_records": len(existing) + appended,
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return status


def _fetch_json(url: str, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "propstein-close-capture/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _last_volume_by_market(
    records: Sequence[Mapping[str, Any]], before: datetime
) -> dict[tuple[str, str], float]:
    latest: dict[tuple[str, str], tuple[datetime, float]] = {}
    for r in analysis_eligible_captures(records):
        v = _float_or_none(r.get("volume_total"))
        captured = _parse_utc(r.get("captured_at"))
        if v is None or captured is None or captured >= before:
            continue
        key = (str(r.get("source")), str(r.get("market_id")))
        if key not in latest or captured > latest[key][0]:
            latest[key] = (captured, v)
    return {key: value for key, (_, value) in latest.items()}


GameKey = tuple[str, str, str]  # (source, game_id, scheduled_start ISO)


def _game_key(source: str, game_id: Any, start_dt: datetime) -> GameKey:
    return (source, str(game_id or ""), start_dt.isoformat())


def _iter_feed_markets(viewer_dir: Path, now_dt: datetime):
    """Yield `(source, market_id, game_id, start_dt)` for every market in the live feeds.

    Kalshi NBA comes from today's (ET) `nba_odds_<day>.json`; Polymarket NFL
    from `live_odds_feed.json`, one entry per over/under token.
    """
    et_day = now_dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    nba_odds_path = viewer_dir / f"nba_odds_{et_day}.json"
    if nba_odds_path.exists():
        nba_data = json.loads(nba_odds_path.read_text(encoding="utf-8"))
        for q in nba_data.get("quotes") or ():
            ticker = str(q.get("provider_quote_id") or "").strip()
            start_dt = _parse_utc(q.get("scheduled_start"))
            if ticker and start_dt is not None:
                yield "kalshi", ticker, q.get("game_id"), start_dt

    pm_path = viewer_dir / "live_odds_feed.json"
    if pm_path.exists():
        pm_data = json.loads(pm_path.read_text(encoding="utf-8"))
        for q in pm_data.get("quotes") or ():
            start_dt = _parse_utc(q.get("end_date"))
            if start_dt is None:
                continue
            for side_key in ("over_token_id", "under_token_id"):
                tok = str(q.get(side_key) or "").strip()
                if tok:
                    yield "polymarket", tok, q.get("game_id"), start_dt


def upcoming_games(
    viewer_dir: Path, now: datetime, horizon_minutes: float
) -> dict[GameKey, int]:
    """Games in the live feeds starting after `now` and within the horizon, with market counts."""
    now_dt = now.astimezone(timezone.utc)
    games: dict[GameKey, int] = {}
    for source, _market_id, game_id, start_dt in _iter_feed_markets(viewer_dir, now_dt):
        ahead = (start_dt - now_dt).total_seconds() / 60.0
        if 0 < ahead <= horizon_minutes:
            key = _game_key(source, game_id, start_dt)
            games[key] = games.get(key, 0) + 1
    return games


def collect_live_captures(
    *,
    viewer_dir: Path,
    prev_vols: Mapping[tuple[str, str], float],
    now: datetime,
    window_min_before: float = 75.0,
    window_min_after: float = -10.0,
    only_starts: Collection[datetime] | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[GameKey, dict[str, int]]]:
    """Fetch and normalize captures for markets in the start window.

    `only_starts`, when given, limits the fetch to games with one of those
    scheduled starts (the pre-game session captures one start group at a
    time). Returns `(captures, errors, per_game)` where `per_game` counts the
    markets attempted and the fetch errors for each game.
    """
    now_dt = now.astimezone(timezone.utc)
    wanted = {s.astimezone(timezone.utc) for s in only_starts} if only_starts is not None else None
    captures: list[dict[str, Any]] = []
    errors: list[str] = []
    per_game: dict[GameKey, dict[str, int]] = {}

    for source, market_id, game_id, start_dt in _iter_feed_markets(viewer_dir, now_dt):
        if wanted is not None and start_dt not in wanted:
            continue
        off_min = (start_dt - now_dt).total_seconds() / 60.0
        if not (window_min_after <= off_min <= window_min_before):
            continue
        counts = per_game.setdefault(_game_key(source, game_id, start_dt), {"markets": 0, "errors": 0})
        counts["markets"] += 1
        try:
            if source == "kalshi":
                mkt = _fetch_json(KALSHI_MARKET_URL.format(ticker=market_id))
                ob = _fetch_json(KALSHI_ORDERBOOK_URL.format(ticker=market_id))
                observed_at = datetime.now(timezone.utc)
                captures.append(normalize_kalshi_capture(
                    market_id=market_id,
                    game_id=str(game_id or ""),
                    scheduled_start=start_dt,
                    market_payload=mkt,
                    orderbook_payload=ob,
                    captured_at=observed_at,
                    prev_volume_total=prev_vols.get(("kalshi", market_id)),
                ))
            else:
                bk = _fetch_json(POLYMARKET_BOOK_URL.format(token_id=market_id))
                gm = _fetch_json(POLYMARKET_GAMMA_URL.format(token_id=market_id))
                observed_at = datetime.now(timezone.utc)
                captures.append(normalize_polymarket_capture(
                    market_id=market_id,
                    game_id=str(game_id or ""),
                    scheduled_start=start_dt,
                    book_payload=bk,
                    gamma_payload=gm,
                    captured_at=observed_at,
                    prev_volume_total=prev_vols.get(("polymarket", market_id)),
                ))
        except Exception as exc:
            counts["errors"] += 1
            errors.append(f"{source.upper()}_FETCH_FAILED:{market_id}:{exc}")
    return captures, errors, per_game


def run_live_capture(
    *,
    viewer_dir: Path = Path("viewer"),
    jsonl_path: Path = Path("data/close_quality_captures.jsonl"),
    status_path: Path = Path("data/close_quality_capture_status.json"),
    window_min_before: float = 75.0,
    window_min_after: float = -10.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Capture bid/ask/depth/volume for markets whose scheduled start is within window."""
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    existing, _, _ = load_captures_jsonl(jsonl_path)
    prev_vols = _last_volume_by_market(existing, now_dt)
    captures, errors, _ = collect_live_captures(
        viewer_dir=viewer_dir,
        prev_vols=prev_vols,
        now=now_dt,
        window_min_before=window_min_before,
        window_min_after=window_min_after,
    )
    return append_captures(
        jsonl_path=jsonl_path,
        status_path=status_path,
        captures=captures,
        fetch_errors=errors,
        now=now_dt,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Additive close-quality capture.")
    parser.add_argument("--jsonl", type=Path, default=Path("data/close_quality_captures.jsonl"))
    parser.add_argument("--status", type=Path, default=Path("data/close_quality_capture_status.json"))
    args = parser.parse_args()
    res = run_live_capture(jsonl_path=args.jsonl, status_path=args.status)
    print(json.dumps(res))


if __name__ == "__main__":
    main()
