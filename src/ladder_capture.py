"""Multi-rung Kalshi ladder capture (NBA points + NFL Week 12 receiving yards/receptions).

Uses Kalshi Trade API v2 `orderbook_fp` (`yes_dollars`, `no_dollars` sorted ascending,
best bid last). Empty bid arrays on HTTP 200 are recorded as `NO_BOOK` with null prices,
never as zero-price quotes. One `snapshot_id` is shared per player-market per tick.
Supports gzipped per-tick files + `index.json` on an orphan `capture-data` branch.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from src.crypto_utils import canonical_json_sha256

KALSHI_EVENT_MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets?event_ticker={event_ticker}&limit=200"
KALSHI_SERIES_MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker={series_ticker}&status=open&limit=200"
KALSHI_ORDERBOOK_URL = "https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}/orderbook?depth=5"
NFL_LADDER_SERIES = ("KXNFLRECYDS", "KXNFLREC")

SCHEMA_VERSION = 1
_ONE_DOLLAR = Decimal("1.0000")
_QUANT = Decimal("0.0001")
DEFAULT_MIN_REQUEST_INTERVAL_SEC = 0.11  # ~9 req/s <= half of Kalshi's 20 reads/s limit
_LAST_FETCH_MONO = 0.0


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).strip()).quantize(_QUANT)
    except (InvalidOperation, ValueError):
        return None


def _parse_bids_fp(levels: Sequence[Any]) -> list[dict[str, str]]:
    """Parse Kalshi `orderbook_fp` `[price_dollars, count_fp]` levels (ascending, best bid last)."""
    parsed: list[tuple[Decimal, Decimal]] = []
    for item in levels:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        px = _dec(item[0])
        sz = _dec(item[1])
        if px is None or sz is None or sz <= 0:
            continue
        parsed.append((px, sz))
    parsed.sort(key=lambda pair: pair[0], reverse=True)
    return [{"price": str(px), "size": str(sz)} for px, sz in parsed[:5]]


def make_snapshot_id(*, market_id: str, captured_at: datetime) -> str:
    """Deterministic snapshot_id for one player-market ladder at one tick timestamp."""
    cap_dt = captured_at.astimezone(timezone.utc)
    return canonical_json_sha256({
        "schema_version": SCHEMA_VERSION,
        "market_id": str(market_id),
        "captured_at": cap_dt.isoformat(),
    })[:24]


def infer_rung_spacing(thresholds: Sequence[float]) -> float | None:
    """Infer positive median rung spacing across sorted unique thresholds."""
    uniq = sorted({float(t) for t in thresholds})
    diffs = [round(b - a, 4) for a, b in zip(uniq, uniq[1:]) if b > a]
    if not diffs:
        return None
    diffs.sort()
    return diffs[len(diffs) // 2]


def normalize_kalshi_ladder_rung(
    *,
    market_id: str,
    ticker: str,
    rung_threshold: float | int | str,
    orderbook_payload: Mapping[str, Any] | None,
    snapshot_id: str,
    captured_at: datetime,
    quoted_at: datetime | str | None = None,
    scheduled_start: datetime | str | None = None,
    rung_spacing: float | None = None,
    sport: str = "nba",
    stat_type: str = "points",
) -> dict[str, Any]:
    """Normalize one Kalshi ladder rung from `orderbook_fp` using exact Decimal arithmetic."""
    cap_dt = captured_at.astimezone(timezone.utc)
    quoted_dt = _parse_utc(quoted_at)
    start_dt = _parse_utc(scheduled_start)
    offset_minutes = round((start_dt - cap_dt).total_seconds() / 60.0, 4) if start_dt else None
    pre_start = bool(cap_dt < start_dt) if start_dt else None

    ob_fp = (orderbook_payload or {}).get("orderbook_fp") or {}
    yes_raw = ob_fp.get("yes_dollars") or []
    no_raw = ob_fp.get("no_dollars") or []

    yes_bids = _parse_bids_fp(yes_raw)
    no_bids = _parse_bids_fp(no_raw)

    best_yes_bid = _dec(yes_bids[0]["price"]) if yes_bids else None
    best_no_bid = _dec(no_bids[0]["price"]) if no_bids else None
    yes_ask = (_ONE_DOLLAR - best_no_bid).quantize(_QUANT) if best_no_bid is not None else None

    yes_depth = yes_bids[0]["size"] if yes_bids else "0.0000"
    no_depth = no_bids[0]["size"] if no_bids else "0.0000"

    if best_yes_bid is None and yes_ask is None:
        status = "NO_BOOK"
        executable = False
    elif best_yes_bid is None:
        status = "ONE_SIDED_NO_ONLY"
        executable = False
    elif yes_ask is None:
        status = "ONE_SIDED_YES_ONLY"
        executable = False
    else:
        status = "OK"
        executable = True

    raw_hash = canonical_json_sha256({"ticker": str(ticker), "orderbook": orderbook_payload})
    return {
        "schema_version": SCHEMA_VERSION,
        "sport": sport,
        "stat_type": stat_type,
        "snapshot_id": str(snapshot_id),
        "market_id": str(market_id),
        "ticker": str(ticker),
        "rung_threshold": float(rung_threshold),
        "rung_spacing": rung_spacing,
        "yes_bid": str(best_yes_bid) if best_yes_bid is not None else None,
        "yes_ask": str(yes_ask) if yes_ask is not None else None,
        "yes_bid_depth": yes_depth,
        "no_bid_depth": no_depth,
        "depth_levels": {"yes_bids": yes_bids, "no_bids": no_bids} if (yes_bids or no_bids) else None,
        "status": status,
        "executable": executable,
        "captured_at": cap_dt.isoformat(),
        "quoted_at": quoted_dt.isoformat() if quoted_dt else None,
        "scheduled_start": start_dt.isoformat() if start_dt else None,
        "offset_minutes": offset_minutes,
        "pre_start": pre_start,
        "raw_payload_sha256": raw_hash,
    }


def _read_text_lines_any(path: Path) -> list[str]:
    if not path.exists():
        return []
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip()]
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_ladder_captures_jsonl(
    jsonl_path: Path,
    *,
    expected_initial_prev_hash: str | None = None,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Load existing ladder captures (.jsonl or .jsonl.gz) and verify hash-chain integrity."""
    lines = _read_text_lines_any(jsonl_path)
    if not lines:
        return [], expected_initial_prev_hash, True
    rows: list[dict[str, Any]] = []
    prev_hash: str | None = expected_initial_prev_hash
    chain_valid = True
    for line in lines:
        row = json.loads(line)
        if row.get("prev_record_hash") != prev_hash:
            chain_valid = False
        body = {k: v for k, v in row.items() if k != "record_hash"}
        if canonical_json_sha256(body) != row.get("record_hash"):
            chain_valid = False
        prev_hash = row.get("record_hash")
        rows.append(row)
    return rows, prev_hash, chain_valid


