"""Shadow ledger for non-confirmatory licensed books (cannot unfreeze paid access)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from src.ledger.clv_decomposition import DecomposedPriceCLV


@dataclass(frozen=True)
class ShadowLedgerEntry:
    signal_id: str
    event_id: str
    market: str
    selection: str
    quote_source: str
    book: str
    source_ts: str
    entry_line: float
    entry_american_odds: int
    entry_devig_p: float
    best_tape_devig_p: float | None
    close_state: str
    line_component: float
    juice_component: float
    book_execution_component: float
    closing_snapshot_hash: str | None
    can_unfreeze_paid_access: bool = False

    def __post_init__(self) -> None:
        if self.can_unfreeze_paid_access:
            raise ValueError("shadow ledger entries cannot unfreeze paid access")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_shadow_entry(
    *,
    signal_id: str,
    event_id: str,
    market: str,
    selection: str,
    quote_source: str,
    book: str,
    source_ts: str,
    entry_line: float,
    entry_american_odds: int,
    entry_devig_p: float,
    best_tape_devig_p: float | None,
    decomposed: DecomposedPriceCLV,
    closing_snapshot_hash: str | None,
) -> ShadowLedgerEntry:
    if decomposed.is_confirmatory:
        raise ValueError("shadow ledger rejects confirmatory observations")
    return ShadowLedgerEntry(
        signal_id=signal_id,
        event_id=event_id,
        market=market,
        selection=selection,
        quote_source=quote_source,
        book=book,
        source_ts=source_ts,
        entry_line=float(entry_line),
        entry_american_odds=int(entry_american_odds),
        entry_devig_p=float(entry_devig_p),
        best_tape_devig_p=best_tape_devig_p,
        close_state=decomposed.close_state,
        line_component=decomposed.line_component,
        juice_component=decomposed.juice_component,
        book_execution_component=decomposed.book_execution_component,
        closing_snapshot_hash=closing_snapshot_hash,
        can_unfreeze_paid_access=False,
    )


def shadow_ledger_can_unfreeze_paid_access(_entries: Sequence[ShadowLedgerEntry]) -> bool:
    """Explicit gate invariant: shadow ledger never unfreezes paid access regardless of N."""
    return False
