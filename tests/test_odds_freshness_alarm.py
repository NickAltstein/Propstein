import hashlib
import json
from datetime import datetime, timezone

import pytest

from scripts.check_odds_freshness import check


NOW = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)


def sample_viewer(tmp_path, updated_at="2026-10-31T12:00:00Z", game_day="2026-11-01",
                  feed_status="LIVE_POLYMARKET_CLOB_UNVERIFIED", receipt_matched=0):
    viewer = tmp_path / "viewer"
    (viewer / "feeds").mkdir(parents=True)
    (viewer / "feeds/slate-index.json").write_text(json.dumps({
        "sports": {"nfl": {"default_slate": "2026_w8", "slates": {
            "2026_w8": {"games": "games_2026_w8.json", "slate": "slate_2026_w8.json"}}}}
    }))
    (viewer / "games_2026_w8.json").write_text(json.dumps([{"gameday": game_day}]))
    (viewer / "slate_2026_w8.json").write_text(json.dumps([
        {"player_id": "00-1", "player_display_name": "A Player", "game_id": "2026_08_A_B"}]))
    raw = json.dumps({"updated_at": updated_at, "feed_status": feed_status,
                      "polymarket_quotes_count": 1,
                      "receipt_matched_quotes_count": receipt_matched}).encode()
    (viewer / "live_odds_feed.json").write_bytes(raw)
    (viewer / "live_odds_feed.manifest.json").write_text(json.dumps({
        "status": "PASS_ATOMIC_PUBLISHED", "sha256": hashlib.sha256(raw).hexdigest()}))
    return viewer


def test_fresh_active_feed_passes_without_claiming_verification(tmp_path):
    result = check(sample_viewer(tmp_path), now=NOW)
    assert "manifest hash matches" in result
    assert "unverified" in result


def test_fresh_receipted_feed_clears_the_publication_gate(tmp_path):
    """Review finding #1 on PR #84: the freshness check runs in the feed
    workflow BEFORE the publish step, so accepting only UNVERIFIED blocked
    publication of the very receipt-matched feeds the receipt stack exists to
    produce. A fresh, hash-matched receipted feed must pass — while the result
    message still never claims VERIFIED (receipts are evidence, not provider
    verification)."""
    result = check(sample_viewer(tmp_path, feed_status="LIVE_POLYMARKET_CLOB_RECEIPTED",
                                 receipt_matched=3), now=NOW)
    assert "manifest hash matches" in result
    assert "3 quotes receipt-matched" in result
    assert "not VERIFIED" in result


def test_feed_status_outside_the_contract_vocabulary_fails_closed(tmp_path):
    """Opening the gate for RECEIPTED must not open it for everything: the
    allowed statuses mirror the frozen PolymarketMergedFeed vocabulary, and a
    status outside it (e.g. a VERIFIED claim this feed cannot support) still
    fails loudly."""
    viewer = sample_viewer(tmp_path, feed_status="LIVE_POLYMARKET_CLOB_VERIFIED")
    with pytest.raises(ValueError, match="not a live Polymarket CLOB evidence level"):
        check(viewer, now=NOW)


def test_stale_active_feed_fails_loudly(tmp_path):
    viewer = sample_viewer(tmp_path, updated_at="2026-10-31T07:00:00Z")
    with pytest.raises(ValueError, match="8.00 hours old"):
        check(viewer, now=NOW)


def test_manifest_mismatch_fails_even_when_timestamp_is_fresh(tmp_path):
    viewer = sample_viewer(tmp_path)
    (viewer / "live_odds_feed.json").write_text('{"updated_at":"2026-10-31T14:00:00Z"}')
    with pytest.raises(ValueError, match="provenance manifest"):
        check(viewer, now=NOW)


def test_no_upcoming_games_skips_alarm(tmp_path):
    viewer = sample_viewer(tmp_path, updated_at="2026-01-01T12:00:00Z", game_day="2026-10-01")
    assert "skipped" in check(viewer, now=NOW)


def between_weeks_viewer(tmp_path, last_gameday="2026-10-26"):
    """A viewer in the weekly gap: last week is historical, default is null."""
    viewer = sample_viewer(tmp_path)
    (viewer / "games_2026_w7.json").write_text(json.dumps([{"gameday": last_gameday}]))
    (viewer / "feeds/slate-index.json").write_text(json.dumps({
        "generated_at": "2026-10-27T05:10:00Z",
        "sports": {"nfl": {"default_slate": None, "slates": {
            "2026_w7": {"games": "games_2026_w7.json", "slate": "slate_2026_w7.json",
                        "historical": True}}}}
    }))
    return viewer


def test_between_weeks_is_reported_not_alarmed_before_the_next_ingest(tmp_path):
    # MNF 2026-10-26 -> gap opens Tue 00:00 ET (04:00 UTC); next ingest run
    # 11:00 UTC + 6h buffer -> alarm only after 17:00 UTC.
    viewer = between_weeks_viewer(tmp_path)
    result = check(viewer, now=datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc))
    assert result.startswith("NO_CURRENT_SLATE:")
    assert "2026-10-27T17:00:00Z" in result
    assert "alarm skipped" in result


def test_between_weeks_alarms_once_it_outlasts_ingest_plus_buffer(tmp_path):
    viewer = between_weeks_viewer(tmp_path)
    with pytest.raises(ValueError, match="NO_CURRENT_SLATE: .*overdue"):
        check(viewer, now=datetime(2026, 10, 27, 17, 1, tzinfo=timezone.utc))


def test_default_slate_not_in_slates_is_no_slate_rows(tmp_path):
    viewer = sample_viewer(tmp_path)
    (viewer / "feeds/slate-index.json").write_text(json.dumps({
        "sports": {"nfl": {"default_slate": "2026_w12", "slates": {}}}
    }))
    with pytest.raises(ValueError, match="NO_SLATE_ROWS: default slate '2026_w12' is not listed"):
        check(viewer, now=NOW)


def test_live_slate_with_zero_rows_fails_immediately(tmp_path):
    viewer = sample_viewer(tmp_path)
    (viewer / "slate_2026_w8.json").write_text("[]")
    with pytest.raises(ValueError, match="NO_SLATE_ROWS: slate '2026_w8' has 0 priced rows"):
        check(viewer, now=NOW)


def test_fully_gated_slate_reports_exclusion_reasons(tmp_path):
    viewer = sample_viewer(tmp_path)
    (viewer / "slate_2026_w8.json").write_text(json.dumps([
        {"game_id": "2026_08_A_B", "matchup": "A @ B", "context_status": "CONTEXT_MISSING",
         "position": "CONTEXT_MISSING", "excl_reason": "CONTEXT_MISSING: spread_close"}]))
    result = check(viewer, now=NOW)
    assert result.startswith("SLATE_BLOCKED:")
    assert "A @ B: CONTEXT_MISSING: spread_close" in result
