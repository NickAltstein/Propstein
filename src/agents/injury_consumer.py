"""Injury Consumer for propwire.events.injury: availability_void, availability_uncertain, CONTEXT_DIRTY, STALE_CONTEXT."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Mapping, Sequence

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_ADAPTER_PREFIXES = ("dom", "browser", "scraped", "extension")


def _validate_post_news_tape_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Self-contained tape provenance check so injury consumer does not depend on BookQuoteTape."""
    source_dt = _parse_utc(row.get("source_ts"))
    if source_dt is None:
        return None
    raw_hash = str(row.get("raw_payload_sha256") or "").strip().lower()
    if not _SHA256_RE.match(raw_hash):
        return None
    book = str(row.get("book") or "").strip().lower()
    quote_source = str(row.get("quote_source") or book).strip().lower()
    if not quote_source or any(quote_source.startswith(p) or book.startswith(p) for p in _FORBIDDEN_ADAPTER_PREFIXES):
        return None
    return {
        "market_id": str(row.get("market_id") or ""),
        "source_ts": source_dt.isoformat(),
        "raw_payload_sha256": raw_hash,
    }

VOID_STATUSES = frozenset({"OUT", "INACTIVE", "SCRATCHED", "IR", "REPORTED_OUT"})
UNCERTAIN_STATUSES = frozenset({"QUESTIONABLE", "DOUBTFUL", "LIMITED"})

STATE_AVAILABILITY_VOID = "availability_void"
STATE_AVAILABILITY_UNCERTAIN = "availability_uncertain"
STATE_CONTEXT_DIRTY = "CONTEXT_DIRTY"
STATE_STALE_CONTEXT = "STALE_CONTEXT"
DIAG_ROLE_SURGE_UNCONFIRMED = "ROLE_SURGE_UNCONFIRMED"


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


@dataclass
class ReceiptRecord:
    receipt_id: str
    player_id: str
    team: str
    market: str
    receipt_hash: str
    line: float
    base_projection: float
    base_edge_pp: float
    active: bool = True
    can_execute: bool = True
    excluded_from_n: bool = False
    state: str = "ACTIVE"
    status_ts: str | None = None


@dataclass
class InjuryConsumerState:
    gate_paused: bool = False
    pause_reason: str | None = None
    voided_receipt_ids: set[str] = field(default_factory=set)
    uncertain_receipt_ids: set[str] = field(default_factory=set)
    dirty_receipt_ids: set[str] = field(default_factory=set)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    receipts: dict[str, ReceiptRecord] = field(default_factory=dict)


def consume_injury_events(
    events: Sequence[Mapping[str, Any]],
    *,
    receipts: Sequence[ReceiptRecord],
    model_usage_table: Mapping[str, Mapping[str, Any]],
    news_week: int | str | None,
    board_week: int | str | None,
    capture_week: int | str | None,
    tape_rows: Sequence[Mapping[str, Any]] = (),
    min_edge_pp: float = 10.0,
    max_normal_edge_pp: float = 12.0,
    now: datetime | None = None,
) -> InjuryConsumerState:
    """Process propwire.events.injury deterministically and fail closed on misalignment."""
    state = InjuryConsumerState(
        receipts={r.receipt_id: ReceiptRecord(**vars(r)) for r in receipts}
    )

    # 1. Week alignment guard: news_week, board_week, capture_week must all match.
    if (
        news_week is None
        or board_week is None
        or capture_week is None
        or str(news_week) != str(board_week)
        or str(board_week) != str(capture_week)
    ):
        state.gate_paused = True
        state.pause_reason = STATE_STALE_CONTEXT
        for rec in state.receipts.values():
            rec.active = False
            rec.can_execute = False
            rec.state = STATE_STALE_CONTEXT
        return state

    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    for ev in events:
        player_id = str(ev.get("player_id") or "").strip()
        team = str(ev.get("team") or "").strip().upper()
        status = str(ev.get("status") or "").strip().upper()
        status_ts_raw = ev.get("status_ts") or ev.get("fetched_at") or now_dt.isoformat()
        status_dt = _parse_utc(status_ts_raw) or now_dt

        if status in VOID_STATUSES:
            # Void the injured player's open receipts: leave /v1/edges, excluded from N, pre-news hash dead.
            for rec in state.receipts.values():
                if rec.player_id == player_id:
                    rec.active = False
                    rec.can_execute = False
                    rec.excluded_from_n = True
                    rec.state = STATE_AVAILABILITY_VOID
                    rec.status_ts = status_dt.isoformat()
                    state.voided_receipt_ids.add(rec.receipt_id)

            # model_usage_table may be used only to record which teammates are affected.
            usage_entry = model_usage_table.get(player_id) or {}
            beneficiary_weights: Mapping[str, float] = usage_entry.get("beneficiary_weights") or {}

            # Quarantine every open teammate receipt as CONTEXT_DIRTY (never raise edge or re-hash).
            for rec in state.receipts.values():
                if rec.team.upper() == team and rec.player_id != player_id:
                    rec.state = STATE_CONTEXT_DIRTY
                    rec.active = False
                    rec.can_execute = False
                    rec.status_ts = status_dt.isoformat()
                    state.dirty_receipt_ids.add(rec.receipt_id)
                    if rec.player_id in beneficiary_weights:
                        state.diagnostics.append({
                            "receipt_id": rec.receipt_id,
                            "player_id": rec.player_id,
                            "code": STATE_CONTEXT_DIRTY,
                            "requires_model_rerun": True,
                        })

        elif status in UNCERTAIN_STATUSES:
            # QUESTIONABLE / DOUBTFUL / LIMITED -> availability_uncertain.
            # Quarantine-only: keep old hash untouched, block action, never re-hash or rerun.
            for rec in state.receipts.values():
                if rec.player_id == player_id:
                    rec.state = STATE_AVAILABILITY_UNCERTAIN
                    rec.status_ts = status_dt.isoformat()
                    rec.active = False
                    rec.can_execute = False
                    state.uncertain_receipt_ids.add(rec.receipt_id)

    return state


