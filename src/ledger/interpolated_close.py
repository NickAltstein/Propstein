"""Shadow-only model-free interpolated close helper (AMEND-004 DRAFT).

Pure helper; NOT called by any gate, grading path, or API endpoint.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

CLOSE_INTERPOLATED = "CLOSE_INTERPOLATED"
CLOSE_UNEVALUABLE = "CLOSE_UNEVALUABLE"
METHOD_NAME = "monotone_probability_linear_bracket_v1"

# Guard-rail placeholders for Nick's approval (AMEND-004 DRAFT)
PLACEHOLDER_MAX_RUNG_GAP = 10.0
PLACEHOLDER_MIN_DEPTH = 5.0
PLACEHOLDER_CLOSE_WINDOW_MINUTES = 15.0


def _rung_mid_prob(rung: Mapping[str, Any]) -> float | None:
    if rung.get("status") not in (None, "OK"):
        return None
    bid_raw = rung.get("yes_bid")
    ask_raw = rung.get("yes_ask")
    if bid_raw is None or ask_raw is None:
        return None
    bid = float(bid_raw)
    ask = float(ask_raw)
    if not (0.0 <= bid <= ask <= 1.0):
        return None
    return 0.5 * (bid + ask)


def _has_min_depth(rung: Mapping[str, Any], min_depth: float) -> bool:
    y_dep = rung.get("yes_bid_depth")
    n_dep = rung.get("no_bid_depth")
    if y_dep is None or n_dep is None:
        return False
    return float(y_dep) >= min_depth and float(n_dep) >= min_depth


def compute_interpolated_close(
    *,
    bet_line: float,
    rungs: Sequence[Mapping[str, Any]],
    max_rung_gap: float = PLACEHOLDER_MAX_RUNG_GAP,
    min_depth: float = PLACEHOLDER_MIN_DEPTH,
    close_window_minutes: float = PLACEHOLDER_CLOSE_WINDOW_MINUTES,
    close_translated_prob: float | None = None,
) -> dict[str, Any]:
    """Compute model-free CLOSE_INTERPOLATED or fail closed as CLOSE_UNEVALUABLE."""
    ordered = sorted(rungs, key=lambda r: float(r["rung_threshold"]))
    if len(ordered) < 2:
        return {
            "close_state": CLOSE_UNEVALUABLE,
            "reason": "INSUFFICIENT_RUNGS",
            "interpolated_prob": None,
            "close_translated_prob": close_translated_prob,
            "method_name": METHOD_NAME,
        }

    # Find adjacent pair bracketing bet_line
    low_rung: Mapping[str, Any] | None = None
    high_rung: Mapping[str, Any] | None = None
    for i in range(len(ordered) - 1):
        k_lo = float(ordered[i]["rung_threshold"])
        k_hi = float(ordered[i + 1]["rung_threshold"])
        if k_lo <= bet_line <= k_hi and k_lo < k_hi:
            low_rung = ordered[i]
            high_rung = ordered[i + 1]
            break

    if low_rung is None or high_rung is None:
        return {
            "close_state": CLOSE_UNEVALUABLE,
            "reason": "OUTSIDE_LADDER",
            "interpolated_prob": None,
            "close_translated_prob": close_translated_prob,
            "method_name": METHOD_NAME,
        }

    k_lo = float(low_rung["rung_threshold"])
    k_hi = float(high_rung["rung_threshold"])
    if (k_hi - k_lo) > max_rung_gap:
        return {
            "close_state": CLOSE_UNEVALUABLE,
            "reason": "GAP_TOO_LARGE",
            "interpolated_prob": None,
            "close_translated_prob": close_translated_prob,
            "method_name": METHOD_NAME,
        }

    if low_rung.get("snapshot_id") != high_rung.get("snapshot_id") or not low_rung.get("snapshot_id"):
        return {
            "close_state": CLOSE_UNEVALUABLE,
            "reason": "SNAPSHOT_ID_MISMATCH",
            "interpolated_prob": None,
            "close_translated_prob": close_translated_prob,
            "method_name": METHOD_NAME,
        }

    for r in (low_rung, high_rung):
        off = r.get("offset_minutes")
        if off is not None and not (0.0 <= float(off) <= close_window_minutes):
            return {
                "close_state": CLOSE_UNEVALUABLE,
                "reason": "OUTSIDE_CLOSE_WINDOW",
                "interpolated_prob": None,
                "close_translated_prob": close_translated_prob,
                "method_name": METHOD_NAME,
            }

    p_lo = _rung_mid_prob(low_rung)
    p_hi = _rung_mid_prob(high_rung)
    if p_lo is None or p_hi is None:
        return {
            "close_state": CLOSE_UNEVALUABLE,
            "reason": "NO_BOOK_OR_INVALID_QUOTE",
            "interpolated_prob": None,
            "close_translated_prob": close_translated_prob,
            "method_name": METHOD_NAME,
        }

    if not (_has_min_depth(low_rung, min_depth) and _has_min_depth(high_rung, min_depth)):
        return {
            "close_state": CLOSE_UNEVALUABLE,
            "reason": "THIN_DEPTH",
            "interpolated_prob": None,
            "close_translated_prob": close_translated_prob,
            "method_name": METHOD_NAME,
        }

    if p_lo < p_hi or float(high_rung["yes_bid"]) > float(low_rung["yes_ask"]):
        return {
            "close_state": CLOSE_UNEVALUABLE,
            "reason": "NON_MONOTONE_LADDER",
            "interpolated_prob": None,
            "close_translated_prob": close_translated_prob,
            "method_name": METHOD_NAME,
        }

    weight = (bet_line - k_lo) / (k_hi - k_lo)
    interp = round(p_lo + weight * (p_hi - p_lo), 6)

    def _rung_summary(r: Mapping[str, Any], prob: float) -> dict[str, Any]:
        return {
            "ticker": r.get("ticker"),
            "rung_threshold": float(r["rung_threshold"]),
            "yes_bid": r.get("yes_bid"),
            "yes_ask": r.get("yes_ask"),
            "yes_bid_depth": r.get("yes_bid_depth"),
            "no_bid_depth": r.get("no_bid_depth"),
            "mid_prob": round(prob, 6),
            "snapshot_id": r.get("snapshot_id"),
        }

    return {
        "close_state": CLOSE_INTERPOLATED,
        "reason": None,
        "interpolated_prob": interp,
        "close_translated_prob": close_translated_prob,
        "method_name": METHOD_NAME,
        "low_rung": _rung_summary(low_rung, p_lo),
        "high_rung": _rung_summary(high_rung, p_hi),
    }
