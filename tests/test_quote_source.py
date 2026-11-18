"""Quote source tags: close identity and unpooled headline CLV."""
from src.ledger.close_match import SOURCE_MISMATCH, CloseQuote, match_close
from src.ledger.quote_source import headline_clv, tag_ledger_row, tag_quote_source


def _quote(source: str, line: float = 4.5) -> CloseQuote:
    return CloseQuote("evt", "player_receptions", "over", line, "nfl_player_prop_v1", -110, -110, source,
                      "2026-10-20T02:00:00Z", "2026-10-20T20:00:00Z", True)


def test_close_requires_the_same_quote_source():
    same = match_close(_quote("kalshi"), _quote("kalshi"))
    assert same.matched is True
    moved = match_close(_quote("kalshi"), _quote("polymarket"))
    assert moved.matched is False
    assert moved.reason_code == SOURCE_MISMATCH
    books = match_close(_quote("odds_api:fanduel"), _quote("odds_api:draftkings"))
    assert books.reason_code == SOURCE_MISMATCH


def test_known_books_backfill_and_the_rest_stay_unknown():
    assert tag_quote_source(book="Bovada") == "odds_api:bovada"
    assert tag_quote_source(book="FanDuel") == "odds_api:fanduel"
    assert tag_quote_source(book="Kalshi") == "kalshi"
    assert tag_quote_source(venue="polymarket") == "polymarket"
    assert tag_quote_source(book="Real Book") == "unknown"
    row = tag_ledger_row({"book": "DraftKings", "clv": 0.1})
    assert row["entry_quote_source"] == "odds_api:draftkings"
    assert row["close_quote_source"] == "odds_api:draftkings"


def test_headline_clv_is_not_pooled_across_source_classes():
    rows = [
        {"entry_quote_source": "kalshi", "close_quote_source": "kalshi", "clv": 2.0},
        {"entry_quote_source": "polymarket", "close_quote_source": "polymarket", "clv": -2.0},
        {"entry_quote_source": "kalshi", "close_quote_source": "odds_api:fanduel", "clv": 9.0},
    ]
    report = headline_clv(rows)
    assert report["headline_source_class"] is None
    assert report["mean"] is None
    assert report["by_source"]["kalshi"]["n"] == 1
    assert report["by_source"]["kalshi"]["mean"] == 2.0
    assert report["by_source"]["polymarket"]["mean"] == -2.0
    assert "odds_api" not in report["by_source"]
