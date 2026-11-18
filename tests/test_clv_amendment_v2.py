"""Tests for Commit 0: AMEND-003 CLV estimand, ladder translation, shadow ledger, and qualification_policy."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from api.main import app
from src.closing_snapshot_cron import (
    closing_receipt_payload,
    closing_receipt_payload_v2,
    verify_closing_receipt_hash,
)
from src.crypto_utils import canonical_json_sha256
from src.contracts.qualification_policy import (
    build_qualification_policy,
    compute_policy_spec_hash,
    fail_closed_action_fields,
    verify_qualification_policy,
)
from src.ledger.close_match import (
    AMENDMENT_003_EFFECTIVE_AT,
    CLOSE_EXACT,
    CLOSE_THIN_DEPTH,
    CLOSE_TRANSLATED,
    CLOSE_UNEVALUABLE,
    CLOSE_WIDE_SPREAD,
    LINE_CHANGED,
    TRANSLATION_METHOD_HASH,
    CloseQuote,
    LadderRung,
    devig_two_way_american,
    evaluate_post_amendment_close,
    load_close_quality_config,
    match_close,
)
from src.ledger.clv_decomposition import decompose_price_clv, summarize_price_clv_cohort
from src.ledger.migrate import MIGRATIONS, guard_historical_close_rows
from src.ledger.shadow_ledger import build_shadow_entry, shadow_ledger_can_unfreeze_paid_access


def test_legacy_line_changed_preserved_pre_amendment():
    expected = CloseQuote(
        event_id="evt-1",
        market="receiving_yards",
        selection="OVER",
        line=49.5,
        settlement_rules="standard",
        over_american_odds=-110,
        under_american_odds=-110,
        quote_source="polymarket",
    )
    observed = CloseQuote(
        event_id="evt-1",
        market="receiving_yards",
        selection="OVER",
        line=50.5,
        settlement_rules="standard",
        over_american_odds=-115,
        under_american_odds=-105,
        quote_source="polymarket",
        quoted_at="2026-11-17T23:00:00+00:00",
        scheduled_start="2026-11-18T01:00:00+00:00",
    )
    assert match_close(expected, observed).reason_code == LINE_CHANGED
    # Observations captured on 2026-11-18 (before 2026-11-19T00:00Z) stay under pre-amendment rules
    res = evaluate_post_amendment_close(
        event_id="evt-1",
        market="receiving_yards",
        selection="OVER",
        entry_line=49.5,
        entry_devig_p=0.50,
        observed=observed,
        closing_ladder=[
            LadderRung(line=48.5, over_american_odds=-125, under_american_odds=105),
            LadderRung(line=50.5, over_american_odds=-115, under_american_odds=-105),
        ],
        observed_at="2026-11-18T12:00:00+00:00",
    )
    assert res.close_state == LINE_CHANGED
    assert res.closing_snapshot_hash is None
    assert res.derived is False


def test_post_amendment_close_exact_and_translated_ladder():
    observed_exact = CloseQuote(
        event_id="evt-2",
        market="receiving_yards",
        selection="OVER",
        line=49.5,
        settlement_rules="standard",
        over_american_odds=-125,
        under_american_odds=105,
        quote_source="polymarket",
        quoted_at="2026-11-19T12:00:00+00:00",
    )
    approved_cfg = {
        "schema": "propwire.close_quality_config.v1",
        "version": "test-approved-1",
        "status": "APPROVED",
        "grades_nothing_until_section_14_approved": False,
        "venues": {
            "polymarket": {"max_bid_ask_spread_prob": 0.06, "min_top_of_book_depth_usd": 250.0},
            "kalshi": {"max_bid_ask_spread_prob": 0.06, "min_top_of_book_depth_usd": 250.0},
        },
    }
    exact_res = evaluate_post_amendment_close(
        event_id="evt-2",
        market="receiving_yards",
        selection="OVER",
        entry_line=49.5,
        entry_devig_p=0.50,
        observed=observed_exact,
        observed_at=AMENDMENT_003_EFFECTIVE_AT,
        raw_provider_payload_sha256="a" * 64,
        closing_spread_prob=0.03,
        closing_depth_usd=500.0,
        close_quality_config=approved_cfg,
    )
    assert exact_res.close_state == CLOSE_EXACT
    assert exact_res.derived is False
    assert exact_res.closing_snapshot_hash is not None
    assert exact_res.translated_close_devig_p > 0.50

    # Line changed with bracketing ladder rungs -> CLOSE_TRANSLATED (labeled derived with method hash)
    observed_shifted = CloseQuote(
        event_id="evt-2",
        market="receiving_yards",
        selection="OVER",
        line=50.5,
        settlement_rules="standard",
        over_american_odds=-110,
        under_american_odds=-110,
        quote_source="polymarket",
        quoted_at="2026-11-19T12:00:00+00:00",
    )
    ladder = [
        LadderRung(line=48.5, over_american_odds=-130, under_american_odds=110),
        LadderRung(line=50.5, over_american_odds=-110, under_american_odds=-110),
    ]
    tr = evaluate_post_amendment_close(
        event_id="evt-2",
        market="receiving_yards",
        selection="OVER",
        entry_line=49.5,
        entry_devig_p=0.50,
        observed=observed_shifted,
        closing_ladder=ladder,
        observed_at="2026-12-01T12:00:00+00:00",
        raw_provider_payload_sha256="b" * 64,
        closing_spread_prob=0.03,
        closing_depth_usd=500.0,
        close_quality_config=approved_cfg,
    )
    assert tr.close_state == CLOSE_TRANSLATED
    assert tr.derived is True
    assert tr.translation_method_hash == TRANSLATION_METHOD_HASH
    assert tr.low_rung == ladder[0]
    assert tr.high_rung == ladder[1]
    assert tr.interpolation_weight == 0.5
    assert tr.closing_snapshot_hash is not None
    p_low, _ = devig_two_way_american(-130, 110)
    p_high, _ = devig_two_way_american(-110, -110)
    assert tr.translated_close_devig_p == pytest.approx((p_low + p_high) / 2.0, abs=1e-5)

    # Single closing number with no neighboring bracketing rung -> CLOSE_UNEVALUABLE
    uneval = evaluate_post_amendment_close(
        event_id="evt-2",
        market="receiving_yards",
        selection="OVER",
        entry_line=49.5,
        entry_devig_p=0.50,
        observed=observed_shifted,
        closing_ladder=[],
        observed_at="2026-12-01T12:00:00+00:00",
        close_quality_config=approved_cfg,
    )
    assert uneval.close_state == CLOSE_UNEVALUABLE
    assert uneval.reason_code == "NO_BRACKETING_RUNGS"
    assert uneval.closing_snapshot_hash is None


def test_close_quality_thresholds_polymarket_and_kalshi():
    cfg = load_close_quality_config()
    assert cfg["version"] == "2026.11.18-1"
    assert cfg["status"] == "UNAPPROVED_PLACEHOLDER"
    assert cfg["grades_nothing_until_section_14_approved"] is True
    approved_cfg = {
        **cfg,
        "status": "APPROVED",
        "grades_nothing_until_section_14_approved": False,
    }
    for venue in ("polymarket", "kalshi"):
        obs = CloseQuote(
            event_id="evt-q",
            market="receiving_yards",
            selection="OVER",
            line=49.5,
            settlement_rules="standard",
            over_american_odds=-110,
            under_american_odds=-110,
            quote_source=venue,
            quoted_at="2026-12-01T12:00:00+00:00",
        )
        unapproved = evaluate_post_amendment_close(
            event_id="evt-q",
            market="receiving_yards",
            selection="OVER",
            entry_line=49.5,
            entry_devig_p=0.50,
            observed=obs,
            observed_at="2026-12-01T12:00:00+00:00",
            closing_spread_prob=0.02,
            closing_depth_usd=500.0,
        )
        assert unapproved.close_state == CLOSE_UNEVALUABLE
        assert unapproved.reason_code == "UNAPPROVED_CLOSE_QUALITY_CONFIG"

        wide = evaluate_post_amendment_close(
            event_id="evt-q",
            market="receiving_yards",
            selection="OVER",
            entry_line=49.5,
            entry_devig_p=0.50,
            observed=obs,
            observed_at="2026-12-01T12:00:00+00:00",
            closing_spread_prob=0.09,
            closing_depth_usd=500.0,
            close_quality_config=approved_cfg,
        )
        assert wide.close_state == CLOSE_UNEVALUABLE
        assert wide.reason_code == CLOSE_WIDE_SPREAD

        thin = evaluate_post_amendment_close(
            event_id="evt-q",
            market="receiving_yards",
            selection="OVER",
            entry_line=49.5,
            entry_devig_p=0.50,
            observed=obs,
            observed_at="2026-12-01T12:00:00+00:00",
            closing_spread_prob=0.02,
            closing_depth_usd=100.0,
            close_quality_config=approved_cfg,
        )
        assert thin.close_state == CLOSE_UNEVALUABLE
        assert thin.reason_code == CLOSE_THIN_DEPTH


def test_v1_and_v2_closing_snapshot_hashes_verify_independently():
    v1_payload = closing_receipt_payload(
        {
            "event_id": "evt-1",
            "player_id": "p-1",
            "book": "polymarket",
            "market": "receiving_yards",
            "closing_line": 49.5,
            "closing_over_odds": -110,
            "closing_under_odds": -110,
            "closing_fair_line": 50.0,
            "provider_event_id": "pe-1",
            "provider_update_ts": 1700000000000,
            "raw_provider_payload_sha256": "c" * 64,
        },
        captured_at="2026-11-17T20:00:00Z",
        kickoff_at="2026-11-17T21:00:00Z",
    )
    v1_hash = canonical_json_sha256(v1_payload)
    assert verify_closing_receipt_hash(v1_payload, v1_hash) is True

    v2_payload = closing_receipt_payload_v2(
        event_id="evt-2",
        player_id="p-1",
        book="polymarket",
        market="receiving_yards",
        close_state=CLOSE_TRANSLATED,
        entry_line=49.5,
        closing_line=50.5,
        closing_over_odds=-110,
        closing_under_odds=-110,
        closing_devig_pair=[0.5, 0.5],
        translated_close_devig_p=0.521,
        low_rung={"line": 48.5, "over_american_odds": -130, "under_american_odds": 110},
        high_rung={"line": 50.5, "over_american_odds": -110, "under_american_odds": -110},
        interpolation_weight=0.5,
        raw_provider_payload_sha256="d" * 64,
        captured_at="2026-11-18T20:00:00Z",
        kickoff_at="2026-11-18T21:00:00Z",
    )
    v2_hash = canonical_json_sha256(v2_payload)
    assert verify_closing_receipt_hash(v2_payload, v2_hash) is True
    assert v1_payload["schema"] == "propwire.closing_snapshot.v1"
    assert v2_payload["schema"] == "propwire.closing_snapshot.v2"


def test_price_clv_decomposition_and_shadow_ledger_isolation():
    # Same yard, worse juice -> strictly negative CLV (never a push)
    worse_juice = decompose_price_clv(
        "sig-1",
        close_state=CLOSE_EXACT,
        quote_source="polymarket",
        entry_devig_p=0.54,
        translated_close_devig_p=0.50,
        is_confirmatory=True,
    )
    assert worse_juice.line_component == 0.0
    assert worse_juice.juice_component < 0.0
    assert worse_juice.total_price_clv_pp < 0.0
    assert worse_juice.book_execution_component == 0.0

    # Shadow ledger computes book_execution_component only within one quote_source class
    shadow_clv = decompose_price_clv(
        "sig-2",
        close_state=CLOSE_TRANSLATED,
        quote_source="odds_api:circa",
        entry_devig_p=0.51,
        translated_close_devig_p=0.54,
        raw_close_devig_p_at_close_line=0.50,
        is_confirmatory=False,
        best_tape_devig_p=0.525,
        best_tape_quote_source="odds_api:circa",
    )
    assert shadow_clv.line_component == pytest.approx(4.0, abs=1e-4)
    assert shadow_clv.juice_component == pytest.approx(-1.0, abs=1e-4)
    assert shadow_clv.book_execution_component == pytest.approx(1.5, abs=1e-4)

    with pytest.raises(ValueError, match="one quote_source class"):
        decompose_price_clv(
            "sig-bad",
            close_state=CLOSE_EXACT,
            quote_source="odds_api:circa",
            entry_devig_p=0.51,
            translated_close_devig_p=0.52,
            is_confirmatory=False,
            best_tape_devig_p=0.53,
            best_tape_quote_source="polymarket",
        )

    entry = build_shadow_entry(
        signal_id="sig-2",
        event_id="evt-2",
        market="receiving_yards",
        selection="OVER",
        quote_source="odds_api:circa",
        book="circa",
        source_ts="2026-11-18T12:00:00Z",
        entry_line=49.5,
        entry_american_odds=-110,
        entry_devig_p=0.51,
        best_tape_devig_p=0.525,
        decomposed=shadow_clv,
        closing_snapshot_hash="e" * 64,
    )
    assert entry.can_unfreeze_paid_access is False
    assert shadow_ledger_can_unfreeze_paid_access([entry]) is False

    summary = summarize_price_clv_cohort([worse_juice, shadow_clv])
    assert summary["n_signals"] == 2
    assert summary["n_derived"] == 1
    assert summary["ci_low"] <= summary["mean_total_clv_pp"] <= summary["ci_high"]


def test_migration_004_registered_and_historical_guard_rejects_line_changed():
    assert any(name == "004_clv_amendment_v2" for name, _ in MIGRATIONS)
    sql = Path("db/migrations/004_clv_amendment_v2.sql").read_text(encoding="utf-8")
    assert "closing_references_protect_legacy_line_changed" in sql
    assert "OLD.reason_code = 'LINE_CHANGED'" in sql

    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [("row-legacy-1",)]
    with pytest.raises(RuntimeError, match="historical LINE_CHANGED"):
        guard_historical_close_rows(conn, ["row-legacy-1"])


def test_qualification_policy_hash_and_fail_closed_behavior():
    policy = build_qualification_policy()
    assert verify_qualification_policy(policy) is True
    assert policy["policy_spec_hash"] == compute_policy_spec_hash(policy)

    tampered = {**policy, "edge_bounds": {**policy["edge_bounds"], "min_edge_pp": 5.0}}
    assert verify_qualification_policy(tampered) is False

    sample_row = {"actionable": True, "actionable_stake": 0.02, "stake": 0.02, "qualified": True, "can_execute": True}
    assert fail_closed_action_fields(sample_row, policy)["actionable"] is True
    dead = fail_closed_action_fields(sample_row, tampered)
    assert dead["actionable"] is False
    assert dead["actionable_stake"] == 0.0
    assert dead["stake"] == 0.0
    assert dead["qualified"] is False
    assert dead["can_execute"] is False
    assert fail_closed_action_fields(sample_row, None)["actionable"] is False

    client = TestClient(app)
    resp = client.get("/api/v1/edges?slate=2026_w7")
    assert resp.status_code == 200
    body = resp.json()
    assert verify_qualification_policy(body["qualification_policy"]) is True