def consume_injury_stream(
    stream_bus: Any,
    *,
    receipts: Sequence[ReceiptRecord],
    model_usage_table: Mapping[str, Mapping[str, Any]],
    news_week: int | str | None,
    board_week: int | str | None,
    capture_week: int | str | None,
    tape_rows: Sequence[Mapping[str, Any]] = (),
    topic: str = "propwire.events.injury",
    now: datetime | None = None,
) -> InjuryConsumerState:
    """Read real injury.status_changed events from DurableRedisStreamBus and evaluate state."""
    if hasattr(stream_bus, "recent_entries"):
        raw_records = stream_bus.recent_entries(topic)
    elif hasattr(stream_bus, "read"):
        raw_records = stream_bus.read(topic)
    else:
        raw_records = []
    events: list[dict[str, Any]] = []
    for item in raw_records:
        payload = item.get("payload") if isinstance(item, Mapping) else None
        if isinstance(payload, Mapping):
            inner = payload.get("payload") if isinstance(payload.get("payload"), Mapping) else payload
            events.append({
                "player_id": inner.get("player_id"),
                "team": inner.get("team"),
                "status": inner.get("status"),
                "status_ts": payload.get("fetched_at") or inner.get("fetched_at"),
            })
    return consume_injury_events(
        events,
        receipts=receipts,
        model_usage_table=model_usage_table,
        news_week=news_week,
        board_week=board_week,
        capture_week=capture_week,
        tape_rows=tape_rows,
        now=now,
    )


def load_availability_events_jsonl(jsonl_path: Any) -> list[dict[str, Any]]:
    """Load append-only availability events from JSONL and return effective latest state per player."""
    import json
    from pathlib import Path

    path = Path(jsonl_path)
    if not path.exists():
        return []
    raw_events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw_events.append(json.loads(line))
    return raw_events


def hydrate_bus_from_jsonl(
    jsonl_path: Any,
    stream_bus: Any,
    *,
    topic: str = "propwire.events.injury",
) -> int:
    """Hydrate DurableRedisStreamBus from append-only availability_events.jsonl."""
    raw_events = load_availability_events_jsonl(jsonl_path)
    published = 0
    for ev in raw_events:
        envelope = {
            "event_id": ev.get("event_id"),
            "fetched_at": ev.get("observed_at"),
            "payload": {
                "player_id": ev.get("player_id"),
                "team": ev.get("team"),
                "status": ev.get("category") or ev.get("new_status"),
                "source": ev.get("source"),
                "source_url": ev.get("source_url"),
                "fetched_at": ev.get("observed_at"),
            },
        }
        if hasattr(stream_bus, "xadd"):
            stream_bus.xadd(topic, envelope, source_hash=ev.get("raw_payload_sha256"))
            published += 1
        elif hasattr(stream_bus, "publish"):
            stream_bus.publish(topic, envelope)
            published += 1
    return published


def consume_availability_jsonl(
    jsonl_path: Any,
    *,
    receipts: Sequence[ReceiptRecord],
    model_usage_table: Mapping[str, Mapping[str, Any]],
    news_week: int | str | None,
    board_week: int | str | None,
    capture_week: int | str | None,
    tape_rows: Sequence[Mapping[str, Any]] = (),
    now: datetime | None = None,
) -> InjuryConsumerState:
    """Consume append-only availability_events.jsonl directly into InjuryConsumerState."""
    raw_events = load_availability_events_jsonl(jsonl_path)
    latest_by_player: dict[str, dict[str, Any]] = {}
    for ev in raw_events:
        pid = str(ev.get("player_id") or "").strip()
        if not pid:
            continue
        cat = str(ev.get("category") or ev.get("new_status") or "").strip().upper()
        if cat == "ACTIVE":
            latest_by_player.pop(pid, None)
            continue
        latest_by_player[pid] = {
            "player_id": pid,
            "team": ev.get("team"),
            "status": cat,
            "status_ts": ev.get("observed_at"),
            "source": ev.get("source"),
            "source_url": ev.get("source_url"),
        }
    return consume_injury_events(
        list(latest_by_player.values()),
        receipts=receipts,
        model_usage_table=model_usage_table,
        news_week=news_week,
        board_week=board_week,
        capture_week=capture_week,
        tape_rows=tape_rows,
        now=now,
    )
