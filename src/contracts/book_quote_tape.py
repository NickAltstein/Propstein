"""BookQuoteTape: timestamped, provenance-verified quote tape from licensed sources only."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

LICENSED_QUOTE_SOURCES: frozenset[str] = frozenset({
    "polymarket",
    "kalshi",
    "circa",
    "pinnacle",
    "odds_api:circa",
    "odds_api:pinnacle",
    "odds_api:fanduel",
    "odds_api:draftkings",
    "odds_api:betmgm",
    "odds_api:caesars",
    "odds_api:bovada",
})

FORBIDDEN_ADAPTER_PREFIXES = ("dom", "browser", "scraped", "extension")
DEFAULT_MAX_TAPE_AGE_SECONDS = 5400.0  # 90 minutes (verification.v1 pregame window)


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if not (text.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", text)):
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def is_licensed_source(quote_source: Any, book: Any = None) -> bool:
    src = str(quote_source or "").strip().lower()
    bk = str(book or "").strip().lower()
    if any(src.startswith(prefix) or bk.startswith(prefix) for prefix in FORBIDDEN_ADAPTER_PREFIXES):
        return False
    if src in LICENSED_QUOTE_SOURCES:
        return True
    if not src and bk in LICENSED_QUOTE_SOURCES:
        return True
    return False


@dataclass(frozen=True)
class BookQuoteTapeRow:
    market_id: str
    line: float
    book: str
    american: int
    devig_p: float
    source_ts: str
    observed_at: str
    raw_payload_sha256: str
    quote_source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_tape_row(
    row: BookQuoteTapeRow | Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: float = DEFAULT_MAX_TAPE_AGE_SECONDS,
) -> dict[str, Any] | None:
    """Validate provenance, licensed source, and freshness.

    Returns canonical edge tape entry dict with age_seconds, or None if invalid/stale.
    Stale or un-timestamped rows return None (absent from /v1/edges, never routed to diagnostics).
    """
    data = row.as_dict() if isinstance(row, BookQuoteTapeRow) else dict(row)
    source_dt = _parse_utc(data.get("source_ts"))
    observed_dt = _parse_utc(data.get("observed_at"))
    if source_dt is None or observed_dt is None:
        return None

    raw_hash = str(data.get("raw_payload_sha256") or "").strip().lower()
    if not _SHA256_RE.match(raw_hash):
        return None

    book = str(data.get("book") or "").strip()
    quote_source = str(data.get("quote_source") or book).strip().lower()
    if not is_licensed_source(quote_source, book):
        return None

    try:
        market_id = str(data["market_id"]).strip()
        line = float(data["line"])
        american = int(data["american"])
        devig_p = float(data["devig_p"])
    except (KeyError, TypeError, ValueError):
        return None

    if not market_id or american == 0 or not (0.0 < devig_p < 1.0):
        return None

    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_seconds = (now_dt - source_dt).total_seconds()
    if age_seconds < 0.0 or age_seconds > float(max_age_seconds):
        return None

    return {
        "market_id": market_id,
        "book": book or quote_source,
        "quote_source": quote_source,
        "line": line,
        "american": american,
        "devig_p": round(devig_p, 6),
        "source_ts": source_dt.isoformat(),
        "observed_at": observed_dt.isoformat(),
        "age_seconds": round(age_seconds, 3),
        "raw_payload_sha256": raw_hash,
    }


def filter_fresh_tape(
    rows: Sequence[BookQuoteTapeRow | Mapping[str, Any]],
    *,
    now: datetime | None = None,
    max_age_seconds: float = DEFAULT_MAX_TAPE_AGE_SECONDS,
) -> list[dict[str, Any]]:
    """Return only fresh, licensed, provenance-verified tape rows."""
    out: list[dict[str, Any]] = []
    for row in rows:
        validated = validate_tape_row(row, now=now, max_age_seconds=max_age_seconds)
        if validated is not None:
            out.append(validated)
    return out


def best_price_view(
    rows: Sequence[BookQuoteTapeRow | Mapping[str, Any]],
    *,
    market_id: str | None = None,
    now: datetime | None = None,
    max_age_seconds: float = DEFAULT_MAX_TAPE_AGE_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Compute best available price per market_id over fresh timestamped tape rows."""
    fresh = filter_fresh_tape(rows, now=now, max_age_seconds=max_age_seconds)
    best: dict[str, dict[str, Any]] = {}
    for entry in fresh:
        mid = entry["market_id"]
        if market_id is not None and mid != market_id:
            continue
        cur = best.get(mid)
        if cur is None or entry["devig_p"] < cur["devig_p"] or (
            entry["devig_p"] == cur["devig_p"] and entry["age_seconds"] < cur["age_seconds"]
        ):
            best[mid] = entry
    return best
