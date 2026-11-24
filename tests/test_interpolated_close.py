"""Tests for P11 Part C: shadow-only CLOSE_INTERPOLATED helper."""

from __future__ import annotations

from src.ledger.interpolated_close import (
    CLOSE_INTERPOLATED,
    CLOSE_UNEVALUABLE,
    METHOD_NAME,
    compute_interpolated_close,
)


def _rung(
    thr: float,
    bid: str | None,
    ask: str | None,
    *,
    sid: str = "snap-1",
    depth: str = "10.0000",
    status: str = "OK",
    offset_minutes: float = 5.0,
) -> dict:
    return {
        "ticker": f"KXMKT-{thr}",
        "rung_threshold": thr,
        "yes_bid": bid,
        "yes_ask": ask,
        "yes_bid_depth": depth if bid is not None else "0.0000",
        "no_bid_depth": depth if ask is not None else "0.0000",
        "status": status,
        "snapshot_id": sid,
        "offset_minutes": offset_minutes,
    }


def test_exact_bracket_interpolates_monotonically_and_compares_to_translated():
    rungs = [
        _rung(15, "0.5900", "0.6100"),
        _rung(20, "0.3900", "0.4100"),
    ]
    res = compute_interpolated_close(bet_line=17.5, rungs=rungs, close_translated_prob=0.505)
    assert res["close_state"] == CLOSE_INTERPOLATED
    assert res["interpolated_prob"] == 0.5
    assert res["close_translated_prob"] == 0.505
    assert res["method_name"] == METHOD_NAME
    assert res["low_rung"]["rung_threshold"] == 15.0
    assert res["high_rung"]["rung_threshold"] == 20.0


def test_bet_line_outside_ladder_is_unevaluable():
    rungs = [_rung(15, "0.5900", "0.6100"), _rung(20, "0.3900", "0.4100")]
    res = compute_interpolated_close(bet_line=22.5, rungs=rungs)
    assert res["close_state"] == CLOSE_UNEVALUABLE
    assert res["reason"] == "OUTSIDE_LADDER"


def test_gap_too_large_is_unevaluable():
    rungs = [_rung(10, "0.7900", "0.8100"), _rung(25, "0.1900", "0.2100")]
    res = compute_interpolated_close(bet_line=17.5, rungs=rungs, max_rung_gap=10.0)
    assert res["close_state"] == CLOSE_UNEVALUABLE
    assert res["reason"] == "GAP_TOO_LARGE"


def test_non_monotone_ladder_is_unevaluable():
    rungs = [_rung(15, "0.3900", "0.4100"), _rung(20, "0.5900", "0.6100")]
    res = compute_interpolated_close(bet_line=17.5, rungs=rungs)
    assert res["close_state"] == CLOSE_UNEVALUABLE
    assert res["reason"] == "NON_MONOTONE_LADDER"


def test_no_book_rung_is_unevaluable():
    rungs = [_rung(15, "0.5900", "0.6100"), _rung(20, None, None, status="NO_BOOK")]
    res = compute_interpolated_close(bet_line=17.5, rungs=rungs)
    assert res["close_state"] == CLOSE_UNEVALUABLE
    assert res["reason"] == "NO_BOOK_OR_INVALID_QUOTE"


def test_different_snapshot_ids_is_unevaluable():
    rungs = [_rung(15, "0.5900", "0.6100", sid="snap-1"), _rung(20, "0.3900", "0.4100", sid="snap-2")]
    res = compute_interpolated_close(bet_line=17.5, rungs=rungs)
    assert res["close_state"] == CLOSE_UNEVALUABLE
    assert res["reason"] == "SNAPSHOT_ID_MISMATCH"
