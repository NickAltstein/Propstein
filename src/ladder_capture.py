"""Multi-rung Kalshi ladder capture (append-only, hash-chained, Decimal exact).

Uses Kalshi Trade API v2 `orderbook_fp` (`yes_dollars`, `no_dollars` sorted ascending,
best bid last). Empty bid arrays on HTTP 200 are recorded as `NO_BOOK` with null prices,
never as zero-price quotes. One `snapshot_id` is shared per player-market per tick.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
import urllib.request
from zoneinfo import ZoneInfo

from src.crypto_utils import canonical_json_sha256

KALSHI_EVENT_MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets?event_ticker={event_ticker}&limit=200"
KALSHI_ORDERBOOK_URL = "https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}/orderbook?depth=5"

SCHEMA_VERSION = 1
_ONE_DOLLAR = Decimal("1.0000")
_QUANT = Decimal("0.0001")


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
    # Sort descending by bid price so index 0 is best bid, keeping top 5
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


def normalize_kalshi_ladder_rung(
    *,
    market_id: str,
    ticker: str,
    rung_threshold: float | int | str,
    orderbook_payload: Mapping[str, Any] | None,
    snapshot_id: str,
    captured_at: datetime,
    quoted_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Normalize one Kalshi ladder rung from `orderbook_fp` using exact Decimal arithmetic."""
    cap_dt = captured_at.astimezone(timezone.utc)
    quoted_dt = _parse_utc(quoted_at)
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
        "snapshot_id": str(snapshot_id),
        "market_id": str(market_id),
        "ticker": str(ticker),
        "rung_threshold": float(rung_threshold),
        "yes_bid": str(best_yes_bid) if best_yes_bid is not None else None,
        "yes_ask": str(yes_ask) if yes_ask is not None else None,
        "yes_bid_depth": yes_depth,
        "no_bid_depth": no_depth,
        "depth_levels": {"yes_bids": yes_bids, "no_bids": no_bids} if (yes_bids or no_bids) else None,
        "status": status,
        "executable": executable,
        "captured_at": cap_dt.isoformat(),
        "quoted_at": quoted_dt.isoformat() if quoted_dt else None,
        "raw_payload_sha256": raw_hash,
    }


def load_ladder_captures_jsonl(jsonl_path: Path) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Load existing ladder captures and verify hash-chain integrity."""
    if not jsonl_path.exists():
        return [], None, True
    rows: list[dict[str, Any]] = []
    prev_hash: str | None = None
    chain_valid = True
    for raw_line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("prev_record_hash") != prev_hash:
            chain_valid = False
        body = {k: v for k, v in row.items() if k != "record_hash"}
        if canonical_json_sha256(body) != row.get("record_hash"):
            chain_valid = False
        prev_hash = row.get("record_hash")
        rows.append(row)
    return rows, prev_hash, chain_valid


def append_ladder_captures(
    *,
    jsonl_path: Path,
    captures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Append hash-chained ladder captures; enforce one captured_at per snapshot_id."""
    existing, prev_hash, chain_valid = load_ladder_captures_jsonl(jsonl_path)
    seen_ids = {str(r.get("record_id")) for r in existing}
    snapshot_times: dict[str, str] = {
        str(r.get("snapshot_id")): str(r.get("captured_at"))
        for r in existing
        if r.get("snapshot_id") and r.get("captured_at")
    }

    new_lines: list[str] = []
    appended = 0
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
        new_lines.append(json.dumps(rec, sort_keys=True, separators=(",", ":")))
        appended += 1

    if new_lines:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as fh:
            for line in new_lines:
                fh.write(line + "\n")

    return {
        "appended": appended,
        "total_records": len(existing) + appended,
        "chain_valid": chain_valid,
        "last_record_hash": prev_hash,
    }


def _fetch_json(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "PropStein-LadderCapture/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def run_live_ladder_capture(
    *,
    viewer_dir: Path = Path("viewer"),
    jsonl_path: Path = Path("data/ladder_captures.jsonl"),
    window_min_before: float = 75.0,
    window_min_after: float = -10.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Capture all sibling ladder rungs for active Kalshi player-markets."""
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    et_day = now_dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    nba_odds_path = viewer_dir / f"nba_odds_{et_day}.json"
    if not nba_odds_path.exists():
        return {"appended": 0, "total_records": 0, "chain_valid": True, "errors": []}

    nba_data = json.loads(nba_odds_path.read_text(encoding="utf-8"))
    event_cache: dict[str, list[dict[str, Any]]] = {}
    captures: list[dict[str, Any]] = []
    errors: list[str] = []
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
        tick_dt = datetime.now(timezone.utc)
        snap_id = make_snapshot_id(market_id=prefix, captured_at=tick_dt)
        for rung in rungs:
            rung_ticker = str(rung.get("ticker") or "")
            threshold = rung.get("floor_strike")
            if threshold is None:
                try:
                    threshold = float(rung_ticker.rsplit("-", 1)[-1])
                except ValueError:
                    continue
            try:
                ob = _fetch_json(KALSHI_ORDERBOOK_URL.format(ticker=rung_ticker))
                captures.append(normalize_kalshi_ladder_rung(
                    market_id=prefix,
                    ticker=rung_ticker,
                    rung_threshold=threshold,
                    orderbook_payload=ob,
                    snapshot_id=snap_id,
                    captured_at=tick_dt,
                    quoted_at=rung.get("updated_time"),
                ))
            except Exception as exc:
                errors.append(f"KALSHI_RUNG_FETCH_FAILED:{rung_ticker}:{exc}")

    summary = append_ladder_captures(jsonl_path=jsonl_path, captures=captures)
    return {**summary, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Append-only multi-rung Kalshi ladder capture.")
    parser.add_argument("--jsonl", type=Path, default=Path("data/ladder_captures.jsonl"))
    args = parser.parse_args()
    print(json.dumps(run_live_ladder_capture(jsonl_path=args.jsonl)))


if __name__ == "__main__":
    main()
