"""Tests for Commit 2: Injury consumer (availability_void, availability_uncertain, CONTEXT_DIRTY, STALE_CONTEXT)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.agents.injury_agent import InjuryEventAgent
from src.agents.persistence import DurableRedisStreamBus, TruthStore
from src.agents.injury_consumer import (
    DIAG_ROLE_SURGE_UNCONFIRMED,
    STATE_AVAILABILITY_UNCERTAIN,
    STATE_AVAILABILITY_VOID,
    STATE_CONTEXT_DIRTY,
    STATE_STALE_CONTEXT,
    ReceiptRecord,
    consume_injury_events,
    consume_injury_stream,
)


def _usage_table():
    return {
        "p-out": {
            "target_share": 0.20,
            "beneficiary_weights": {
                "p-wr2": 0.50,  # +10% share -> +2.5 pp edge (10.5 -> 11.75? let's test exact thresholds)
                "p-surge": 0.80,
            },
        }
    }


def _receipts():
    return [
        ReceiptRecord(
            receipt_id="rcpt-out",
            player_id="p-out",
            team="DET",
            market="receiving_yards",
            receipt_hash="hash-out-pre",
            line=74.5,
            base_projection=80.0,
            base_edge_pp=10.8,
        ),
        ReceiptRecord(
            receipt_id="rcpt-wr2",
            player_id="p-wr2",
            team="DET",
            market="receiving_yards",
            receipt_hash="hash-wr2-pre",
            line=49.5,
            base_projection=55.0,
            base_edge_pp=8.5,
        ),
        ReceiptRecord(
            receipt_id="rcpt-surge",
            player_id="p-surge",
            team="DET",
            market="receiving_yards",
            receipt_hash="hash-surge-pre",
            line=34.5,
            base_projection=40.0,
            base_edge_pp=10.0,
        ),
    ]


def test_out_voids_open_receipts_ignores_caller_vacated_and_reruns_beneficiaries(tmp_path):
    now = datetime(2026, 11, 18, 15, 0, 0, tzinfo=timezone.utc)
    # Even if caller tries to inject huge vacated_targets/vacated_usage, InjuryEventAgent strips it.
    agent = InjuryEventAgent(truth_store=TruthStore(db_path=str(tmp_path / "truth.db")))
    ingested = agent.ingest_event(
        {
            "sport": "nfl",
            "game_id": "2026_11_DET_GB",
            "player_id": "p-out-amend",
            "team": "DET",
            "opponent": "GB",
            "status": "OUT",
            "body_part": "hamstring",
            "vacated_targets": 99.0,
            "vacated_usage": 0.99,
            "fetched_at": now.isoformat(),
        },
        now=now,
    )
    assert ingested["emitted"] is True
    assert ingested["payload"].vacated_targets == 0.0
    assert ingested["payload"].vacated_usage == 0.0

    res = consume_injury_events(
        [{"player_id": "p-out", "team": "DET", "status": "OUT", "status_ts": now.isoformat()}],
        receipts=_receipts(),
        model_usage_table=_usage_table(),
        news_week=11,
        board_week=11,
        capture_week=11,
        now=now,
    )
    assert res.gate_paused is False

    # OUT receipt leaves /v1/edges, excluded from N, pre-news hash dead
    voided = res.receipts["rcpt-out"]
    assert voided.state == STATE_AVAILABILITY_VOID
    assert voided.active is False
    assert voided.can_execute is False
    assert voided.excluded_from_n is True

    # Beneficiary passing gate (8.5 + 0.10*25 = 11.0 in [10, 12]) gets new hash
    wr2 = res.receipts["rcpt-wr2"]
    assert wr2.state == STATE_CONTEXT_DIRTY
    assert wr2.active is True
    assert wr2.can_execute is True
    assert wr2.receipt_hash != "hash-wr2-pre"

    # Oversized role-surge (10.0 + 0.16*25 = 14.0 > 12.0) goes to diagnostics as ROLE_SURGE_UNCONFIRMED
    surge = res.receipts["rcpt-surge"]
    assert surge.active is False
    assert surge.can_execute is False
    assert any(
        d["receipt_id"] == "rcpt-surge" and d["code"] == DIAG_ROLE_SURGE_UNCONFIRMED
        for d in res.diagnostics
    )


def test_questionable_limited_produces_availability_uncertain_without_rerunning_model():
    now = datetime(2026, 11, 18, 15, 0, 0, tzinfo=timezone.utc)
    status_ts = now - timedelta(minutes=10)
    receipt = ReceiptRecord(
        receipt_id="rcpt-q",
        player_id="p-q",
        team="KC",
        market="receiving_yards",
        receipt_hash="hash-q-orig",
        line=61.5,
        base_projection=68.0,
        base_edge_pp=10.5,
    )

    # Without a post-news BookQuoteTape row: old hash stays, cannot act
    res_no_tape = consume_injury_events(
        [{"player_id": "p-q", "team": "KC", "status": "LIMITED", "status_ts": status_ts.isoformat()}],
        receipts=[receipt],
        model_usage_table={},
        news_week=11,
        board_week=11,
        capture_week=11,
        tape_rows=[],
        now=now,
    )
    r1 = res_no_tape.receipts["rcpt-q"]
    assert r1.state == STATE_AVAILABILITY_UNCERTAIN
    assert r1.receipt_hash == "hash-q-orig"
    assert r1.can_execute is False

    # Tape row BEFORE status_ts does NOT unlock
    stale_tape = {
        "market_id": "nfl:p-q:receiving_yards",
        "player_id": "p-q",
        "line": 61.5,
        "book": "polymarket",
        "american": -110,
        "devig_p": 0.50,
        "source_ts": (status_ts - timedelta(minutes=5)).isoformat(),
        "observed_at": now.isoformat(),
        "raw_payload_sha256": "a" * 64,
    }
    res_old_tape = consume_injury_events(
        [{"player_id": "p-q", "team": "KC", "status": "QUESTIONABLE", "status_ts": status_ts.isoformat()}],
        receipts=[receipt],
        model_usage_table={},
        news_week=11,
        board_week=11,
        capture_week=11,
        tape_rows=[stale_tape],
        now=now,
    )
    assert res_old_tape.receipts["rcpt-q"].can_execute is False
    assert res_old_tape.receipts["rcpt-q"].receipt_hash == "hash-q-orig"

    # Fresh tape row AFTER status_ts unlocks with new hash
    fresh_tape = {**stale_tape, "source_ts": (status_ts + timedelta(minutes=2)).isoformat(), "raw_payload_sha256": "f" * 64}
    res_fresh_tape = consume_injury_events(
        [{"player_id": "p-q", "team": "KC", "status": "QUESTIONABLE", "status_ts": status_ts.isoformat()}],
        receipts=[receipt],
        model_usage_table={},
        news_week=11,
        board_week=11,
        capture_week=11,
        tape_rows=[fresh_tape],
        now=now,
    )
    assert res_fresh_tape.receipts["rcpt-q"].can_execute is True
    assert res_fresh_tape.receipts["rcpt-q"].receipt_hash != "hash-q-orig"


def test_week_misalignment_pauses_gate_with_stale_context():
    now = datetime(2026, 11, 18, 15, 0, 0, tzinfo=timezone.utc)
    res = consume_injury_events(
        [{"player_id": "p-out", "team": "DET", "status": "OUT", "status_ts": now.isoformat()}],
        receipts=_receipts(),
        model_usage_table=_usage_table(),
        news_week=10,
        board_week=11,
        capture_week=11,
        now=now,
    )
    assert res.gate_paused is True
    assert res.pause_reason == STATE_STALE_CONTEXT
    assert all(not r.active and not r.can_execute and r.state == STATE_STALE_CONTEXT for r in res.receipts.values())


def test_real_stream_bus_ingests_official_report_and_ignores_caller_vacated(tmp_path):
    now = datetime(2026, 11, 18, 15, 0, 0, tzinfo=timezone.utc)
    bus = DurableRedisStreamBus()
    agent = InjuryEventAgent(stream_bus=bus, truth_store=TruthStore(db_path=str(tmp_path / "truth.db")))
    out = agent.ingest_event(
        {
            "sport": "nfl",
            "game_id": "2026_11_DET_GB",
            "player_id": "p-out",
            "team": "DET",
            "opponent": "GB",
            "status": "OUT",
            "source_provider": "official_report",
            "vacated_targets": 50.0,
            "vacated_usage": 0.90,
            "fetched_at": now.isoformat(),
        },
        now=now,
    )
    assert out["emitted"] is True
    res = consume_injury_stream(
        bus,
        receipts=_receipts(),
        model_usage_table=_usage_table(),
        news_week=11,
        board_week=11,
        capture_week=11,
        now=now,
    )
    assert res.receipts["rcpt-out"].state == STATE_AVAILABILITY_VOID
    assert res.receipts["rcpt-wr2"].state == STATE_CONTEXT_DIRTY
    assert res.receipts["rcpt-wr2"].base_edge_pp == 11.0  # from model_usage_table, NOT caller's 0.90
