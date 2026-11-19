"""Decomposed CLV Attribution Engine.

Decomposes total Closing Line Value into:
1. Line Value (beating the number / spread movement)
2. Price Value (beating the juice / vig at the given line)
3. Timing Value (early market entry vs. last-minute noise)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class DecomposedSignalCLV:
    signal_id: str
    total_clv_pp: float
    line_value_pp: float
    price_value_pp: float
    hours_before_close: float
    timing_attributed_pp: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CohortDecompositionSummary:
    n_signals: int
    mean_total_clv_pp: float
    mean_line_value_pp: float
    mean_price_value_pp: float
    mean_timing_value_pp: float
    line_beat_rate: float
    price_beat_rate: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def decompose_single_clv(
    signal_id: str,
    *,
    entry_line: float,
    close_line: float,
    entry_implied_prob: float,
    close_devig_prob: float,
    side: str = "OVER",
    hours_before_close: float = 2.0,
    line_sensitivity_pp_per_unit: float = 2.5,  # ~2.5 pp probability change per yard
) -> DecomposedSignalCLV:
    """Decompose CLV for one graded signal into line, price, and timing components."""
    side_mult = 1.0 if str(side).upper() == "OVER" else -1.0
    line_delta = (close_line - entry_line) * side_mult

    total_clv_pp = (close_devig_prob - entry_implied_prob) * 100.0

    # Line movement value
    line_val_pp = line_delta * line_sensitivity_pp_per_unit

    # Price value is total minus line shift
    price_val_pp = total_clv_pp - line_val_pp

    # Timing attribution: early entries (> 4h) capture early bookmaker unhedged variance
    h = max(0.0, float(hours_before_close))
    timing_factor = min(1.0, h / 12.0)
    timing_attributed = round(total_clv_pp * timing_factor * 0.25, 4)

    return DecomposedSignalCLV(
        signal_id=signal_id,
        total_clv_pp=round(total_clv_pp, 4),
        line_value_pp=round(line_val_pp, 4),
        price_value_pp=round(price_val_pp, 4),
        hours_before_close=round(h, 2),
        timing_attributed_pp=timing_attributed,
    )


def summarize_cohort_decomposition(
    decomposed_signals: Sequence[DecomposedSignalCLV],
) -> CohortDecompositionSummary:
    """Aggregate decomposed components across an entire cohort."""
    n = len(decomposed_signals)
    if n == 0:
        return CohortDecompositionSummary(
            n_signals=0,
            mean_total_clv_pp=0.0,
            mean_line_value_pp=0.0,
            mean_price_value_pp=0.0,
            mean_timing_value_pp=0.0,
            line_beat_rate=0.0,
            price_beat_rate=0.0,
        )

    mean_total = sum(s.total_clv_pp for s in decomposed_signals) / n
    mean_line = sum(s.line_value_pp for s in decomposed_signals) / n
    mean_price = sum(s.price_value_pp for s in decomposed_signals) / n
    mean_timing = sum(s.timing_attributed_pp for s in decomposed_signals) / n

    line_beats = sum(1 for s in decomposed_signals if s.line_value_pp > 0.0)
    price_beats = sum(1 for s in decomposed_signals if s.price_value_pp > 0.0)

    return CohortDecompositionSummary(
        n_signals=n,
        mean_total_clv_pp=round(mean_total, 4),
        mean_line_value_pp=round(mean_line, 4),
        mean_price_value_pp=round(mean_price, 4),
        mean_timing_value_pp=round(mean_timing, 4),
        line_beat_rate=round(line_beats / n, 4),
        price_beat_rate=round(price_beats / n, 4),
    )


@dataclass(frozen=True)
class DecomposedPriceCLV:
    signal_id: str
    close_state: str
    is_confirmatory: bool
    quote_source: str
    total_price_clv_pp: float
    line_component: float
    juice_component: float
    book_execution_component: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def decompose_price_clv(
    signal_id: str,
    *,
    close_state: str,
    quote_source: str,
    entry_devig_p: float,
    translated_close_devig_p: float,
    raw_close_devig_p_at_close_line: float | None = None,
    is_confirmatory: bool = True,
    best_tape_devig_p: float | None = None,
    best_tape_quote_source: str | None = None,
) -> DecomposedPriceCLV:
    """Decompose price CLV into line_component, juice_component, and book_execution_component.

    - Same yard, worse juice scores strictly negative (total_price_clv_pp < 0).
    - Confirmatory sample (Polymarket vs Polymarket): book_execution_component is identically 0.0.
    - Shadow ledger (is_confirmatory=False): book_execution_component is computed only inside
      one quote_source class against best_tape_devig_p at the same source_ts.
    """
    if close_state not in {"CLOSE_EXACT", "CLOSE_TRANSLATED"}:
        raise ValueError(f"cannot decompose non-evaluable close_state={close_state!r}")

    total_pp = (float(translated_close_devig_p) - float(entry_devig_p)) * 100.0
    if close_state == "CLOSE_EXACT" or raw_close_devig_p_at_close_line is None:
        line_pp = 0.0
        juice_pp = total_pp
    else:
        line_pp = (float(translated_close_devig_p) - float(raw_close_devig_p_at_close_line)) * 100.0
        juice_pp = total_pp - line_pp

    if is_confirmatory:
        book_exec_pp = 0.0
    else:
        if best_tape_devig_p is None:
            book_exec_pp = 0.0
        else:
            if best_tape_quote_source and best_tape_quote_source.strip().lower() != quote_source.strip().lower():
                raise ValueError("book_execution_component may only compare within one quote_source class")
            book_exec_pp = (float(best_tape_devig_p) - float(entry_devig_p)) * 100.0

    return DecomposedPriceCLV(
        signal_id=signal_id,
        close_state=close_state,
        is_confirmatory=is_confirmatory,
        quote_source=quote_source,
        total_price_clv_pp=round(total_pp, 4),
        line_component=round(line_pp, 4),
        juice_component=round(juice_pp, 4),
        book_execution_component=round(book_exec_pp, 4),
    )


def clv_stratum_key(item: DecomposedPriceCLV) -> tuple[str, str, str, str]:
    """Return the stratification key (evidential_class, exact_or_translated, quote_source, close_state)."""
    evidential_class = "confirmatory" if item.is_confirmatory else "shadow"
    translation_kind = "translated" if item.close_state == "CLOSE_TRANSLATED" else "exact"
    quote_source = item.quote_source.strip().lower()
    return (evidential_class, translation_kind, quote_source, item.close_state)


def summarize_price_clv_cohort(
    items: Sequence[DecomposedPriceCLV],
    *,
    replicates: int = 2000,
    seed: int = 20261118,
) -> dict[str, Any]:
    """Aggregate decomposed price CLV for a single homogeneous stratum.

    Raises ValueError on mixed input across evidential class, exact vs translated,
    quote source, or close state.
    """
    import random

    n = len(items)
    if n == 0:
        return {
            "stratum": None,
            "n_signals": 0,
            "n_derived": 0,
            "mean_total_clv_pp": 0.0,
            "mean_line_component": 0.0,
            "mean_juice_component": 0.0,
            "mean_book_execution_component": 0.0,
            "ci_level": 0.95,
            "ci_low": 0.0,
            "ci_high": 0.0,
        }
    strata = {clv_stratum_key(s) for s in items}
    if len(strata) > 1:
        raise ValueError(
            f"cannot pool mixed price CLV strata in summarize_price_clv_cohort: {sorted(strata)}"
        )
    stratum = next(iter(strata))
    totals = [s.total_price_clv_pp for s in items]
    mean_total = sum(totals) / n
    rng = random.Random(seed)
    boot = sorted(sum(rng.choice(totals) for _ in range(n)) / n for _ in range(replicates))
    lo_idx = max(0, int(0.025 * replicates))
    hi_idx = min(replicates - 1, int(0.975 * replicates))
    return {
        "stratum": {
            "evidential_class": stratum[0],
            "translation_kind": stratum[1],
            "quote_source": stratum[2],
            "close_state": stratum[3],
        },
        "n_signals": n,
        "n_derived": sum(1 for s in items if s.close_state == "CLOSE_TRANSLATED"),
        "mean_total_clv_pp": round(mean_total, 4),
        "mean_line_component": round(sum(s.line_component for s in items) / n, 4),
        "mean_juice_component": round(sum(s.juice_component for s in items) / n, 4),
        "mean_book_execution_component": round(sum(s.book_execution_component for s in items) / n, 4),
        "ci_level": 0.95,
        "ci_low": round(boot[lo_idx], 4),
        "ci_high": round(boot[hi_idx], 4),
    }


def summarize_price_clv_by_stratum(
    items: Sequence[DecomposedPriceCLV],
    *,
    replicates: int = 2000,
    seed: int = 20261118,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    """Group decomposed price CLV items by stratum and summarize each independently."""
    grouped: dict[tuple[str, str, str, str], list[DecomposedPriceCLV]] = {}
    for item in items:
        grouped.setdefault(clv_stratum_key(item), []).append(item)
    return {
        key: summarize_price_clv_cohort(group, replicates=replicates, seed=seed)
        for key, group in sorted(grouped.items())
    }

