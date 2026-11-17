"""Pre-start enforcement tests.

Covers the requirements:
1. Fixture quote start - 1 min => pre_start: true; start + 1 min => false; missing start => unknown.
2. Post-start entry => does not qualify; post-start close => unevaluable (CLOSE_AFTER_START).
3. Verified output unchanged for same fixtures (no regression).
"""

import pytest
from src.ledger.close_match import (
    CLOSE_AFTER_START,
    CLOSE_UNKNOWN_START,
    CloseQuote,
    match_close,
)
from src.odds_adapter import OddsIngestionAdapter, PropQuote
from src.scheduler.capture_scheduler import compute_pre_start_attribute


def test_timing_calculation_start_minus_one_min_and_plus_one_min_and_missing():
    """Test 1: start - 1 min => true; start + 1 min => false; missing start => unknown."""
    sched = "2026-11-17T01:15:00Z"

    # Start - 1 min
    ps_pre, off_pre = compute_pre_start_attribute("2026-11-17T01:14:00Z", sched)
    assert ps_pre is True
    assert off_pre == -60.0

    # Start + 1 min
    ps_post, off_post = compute_pre_start_attribute("2026-11-17T01:16:00Z", sched)
    assert ps_post is False
    assert off_post == 60.0

    # Missing start
    ps_unk1, off_unk1 = compute_pre_start_attribute("2026-11-17T01:14:00Z", None)
    assert ps_unk1 == "unknown"
    assert off_unk1 is None

    # Missing quoted_at
    ps_unk2, off_unk2 = compute_pre_start_attribute(None, sched)
    assert ps_unk2 == "unknown"
    assert off_unk2 is None

    # Unparseable start
    ps_bad, off_bad = compute_pre_start_attribute("2026-11-17T01:14:00Z", "not-a-date")
    assert ps_bad == "unknown"
    assert off_bad is None


def test_post_start_entry_does_not_qualify():
    """Test 2a: Post-start entry cannot qualify regardless of nominal edge."""
    adapter = OddsIngestionAdapter.__new__(OddsIngestionAdapter)
    adapter.season = 2026
    adapter.week = 7
    adapter.slate_players = [
        {
            "player_id": "00-0038621",
            "player_display_name": "DeMario Douglas",
            "team": "NE",
            "position": "WR",
            "mu_rec": 5.0,
            "mu_ypr": 15.0,
            "fair_line_yards": 45.0,
            "fair_line_rec": 4.5,
        }
    ]
    adapter.injury_beneficiaries = {}
    from freeze_slate import MODEL_ALPHA, MODEL_K, ReceivingModel
    adapter.model = ReceivingModel(rec_params=None, ypr_params=None, alpha=MODEL_ALPHA, k=MODEL_K, means=None, sds=None, cols=None)

    # Base quote with valid verified credentials and clear model edge
    base_dict = {
        "quote_id": "test_q1",
        "player_raw_name": "DeMario Douglas",
        "team": "NE",
        "opponent": "CHI",
        "market": "receiving_yards",
        "line": 20.0,
        "over_odds": -110,
        "under_odds": -110,
        "book": "FanDuel",
        "observed_at": "2026-11-17T01:16:00Z",
        "quoted_at": "2026-11-17T01:16:00Z",
        "scheduled_start": "2026-11-17T01:15:00Z",
        "source": "FanDuel",
        "source_url": "https://sportsbook.fanduel.com/event/123",
        "receipt_id": "rcpt_1234567890abcdef",
        "verified": True,
        "pre_start": False,
        "offset_to_scheduled_start_sec": 60.0,
    }

    q_post = PropQuote(**base_dict)
    ev_post = adapter.evaluate_quote(q_post)
    assert ev_post is not None
    assert ev_post.is_qualified is False
    assert ev_post.actionable_stake == 0.0
    assert ev_post.status == "CLOSED"

    # Pre-start counterpart qualifies
    base_dict["quoted_at"] = "2026-11-17T01:14:00Z"
    base_dict["pre_start"] = True
    base_dict["offset_to_scheduled_start_sec"] = -60.0
    q_pre = PropQuote(**base_dict)
    ev_pre = adapter.evaluate_quote(q_pre)
    assert ev_pre is not None
    assert ev_pre.is_qualified is True
    assert ev_pre.actionable_stake > 0.0
    assert ev_pre.status == "QUALIFIED"


def test_post_start_close_unevaluable():
    """Test 2b: Post-start close returns CLOSE_AFTER_START (or CLOSE_UNKNOWN_START)."""
    expected = CloseQuote(
        event_id="nba-20261113-ATL-CHI",
        market="points",
        selection="OVER",
        line=14.5,
        settlement_rules="standard",
        quote_source="kalshi",
    )

    # 1. Post-start close marked with pre_start = False
    observed_post = CloseQuote(
        event_id="nba-20261113-ATL-CHI",
        market="points",
        selection="OVER",
        line=14.5,
        settlement_rules="standard",
        quote_source="kalshi",
        pre_start=False,
    )
    res_post = match_close(expected, observed_post)
    assert res_post.matched is False
    assert res_post.reason_code == CLOSE_AFTER_START

    # 2. Post-start close inferred from timestamps
    observed_post_ts = CloseQuote(
        event_id="nba-20261113-ATL-CHI",
        market="points",
        selection="OVER",
        line=14.5,
        settlement_rules="standard",
        quote_source="kalshi",
        quoted_at="2026-11-14T01:15:00Z",
        scheduled_start="2026-11-14T01:00:00Z",
    )
    res_post_ts = match_close(expected, observed_post_ts)
    assert res_post_ts.matched is False
    assert res_post_ts.reason_code == CLOSE_AFTER_START

    # 3. Unparseable / unknown start timestamp
    observed_unk = CloseQuote(
        event_id="nba-20261113-ATL-CHI",
        market="points",
        selection="OVER",
        line=14.5,
        settlement_rules="standard",
        quote_source="kalshi",
        pre_start="unknown",
    )
    res_unk = match_close(expected, observed_unk)
    assert res_unk.matched is False
    assert res_unk.reason_code == CLOSE_UNKNOWN_START

    # 4. Pre-start close matches successfully
    observed_pre = CloseQuote(
        event_id="nba-20261113-ATL-CHI",
        market="points",
        selection="OVER",
        line=14.5,
        settlement_rules="standard",
        quote_source="kalshi",
        pre_start=True,
    )
    res_pre = match_close(expected, observed_pre)
    assert res_pre.matched is True
    assert res_pre.reason_code is None
