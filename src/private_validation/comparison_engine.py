"""
PropStein PRIVATE_VALIDATION — Pre-Bet Quote-Integrity & Comparison Engine
==========================================================================
Strictly scoped to PRIVATE_VALIDATION, pregame, full-game NBA 3+ and 4+ made
threes on DraftKings and FanDuel ONLY.

Non-negotiable invariants:
  1. Fail-closed health: per-book, per-run HealthObservation is re-evaluated at
     render time; unavailable, unknown, malformed, timed-out, transport-error,
     or stale health forces COMPARISON_SUPPRESSED (even with cached quotes).
  2. Strict newest-eligible selection per book: deterministic ordering
     (observed_at desc, received_at desc, source_ts desc, observation_seq desc,
     run_id desc). Never picks an older better price over a newer worse price,
     and never falls back to an older cached quote when the newest observation
     is malformed or fails integrity.
  3. Double-enforced fixture/test exclusion: rejected at ingestion/persistence
     AND re-checked at read/eligibility time.
  4. Strict provenance separation: SlipSnapshot is never promoted to a
     BookObservation.
  5. Exact canonical identity matching only: (game_id, player_id, market,
     period, rung). No fuzzy-name fallback.
  6. actionable is ALWAYS False; zero edge/Kelly/wager-placement copy.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ALLOWED_BOOKS: frozenset[str] = frozenset({"draftkings", "fanduel"})
ALLOWED_MARKET = "nba_threes"
ALLOWED_PERIOD = "full_game"
ALLOWED_RUNGS: frozenset[int] = frozenset({3, 4})

# Explicit numeric freshness thresholds (seconds), re-evaluated at render time.
QUOTE_FRESHNESS_MAX_SECONDS = 30.0
HEALTH_FRESHNESS_MAX_SECONDS = 30.0
MAX_FUTURE_CLOCK_SKEW_SECONDS = 5.0
TEN_SECOND_BUDGET_MS = 10_000

VALID_HEALTH_STATUSES: frozenset[str] = frozenset({
    "HEALTHY",
    "UNAVAILABLE",
    "UNKNOWN",
    "MALFORMED",
    "TIMEOUT",
    "TRANSPORT_ERROR",
})

_FORBIDDEN_PROVENANCE_RE = re.compile(r"fixture|synthetic|test|seed|replay|demo", re.IGNORECASE)
_FORBIDDEN_TEST_HEADERS: frozenset[str] = frozenset({
    "x-propstein-fixture",
    "x-test-seed",
    "x-replay-mode",
    "x-seeded-quote",
    "x-synthetic-quote",
})
_EPOCH_MIN = datetime(1970, 1, 1, tzinfo=timezone.utc)


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


def american_to_decimal(odds: Any) -> float | None:
    """Convert signed American odds (|odds| >= 100) to decimal payout, or None."""
    if isinstance(odds, bool) or odds is None:
        return None
    try:
        n = int(odds)
    except (TypeError, ValueError):
        return None
    if abs(n) < 100 or abs(n) > 100_000:
        return None
    if n > 0:
        return 1.0 + (n / 100.0)
    return 1.0 + (100.0 / abs(n))


def fmt_american(odds: int | None) -> str:
    if odds is None:
        return "—"
    return f"+{odds}" if odds > 0 else f"{odds}"


def _book_label(book: str) -> str:
    return "DK" if book == "draftkings" else "FD" if book == "fanduel" else book.upper()


# ---------------------------------------------------------------------------
# Strictly separated domain types (never promoted between each other)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SlipSnapshot:
    """User-entered, pasted, or screenshot-derived slip information.
    NEVER promoted to a BookObservation.
    """
    slip_id: str
    check_version: int
    player_id: str
    player_name: str
    game_id: str
    market: str
    period: str
    rung: int
    book: str
    captured_price: int
    captured_at: str
    capture_method: str = "paste"  # "paste" | "manual" | "screenshot"


@dataclass(frozen=True)
class BookObservation:
    """A quote obtained through an approved private adapter."""
    book: str
    game_id: str
    player_id: str
    market: str
    period: str
    rung: int
    american_odds: int | None
    observed_at: str
    received_at: str
    adapter_id: str
    run_id: str
    source_env: str = "PRIVATE_VALIDATION"
    source_ts: str | None = None
    observation_seq: int = 0
    is_fixture: bool = False
    is_seeded: bool = False
    is_replayed: bool = False
    is_test: bool = False
    provenance: str = "private_adapter"
    raw_artifact_sha256: str | None = None
    malformed_reason: str | None = None

    def canonical_key(self) -> tuple[str, str, str, str, str, int]:
        return (
            self.book.strip().lower(),
            self.game_id.strip(),
            self.player_id.strip(),
            self.market.strip().lower(),
            self.period.strip().lower(),
            int(self.rung) if isinstance(self.rung, int) and not isinstance(self.rung, bool) else -1,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HealthObservation:
    """Per-book, per-comparison-run feed-health validation result."""
    book: str
    run_id: str
    status: str  # HEALTHY | UNAVAILABLE | UNKNOWN | MALFORMED | TIMEOUT | TRANSPORT_ERROR
    checked_at: str
    latency_ms: float | None = None
    detail: str = ""


@dataclass(frozen=True)
class TenSecondTrace:
    """Stage timestamps (ms since epoch) for the 10-second pre-bet flow."""
    t0_capture_start_ms: int
    t_capture_complete_ms: int
    t_answer_rendered_ms: int
    t_correction_open_ms: int | None = None
    t_correction_complete_ms: int | None = None
    t_dk_adapter_ms: int | None = None
    t_fd_adapter_ms: int | None = None
    t_return_to_book_ms: int | None = None

    def summary(self) -> dict[str, Any]:
        capture_ms = max(0, self.t_capture_complete_ms - self.t0_capture_start_ms)
        correction_ms = (
            max(0, self.t_correction_complete_ms - self.t_correction_open_ms)
            if self.t_correction_open_ms is not None and self.t_correction_complete_ms is not None
            else 0
        )
        total_ms = max(0, self.t_answer_rendered_ms - self.t0_capture_start_ms)
        return_ms = (
            max(0, self.t_return_to_book_ms - self.t_answer_rendered_ms)
            if self.t_return_to_book_ms is not None
            else None
        )
        return {
            "capture_ms": capture_ms,
            "correction_ms": correction_ms,
            "dk_adapter_ms": (
                max(0, self.t_dk_adapter_ms - self.t_capture_complete_ms)
                if self.t_dk_adapter_ms is not None
                else None
            ),
            "fd_adapter_ms": (
                max(0, self.t_fd_adapter_ms - self.t_capture_complete_ms)
                if self.t_fd_adapter_ms is not None
                else None
            ),
            "capture_to_answer_ms": total_ms,
            "return_to_book_ms": return_ms,
            "within_ten_seconds": total_ms <= TEN_SECOND_BUDGET_MS,
            "budget_ms": TEN_SECOND_BUDGET_MS,
        }


@dataclass(frozen=True)
class ComparisonVerdict:
    state: str  # COMPARISON_COMPLETE | COMPARISON_INCOMPLETE | COMPARISON_SUPPRESSED
    variant: str
    reason_code: str
    headline: str
    evidence_copy: str
    return_to_book_available: bool
    actionable: bool = False  # Permanently False throughout this sprint
    check_version: int = 1
    slip_price_differs_from_latest_book: bool = False
    slip_captured_price: int | None = None
    dk_observation: dict[str, Any] | None = None
    fd_observation: dict[str, Any] | None = None
    health_summary: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Provenance & fixture/test exclusion (double-enforced: ingest + read)
# ---------------------------------------------------------------------------


def is_fixture_or_test_observation(
    obs: BookObservation | Mapping[str, Any],
    headers: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    """Return (True, reason) if observation carries fixture/seeded/replayed/test provenance."""
    if headers:
        lower_headers = {str(k).strip().lower(): str(v).strip().lower() for k, v in headers.items()}
        for forbidden in _FORBIDDEN_TEST_HEADERS:
            if forbidden in lower_headers and lower_headers[forbidden] not in ("", "0", "false"):
                return True, f"forbidden test header {forbidden}"

    data = obs.as_dict() if isinstance(obs, BookObservation) else dict(obs)
    for flag in ("is_fixture", "is_seeded", "is_replayed", "is_test", "_fixture", "fixture", "synthetic", "seeded", "replayed"):
        if data.get(flag) is True:
            return True, f"forbidden boolean flag {flag}=True"

    source_env = str(data.get("source_env") or "").strip().upper()
    if source_env != "PRIVATE_VALIDATION":
        return True, f"source_env={source_env!r} is not PRIVATE_VALIDATION"

    provenance = str(data.get("provenance") or "").strip()
    if not provenance or _FORBIDDEN_PROVENANCE_RE.search(provenance):
        return True, f"forbidden provenance={provenance!r}"

    adapter_id = str(data.get("adapter_id") or "").strip()
    if not adapter_id or _FORBIDDEN_PROVENANCE_RE.search(adapter_id):
        return True, f"forbidden adapter_id={adapter_id!r}"

    return False, ""


def _ordering_key(obs: BookObservation) -> tuple[datetime, datetime, datetime, int, str]:
    """Deterministic 'newer' ordering tuple per book.

    Order:
      1. observed_at (UTC) — when the approved private adapter saw the price
      2. received_at (UTC) — when the engine ingested it (handles equal observed_at / out-of-order)
      3. source_ts (UTC or epoch min) — book-supplied timestamp when present
      4. observation_seq — monotonic adapter sequence
      5. run_id — lexical tie-breaker
    """
    obs_dt = _parse_utc(obs.observed_at) or _EPOCH_MIN
    rec_dt = _parse_utc(obs.received_at) or _EPOCH_MIN
    src_dt = _parse_utc(obs.source_ts) or _EPOCH_MIN
    return (obs_dt, rec_dt, src_dt, int(obs.observation_seq), str(obs.run_id or ""))


# ---------------------------------------------------------------------------
# Isolated PRIVATE_VALIDATION observation store (never read by public /check/)
# ---------------------------------------------------------------------------


class PrivateObservationStore:
    """In-memory + optional JSONL-persisted store for PRIVATE_VALIDATION BookObservations.

    Enforces:
      - Fixture/seeded/replayed/test rejection at ingest AND on reload.
      - Strict newer-only replacement per (book, game_id, player_id, market, period, rung).
      - Out-of-order delivery never resurrects an older observation.
      - Malformed newest observation overwrites older cache as a fail-closed tombstone
        so evaluation never falls back to a stale/older price.
    """

    def __init__(self, persist_path: Path | None = None) -> None:
        self._persist_path = persist_path
        self._latest: dict[tuple[str, str, str, str, str, int], BookObservation] = {}
        if persist_path and persist_path.is_file():
            self._reload()

    def _reload(self) -> None:
        assert self._persist_path is not None
        for line in self._persist_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                obs = BookObservation(**raw)
            except Exception:
                continue
            contaminated, _ = is_fixture_or_test_observation(obs)
            if contaminated:
                continue
            self._apply_if_newer(obs)

    def _apply_if_newer(self, obs: BookObservation) -> bool:
        key = obs.canonical_key()
        existing = self._latest.get(key)
        if existing is not None and _ordering_key(obs) <= _ordering_key(existing):
            return False
        self._latest[key] = obs
        return True

    def ingest(
        self,
        obs: BookObservation,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[bool, str]:
        if isinstance(obs, SlipSnapshot):
            return False, "SlipSnapshot can never be promoted to BookObservation"
        contaminated, reason = is_fixture_or_test_observation(obs, headers=headers)
        if contaminated:
            return False, f"REJECTED_FIXTURE_OR_TEST: {reason}"

        book = obs.book.strip().lower()
        if book not in ALLOWED_BOOKS:
            return False, f"UNSUPPORTED_BOOK: {obs.book}"
        if obs.market.strip().lower() != ALLOWED_MARKET or obs.period.strip().lower() != ALLOWED_PERIOD:
            return False, f"UNSUPPORTED_SCOPE: {obs.market}/{obs.period}"
        if obs.rung not in ALLOWED_RUNGS:
            return False, f"UNSUPPORTED_RUNG: {obs.rung}"

        applied = self._apply_if_newer(obs)
        if not applied:
            return False, "IGNORED_OLDER_OUT_OF_ORDER"

        if self._persist_path is not None:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._persist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obs.as_dict()) + "\n")
        return True, "STORED"

    def get_latest(
        self,
        *,
        book: str,
        game_id: str,
        player_id: str,
        market: str = ALLOWED_MARKET,
        period: str = ALLOWED_PERIOD,
        rung: int = 4,
    ) -> BookObservation | None:
        key = (book.strip().lower(), game_id.strip(), player_id.strip(), market.strip().lower(), period.strip().lower(), int(rung))
        return self._latest.get(key)

    def _inject_raw_for_contamination_test(self, obs: BookObservation) -> None:
        """Test-only helper simulating a corrupted cache/DB to prove read-time exclusion."""
        self._latest[obs.canonical_key()] = obs


# ---------------------------------------------------------------------------
# Health & Quote Evaluation (re-evaluated at render_now)
# ---------------------------------------------------------------------------


def _evaluate_book_health(
    health: HealthObservation | None,
    *,
    book: str,
    run_id: str,
    render_now: datetime,
    max_health_age_seconds: float,
) -> tuple[bool, str, dict[str, Any]]:
    if health is None:
        return False, "HEALTH_UNAVAILABLE", {"book": book, "status": "UNAVAILABLE", "reason": "missing health observation"}
    if health.book.strip().lower() != book:
        return False, "HEALTH_MALFORMED", {"book": book, "status": "MALFORMED", "reason": "health book mismatch"}
    if health.run_id != run_id:
        return False, "HEALTH_STALE", {"book": book, "status": "STALE", "reason": "health run_id mismatch"}
    status = str(health.status or "").strip().upper()
    if status not in VALID_HEALTH_STATUSES:
        return False, "HEALTH_UNKNOWN", {"book": book, "status": status or "UNKNOWN", "reason": "unrecognized health status"}
    checked_dt = _parse_utc(health.checked_at)
    if checked_dt is None:
        return False, "HEALTH_MALFORMED", {"book": book, "status": "MALFORMED", "reason": "unparseable checked_at"}

    age_sec = (render_now - checked_dt).total_seconds()
    if age_sec < -MAX_FUTURE_CLOCK_SKEW_SECONDS:
        return False, "HEALTH_MALFORMED", {"book": book, "status": "MALFORMED", "reason": "future health timestamp"}
    if status != "HEALTHY":
        return False, f"HEALTH_{status}", {"book": book, "status": status, "age_seconds": round(age_sec, 3), "detail": health.detail}
    if age_sec > max_health_age_seconds:
        return False, "HEALTH_STALE", {"book": book, "status": "STALE", "age_seconds": round(age_sec, 3)}
    return True, "OK", {"book": book, "status": "HEALTHY", "age_seconds": round(max(0.0, age_sec), 3)}


def _validate_observation_at_render(
    obs: BookObservation | None,
    *,
    render_now: datetime,
    max_quote_age_seconds: float,
) -> tuple[str, dict[str, Any] | None, str]:
    """Validate one book's latest observation at render time.

    Returns (status, serialized_obs_or_none, detail):
      - "FRESH"
      - "MISSING"
      - "STALE"
      - "CONTAMINATED"   (triggers COMPARISON_SUPPRESSED)
      - "INVALID_NEWEST" (triggers COMPARISON_SUPPRESSED — never falls back to older quote)
    """
    if obs is None:
        return "MISSING", None, "unavailable"

    # Second-line fixture/test check at read/eligibility time (P0-3)
    contaminated, cont_reason = is_fixture_or_test_observation(obs)
    if contaminated:
        return "CONTAMINATED", None, cont_reason

    if obs.malformed_reason:
        return "INVALID_NEWEST", None, f"newest observation malformed: {obs.malformed_reason}"

    dec = american_to_decimal(obs.american_odds)
    if dec is None:
        return "INVALID_NEWEST", None, "newest observation has invalid American odds"

    obs_dt = _parse_utc(obs.observed_at)
    rec_dt = _parse_utc(obs.received_at)
    if obs_dt is None or rec_dt is None:
        return "INVALID_NEWEST", None, "newest observation has unparseable timestamp"

    age_sec = (render_now - obs_dt).total_seconds()
    if age_sec < -MAX_FUTURE_CLOCK_SKEW_SECONDS:
        return "INVALID_NEWEST", None, "newest observation timestamp exceeds future clock skew limit"

    payload = {
        "book": obs.book,
        "american_odds": obs.american_odds,
        "decimal_odds": round(dec, 6),
        "observed_at": obs_dt.isoformat(),
        "received_at": rec_dt.isoformat(),
        "age_seconds": round(max(0.0, age_sec), 1),
        "adapter_id": obs.adapter_id,
        "run_id": obs.run_id,
        "source_env": obs.source_env,
        "raw_artifact_sha256": obs.raw_artifact_sha256,
    }
    if age_sec > max_quote_age_seconds:
        return "STALE", payload, f"stale ({int(round(age_sec))}s ago)"
    return "FRESH", payload, f"observed {int(round(max(0.0, age_sec)))}s ago"


def evaluate_private_comparison(
    slip: SlipSnapshot,
    *,
    store: PrivateObservationStore,
    dk_health: HealthObservation | None,
    fd_health: HealthObservation | None,
    run_id: str,
    render_now: datetime,
    expected_check_version: int | None = None,
    private_validation_enabled: bool = True,
    max_quote_age_seconds: float = QUOTE_FRESHNESS_MAX_SECONDS,
    max_health_age_seconds: float = HEALTH_FRESHNESS_MAX_SECONDS,
    trace: TenSecondTrace | None = None,
) -> ComparisonVerdict:
    """Evaluate a pre-bet comparison in PRIVATE_VALIDATION mode.

    Returns one of exactly three top-level verdict states:
      - COMPARISON_COMPLETE
      - COMPARISON_INCOMPLETE
      - COMPARISON_SUPPRESSED
    """
    timing = trace.summary() if trace is not None else None
    render_dt = render_now.astimezone(timezone.utc)

    # Gate 0: Private validation must be explicitly enabled.
    if not private_validation_enabled:
        return ComparisonVerdict(
            state="COMPARISON_SUPPRESSED",
            variant="DISABLED",
            reason_code="PRIVATE_VALIDATION_DISABLED",
            headline="Comparison suppressed",
            evidence_copy="Private validation mode is disabled. Public /check/ remains fail-closed.",
            return_to_book_available=True,
            actionable=False,
            check_version=slip.check_version,
            slip_captured_price=slip.captured_price,
            timing=timing,
        )

    # Gate 1: Check-version invalidation on slip corrections.
    if expected_check_version is not None and slip.check_version != expected_check_version:
        return ComparisonVerdict(
            state="COMPARISON_SUPPRESSED",
            variant="VERSION_INVALIDATED",
            reason_code="CHECK_VERSION_INVALIDATED",
            headline="Comparison suppressed",
            evidence_copy="Slip was edited after capture; previous check version is invalidated.",
            return_to_book_available=True,
            actionable=False,
            check_version=slip.check_version,
            slip_captured_price=slip.captured_price,
            timing=timing,
        )

    # Gate 2: Scope validation (pregame, full-game NBA 3+ and 4+ made threes on DK/FD only).
    if (
        slip.market.strip().lower() != ALLOWED_MARKET
        or slip.period.strip().lower() != ALLOWED_PERIOD
        or slip.rung not in ALLOWED_RUNGS
        or slip.book.strip().lower() not in ALLOWED_BOOKS
        or not slip.game_id.strip()
        or not slip.player_id.strip()
        or american_to_decimal(slip.captured_price) is None
    ):
        return ComparisonVerdict(
            state="COMPARISON_INCOMPLETE",
            variant="UNSUPPORTED_SCOPE",
            reason_code="UNSUPPORTED_SCOPE",
            headline="Comparison incomplete",
            evidence_copy="Only pregame full-game NBA 3+ and 4+ made threes on DraftKings and FanDuel are supported.",
            return_to_book_available=True,
            actionable=False,
            check_version=slip.check_version,
            slip_captured_price=slip.captured_price,
            timing=timing,
        )

    # Gate 3: Per-book health check re-evaluated at render_now (P0-1).
    dk_ok, dk_reason, dk_h_info = _evaluate_book_health(
        dk_health, book="draftkings", run_id=run_id, render_now=render_dt, max_health_age_seconds=max_health_age_seconds
    )
    fd_ok, fd_reason, fd_h_info = _evaluate_book_health(
        fd_health, book="fanduel", run_id=run_id, render_now=render_dt, max_health_age_seconds=max_health_age_seconds
    )
    health_summary = {"draftkings": dk_h_info, "fanduel": fd_h_info}
    if not dk_ok or not fd_ok:
        primary_reason = dk_reason if not dk_ok else fd_reason
        return ComparisonVerdict(
            state="COMPARISON_SUPPRESSED",
            variant="HEALTH_FAILED",
            reason_code=primary_reason,
            headline="Comparison suppressed",
            evidence_copy=f"Feed health check did not pass ({primary_reason}). Comparison verdict suppressed.",
            return_to_book_available=True,
            actionable=False,
            check_version=slip.check_version,
            slip_captured_price=slip.captured_price,
            health_summary=health_summary,
            timing=timing,
        )

    # Gate 4: Exact canonical lookup for DK and FD independently (never promotes SlipSnapshot).
    dk_raw = store.get_latest(
        book="draftkings",
        game_id=slip.game_id,
        player_id=slip.player_id,
        market=slip.market,
        period=slip.period,
        rung=slip.rung,
    )
    fd_raw = store.get_latest(
        book="fanduel",
        game_id=slip.game_id,
        player_id=slip.player_id,
        market=slip.market,
        period=slip.period,
        rung=slip.rung,
    )

    dk_status, dk_obs, dk_detail = _validate_observation_at_render(
        dk_raw, render_now=render_dt, max_quote_age_seconds=max_quote_age_seconds
    )
    fd_status, fd_obs, fd_detail = _validate_observation_at_render(
        fd_raw, render_now=render_dt, max_quote_age_seconds=max_quote_age_seconds
    )

    # Any fixture/test contamination or malformed newest observation suppresses the verdict (P0-2 & P0-3).
    if dk_status == "CONTAMINATED" or fd_status == "CONTAMINATED":
        return ComparisonVerdict(
            state="COMPARISON_SUPPRESSED",
            variant="FIXTURE_CONTAMINATION",
            reason_code="INTEGRITY_FIXTURE_CONTAMINATION",
            headline="Comparison suppressed",
            evidence_copy="Non-production fixture/test observation detected and blocked.",
            return_to_book_available=True,
            actionable=False,
            check_version=slip.check_version,
            slip_captured_price=slip.captured_price,
            health_summary=health_summary,
            timing=timing,
        )

    if dk_status == "INVALID_NEWEST" or fd_status == "INVALID_NEWEST":
        return ComparisonVerdict(
            state="COMPARISON_SUPPRESSED",
            variant="INVALID_NEWEST",
            reason_code="INTEGRITY_NEWEST_OBSERVATION_INVALID",
            headline="Comparison suppressed",
            evidence_copy="Newest book observation failed integrity validation; no fallback to older quotes.",
            return_to_book_available=True,
            actionable=False,
            check_version=slip.check_version,
            slip_captured_price=slip.captured_price,
            health_summary=health_summary,
            timing=timing,
        )

    # Check if slip captured price differs from latest observation for the slip's own book.
    slip_book_obs = fd_obs if slip.book.strip().lower() == "fanduel" else dk_obs
    slip_differs = bool(
        slip_book_obs is not None
        and slip_book_obs.get("american_odds") is not None
        and int(slip_book_obs["american_odds"]) != int(slip.captured_price)
    )

    # Gate 5: Both books must have FRESH exact-match observations for COMPARISON_COMPLETE.
    if dk_status != "FRESH" or fd_status != "FRESH":
        dk_part = (
            f"DK {fmt_american(dk_obs['american_odds'])} {dk_detail}"
            if dk_status == "FRESH" and dk_obs is not None
            else f"DK {dk_detail}"
        )
        fd_part = (
            f"FD {fmt_american(fd_obs['american_odds'])} {fd_detail}"
            if fd_status == "FRESH" and fd_obs is not None
            else f"FD {fd_detail}"
        )
        if dk_status == "STALE" or fd_status == "STALE":
            reason_code = "QUOTE_STALE_AT_RENDER"
        elif dk_status == "MISSING" and fd_status == "MISSING":
            reason_code = "BOTH_BOOKS_MISSING"
        else:
            reason_code = "ONE_BOOK_MISSING"
        return ComparisonVerdict(
            state="COMPARISON_INCOMPLETE",
            variant=reason_code,
            reason_code=reason_code,
            headline="Comparison incomplete",
            evidence_copy=f"{dk_part} · {fd_part} · comparison incomplete",
            return_to_book_available=True,
            actionable=False,
            check_version=slip.check_version,
            slip_price_differs_from_latest_book=slip_differs,
            slip_captured_price=slip.captured_price,
            dk_observation=dk_obs if dk_status == "FRESH" else None,
            fd_observation=fd_obs if fd_status == "FRESH" else None,
            health_summary=health_summary,
            timing=timing,
        )

    # Gate 6: COMPARISON_COMPLETE — compare exact decimal payouts (DK vs FD).
    assert dk_obs is not None and fd_obs is not None
    dk_dec = float(dk_obs["decimal_odds"])
    fd_dec = float(fd_obs["decimal_odds"])
    slip_note = (
        f" · Your slip captured {_book_label(slip.book.lower())} {fmt_american(slip.captured_price)}"
        if slip_differs
        else ""
    )
    evidence = (
        f"DK {fmt_american(dk_obs['american_odds'])} {dk_detail} · "
        f"FD {fmt_american(fd_obs['american_odds'])} {fd_detail}{slip_note}"
    )

    if math.isclose(dk_dec, fd_dec, rel_tol=0.0, abs_tol=1e-9):
        return ComparisonVerdict(
            state="COMPARISON_COMPLETE",
            variant="SAME_PRICE_AT_BOTH",
            reason_code="SAME_PRICE_AT_BOTH",
            headline="Same price at both",
            evidence_copy=evidence,
            return_to_book_available=True,
            actionable=False,
            check_version=slip.check_version,
            slip_price_differs_from_latest_book=slip_differs,
            slip_captured_price=slip.captured_price,
            dk_observation=dk_obs,
            fd_observation=fd_obs,
            health_summary=health_summary,
            timing=timing,
        )

    if dk_dec > fd_dec:
        return ComparisonVerdict(
            state="COMPARISON_COMPLETE",
            variant="DK_BETTER_PRICE",
            reason_code="DK_BETTER_PRICE",
            headline="DraftKings has a better observed price",
            evidence_copy=evidence,
            return_to_book_available=True,
            actionable=False,
            check_version=slip.check_version,
            slip_price_differs_from_latest_book=slip_differs,
            slip_captured_price=slip.captured_price,
            dk_observation=dk_obs,
            fd_observation=fd_obs,
            health_summary=health_summary,
            timing=timing,
        )

    return ComparisonVerdict(
        state="COMPARISON_COMPLETE",
        variant="DK_NOT_BETTER",
        reason_code="DK_NOT_BETTER",
        headline="DraftKings does not have a better observed price",
        evidence_copy=evidence,
        return_to_book_available=True,
        actionable=False,
        check_version=slip.check_version,
        slip_price_differs_from_latest_book=slip_differs,
        slip_captured_price=slip.captured_price,
        dk_observation=dk_obs,
        fd_observation=fd_obs,
        health_summary=health_summary,
        timing=timing,
    )