def _chain_records(
    captures: Sequence[Mapping[str, Any]],
    *,
    initial_prev_hash: str | None,
    seen_ids: set[str],
    snapshot_times: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    prev_hash = initial_prev_hash
    chained: list[dict[str, Any]] = []
    lines: list[str] = []
    for cap in captures:
        rec = dict(cap)
        sid = str(rec.get("snapshot_id") or "")
        cat = str(rec.get("captured_at") or "")
        if not sid or not cat:
            raise ValueError("ladder capture requires non-empty snapshot_id and captured_at")
        if sid in snapshot_times and snapshot_times[sid] != cat:
            raise ValueError(
                f"snapshot_id {sid!r} cannot span different captured_at timestamps "
                f"({snapshot_times[sid]!r} vs {cat!r})"
            )
        snapshot_times[sid] = cat

        record_id = canonical_json_sha256({
            "snapshot_id": sid,
            "ticker": rec.get("ticker"),
            "captured_at": cat,
        })[:24]
        if record_id in seen_ids:
            continue
        seen_ids.add(record_id)
        rec["record_id"] = record_id
        rec["prev_record_hash"] = prev_hash
        rec["record_hash"] = canonical_json_sha256(rec)
        prev_hash = rec["record_hash"]
        chained.append(rec)
        lines.append(json.dumps(rec, sort_keys=True, separators=(",", ":")))
    return chained, lines, prev_hash


def append_ladder_captures(
    *,
    jsonl_path: Path,
    captures: Sequence[Mapping[str, Any]],
    expected_initial_prev_hash: str | None = None,
) -> dict[str, Any]:
    """Append hash-chained ladder captures; enforce one captured_at per snapshot_id."""
    existing, prev_hash, chain_valid = load_ladder_captures_jsonl(
        jsonl_path, expected_initial_prev_hash=expected_initial_prev_hash
    )
    seen_ids = {str(r.get("record_id")) for r in existing}
    snapshot_times: dict[str, str] = {
        str(r.get("snapshot_id")): str(r.get("captured_at"))
        for r in existing
        if r.get("snapshot_id") and r.get("captured_at")
    }
    _, new_lines, last_hash = _chain_records(
        captures,
        initial_prev_hash=prev_hash,
        seen_ids=seen_ids,
        snapshot_times=snapshot_times,
    )
    if new_lines:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as fh:
            for line in new_lines:
                fh.write(line + "\n")

    return {
        "appended": len(new_lines),
        "total_records": len(existing) + len(new_lines),
        "chain_valid": chain_valid,
        "last_record_hash": last_hash,
    }


def write_tick_to_capture_dir(
    *,
    capture_dir: Path,
    captures: Sequence[Mapping[str, Any]],
    tick_time: datetime | None = None,
    expected_cadence_minutes: float = 10.0,
) -> dict[str, Any]:
    """Write one gzipped per-tick JSONL file and update orphan-branch `index.json`."""
    now_dt = (tick_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    index_path = capture_dir / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index = {
            "schema": "propwire.ladder_capture_index.v1",
            "last_record_hash": None,
            "last_record_id": None,
            "last_tick_at": None,
            "total_records": 0,
            "ticks": [],
            "gaps": [],
        }

    prev_tick_dt = _parse_utc(index.get("last_tick_at"))
    if prev_tick_dt is not None:
        gap_min = (now_dt - prev_tick_dt).total_seconds() / 60.0
        if gap_min > expected_cadence_minutes * 1.5:
            index.setdefault("gaps", []).append({
                "from": prev_tick_dt.isoformat(),
                "to": now_dt.isoformat(),
                "gap_minutes": round(gap_min, 2),
                "note": "dropped_tick_chain_continued",
            })

    prev_hash = index.get("last_record_hash")
    chained, lines, last_hash = _chain_records(
        captures,
        initial_prev_hash=prev_hash,
        seen_ids=set(),
        snapshot_times={},
    )
    if not chained:
        return {
            "appended": 0,
            "total_records": int(index.get("total_records", 0)),
            "last_record_hash": prev_hash,
            "tick_file": None,
        }

    rel_path = f"ticks/{now_dt.strftime('%Y-%m-%d')}/tick_{now_dt.strftime('%Y%m%dT%H%M%SZ')}.jsonl.gz"
    tick_path = capture_dir / rel_path
    tick_path.parent.mkdir(parents=True, exist_ok=True)
    payload_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    with tick_path.open("wb") as raw_fh:
        with gzip.GzipFile(fileobj=raw_fh, mode="wb", mtime=0) as gz_fh:
            gz_fh.write(payload_bytes)
    gz_sha256 = hashlib.sha256(tick_path.read_bytes()).hexdigest()

    entry = {
        "file_name": rel_path,
        "sha256": gz_sha256,
        "record_count": len(chained),
        "first_record_id": chained[0]["record_id"],
        "last_record_id": chained[-1]["record_id"],
        "first_prev_record_hash": chained[0].get("prev_record_hash"),
        "last_record_hash": last_hash,
        "captured_at": now_dt.isoformat(),
    }
    index.setdefault("ticks", []).append(entry)
    index["last_record_hash"] = last_hash
    index["last_record_id"] = chained[-1]["record_id"]
    index["last_tick_at"] = now_dt.isoformat()
    index["total_records"] = int(index.get("total_records", 0)) + len(chained)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "appended": len(chained),
        "total_records": index["total_records"],
        "last_record_hash": last_hash,
        "tick_file": rel_path,
        "gaps_recorded": len(index.get("gaps", [])),
    }


def _fetch_json(
    url: str,
    *,
    timeout: float = 10.0,
    min_interval_sec: float = DEFAULT_MIN_REQUEST_INTERVAL_SEC,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Rate-limited GET with exponential backoff on HTTP 429."""
    global _LAST_FETCH_MONO
    for attempt in range(max_retries + 1):
        elapsed = time.monotonic() - _LAST_FETCH_MONO
        if elapsed < min_interval_sec:
            time.sleep(min_interval_sec - elapsed)
        _LAST_FETCH_MONO = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": "PropStein-LadderCapture/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                time.sleep(1.0 * (2 ** attempt))
                continue
            raise
    raise RuntimeError(f"exhausted retries for {url}")


def _event_ticker_from_market_ticker(ticker: str) -> str | None:
    parts = ticker.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return None


def _player_prefix_from_market_ticker(ticker: str) -> str | None:
    parts = ticker.split("-")
    if len(parts) >= 3:
        return f"{parts[0]}-{parts[1]}-{parts[2]}"
    return None


def _capture_ladder_group(
    *,
    prefix: str,
    rungs: Sequence[Mapping[str, Any]],
    scheduled_start: datetime | str | None,
    sport: str,
    stat_type: str,
    captures: list[dict[str, Any]],
    errors: list[str],
) -> None:
    thresholds: list[float] = []
    parsed_rungs: list[tuple[Mapping[str, Any], str, float]] = []
    for rung in rungs:
        rung_ticker = str(rung.get("ticker") or "")
        threshold = rung.get("floor_strike")
        if threshold is None:
            try:
                threshold = float(rung_ticker.rsplit("-", 1)[-1])
            except ValueError:
                continue
        thr_f = float(threshold)
        thresholds.append(thr_f)
        parsed_rungs.append((rung, rung_ticker, thr_f))

    if not parsed_rungs:
        return
    spacing = infer_rung_spacing(thresholds)
    tick_dt = datetime.now(timezone.utc)
    snap_id = make_snapshot_id(market_id=prefix, captured_at=tick_dt)
    for rung, rung_ticker, thr_f in parsed_rungs:
        try:
            ob = _fetch_json(KALSHI_ORDERBOOK_URL.format(ticker=rung_ticker))
            captures.append(normalize_kalshi_ladder_rung(
                market_id=prefix,
                ticker=rung_ticker,
                rung_threshold=thr_f,
                orderbook_payload=ob,
                snapshot_id=snap_id,
                captured_at=tick_dt,
                quoted_at=rung.get("updated_time"),
                scheduled_start=scheduled_start,
                rung_spacing=spacing,
                sport=sport,
                stat_type=stat_type,
            ))
        except Exception as exc:
            errors.append(f"KALSHI_RUNG_FETCH_FAILED:{rung_ticker}:{exc}")


def run_live_ladder_capture(
    *,
    viewer_dir: Path = Path("viewer"),
    jsonl_path: Path | None = Path("data/ladder_captures.jsonl"),
    capture_dir: Path | None = None,
    window_min_before: float = 75.0,
    window_min_after: float = -10.0,
    include_nfl: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Capture all sibling ladder rungs for active Kalshi NBA + NFL player-markets."""
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    captures: list[dict[str, Any]] = []
    errors: list[str] = []

    # 1. Kalshi NBA points ladders
    et_day = now_dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    nba_odds_path = viewer_dir / f"nba_odds_{et_day}.json"
    if nba_odds_path.exists():
        nba_data = json.loads(nba_odds_path.read_text(encoding="utf-8"))
        event_cache: dict[str, list[dict[str, Any]]] = {}
        seen_ladders: set[str] = set()
        for q in nba_data.get("quotes") or ():
            ticker = str(q.get("provider_quote_id") or "").strip()
            start_dt = _parse_utc(q.get("scheduled_start"))
            if not ticker or start_dt is None:
                continue
            off_min = (start_dt - now_dt).total_seconds() / 60.0
            if not (window_min_after <= off_min <= window_min_before):
                continue
            prefix = _player_prefix_from_market_ticker(ticker)
            event_ticker = _event_ticker_from_market_ticker(ticker)
            if not prefix or not event_ticker or prefix in seen_ladders:
                continue
            seen_ladders.add(prefix)
            if event_ticker not in event_cache:
                try:
                    event_cache[event_ticker] = _fetch_json(
                        KALSHI_EVENT_MARKETS_URL.format(event_ticker=event_ticker)
                    ).get("markets") or []
                except Exception as exc:
                    errors.append(f"KALSHI_EVENT_FETCH_FAILED:{event_ticker}:{exc}")
                    event_cache[event_ticker] = []
            rungs = [m for m in event_cache[event_ticker] if str(m.get("ticker") or "").startswith(prefix + "-")]
            _capture_ladder_group(
                prefix=prefix,
                rungs=rungs,
                scheduled_start=start_dt,
                sport="nba",
                stat_type=str(q.get("market") or "points"),
                captures=captures,
                errors=errors,
            )

    # 2. Kalshi NFL receiving yards & receptions ladders (KXNFLRECYDS, KXNFLREC)
    if include_nfl:
        for series in NFL_LADDER_SERIES:
            stat_type = "receiving_yards" if series == "KXNFLRECYDS" else "receptions"
            try:
                mkts = _fetch_json(KALSHI_SERIES_MARKETS_URL.format(series_ticker=series)).get("markets") or []
            except Exception as exc:
                errors.append(f"KALSHI_SERIES_FETCH_FAILED:{series}:{exc}")
                continue
            grouped: dict[str, list[Mapping[str, Any]]] = {}
            for m in mkts:
                t = str(m.get("ticker") or "")
                prefix = _player_prefix_from_market_ticker(t)
                if prefix:
                    grouped.setdefault(prefix, []).append(m)
            for prefix, rungs in grouped.items():
                start_raw = rungs[0].get("expected_expiration_time") or rungs[0].get("close_time")
                start_dt = _parse_utc(start_raw)
                if start_dt is not None:
                    off_min = (start_dt - now_dt).total_seconds() / 60.0
                    if not (window_min_after <= off_min <= window_min_before):
                        continue
                _capture_ladder_group(
                    prefix=prefix,
                    rungs=rungs,
                    scheduled_start=start_dt,
                    sport="nfl",
                    stat_type=stat_type,
                    captures=captures,
                    errors=errors,
                )

    if capture_dir is not None:
        summary = write_tick_to_capture_dir(capture_dir=capture_dir, captures=captures, tick_time=now_dt)
    elif jsonl_path is not None:
        summary = append_ladder_captures(jsonl_path=jsonl_path, captures=captures)
    else:
        summary = {"appended": len(captures), "total_records": len(captures)}
    return {**summary, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Append-only multi-rung Kalshi ladder capture.")
    parser.add_argument("--jsonl", type=Path, default=None)
    parser.add_argument("--capture-dir", type=Path, default=Path("_capture_data"))
    parser.add_argument("--window-before", type=float, default=75.0)
    parser.add_argument("--window-after", type=float, default=-10.0)
    parser.add_argument("--no-nfl", action="store_true")
    args = parser.parse_args()
    res = run_live_ladder_capture(
        jsonl_path=args.jsonl,
        capture_dir=None if args.jsonl is not None else args.capture_dir,
        window_min_before=args.window_before,
        window_min_after=args.window_after,
        include_nfl=not args.no_nfl,
    )
    print(json.dumps(res))


if __name__ == "__main__":
    main()
