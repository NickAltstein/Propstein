"""Close matching. A line change is unmatched and is never a price-only comparison."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.crypto_utils import canonical_json_sha256

LINE_CHANGED = "LINE_CHANGED"
CLOSE_EXACT = "CLOSE_EXACT"
CLOSE_TRANSLATED = "CLOSE_TRANSLATED"
CLOSE_UNEVALUABLE = "CLOSE_UNEVALUABLE"
CLOSE_WIDE_SPREAD = "CLOSE_WIDE_SPREAD"
CLOSE_THIN_DEPTH = "CLOSE_THIN_DEPTH"
AMENDMENT_003_EFFECTIVE_AT = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)

TRANSLATION_METHOD_SPEC = {
    "schema": "propwire.ladder_translation_method.v1",
    "method": "linear_interpolation_between_adjacent_closing_rungs",
    "devig": "multiplicative_two_way",
    "requires_bracketing_rungs": True,
    "uses_model_distribution": False,
}
TRANSLATION_METHOD_HASH = canonical_json_sha256(TRANSLATION_METHOD_SPEC)

_CLOSE_QUALITY_CONFIG_PATH = Path(__file__).resolve().parents[1] / "contracts" / "close_quality_config.json"


def load_close_quality_config() -> dict[str, Any]:
    return json.loads(_CLOSE_QUALITY_CONFIG_PATH.read_text(encoding="utf-8"))
MARKET_MISMATCH = "MARKET_MISMATCH"
SELECTION_MISMATCH = "SELECTION_MISMATCH"
SETTLEMENT_RULES_MISMATCH = "SETTLEMENT_RULES_MISMATCH"
MISSING_SETTLEMENT_RULES = "MISSING_SETTLEMENT_RULES"
EVENT_MISMATCH = "EVENT_MISMATCH"
SOURCE_MISMATCH = "SOURCE_MISMATCH"
CLOSE_AFTER_START = "CLOSE_AFTER_START"
CLOSE_UNKNOWN_START = "CLOSE_UNKNOWN_START"


@dataclass(frozen=True)
class CloseQuote:
    event_id: str
    market: str
    selection: str
    line: float
    settlement_rules: str | None
    over_american_odds: int | None = None
    under_american_odds: int | None = None
    quote_source: str = "unknown"
    quoted_at: str | None = None
    scheduled_start: str | None = None
    pre_start: bool | str | None = None


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    reason_code: str | None


def _same_line(left: float, right: float) -> bool:
    return round(float(left), 4) == round(float(right), 4)


def match_close(expected: CloseQuote, observed: CloseQuote) -> MatchResult:
    """Require the same event, market, selection, line, settlement rules, and quote source.

    Odds are not read unless every identity field matches. A different line
    returns LINE_CHANGED and does not compare prices. A different quote source
    returns SOURCE_MISMATCH and is not a price comparison.
    """
    if expected.event_id != observed.event_id:
        return MatchResult(False, EVENT_MISMATCH)
    if expected.market != observed.market:
        return MatchResult(False, MARKET_MISMATCH)
    if expected.selection != observed.selection:
        return MatchResult(False, SELECTION_MISMATCH)
    if not expected.settlement_rules or not observed.settlement_rules:
        return MatchResult(False, MISSING_SETTLEMENT_RULES)
    if expected.settlement_rules != observed.settlement_rules:
        return MatchResult(False, SETTLEMENT_RULES_MISMATCH)
    if _source(expected.quote_source) != _source(observed.quote_source):
        return MatchResult(False, SOURCE_MISMATCH)
    if not _same_line(expected.line, observed.line):
        return MatchResult(False, LINE_CHANGED)

    # Pre-start gate: a close quote must be captured prior to event start
    from src.scheduler.capture_scheduler import compute_pre_start_attribute
    obs_pre, _ = compute_pre_start_attribute(observed.quoted_at, observed.scheduled_start)
    if observed.pre_start is False:
        obs_pre = False

    if obs_pre is False:
        return MatchResult(False, CLOSE_AFTER_START)
    if obs_pre == "unknown":
        return MatchResult(False, CLOSE_UNKNOWN_START)

    return MatchResult(True, None)


def _source(value: str | None) -> str:
    text = (value or "").strip().lower()
    return text or "unknown"


@dataclass(frozen=True)
class LadderRung:
    line: float
    over_american_odds: int
    under_american_odds: int


@dataclass(frozen=True)
class LadderTranslation:
    close_state: str
    entry_line: float
    close_line: float
    entry_devig_p: float
    translated_close_devig_p: float | None
    low_rung: LadderRung | None
    high_rung: LadderRung | None
    interpolation_weight: float | None
    closing_devig_pair: tuple[float, float] | None
    closing_snapshot_hash: str | None
    reason_code: str | None = None
    derived: bool = False
    translation_method_hash: str | None = None
    close_quality_version: str | None = None


def american_to_implied_prob(american: int) -> float:
    odds = int(american)
    if odds == 0:
        raise ValueError("american odds cannot be 0")
    if odds < 0:
        return (-odds) / ((-odds) + 100.0)
    return 100.0 / (odds + 100.0)


def devig_two_way_american(over_american: int, under_american: int) -> tuple[float, float]:
    """Multiplicative two-way de-vig returning (p_over, p_under)."""
    p_over_raw = american_to_implied_prob(over_american)
    p_under_raw = american_to_implied_prob(under_american)
    overround = p_over_raw + p_under_raw
    if overround < 0.98 or overround > 1.45:
        raise ValueError(f"impossible two-way vig overround: {overround:.4f}")
    return (p_over_raw / overround, p_under_raw / overround)


def _parse_utc(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return value.astimezone(timezone.utc)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return dt.astimezone(timezone.utc)


def evaluate_post_amendment_close(
    *,
    event_id: str,
    market: str,
    selection: str,
    entry_line: float,
    entry_devig_p: float,
    observed: CloseQuote,
    closing_ladder: Sequence[LadderRung] = (),
    observed_at: str | datetime | None = None,
    raw_provider_payload_sha256: str = "",
    closing_spread_prob: float | None = None,
    closing_depth_usd: float | None = None,
    close_quality_config: Mapping[str, Any] | None = None,
) -> LadderTranslation:
    """Classify post-amendment close into CLOSE_EXACT, CLOSE_TRANSLATED, or CLOSE_UNEVALUABLE.

    Pre-amendment observations (< AMENDMENT_003_EFFECTIVE_AT) preserve legacy LINE_CHANGED
    without reinterpretation. Translation uses only the closing quoted ladder rungs—never a
    model distribution or a half-yard heuristic without a neighboring bracketing rung.
    """
    obs_dt = _parse_utc(observed_at) or _parse_utc(observed.quoted_at)
    is_post_amendment = obs_dt is None or obs_dt >= AMENDMENT_003_EFFECTIVE_AT
    sel = str(selection).upper()
    if sel not in {"OVER", "UNDER"}:
        raise ValueError(f"selection must be OVER or UNDER, got {selection!r}")

    if observed.over_american_odds is None or observed.under_american_odds is None:
        return LadderTranslation(
            close_state=CLOSE_UNEVALUABLE,
            entry_line=float(entry_line),
            close_line=float(observed.line),
            entry_devig_p=float(entry_devig_p),
            translated_close_devig_p=None,
            low_rung=None,
            high_rung=None,
            interpolation_weight=None,
            closing_devig_pair=None,
            closing_snapshot_hash=None,
            reason_code="INCOMPLETE_PRICE",
        )

    close_pair = devig_two_way_american(observed.over_american_odds, observed.under_american_odds)
    close_side_p = close_pair[0] if sel == "OVER" else close_pair[1]

    cfg = dict(close_quality_config) if close_quality_config is not None else load_close_quality_config()
    cfg_version = str(cfg.get("version") or "unknown")
    venue_cfg = (cfg.get("venues") or {}).get(_source(observed.quote_source))
    if is_post_amendment and isinstance(venue_cfg, Mapping):
        max_spread = float(venue_cfg["max_bid_ask_spread_prob"])
        min_depth = float(venue_cfg["min_top_of_book_depth_usd"])
        if closing_spread_prob is not None and float(closing_spread_prob) > max_spread:
            return LadderTranslation(
                close_state=CLOSE_UNEVALUABLE,
                entry_line=float(entry_line),
                close_line=float(observed.line),
                entry_devig_p=float(entry_devig_p),
                translated_close_devig_p=None,
                low_rung=None,
                high_rung=None,
                interpolation_weight=None,
                closing_devig_pair=(round(close_pair[0], 6), round(close_pair[1], 6)),
                closing_snapshot_hash=None,
                reason_code=CLOSE_WIDE_SPREAD,
                derived=False,
                close_quality_version=cfg_version,
            )
        if closing_depth_usd is not None and float(closing_depth_usd) < min_depth:
            return LadderTranslation(
                close_state=CLOSE_UNEVALUABLE,
                entry_line=float(entry_line),
                close_line=float(observed.line),
                entry_devig_p=float(entry_devig_p),
                translated_close_devig_p=None,
                low_rung=None,
                high_rung=None,
                interpolation_weight=None,
                closing_devig_pair=(round(close_pair[0], 6), round(close_pair[1], 6)),
                closing_snapshot_hash=None,
                reason_code=CLOSE_THIN_DEPTH,
                derived=False,
                close_quality_version=cfg_version,
            )

    if _same_line(entry_line, observed.line):
        v2_payload = {
            "schema": "propwire.closing_snapshot.v2",
            "close_state": CLOSE_EXACT,
            "event_id": event_id,
            "market": market,
            "selection": sel,
            "entry_line": round(float(entry_line), 4),
            "closing_line": round(float(observed.line), 4),
            "closing_over_odds": int(observed.over_american_odds),
            "closing_under_odds": int(observed.under_american_odds),
            "closing_devig_pair": [round(close_pair[0], 6), round(close_pair[1], 6)],
            "translated_close_devig_p": round(close_side_p, 6),
            "quote_source": _source(observed.quote_source),
            "quoted_at": observed.quoted_at,
            "raw_provider_payload_sha256": raw_provider_payload_sha256,
        }
        return LadderTranslation(
            close_state=CLOSE_EXACT,
            entry_line=float(entry_line),
            close_line=float(observed.line),
            entry_devig_p=float(entry_devig_p),
            translated_close_devig_p=round(close_side_p, 6),
            low_rung=None,
            high_rung=None,
            interpolation_weight=0.0,
            closing_devig_pair=(round(close_pair[0], 6), round(close_pair[1], 6)),
            closing_snapshot_hash=canonical_json_sha256(v2_payload),
            reason_code=None,
        )

    if not is_post_amendment:
        return LadderTranslation(
            close_state=LINE_CHANGED,
            entry_line=float(entry_line),
            close_line=float(observed.line),
            entry_devig_p=float(entry_devig_p),
            translated_close_devig_p=None,
            low_rung=None,
            high_rung=None,
            interpolation_weight=None,
            closing_devig_pair=(round(close_pair[0], 6), round(close_pair[1], 6)),
            closing_snapshot_hash=None,
            reason_code=LINE_CHANGED,
        )

    # Deduplicate ladder rungs by rounded line and require bracketing rungs around entry_line.
    by_line: dict[float, LadderRung] = {}
    for rung in closing_ladder:
        by_line[round(float(rung.line), 4)] = rung
    if round(float(observed.line), 4) not in by_line:
        by_line[round(float(observed.line), 4)] = LadderRung(
            line=float(observed.line),
            over_american_odds=int(observed.over_american_odds),
            under_american_odds=int(observed.under_american_odds),
        )
    sorted_rungs = [by_line[k] for k in sorted(by_line.keys())]
    target = round(float(entry_line), 4)

    # Exact rung present inside the closing ladder
    if target in by_line:
        exact_rung = by_line[target]
        rung_pair = devig_two_way_american(exact_rung.over_american_odds, exact_rung.under_american_odds)
        rung_p = rung_pair[0] if sel == "OVER" else rung_pair[1]
        v2_payload = {
            "schema": "propwire.closing_snapshot.v2",
            "close_state": CLOSE_TRANSLATED,
            "derived": True,
            "translation_method_hash": TRANSLATION_METHOD_HASH,
            "event_id": event_id,
            "market": market,
            "selection": sel,
            "entry_line": target,
            "closing_line": round(float(observed.line), 4),
            "low_rung": {"line": target, "over_american_odds": exact_rung.over_american_odds, "under_american_odds": exact_rung.under_american_odds},
            "high_rung": {"line": target, "over_american_odds": exact_rung.over_american_odds, "under_american_odds": exact_rung.under_american_odds},
            "interpolation_weight": 0.0,
            "closing_devig_pair": [round(rung_pair[0], 6), round(rung_pair[1], 6)],
            "translated_close_devig_p": round(rung_p, 6),
            "quote_source": _source(observed.quote_source),
            "quoted_at": observed.quoted_at,
            "raw_provider_payload_sha256": raw_provider_payload_sha256,
        }
        return LadderTranslation(
            close_state=CLOSE_TRANSLATED,
            entry_line=float(entry_line),
            close_line=float(observed.line),
            entry_devig_p=float(entry_devig_p),
            translated_close_devig_p=round(rung_p, 6),
            low_rung=exact_rung,
            high_rung=exact_rung,
            interpolation_weight=0.0,
            closing_devig_pair=(round(rung_pair[0], 6), round(rung_pair[1], 6)),
            closing_snapshot_hash=canonical_json_sha256(v2_payload),
            reason_code=None,
            derived=True,
            translation_method_hash=TRANSLATION_METHOD_HASH,
            close_quality_version=cfg_version,
        )

    low_candidates = [r for r in sorted_rungs if round(float(r.line), 4) < target]
    high_candidates = [r for r in sorted_rungs if round(float(r.line), 4) > target]
    if not low_candidates or not high_candidates:
        return LadderTranslation(
            close_state=CLOSE_UNEVALUABLE,
            entry_line=float(entry_line),
            close_line=float(observed.line),
            entry_devig_p=float(entry_devig_p),
            translated_close_devig_p=None,
            low_rung=None,
            high_rung=None,
            interpolation_weight=None,
            closing_devig_pair=(round(close_pair[0], 6), round(close_pair[1], 6)),
            closing_snapshot_hash=None,
            reason_code="NO_BRACKETING_RUNGS",
        )

    low_rung = low_candidates[-1]
    high_rung = high_candidates[0]
    low_pair = devig_two_way_american(low_rung.over_american_odds, low_rung.under_american_odds)
    high_pair = devig_two_way_american(high_rung.over_american_odds, high_rung.under_american_odds)
    low_p = low_pair[0] if sel == "OVER" else low_pair[1]
    high_p = high_pair[0] if sel == "OVER" else high_pair[1]
    weight = (float(entry_line) - float(low_rung.line)) / (float(high_rung.line) - float(low_rung.line))
    translated_p = round(low_p + weight * (high_p - low_p), 6)

    v2_payload = {
        "schema": "propwire.closing_snapshot.v2",
        "close_state": CLOSE_TRANSLATED,
        "derived": True,
        "translation_method_hash": TRANSLATION_METHOD_HASH,
        "event_id": event_id,
        "market": market,
        "selection": sel,
        "entry_line": target,
        "closing_line": round(float(observed.line), 4),
        "low_rung": {
            "line": round(float(low_rung.line), 4),
            "over_american_odds": int(low_rung.over_american_odds),
            "under_american_odds": int(low_rung.under_american_odds),
            "devig_pair": [round(low_pair[0], 6), round(low_pair[1], 6)],
        },
        "high_rung": {
            "line": round(float(high_rung.line), 4),
            "over_american_odds": int(high_rung.over_american_odds),
            "under_american_odds": int(high_rung.under_american_odds),
            "devig_pair": [round(high_pair[0], 6), round(high_pair[1], 6)],
        },
        "interpolation_weight": round(weight, 6),
        "closing_devig_pair": [round(close_pair[0], 6), round(close_pair[1], 6)],
        "translated_close_devig_p": translated_p,
        "quote_source": _source(observed.quote_source),
        "quoted_at": observed.quoted_at,
        "raw_provider_payload_sha256": raw_provider_payload_sha256,
    }
    return LadderTranslation(
        close_state=CLOSE_TRANSLATED,
        entry_line=float(entry_line),
        close_line=float(observed.line),
        entry_devig_p=float(entry_devig_p),
        translated_close_devig_p=translated_p,
        low_rung=low_rung,
        high_rung=high_rung,
        interpolation_weight=round(weight, 6),
        closing_devig_pair=(round(close_pair[0], 6), round(close_pair[1], 6)),
        closing_snapshot_hash=canonical_json_sha256(v2_payload),
        reason_code=None,
        derived=True,
        translation_method_hash=TRANSLATION_METHOD_HASH,
        close_quality_version=cfg_version,
    )
