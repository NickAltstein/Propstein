"""Quote-source tags for ledger entry and close rows.

A tag is one of:
  odds_api:<book>  historical sportsbook quote
  kalshi
  polymarket
  unknown          source could not be read from the row

A close matches only when the tag is the same. Headline CLV is reported per
source class (odds_api, kalshi, polymarket, unknown) and is never one number
across classes.
"""

from __future__ import annotations

import re
from collections import defaultdict

UNKNOWN = "unknown"
CLASSES = ("odds_api", "kalshi", "polymarket", "unknown")

_SLUG = re.compile(r"[^a-z0-9]+")
_BOOKS = {
    "fanduel": "fanduel",
    "draftkings": "draftkings",
    "betmgm": "betmgm",
    "mgm": "betmgm",
    "caesars": "caesars",
    "pinnacle": "pinnacle",
    "bovada": "bovada",
    "betrivers": "betrivers",
    "fanatics": "fanatics",
    "bet365": "bet365",
    "espnbet": "espnbet",
    "circa": "circa",
    "betonlineag": "betonlineag",
    "lowvig": "lowvig",
    "bookmaker": "bookmaker",
}


def _slug(value: str) -> str:
    return _SLUG.sub("", value.lower())


def source_class(tag: str | None) -> str:
    text = (tag or "").strip().lower()
    if text in ("", UNKNOWN):
        return UNKNOWN
    if text == "kalshi" or text.startswith("kalshi:"):
        return "kalshi"
    if text == "polymarket" or text.startswith("polymarket:"):
        return "polymarket"
    if text.startswith("odds_api:") or text == "odds_api":
        return "odds_api"
    return UNKNOWN


def normalize_quote_source(tag: str | None) -> str:
    text = (tag or "").strip().lower()
    if not text:
        return UNKNOWN
    kind = source_class(text)
    if kind == "kalshi":
        return "kalshi"
    if kind == "polymarket":
        return "polymarket"
    if kind == "odds_api":
        book = text.split(":", 1)[1] if ":" in text else ""
        book = _BOOKS.get(_slug(book), _slug(book))
        return f"odds_api:{book}" if book else "odds_api:unknown"
    return UNKNOWN


def tag_quote_source(*, book: str | None = None, venue: str | None = None,
                     source: str | None = None, explicit: str | None = None) -> str:
    """Read a tag from the fields a row already has. Otherwise unknown."""
    if explicit:
        tagged = normalize_quote_source(explicit)
        if tagged != UNKNOWN:
            return tagged
    blob = " ".join(part for part in (book, venue, source) if part).lower()
    if "kalshi" in blob:
        return "kalshi"
    if "polymarket" in blob:
        return "polymarket"
    if "odds api" in blob or "odds_api" in blob or "the-odds-api" in blob:
        book_tag = _BOOKS.get(_slug(book or ""), _slug(book or ""))
        return f"odds_api:{book_tag}" if book_tag else "odds_api:unknown"
    book_tag = _BOOKS.get(_slug(book or ""))
    if book_tag:
        return f"odds_api:{book_tag}"
    return UNKNOWN


def sources_match(entry: str | None, close: str | None) -> bool:
    return normalize_quote_source(entry) == normalize_quote_source(close)


def headline_clv(rows: list[dict], value_key: str = "clv") -> dict:
    """One mean per source class. A cross-class mean is not returned."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        entry = row.get("entry_quote_source") or row.get("quote_source")
        close = row.get("close_quote_source") or row.get("quote_source")
        if not sources_match(entry, close):
            continue
        value = row.get(value_key)
        if value is None:
            continue
        grouped[source_class(entry)].append(float(value))
    by_source = {}
    for kind in CLASSES:
        values = grouped.get(kind) or []
        if not values:
            continue
        by_source[kind] = {
            "n": len(values),
            "mean": sum(values) / len(values),
        }
    headline = next(iter(by_source)) if len(by_source) == 1 else None
    return {
        "headline_source_class": headline,
        "mean": None if headline is None else by_source[headline]["mean"],
        "n": 0 if headline is None else by_source[headline]["n"],
        "by_source": by_source,
    }


def tag_ledger_row(row: dict, *, book_key: str = "book") -> dict:
    """Copy a row and set entry and close tags. Does not invent a book."""
    tagged = dict(row)
    entry = tag_quote_source(
        book=row.get(book_key) or row.get("entry_book"),
        venue=row.get("venue_type") or row.get("venue"),
        source=row.get("source") or row.get("entry_source"),
        explicit=row.get("entry_quote_source"),
    )
    close_book = row.get("close_book") or row.get(book_key)
    close_source = row.get("close_source") or row.get("source")
    close = tag_quote_source(
        book=close_book,
        venue=row.get("venue_type") or row.get("venue"),
        source=close_source,
        explicit=row.get("close_quote_source"),
    )
    # A close book was not stored separately. Keep the entry tag only when the
    # row has no distinct close source field to read.
    if row.get("close_quote_source"):
        close = normalize_quote_source(row.get("close_quote_source"))
    elif row.get("close_book") or row.get("close_source"):
        pass
    else:
        close = entry
    tagged["entry_quote_source"] = entry
    tagged["close_quote_source"] = close
    return tagged
