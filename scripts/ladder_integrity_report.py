"""Read-only Kalshi ladder integrity diagnostic report generator.

Excluded from the CLV gate. Produces no action fields, no edges, and no stakes.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.contracts.ladder_placeholders import (
    PLACEHOLDER_MIN_DEPTH_CONTRACTS as _SHARED_MIN_DEPTH,
    PLACEHOLDER_OUTLIER_THRESHOLD_CENTS as _SHARED_OUTLIER_CENTS,
)

# Placeholder thresholds for Nick's approval (docs/ladder-integrity-spec.md)
PLACEHOLDER_OUTLIER_THRESHOLD_CENTS = Decimal(str(_SHARED_OUTLIER_CENTS))
PLACEHOLDER_MIN_DEPTH_CONTRACTS = Decimal(str(_SHARED_MIN_DEPTH))


def _pava_non_increasing(values: Sequence[float]) -> list[float]:
    """Pool Adjacent Violators Algorithm for non-increasing (survival) curve."""
    blocks: list[list[float]] = []  # [sum, count]
    for v in values:
        blocks.append([float(v), 1.0])
        while len(blocks) >= 2:
            m1 = blocks[-2][0] / blocks[-2][1]
            m2 = blocks[-1][0] / blocks[-1][1]
            if m1 < m2:
                b2 = blocks.pop()
                blocks[-1][0] += b2[0]
                blocks[-1][1] += b2[1]
            else:
                break
    out: list[float] = []
    for total, count in blocks:
        mean = total / count
        out.extend([mean] * int(count))
    return out


def _interval_distance_cents(curve_val: float, bid: float, ask: float) -> float:
    if bid <= curve_val <= ask:
        return 0.0
    if curve_val < bid:
        return round((bid - curve_val) * 100.0, 4)
    return round((curve_val - ask) * 100.0, 4)


def evaluate_ladder_snapshot(
    rungs: Sequence[Mapping[str, Any]],
    *,
    outlier_threshold_cents: float = float(PLACEHOLDER_OUTLIER_THRESHOLD_CENTS),
    min_depth: float = float(PLACEHOLDER_MIN_DEPTH_CONTRACTS),
) -> dict[str, Any]:
    """Evaluate one snapshot_id ladder for executable inclusion, monotonicity breaks, and outliers."""
    ordered = sorted(rungs, key=lambda r: float(r["rung_threshold"]))
    excluded_count = 0
    executable: list[dict[str, Any]] = []

    for r in ordered:
        bid_raw = r.get("yes_bid")
        ask_raw = r.get("yes_ask")
        y_dep = r.get("yes_bid_depth")
        n_dep = r.get("no_bid_depth")
        if bid_raw is None or ask_raw is None or y_dep is None or n_dep is None:
            excluded_count += 1
            continue
        bid = float(bid_raw)
        ask = float(ask_raw)
        if float(y_dep) < min_depth or float(n_dep) < min_depth or bid > ask:
            excluded_count += 1
            continue
        executable.append({
            "ticker": str(r.get("ticker") or ""),
            "threshold": float(r["rung_threshold"]),
            "bid": bid,
            "ask": ask,
            "mid": 0.5 * (bid + ask),
        })

    # Monotonicity break on a survival ladder (k_high > k_low):
    # higher rung executable yes_bid strictly above lower rung executable yes_ask.
    breaks: list[dict[str, Any]] = []
    for i in range(len(executable)):
        for j in range(i + 1, len(executable)):
            lo = executable[i]
            hi = executable[j]
            if hi["bid"] > lo["ask"]:
                breaks.append({
                    "low_threshold": lo["threshold"],
                    "high_threshold": hi["threshold"],
                    "low_yes_ask": lo["ask"],
                    "high_yes_bid": hi["bid"],
                    "inversion_cents": round((hi["bid"] - lo["ask"]) * 100.0, 4),
                })

    # Leave-one-out monotone curve outlier check for interior rungs (requires >= 3 executable rungs)
    outliers: list[dict[str, Any]] = []
    max_dev_cents = 0.0
    if len(executable) >= 3:
        for idx in range(1, len(executable) - 1):
            target = executable[idx]
            others = [executable[k] for k in range(len(executable)) if k != idx]
            fitted = _pava_non_increasing([o["mid"] for o in others])
            left = others[idx - 1]["threshold"]
            right = others[idx]["threshold"]
            f_left = fitted[idx - 1]
            f_right = fitted[idx]
            w = (target["threshold"] - left) / (right - left) if right != left else 0.5
            expected = f_left + w * (f_right - f_left)
            dev_cents = _interval_distance_cents(expected, target["bid"], target["ask"])
            if dev_cents > max_dev_cents:
                max_dev_cents = dev_cents
            if dev_cents > outlier_threshold_cents:
                outliers.append({
                    "ticker": target["ticker"],
                    "threshold": target["threshold"],
                    "expected_prob": round(expected, 4),
                    "yes_bid": target["bid"],
                    "yes_ask": target["ask"],
                    "deviation_cents": dev_cents,
                })

    return {
        "total_rungs": len(ordered),
        "executable_rungs": len(executable),
        "excluded_rungs": excluded_count,
        "monotonicity_breaks": breaks,
        "outliers": outliers,
        "max_deviation_cents": round(max_dev_cents, 4),
        "insufficient_for_outlier_fit": len(executable) < 3,
    }


def generate_ladder_integrity_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    outlier_threshold_cents: float = float(PLACEHOLDER_OUTLIER_THRESHOLD_CENTS),
) -> dict[str, Any]:
    """Aggregate integrity metrics across all ladder snapshots (read-only, excluded from CLV gate)."""
    by_snapshot: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        sid = str(row.get("snapshot_id") or "")
        if sid:
            by_snapshot.setdefault(sid, []).append(row)

    results = [
        evaluate_ladder_snapshot(rungs, outlier_threshold_cents=outlier_threshold_cents)
        for rungs in by_snapshot.values()
    ]
    return {
        "schema": "propwire.ladder_integrity_report.v1",
        "excluded_from_clv_gate": True,
        "placeholder_outlier_threshold_cents": outlier_threshold_cents,
        "snapshot_count": len(results),
        "total_rungs": sum(r["total_rungs"] for r in results),
        "executable_rungs": sum(r["executable_rungs"] for r in results),
        "excluded_rungs": sum(r["excluded_rungs"] for r in results),
        "monotonicity_break_snapshots": sum(1 for r in results if r["monotonicity_breaks"]),
        "outlier_snapshots": sum(1 for r in results if r["outliers"]),
        "max_deviation_cents": max((r["max_deviation_cents"] for r in results), default=0.0),
    }


def render_markdown_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Kalshi Ladder Integrity Report",
        "",
        "> **Excluded from the CLV gate.** Read-only diagnostic; produces no action fields, no edges, and no stakes.",
        "",
        f"- **Snapshots evaluated:** {summary['snapshot_count']}",
        f"- **Total rungs:** {summary['total_rungs']} (executable: {summary['executable_rungs']}, excluded: {summary['excluded_rungs']})",
        f"- **Monotonicity break snapshots:** {summary['monotonicity_break_snapshots']}",
        f"- **Outlier snapshots (>{summary['placeholder_outlier_threshold_cents']}c placeholder):** {summary['outlier_snapshots']}",
        f"- **Max deviation (cents):** {summary['max_deviation_cents']}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate read-only ladder integrity report.")
    parser.add_argument("--jsonl", type=Path, default=Path("data/ladder_captures.jsonl"))
    parser.add_argument("--out-json", type=Path, default=Path("reports/ladder_integrity_report.json"))
    parser.add_argument("--out-md", type=Path, default=Path("reports/ladder_integrity_report.md"))
    args = parser.parse_args()

    rows = (
        [json.loads(line) for line in args.jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
        if args.jsonl.exists()
        else []
    )
    summary = generate_ladder_integrity_report(rows)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.out_md.write_text(render_markdown_report(summary), encoding="utf-8")


if __name__ == "__main__":
    main()
