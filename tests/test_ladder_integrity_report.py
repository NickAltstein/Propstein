"""Tests for P11 Part B: synthetic ladder integrity report."""

from __future__ import annotations

from scripts.ladder_integrity_report import (
    evaluate_ladder_snapshot,
    generate_ladder_integrity_report,
    render_markdown_report,
)


def _rung(thr: float, bid: str | None, ask: str | None, *, depth: str = "10.0000", sid: str = "snap-1") -> dict:
    return {
        "snapshot_id": sid,
        "ticker": f"KXMKT-{thr}",
        "rung_threshold": thr,
        "yes_bid": bid,
        "yes_ask": ask,
        "yes_bid_depth": depth if bid is not None else "0.0000",
        "no_bid_depth": depth if ask is not None else "0.0000",
    }


def test_clean_monotone_ladder():
    rungs = [
        _rung(10, "0.7800", "0.8000"),
        _rung(15, "0.5800", "0.6000"),
        _rung(20, "0.3800", "0.4000"),
        _rung(25, "0.1800", "0.2000"),
    ]
    res = evaluate_ladder_snapshot(rungs)
    assert res["executable_rungs"] == 4
    assert res["excluded_rungs"] == 0
    assert res["monotonicity_breaks"] == []
    assert res["outliers"] == []


def test_monotonicity_break_flagged():
    rungs = [
        _rung(10, "0.7000", "0.7200"),
        _rung(15, "0.5500", "0.5700"),
        # Higher rung executable yes_bid (0.60) strictly above lower rung yes_ask (0.57)
        _rung(20, "0.6000", "0.6200"),
    ]
    res = evaluate_ladder_snapshot(rungs)
    assert len(res["monotonicity_breaks"]) == 1
    assert res["monotonicity_breaks"][0]["low_threshold"] == 15.0
    assert res["monotonicity_breaks"][0]["high_threshold"] == 20.0


def test_single_outlier_rung_flagged():
    rungs = [
        _rung(10, "0.7900", "0.8100"),
        _rung(15, "0.6900", "0.7100"),  # deviates > 5c from ~0.60 interpolated curve
        _rung(20, "0.3900", "0.4100"),
        _rung(25, "0.1900", "0.2100"),
    ]
    res = evaluate_ladder_snapshot(rungs, outlier_threshold_cents=5.0)
    assert len(res["outliers"]) == 1
    assert res["outliers"][0]["threshold"] == 15.0
    assert res["outliers"][0]["deviation_cents"] > 5.0


def test_no_book_rung_excluded_and_counted():
    rungs = [
        _rung(10, "0.7800", "0.8000"),
        _rung(15, None, None),
        _rung(20, "0.3800", "0.4000"),
        _rung(25, "0.1800", "0.2000"),
    ]
    res = evaluate_ladder_snapshot(rungs)
    assert res["total_rungs"] == 4
    assert res["executable_rungs"] == 3
    assert res["excluded_rungs"] == 1


def test_two_rung_ladder_skips_outlier_fit_and_report_states_excluded_from_gate():
    rungs = [_rung(10, "0.7800", "0.8000"), _rung(15, "0.5800", "0.6000")]
    res = evaluate_ladder_snapshot(rungs)
    assert res["executable_rungs"] == 2
    assert res["insufficient_for_outlier_fit"] is True
    assert res["outliers"] == []

    summary = generate_ladder_integrity_report(rungs)
    assert summary["excluded_from_clv_gate"] is True
    md = render_markdown_report(summary)
    assert "Excluded from the CLV gate" in md
