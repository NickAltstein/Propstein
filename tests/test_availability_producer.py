"""Fixture-only tests for scheduled NFL availability producer and #195 JSONL replay."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from src.agents.injury_consumer import (
    STATE_AVAILABILITY_VOID,
    STATE_CONTEXT_DIRTY,
    ReceiptRecord,
    consume_availability_jsonl,
    consume_injury_stream,
    hydrate_bus_from_jsonl,
)
from src.agents.persistence import DurableRedisStreamBus
from src.availability_producer import (
    load_events_jsonl,
    run_availability_poll,
)


def _espn_payload(entries: list[dict], timestamp: str = "2026-11-18T14:52:00Z") -> dict:
    return {
        "timestamp": timestamp,
        "status": "success",
        "injuries": [{"injuries": entries}],
    }


def _espn_entry(name: str, team: str, status: str, espn_id: str, date: str = "2026-11-18T14:50:00Z") -> dict:
    return {
        "status": status,
        "date": date,
        "shortComment": f"{name} {status}",
        "athlete": {
            "displayName": name,
            "team": {"abbreviation": team},
            "links": [{"href": f"https://www.espn.com/nfl/player/_/id/{espn_id}/player"}],
        },
    }


def test_status_change_creates_one_event_and_replay_is_idempotent(tmp_path):
    jsonl_path = tmp_path / "availability_events.jsonl"
    report_path = tmp_path / "availability_report.json"
    roster = [{"gsis_id": "00-0039075", "espn_id": "4426515", "team": "LA", "status": "ACT", "status_description_abbr": "A01"}]
    espn = _espn_payload([_espn_entry("Puka Nacua", "LAR", "Out", "4426515", date="2026-11-18T14:00:00Z")])
    now = datetime(2026, 11, 18, 14, 15, 0, tzinfo=timezone.utc)

    rep1 = run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=espn,
        roster_rows=roster,
        now=now,
    )
    assert rep1["new_events_count"] == 1
    assert rep1["sticky_excluded_count"] == 1
    assert "00-0039075" in rep1["excluded_player_ids"]
    assert rep1["disagreements"]  # ESPN Out vs roster ACT -> more restrictive (OUT) wins

    events, _, chain_ok = load_events_jsonl(jsonl_path)
    assert chain_ok is True
    assert len(events) == 1
    assert events[0]["prev_event_hash"] == "GENESIS"
    assert events[0]["latency_seconds"] == 900.0

    # Replay same payload -> zero new events
    rep2 = run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=espn,
        roster_rows=roster,
        now=now + timedelta(minutes=15),
    )
    assert rep2["new_events_count"] == 0
    events2, _, chain_ok2 = load_events_jsonl(jsonl_path)
    assert chain_ok2 is True
    assert len(events2) == 1


def test_sticky_exclusion_persists_when_absent_and_roster_res_emits_event(tmp_path):
    jsonl_path = tmp_path / "availability_events.jsonl"
    report_path = tmp_path / "availability_report.json"
    roster_act = [{"gsis_id": "00-0036900", "espn_id": "4040715", "team": "CIN", "status": "ACT", "status_description_abbr": "A01"}]

    # Poll 1: player is OUT on ESPN
    t1 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=_espn_payload([_espn_entry("Tee Higgins", "CIN", "Out", "4040715")], timestamp=t1.isoformat()),
        roster_rows=roster_act,
        now=t1,
    )

    # Poll 2: player is ABSENT from ESPN, and Nacua is ABSENT from ESPN while roster shows RES
    t2 = datetime(2026, 11, 18, 14, 0, tzinfo=timezone.utc)
    roster_res = roster_act + [
        {"gsis_id": "00-0039075", "espn_id": "4426515", "team": "LA", "status": "RES", "status_description_abbr": "R01"}
    ]
    rep2 = run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=_espn_payload([], timestamp=t2.isoformat()),
        roster_rows=roster_res,
        now=t2,
    )
    assert "00-0036900" in rep2["excluded_player_ids"]  # Sticky: absent from feed != healthy
    assert "00-0039075" in rep2["excluded_player_ids"]  # Nacua excluded via nflverse_roster RES
    events, _, chain_ok = load_events_jsonl(jsonl_path)
    assert chain_ok is True
    nacua_ev = [e for e in events if e["player_id"] == "00-0039075"][0]
    assert nacua_ev["source"] == "nflverse_roster"
    assert nacua_ev["category"] == "IR"
    assert nacua_ev["observed_at"] == t2.isoformat()


def test_failed_fetch_keeps_prior_exclusions_and_emits_stale_context(tmp_path):
    jsonl_path = tmp_path / "availability_events.jsonl"
    report_path = tmp_path / "availability_report.json"
    roster = [{"gsis_id": "00-0039075", "espn_id": "4426515", "team": "LA", "status": "RES", "status_description_abbr": "R01"}]
    t1 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=_espn_payload([], timestamp=t1.isoformat()),
        roster_rows=roster,
        now=t1,
    )

    t2 = datetime(2026, 11, 18, 14, 0, tzinfo=timezone.utc)
    rep_fail = run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=None,
        espn_error="HTTP 503",
        roster_rows=None,
        roster_error="timeout",
        now=t2,
    )
    assert rep_fail["stale_context"] is True
    assert any("ESPN_FETCH_FAILED" in r for r in rep_fail["stale_reasons"])
    assert "00-0039075" in rep_fail["excluded_player_ids"]


def test_unmatched_and_ambiguous_player_never_guessed(tmp_path):
    jsonl_path = tmp_path / "availability_events.jsonl"
    report_path = tmp_path / "availability_report.json"
    roster = [
        {"gsis_id": "00-0000001", "espn_id": "999", "team": "NYG", "status": "ACT", "status_description_abbr": "A01"},
        {"gsis_id": "00-0000002", "espn_id": "999", "team": "NYG", "status": "ACT", "status_description_abbr": "A01"},
    ]
    espn = _espn_payload([
        _espn_entry("Greg Desrosiers Jr.", "NYG", "Out", "4691889"),
        _espn_entry("Ambiguous Player", "NYG", "Out", "999"),
    ])
    rep = run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=espn,
        roster_rows=roster,
        now=datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc),
    )
    assert rep["excluded_player_ids"] == []
    assert len(rep["unmatched"]) == 2
    reasons = {u["player_name"]: u["reason"] for u in rep["unmatched"]}
    assert reasons["Greg Desrosiers Jr."] == "UNMATCHED_ESPN_ID"
    assert reasons["Ambiguous Player"] == "AMBIGUOUS_ESPN_ID"


def test_week10_and_week11_nacua_replay_and_consumer_jsonl(tmp_path):
    jsonl_path = tmp_path / "availability_events.jsonl"
    report_path = tmp_path / "availability_report.json"
    w10_override = Path("viewer/official_inactives_2026_w10.json")
    assert w10_override.exists()

    # Week 10 replay: coach statement override -> REPORTED_OUT, plus Desrosiers unmatched
    t_w10 = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
    rep_w10 = run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=_espn_payload([_espn_entry("Greg Desrosiers Jr.", "NYG", "Active", "4691889")], timestamp=t_w10.isoformat()),
        roster_rows=[],
        override_paths=[w10_override],
        now=t_w10,
    )
    assert "00-0039075" in rep_w10["excluded_player_ids"]
    assert rep_w10["counts_by_category"].get("REPORTED_OUT") == 1
    assert any(u["player_name"] == "Greg Desrosiers Jr." for u in rep_w10["unmatched"])

    # Week 11 replay: roster shows RES -> upgrades REPORTED_OUT -> IR
    t_w11 = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
    rep_w11 = run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=_espn_payload([], timestamp=t_w11.isoformat()),
        roster_rows=[{"gsis_id": "00-0039075", "espn_id": "4426515", "team": "LA", "status": "RES", "status_description_abbr": "R01"}],
        now=t_w11,
    )
    assert "00-0039075" in rep_w11["excluded_player_ids"]
    assert rep_w11["counts_by_category"].get("IR") == 1

    # Consumer JSONL replay & DurableRedisStreamBus hydration reproduce identical exclusions
    receipts_direct = [
        ReceiptRecord(receipt_id="r-nacua", player_id="00-0039075", team="LA", market="receiving_yards", line=85.5, receipt_hash="h-nacua", base_projection=85.0, base_edge_pp=6.0),
        ReceiptRecord(receipt_id="r-kupp", player_id="00-0033908", team="LA", market="receiving_yards", line=65.5, receipt_hash="h-kupp", base_projection=65.0, base_edge_pp=5.5),
    ]
    receipts_bus = [
        ReceiptRecord(receipt_id="r-nacua", player_id="00-0039075", team="LA", market="receiving_yards", line=85.5, receipt_hash="h-nacua", base_projection=85.0, base_edge_pp=6.0),
        ReceiptRecord(receipt_id="r-kupp", player_id="00-0033908", team="LA", market="receiving_yards", line=65.5, receipt_hash="h-kupp", base_projection=65.0, base_edge_pp=5.5),
    ]
    usage_table = {"00-0039075": {"target_share": 0.30, "beneficiary_weights": {"00-0033908": 0.40}}}

    st_direct = consume_availability_jsonl(
        jsonl_path,
        receipts=receipts_direct,
        model_usage_table=usage_table,
        news_week=11,
        board_week=11,
        capture_week=11,
        now=t_w11,
    )
    bus = DurableRedisStreamBus(wal_db_path=tmp_path / "wal.db")
    assert hydrate_bus_from_jsonl(jsonl_path, bus) == 2
    st_bus = consume_injury_stream(
        bus,
        receipts=receipts_bus,
        model_usage_table=usage_table,
        news_week=11,
        board_week=11,
        capture_week=11,
        now=t_w11,
    )
    assert st_direct.voided_receipt_ids == {"r-nacua"}
    assert st_bus.voided_receipt_ids == {"r-nacua"}
    assert st_direct.receipts["r-nacua"].state == STATE_AVAILABILITY_VOID
    assert st_direct.receipts["r-kupp"].state == STATE_CONTEXT_DIRTY
    assert st_bus.receipts["r-kupp"].state == STATE_CONTEXT_DIRTY


def test_end_to_end_jsonl_sets_excl_reason_and_drops_from_slate_board(tmp_path):
    import sys
    import pandas as pd

    if "src" not in sys.path:
        sys.path.insert(0, "src")
    from slate import availability_eligible, availability_events_exclusions

    jsonl_path = tmp_path / "availability_events.jsonl"
    report_path = tmp_path / "availability_report.json"
    roster = [
        {"gsis_id": "00-0039075", "espn_id": "4426515", "team": "LA", "status": "ACT", "status_description_abbr": "A01"},
        {"gsis_id": "00-0033908", "espn_id": "2977187", "team": "LA", "status": "ACT", "status_description_abbr": "A01"},
    ]
    run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=_espn_payload([_espn_entry("Puka Nacua", "LAR", "Out", "4426515")]),
        roster_rows=roster,
        now=datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc),
    )

    slate_df = pd.DataFrame([
        {"player_id": "00-0039075", "player_display_name": "Puka Nacua", "team": "LA", "status": "ACT"},
        {"player_id": "00-0033908", "player_display_name": "Cooper Kupp", "team": "LA", "status": "ACT"},
    ])
    ev_excl = availability_events_exclusions(str(jsonl_path))
    eligible = availability_eligible(slate_df, injured=set(), confirmed_inactive=set(), espn={}, availability_events=ev_excl)
    nacua_row = eligible[eligible["player_id"] == "00-0039075"].iloc[0]
    assert "out" in nacua_row["excl_reason"]
    viewer_board = eligible[eligible["excl_reason"] == ""]
    assert list(viewer_board["player_id"]) == ["00-0033908"]


def test_sticky_clearing_rule_and_stale_review_report(tmp_path):
    jsonl_path = tmp_path / "availability_events.jsonl"
    report_path = tmp_path / "availability_report.json"
    roster_nacua_act = [{"gsis_id": "00-0039075", "espn_id": "4426515", "team": "LA", "status": "ACT", "status_description_abbr": "A01"}]

    # 1. Nacua-style sequence: REPORTED_OUT, ESPN absent, roster ACT for 10 polls (>48h) -> stays excluded
    w10_override = Path("viewer/official_inactives_2026_w10.json")
    t0 = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
    r0 = run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=_espn_payload([], timestamp=t0.isoformat()),
        roster_rows=roster_nacua_act,
        override_paths=[w10_override],
        now=t0,
    )
    assert "00-0039075" in r0["excluded_player_ids"]

    for i in range(1, 11):
        ti = t0 + timedelta(hours=12 * i)
        ri = run_availability_poll(
            jsonl_path=jsonl_path,
            report_path=report_path,
            espn_payload=_espn_payload([], timestamp=ti.isoformat()),
            roster_rows=roster_nacua_act,
            now=ti,
        )
        assert "00-0039075" in ri["excluded_player_ids"]

    # After 8 days -> flagged in stale_exclusions_for_review
    r_old = run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=_espn_payload([], timestamp=(t0 + timedelta(days=8)).isoformat()),
        roster_rows=roster_nacua_act,
        now=t0 + timedelta(days=8),
        review_age_days=7,
    )
    assert any(item["player_id"] == "00-0039075" for item in r_old["stale_exclusions_for_review"])

    # 2. Roster RES -> ACT transition clears ONLY after 48h minimum age
    roster_res = [{"gsis_id": "00-0036900", "espn_id": "4040715", "team": "CIN", "status": "RES", "status_description_abbr": "R01"}]
    roster_act = [{"gsis_id": "00-0036900", "espn_id": "4040715", "team": "CIN", "status": "ACT", "status_description_abbr": "A01"}]
    t_res = t0 + timedelta(days=9)
    run_availability_poll(
        jsonl_path=jsonl_path, report_path=report_path,
        espn_payload=_espn_payload([], timestamp=t_res.isoformat()),
        roster_rows=roster_res, now=t_res,
    )
    # Before 48h -> stays excluded even on RES -> ACT transition
    r_early = run_availability_poll(
        jsonl_path=jsonl_path, report_path=report_path,
        espn_payload=_espn_payload([], timestamp=(t_res + timedelta(hours=24)).isoformat()),
        roster_rows=roster_act, now=t_res + timedelta(hours=24),
    )
    assert "00-0036900" in r_early["excluded_player_ids"]
    # After 48h -> RES -> ACT transition clears
    r_cleared_roster = run_availability_poll(
        jsonl_path=jsonl_path, report_path=report_path,
        espn_payload=_espn_payload([], timestamp=(t_res + timedelta(hours=49)).isoformat()),
        roster_rows=roster_act, now=t_res + timedelta(hours=49),
    )
    assert "00-0036900" not in r_cleared_roster["excluded_player_ids"]

    # 3. ESPN explicit ACTIVE clears ONLY after 48h minimum age
    t_espn = t_res + timedelta(days=3)
    run_availability_poll(
        jsonl_path=jsonl_path, report_path=report_path,
        espn_payload=_espn_payload([_espn_entry("Tee Higgins", "CIN", "Out", "4040715")], timestamp=t_espn.isoformat()),
        roster_rows=roster_act, now=t_espn,
    )
    r_espn_early = run_availability_poll(
        jsonl_path=jsonl_path, report_path=report_path,
        espn_payload=_espn_payload([_espn_entry("Tee Higgins", "CIN", "Active", "4040715")], timestamp=(t_espn + timedelta(hours=12)).isoformat()),
        roster_rows=roster_act, now=t_espn + timedelta(hours=12),
    )
    assert "00-0036900" in r_espn_early["excluded_player_ids"]
    r_espn_cleared = run_availability_poll(
        jsonl_path=jsonl_path, report_path=report_path,
        espn_payload=_espn_payload([_espn_entry("Tee Higgins", "CIN", "Active", "4040715")], timestamp=(t_espn + timedelta(hours=49)).isoformat()),
        roster_rows=roster_act, now=t_espn + timedelta(hours=49),
    )
    assert "00-0036900" not in r_espn_cleared["excluded_player_ids"]


def test_availability_poll_wires_injury_consumer_void_dirty_and_stale_context(tmp_path):
    from src.agents.injury_consumer import (
        STATE_AVAILABILITY_VOID,
        STATE_CONTEXT_DIRTY,
        STATE_STALE_CONTEXT,
        ReceiptRecord,
    )

    jsonl_path = tmp_path / "availability_events.jsonl"
    report_path = tmp_path / "availability_report.json"
    receipts_path = tmp_path / "open_receipts.json"
    roster = [
        {"gsis_id": "00-0039075", "espn_id": "4426515", "team": "LA", "status": "ACT", "status_description_abbr": "A01"},
        {"gsis_id": "00-0033908", "espn_id": "2977187", "team": "LA", "status": "ACT", "status_description_abbr": "A01"},
    ]
    open_receipts = [
        ReceiptRecord(
            receipt_id="rcpt-nacua",
            player_id="00-0039075",
            team="LA",
            market="receiving_yards",
            receipt_hash="a" * 64,
            line=74.5,
            base_projection=82.0,
            base_edge_pp=11.0,
        ),
        ReceiptRecord(
            receipt_id="rcpt-kupp",
            player_id="00-0033908",
            team="LA",
            market="receiving_yards",
            receipt_hash="b" * 64,
            line=68.5,
            base_projection=73.0,
            base_edge_pp=10.2,
        ),
    ]
    usage_table = {
        "00-0039075": {
            "target_share": 0.28,
            "beneficiary_weights": {"00-0033908": 0.25},
        }
    }
    now = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
    report = run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=_espn_payload([_espn_entry("Puka Nacua", "LAR", "Out", "4426515")], timestamp=now.isoformat()),
        roster_rows=roster,
        now=now,
        open_receipts=open_receipts,
        receipts_path=receipts_path,
        model_usage_table=usage_table,
        news_week=11,
        board_week=11,
        capture_week=11,
    )
    assert report["receipt_consumer"]["voided_receipt_ids"] == ["rcpt-nacua"]
    assert report["receipt_consumer"]["dirty_receipt_ids"] == ["rcpt-kupp"]

    persisted = {r["receipt_id"]: r for r in json.loads(receipts_path.read_text(encoding="utf-8"))}
    assert persisted["rcpt-nacua"]["state"] == STATE_AVAILABILITY_VOID
    assert persisted["rcpt-nacua"]["active"] is False
    assert persisted["rcpt-nacua"]["can_execute"] is False
    assert persisted["rcpt-nacua"]["excluded_from_n"] is True
    assert persisted["rcpt-kupp"]["state"] == STATE_CONTEXT_DIRTY

    # Stale context (e.g., ESPN fetch failure) pauses action fields across open receipts
    stale_report = run_availability_poll(
        jsonl_path=jsonl_path,
        report_path=report_path,
        espn_payload=None,
        espn_error="timeout",
        roster_rows=roster,
        now=now + timedelta(minutes=5),
        receipts_path=receipts_path,
        model_usage_table=usage_table,
        news_week=11,
        board_week=11,
        capture_week=11,
    )
    assert stale_report["stale_context"] is True
    assert stale_report["receipt_consumer"]["gate_paused"] is True
    assert stale_report["receipt_consumer"]["pause_reason"] == STATE_STALE_CONTEXT
    paused = {r["receipt_id"]: r for r in json.loads(receipts_path.read_text(encoding="utf-8"))}
    assert all(r["active"] is False and r["can_execute"] is False and r["state"] == STATE_STALE_CONTEXT for r in paused.values())
