"""
Minimum Quote-Integrity Regression Matrix for PRIVATE_VALIDATION pre-bet checks.

Covers every required P0 and verdict invariant:
  1. Health timeout, transport error, malformed, unknown, unavailable, stale -> COMPARISON_SUPPRESSED (even with cached quotes)
  2. Newer worse DK price replaces older better DK price
  3. Out-of-order delivery cannot resurrect older price (in-memory & across JSONL restart)
  4. Malformed newest observation fails closed (never falls back to older cached quote)
  5. Quote becoming stale while response is assembled cannot render as fresh
  6. Fixture/seeded/replayed/test quotes rejected at ingest AND suppressed at read time (incl. test headers)
  7. Exact canonical identity: Jaylen Brown != Jalen Brunson, 3+ != 4+, full_game != 1q, same name different games != match
  8. SlipSnapshot never promoted to BookObservation; slip price drift vs latest FD quote preserved
  9. Slip correction increments check_version and invalidates prior version
 10. Negative odds, positive odds, equal prices ("Same price at both"), DK better, DK worse ("DraftKings does not have a better observed price")
 11. Public isolation: public router cannot read private observations; disabled mode returns 503
 12. Zero edge/Kelly copy and actionable=False in every verdict state
 13. 10-second stage instrumentation
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from api.extension_router import router as public_ext_router
from api.private_validation_router import reset_private_store, router as pv_router
from src.private_validation import (
    BookObservation,
    HealthObservation,
    PrivateObservationStore,
    SlipSnapshot,
    TenSecondTrace,
    evaluate_private_comparison,
)

RENDER_NOW = datetime(2026, 11, 24, 18, 0, 20, tzinfo=timezone.utc)
FORBIDDEN_COPY_RE = re.compile(
    r"\bkelly\b|\bedge\b|\bEV\b|expected return|your book is best|guaranteed|executable|verified wager|placed bet",
    re.IGNORECASE,
)


def _slip(**overrides) -> SlipSnapshot:
    base = dict(
        slip_id="slip-001",
        check_version=1,
        player_id="nba-jaylen-brown",
        player_name="Jaylen Brown",
        game_id="nba-20261124-NYK-BOS",
        market="nba_threes",
        period="full_game",
        rung=4,
        book="fanduel",
        captured_price=185,
        captured_at="2026-11-24T18:00:00Z",
        capture_method="paste",
    )
    base.update(overrides)
    return SlipSnapshot(**base)


def _obs(book: str, odds: int | None, observed_at: str = "2026-11-24T18:00:08Z", **overrides) -> BookObservation:
    base = dict(
        book=book,
        game_id="nba-20261124-NYK-BOS",
        player_id="nba-jaylen-brown",
        market="nba_threes",
        period="full_game",
        rung=4,
        american_odds=odds,
        observed_at=observed_at,
        received_at="2026-11-24T18:00:09Z",
        adapter_id=f"{book}-private-v1",
        run_id="run-1",
        source_env="PRIVATE_VALIDATION",
        provenance="private_adapter",
    )
    base.update(overrides)
    return BookObservation(**base)


def _health(book: str, status: str = "HEALTHY", checked_at: str = "2026-11-24T18:00:15Z", run_id: str = "run-1") -> HealthObservation:
    return HealthObservation(book=book, run_id=run_id, status=status, checked_at=checked_at, latency_ms=120.0)


def _assert_fail_closed_copy(verdict) -> None:
    assert verdict.actionable is False
    combined = f"{verdict.headline} {verdict.evidence_copy}"
    assert not FORBIDDEN_COPY_RE.search(combined), f"Forbidden copy in verdict: {combined}"


# ---------------------------------------------------------------------------
# 1. Health failures suppress verdict even when fresh cached quotes exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_dk_health,expected_reason",
    [
        (None, "HEALTH_UNAVAILABLE"),
        (_health("draftkings", status="UNAVAILABLE"), "HEALTH_UNAVAILABLE"),
        (_health("draftkings", status="UNKNOWN"), "HEALTH_UNKNOWN"),
        (_health("draftkings", status="WEIRD_STATE"), "HEALTH_UNKNOWN"),
        (_health("draftkings", status="MALFORMED"), "HEALTH_MALFORMED"),
        (_health("draftkings", status="TIMEOUT"), "HEALTH_TIMEOUT"),
        (_health("draftkings", status="TRANSPORT_ERROR"), "HEALTH_TRANSPORT_ERROR"),
        (_health("draftkings", status="HEALTHY", checked_at="2026-11-24T17:58:00Z"), "HEALTH_STALE"),
        (_health("draftkings", status="HEALTHY", checked_at="not-a-timestamp"), "HEALTH_MALFORMED"),
        (_health("draftkings", status="HEALTHY", run_id="stale-run-0"), "HEALTH_STALE"),
    ],
)
def test_health_failures_suppress_verdict_even_with_cached_quotes(bad_dk_health, expected_reason):
    store = PrivateObservationStore()
    store.ingest(_obs("draftkings", 205))
    store.ingest(_obs("fanduel", 185))

    v = evaluate_private_comparison(
        _slip(),
        store=store,
        dk_health=bad_dk_health,
        fd_health=_health("fanduel"),
        run_id="run-1",
        render_now=RENDER_NOW,
    )
    assert v.state == "COMPARISON_SUPPRESSED"
    assert v.reason_code == expected_reason
    assert v.dk_observation is None
    assert v.fd_observation is None
    _assert_fail_closed_copy(v)


# ---------------------------------------------------------------------------
# 2. Newer worse price replaces older better price; out-of-order & restart safe
# ---------------------------------------------------------------------------


def test_newer_worse_dk_price_replaces_older_better_price_and_blocks_out_of_order(tmp_path: Path):
    persist = tmp_path / "private_obs.jsonl"
    store = PrivateObservationStore(persist_path=persist)

    # Older better DK price (+210)
    ok1, _ = store.ingest(_obs("draftkings", 210, observed_at="2026-11-24T18:00:05Z", received_at="2026-11-24T18:00:06Z", observation_seq=1))
    assert ok1 is True
    # Newer worse DK price (+170) replaces +210
    ok2, _ = store.ingest(_obs("draftkings", 170, observed_at="2026-11-24T18:00:12Z", received_at="2026-11-24T18:00:13Z", observation_seq=2))
    assert ok2 is True
    # Out-of-order late arrival of an even better older price (+240) MUST be ignored
    ok3, reason3 = store.ingest(_obs("draftkings", 240, observed_at="2026-11-24T18:00:04Z", received_at="2026-11-24T18:00:14Z", observation_seq=3))
    assert ok3 is False
    assert reason3 == "IGNORED_OLDER_OUT_OF_ORDER"
    store.ingest(_obs("fanduel", 185, observed_at="2026-11-24T18:00:12Z"))

    # Simulate process restart by reloading from JSONL
    reloaded = PrivateObservationStore(persist_path=persist)
    v = evaluate_private_comparison(
        _slip(),
        store=reloaded,
        dk_health=_health("draftkings"),
        fd_health=_health("fanduel"),
        run_id="run-1",
        render_now=RENDER_NOW,
    )
    assert v.state == "COMPARISON_COMPLETE"
    assert v.variant == "DK_NOT_BETTER"
    assert v.dk_observation["american_odds"] == 170
    assert v.headline == "DraftKings does not have a better observed price"
    _assert_fail_closed_copy(v)


def test_malformed_newest_observation_fails_closed_without_falling_back_to_older():
    store = PrivateObservationStore()
    store.ingest(_obs("draftkings", 210, observed_at="2026-11-24T18:00:05Z"))
    # Newer observation is malformed -> MUST NOT fall back to +210
    store.ingest(_obs("draftkings", None, observed_at="2026-11-24T18:00:15Z", malformed_reason="empty_odds_node"))
    store.ingest(_obs("fanduel", 185, observed_at="2026-11-24T18:00:15Z"))

    v = evaluate_private_comparison(
        _slip(),
        store=store,
        dk_health=_health("draftkings"),
        fd_health=_health("fanduel"),
        run_id="run-1",
        render_now=RENDER_NOW,
    )
    assert v.state == "COMPARISON_SUPPRESSED"
    assert v.reason_code == "INTEGRITY_NEWEST_OBSERVATION_INVALID"
    _assert_fail_closed_copy(v)


# ---------------------------------------------------------------------------
# 3. Quote becoming stale at render time cannot render as fresh
# ---------------------------------------------------------------------------


def test_quote_becoming_stale_at_render_time_produces_comparison_incomplete():
    store = PrivateObservationStore()
    store.ingest(_obs("draftkings", 205, observed_at="2026-11-24T17:59:45Z"))  # 35s old at 18:00:20
    store.ingest(_obs("fanduel", 185, observed_at="2026-11-24T18:00:10Z"))     # 10s old

    v = evaluate_private_comparison(
        _slip(),
        store=store,
        dk_health=_health("draftkings"),
        fd_health=_health("fanduel"),
        run_id="run-1",
        render_now=RENDER_NOW,
    )
    assert v.state == "COMPARISON_INCOMPLETE"
    assert v.reason_code == "QUOTE_STALE_AT_RENDER"
    assert v.dk_observation is None
    assert v.fd_observation is not None
    assert "comparison incomplete" in v.evidence_copy
    _assert_fail_closed_copy(v)


# ---------------------------------------------------------------------------
# 4. Fixture/seeded/replayed/test exclusion at ingest AND at read time
# ---------------------------------------------------------------------------


def test_fixture_seeded_replayed_test_quotes_blocked_at_ingest_and_read():
    store = PrivateObservationStore()
    bad_cases = [
        (_obs("draftkings", 220, is_fixture=True), None),
        (_obs("draftkings", 220, is_seeded=True), None),
        (_obs("draftkings", 220, is_replayed=True), None),
        (_obs("draftkings", 220, is_test=True), None),
        (_obs("draftkings", 220, source_env="test"), None),
        (_obs("draftkings", 220, provenance="synthetic-fixture"), None),
        (_obs("draftkings", 220), {"X-Propstein-Fixture": "1"}),
        (_obs("draftkings", 220), {"X-Replay-Mode": "true"}),
    ]
    for obs, headers in bad_cases:
        ok, reason = store.ingest(obs, headers=headers)
        assert ok is False
        assert reason.startswith("REJECTED_FIXTURE_OR_TEST")

    # Even if a contaminated row somehow exists in cache/DB, read-time check suppresses verdict.
    store._inject_raw_for_contamination_test(_obs("draftkings", 220, is_seeded=True))
    store.ingest(_obs("fanduel", 185))
    v = evaluate_private_comparison(
        _slip(),
        store=store,
        dk_health=_health("draftkings"),
        fd_health=_health("fanduel"),
        run_id="run-1",
        render_now=RENDER_NOW,
    )
    assert v.state == "COMPARISON_SUPPRESSED"
    assert v.reason_code == "INTEGRITY_FIXTURE_CONTAMINATION"
    _assert_fail_closed_copy(v)


# ---------------------------------------------------------------------------
# 5. Exact canonical identity: Brown != Brunson, 3+ != 4+, period, game_id
# ---------------------------------------------------------------------------


def test_exact_canonical_identity_never_cross_matches():
    store = PrivateObservationStore()
    # Jalen Brunson 4+ (different player)
    store.ingest(_obs("draftkings", 200, player_id="nba-jalen-brunson"))
    # Jaylen Brown 3+ (different rung)
    store.ingest(_obs("draftkings", -115, rung=3))
    # Jaylen Brown 4+ in a different game
    store.ingest(_obs("draftkings", 210, game_id="nba-20261125-BOS-MIA"))
    # Only FD has exact match for Jaylen Brown 4+ in NYK-BOS
    store.ingest(_obs("fanduel", 185))

    v = evaluate_private_comparison(
        _slip(player_id="nba-jaylen-brown", rung=4, game_id="nba-20261124-NYK-BOS"),
        store=store,
        dk_health=_health("draftkings"),
        fd_health=_health("fanduel"),
        run_id="run-1",
        render_now=RENDER_NOW,
    )
    assert v.state == "COMPARISON_INCOMPLETE"
    assert v.reason_code == "ONE_BOOK_MISSING"
    assert v.dk_observation is None
    assert v.fd_observation["american_odds"] == 185
    _assert_fail_closed_copy(v)

    # Quarter period is unsupported scope -> no comparison
    v_quarter = evaluate_private_comparison(
        _slip(period="1q"),
        store=store,
        dk_health=_health("draftkings"),
        fd_health=_health("fanduel"),
        run_id="run-1",
        render_now=RENDER_NOW,
    )
    assert v_quarter.state == "COMPARISON_INCOMPLETE"
    assert v_quarter.reason_code == "UNSUPPORTED_SCOPE"
    _assert_fail_closed_copy(v_quarter)


# ---------------------------------------------------------------------------
# 6. SlipSnapshot never promoted; slip price drift & check_version invalidation
# ---------------------------------------------------------------------------


def test_slip_not_promoted_and_price_drift_plus_version_invalidation():
    store = PrivateObservationStore()
    # Only DK has a BookObservation; FD +185 is ONLY on SlipSnapshot -> MUST NOT count as FD observation!
    store.ingest(_obs("draftkings", 185, observed_at="2026-11-24T18:00:08Z"))
    v_one = evaluate_private_comparison(
        _slip(book="fanduel", captured_price=185),
        store=store,
        dk_health=_health("draftkings"),
        fd_health=_health("fanduel"),
        run_id="run-1",
        render_now=RENDER_NOW,
    )
    assert v_one.state == "COMPARISON_INCOMPLETE"
    assert v_one.evidence_copy == "DK +185 observed 12s ago · FD unavailable · comparison incomplete"
    _assert_fail_closed_copy(v_one)

    # Now FD adapter observes +175 while user slip captured +185 -> preserve both explicitly
    store.ingest(_obs("fanduel", 175, observed_at="2026-11-24T18:00:10Z"))
    v_drift = evaluate_private_comparison(
        _slip(book="fanduel", captured_price=185),
        store=store,
        dk_health=_health("draftkings"),
        fd_health=_health("fanduel"),
        run_id="run-1",
        render_now=RENDER_NOW,
    )
    assert v_drift.state == "COMPARISON_COMPLETE"
    assert v_drift.variant == "DK_BETTER_PRICE"
    assert v_drift.slip_price_differs_from_latest_book is True
    assert v_drift.slip_captured_price == 185
    assert "Your slip captured FD +185" in v_drift.evidence_copy
    _assert_fail_closed_copy(v_drift)

    # Correction bumps check_version -> old check_version invalidated
    v_invalid = evaluate_private_comparison(
        _slip(check_version=2),
        store=store,
        dk_health=_health("draftkings"),
        fd_health=_health("fanduel"),
        run_id="run-1",
        render_now=RENDER_NOW,
        expected_check_version=1,
    )
    assert v_invalid.state == "COMPARISON_SUPPRESSED"
    assert v_invalid.reason_code == "CHECK_VERSION_INVALIDATED"


# ---------------------------------------------------------------------------
# 7. Positive, negative, and equal American odds ordering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dk_odds,fd_odds,expected_variant,expected_headline",
    [
        (205, 185, "DK_BETTER_PRICE", "DraftKings has a better observed price"),
        (185, 185, "SAME_PRICE_AT_BOTH", "Same price at both"),
        (165, 185, "DK_NOT_BETTER", "DraftKings does not have a better observed price"),
        (-105, -120, "DK_BETTER_PRICE", "DraftKings has a better observed price"),
        (-110, -110, "SAME_PRICE_AT_BOTH", "Same price at both"),
        (-130, -115, "DK_NOT_BETTER", "DraftKings does not have a better observed price"),
        (100, -100, "SAME_PRICE_AT_BOTH", "Same price at both"),
    ],
)
def test_price_ordering_positive_negative_and_equal(dk_odds, fd_odds, expected_variant, expected_headline):
    store = PrivateObservationStore()
    store.ingest(_obs("draftkings", dk_odds))
    store.ingest(_obs("fanduel", fd_odds))
    v = evaluate_private_comparison(
        _slip(),
        store=store,
        dk_health=_health("draftkings"),
        fd_health=_health("fanduel"),
        run_id="run-1",
        render_now=RENDER_NOW,
    )
    assert v.state == "COMPARISON_COMPLETE"
    assert v.variant == expected_variant
    assert v.headline == expected_headline
    _assert_fail_closed_copy(v)


# ---------------------------------------------------------------------------
# 8. Public isolation & 10-second stage instrumentation
# ---------------------------------------------------------------------------


def test_public_router_cannot_access_private_validation_and_disabled_mode_fails_closed(monkeypatch):
    reset_private_store()
    app = FastAPI()
    app.include_router(public_ext_router)
    app.include_router(pv_router)
    client = TestClient(app)

    # Disabled by default -> 503
    monkeypatch.delenv("PROPSTEIN_PRIVATE_VALIDATION", raising=False)
    r_disabled = client.post("/api/private-validation/compare", json={"run_id": "r1", "slip": _slip().__dict__})
    assert r_disabled.status_code == 503

    # Enabled with token -> ingests and compares privately; public health still keeps adapters OFF
    monkeypatch.setenv("PROPSTEIN_PRIVATE_VALIDATION", "1")
    monkeypatch.setenv("PRIVATE_VALIDATION_TOKEN", "pv-secret-token")
    auth = {"Authorization": "Bearer pv-secret-token"}

    r_fixture = client.post(
        "/api/private-validation/observations",
        headers={**auth, "X-Propstein-Fixture": "1"},
        json=_obs("draftkings", 205).as_dict(),
    )
    assert r_fixture.status_code == 422

    assert client.post("/api/private-validation/observations", headers=auth, json=_obs("draftkings", 205).as_dict()).status_code == 200
    assert client.post("/api/private-validation/observations", headers=auth, json=_obs("fanduel", 185).as_dict()).status_code == 200

    # Public extension health remains completely unaffected (real adapters False)
    pub_health = client.get("/api/v1/extension/health").json()
    assert pub_health["capabilities"]["adapters"]["draftkings"] is False
    assert pub_health["capabilities"]["adapters"]["fanduel"] is False

    # Timing trace inside 10s vs exceeding 10s
    trace_ok = TenSecondTrace(
        t0_capture_start_ms=1_000,
        t_capture_complete_ms=2_800,
        t_correction_open_ms=3_000,
        t_correction_complete_ms=4_500,
        t_dk_adapter_ms=5_400,
        t_fd_adapter_ms=5_600,
        t_answer_rendered_ms=6_100,
        t_return_to_book_ms=7_200,
    ).summary()
    assert trace_ok["capture_to_answer_ms"] == 5_100
    assert trace_ok["correction_ms"] == 1_500
    assert trace_ok["return_to_book_ms"] == 1_100
    assert trace_ok["within_ten_seconds"] is True

    trace_slow = TenSecondTrace(
        t0_capture_start_ms=1_000,
        t_capture_complete_ms=5_000,
        t_answer_rendered_ms=12_500,
    ).summary()
    assert trace_slow["capture_to_answer_ms"] == 11_500
    assert trace_slow["within_ten_seconds"] is False
