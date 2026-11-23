"""BookQuoteTape: timestamped, provenance-verified quote tape from licensed sources only."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REGISTRY_PATH = Path(__file__).resolve().parent / "source_evidential_registry.json"

FORBIDDEN_ADAPTER_PREFIXES = ("dom", "browser", "scraped", "extension")
VALID_EVIDENTIAL_CLASSES: frozenset[str] = frozenset({"confirmatory", "shadow"})
VERIFICATION_V1_FRESHNESS_RULES: dict[str, float] = {
    "pregame": 90.0 * 60.0,      # 90 minutes
    "near_start": 15.0 * 60.0,   # 15 minutes
    "in_game": 2.0 * 60.0,       # 2 minutes
}
NEAR_START_WINDOW_SECONDS = 30.0 * 60.0


def load_source_evidential_registry() -> dict[str, str]:
    """Load committed quote_source -> evidential_class ('confirmatory' | 'shadow') registry."""
    raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for src, cls in raw.items():
        norm_src = str(src).strip().lower()
        norm_cls = str(cls).strip().lower()
        if norm_cls in VALID_EVIDENTIAL_CLASSES:
            out[norm_src] = norm_cls
    return out


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
    registry = load_source_evidential_registry()
    if src in registry:
        return True
    if not src and bk in registry:
        return True
    return False


def resolve_freshness_limit_seconds(
    *,
    game_state: str | None = None,
    scheduled_start: Any = None,
    now: datetime | None = None,
    policy_version: str = "verification.v1",
) -> tuple[str, float] | None:
    """Resolve dynamic freshness limit in seconds under verification.v1 rules.

    Returns (resolved_game_state, max_age_seconds), or None if policy_version/state is unknown.
    """
    if policy_version != "verification.v1":
        return None
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if game_state is not None:
        state = str(game_state).strip().lower()
        if state not in VERIFICATION_V1_FRESHNESS_RULES:
            return None
        return state, VERIFICATION_V1_FRESHNESS_RULES[state]

    start_dt = _parse_utc(scheduled_start)
    if start_dt is not None:
        seconds_to_start = (start_dt - now_dt).total_seconds()
        if seconds_to_start <= 0.0:
            return "in_game", VERIFICATION_V1_FRESHNESS_RULES["in_game"]
        if seconds_to_start <= NEAR_START_WINDOW_SECONDS:
            return "near_start", VERIFICATION_V1_FRESHNESS_RULES["near_start"]
        return "pregame", VERIFICATION_V1_FRESHNESS_RULES["pregame"]

    return "pregame", VERIFICATION_V1_FRESHNESS_RULES["pregame"]


def _verify_provenance(data: Mapping[str, Any], expected_sha256: str) -> bool:
    """Recompute SHA-256 directly from raw_payload bytes (no self-signed receipts)."""
    raw_payload = data.get("raw_payload")
    if raw_payload is None:
        return False
    if isinstance(raw_payload, bytes):
        raw_bytes = raw_payload
    elif isinstance(raw_payload, str):
        raw_bytes = raw_payload.encode("utf-8")
    else:
        return False
    return hashlib.sha256(raw_bytes).hexdigest() == expected_sha256


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
    raw_payload: bytes | str | None = None
    game_state: str | None = None
    scheduled_start: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_tape_row(
    row: BookQuoteTapeRow | Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: float | None = None,
    policy_version: str = "verification.v1",
) -> dict[str, Any] | None:
    """Validate provenance, licensed source, evidential class, and dynamic freshness.

    Returns canonical edge tape entry dict with age_seconds and evidential_class, or None if invalid/stale.
    """
    data = row.as_dict() if isinstance(row, BookQuoteTapeRow) else dict(row)
    source_dt = _parse_utc(data.get("source_ts"))
    observed_dt = _parse_utc(data.get("observed_at"))
    if source_dt is None or observed_dt is None:
        return None

    raw_hash = str(data.get("raw_payload_sha256") or "").strip().lower()
    if not _SHA256_RE.match(raw_hash):
        return None
    if not _verify_provenance(data, raw_hash):
        return None

    book = str(data.get("book") or "").strip()
    quote_source = str(data.get("quote_source") or book).strip().lower()
    if not is_licensed_source(quote_source, book):
        return None

    registry = load_source_evidential_registry()
    evidential_class = registry.get(quote_source)
    if evidential_class not in VALID_EVIDENTIAL_CLASSES:
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
    resolved = resolve_freshness_limit_seconds(
        game_state=data.get("game_state"),
        scheduled_start=data.get("scheduled_start"),
        now=now_dt,
        policy_version=policy_version,
    )
    if resolved is None:
        return None
    resolved_state, dynamic_limit = resolved
    effective_limit = float(max_age_seconds) if max_age_seconds is not None else dynamic_limit

    age_seconds = (now_dt - source_dt).total_seconds()
    if age_seconds < 0.0 or age_seconds > effective_limit:
        return None

    return {
        "market_id": market_id,
        "book": book or quote_source,
        "quote_source": quote_source,
        "evidential_class": evidential_class,
        "line": line,
        "american": american,
        "devig_p": round(devig_p, 6),
        "source_ts": source_dt.isoformat(),
        "observed_at": observed_dt.isoformat(),
        "age_seconds": round(age_seconds, 3),
        "game_state": resolved_state,
        "freshness_policy_version": policy_version,
        "max_age_seconds": effective_limit,
        "raw_payload_sha256": raw_hash,
    }


def filter_fresh_tape(
    rows: Sequence[BookQuoteTapeRow | Mapping[str, Any]],
    *,
    now: datetime | None = None,
    max_age_seconds: float | None = None,
    evidential_class: str | None = None,
    policy_version: str = "verification.v1",
) -> list[dict[str, Any]]:
    """Return only fresh, licensed, provenance-verified tape rows, optionally filtered by class."""
    if evidential_class is not None and evidential_class not in VALID_EVIDENTIAL_CLASSES:
        raise ValueError(f"invalid evidential_class={evidential_class!r}")
    out: list[dict[str, Any]] = []
    for row in rows:
        validated = validate_tape_row(
            row,
            now=now,
            max_age_seconds=max_age_seconds,
            policy_version=policy_version,
        )
        if validated is None:
            continue
        if evidential_class is not None and validated["evidential_class"] != evidential_class:
            continue
        out.append(validated)
    return out


def best_price_view(
    rows: Sequence[BookQuoteTapeRow | Mapping[str, Any]],
    *,
    evidential_class: str = "confirmatory",
    market_id: str | None = None,
    now: datetime | None = None,
    max_age_seconds: float | None = None,
    policy_version: str = "verification.v1",
) -> dict[str, dict[str, Any]]:
    """Compute best available price per market_id within a single evidential_class (never pooled)."""
    if evidential_class not in VALID_EVIDENTIAL_CLASSES:
        raise ValueError(f"best_price_view requires a single valid evidential_class, got {evidential_class!r}")
    fresh = filter_fresh_tape(
        rows,
        now=now,
        max_age_seconds=max_age_seconds,
        evidential_class=evidential_class,
        policy_version=policy_version,
    )
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


def best_price_views_by_class(
    rows: Sequence[BookQuoteTapeRow | Mapping[str, Any]],
    *,
    market_id: str | None = None,
    now: datetime | None = None,
    max_age_seconds: float | None = None,
    policy_version: str = "verification.v1",
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return confirmatory and shadow best_price_view maps separately, never pooled."""
    return {
        "confirmatory": best_price_view(
            rows,
            evidential_class="confirmatory",
            market_id=market_id,
            now=now,
            max_age_seconds=max_age_seconds,
            policy_version=policy_version,
        ),
        "shadow": best_price_view(
            rows,
            evidential_class="shadow",
            market_id=market_id,
            now=now,
            max_age_seconds=max_age_seconds,
            policy_version=policy_version,
        ),
    }


def sanitize_legacy_edge_rows(
    records: Sequence[Mapping[str, Any]],
    fresh_tape: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Mark legacy DB rows without a verified fresh tape row as non_actionable_reference with zeroed action fields."""
    verified_markets = {str(r.get("market_id") or "").strip() for r in fresh_tape}
    out: list[dict[str, Any]] = []
    for row in records:
        item = dict(row)
        mid = str(item.get("market_id") or "").strip()
        if mid and mid in verified_markets:
            out.append(item)
            continue
        item["reference_status"] = "non_actionable_reference"
        item["non_actionable_reference"] = True
        item["actionable"] = False
        item["actionable_stake"] = 0.0
        item["stake"] = 0.0
        item["qualified"] = False
        item["can_execute"] = False
        item["recommended_bet"] = None
        item["edge"] = 0.0
        out.append(item)
    return out
