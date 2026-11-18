"""Kalshi NBA fallback: parsing, main-line choice, fail-closed writes, publishing."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts import fetch_kalshi_nba as k  # noqa: E402

DAY = "2026-11-13"
ESPN = {"events": [
    {"id": "1", "date": "2026-11-14T01:00Z", "competitions": [{"competitors": [
        {"homeAway": "away", "team": {"abbreviation": "ATL"}}, {"homeAway": "home", "team": {"abbreviation": "CHI"}}]}]},
    {"id": "2", "date": "2026-11-14T03:00Z", "competitions": [{"competitors": [
        {"homeAway": "away", "team": {"abbreviation": "POR"}}, {"homeAway": "home", "team": {"abbreviation": "SAC"}}]}]},
]}
EVENTS = [{"event_ticker": "KXNBAPTS-26NOV13ATLCHI", "sub_title": "ATL vs CHI (Nov 13)"},
          {"event_ticker": "KXNBAPTS-26NOV14BOSNYK", "sub_title": "BOS vs NYK (Nov 14)"}]


def market(player_seg, name, k_, bid, ask, no_ask, event="KXNBAPTS-26NOV13ATLCHI"):
    return {"ticker": f"{event}-{player_seg}-{k_}", "event_ticker": event, "title": f"{name}: {k_}+ points",
            "strike_type": "greater", "floor_strike": k_ - 0.5,
            "yes_bid_dollars": bid, "yes_ask_dollars": ask, "no_ask_dollars": no_ask}


MARKETS = [
    market("CHIJSMITH25", "Jalen Smith", 10, "0.80", "0.84", "0.20"),
    market("CHIJSMITH25", "Jalen Smith", 15, "0.48", "0.52", "0.52"),
    market("CHIJSMITH25", "Jalen Smith", 20, "0.15", "0.19", "0.85"),
    market("ATLTYOUNG11", "Trae Young", 25, "0.0000", "0.95", "1.00"),   # placeholder
    market("ATLDMURRAY5", "Dejounte Murray", 20, "0.30", "0.55", "0.70"),  # spread too wide
    market("BOSJTATUM0", "Jayson Tatum", 25, "0.50", "0.52", "0.50", event="KXNBAPTS-26NOV14BOSNYK"),  # other day
]


def test_schedule_maps_espn_codes_and_keeps_only_the_slate_day():
    games = k.normalize_espn_games(ESPN, DAY)
    assert [g["game_id"] for g in games] == ["nba-20261113-ATL-CHI", "nba-20261113-POR-SAC"]
    assert games[0]["gametime_et"] == "20:00" and games[0]["_fixture"] is False
    assert k.normalize_espn_games(ESPN, "2026-11-14") == []
    with pytest.raises(ValueError, match="unmapped team"):
        k.normalize_espn_games({"events": [{**ESPN["events"][0], "competitions": [{"competitors": [
            {"homeAway": "away", "team": {"abbreviation": "XXX"}},
            {"homeAway": "home", "team": {"abbreviation": "CHI"}}]}]}]}, DAY)


def test_one_two_sided_main_line_per_player_and_nothing_invented():
    games = k.normalize_espn_games(ESPN, DAY)
    quotes = k.build_quotes(DAY, games, EVENTS, MARKETS, "2026-11-13T16:00:00Z")
    assert [(q["player_name"], q["line"]) for q in quotes] == [("Jalen Smith", 14.5)]
    q = quotes[0]
    assert (q["team"], q["opponent"], q["game_id"]) == ("CHI", "ATL", "nba-20261113-ATL-CHI")
    assert (q["over_odds"], q["under_odds"]) == (-108, -108)
    assert q["book"] == "Kalshi" and q["verified"] is False and q["_fixture"] is False
    assert q["provider_quote_id"] == "KXNBAPTS-26NOV13ATLCHI-CHIJSMITH25-15"
    assert q["quoted_at"] == q["observed_at"] == "2026-11-13T16:00:00Z"
    assert q["pre_start"] is True
    assert q["scheduled_start"] == "2026-11-14T01:00:00Z"
    assert q["offset_to_scheduled_start_sec"] == -32400.0


def test_quote_attaches_pre_start_and_offset():
    games = k.normalize_espn_games(ESPN, DAY)
    # 1. Quoted prior to tipoff -> pre_start is True, offset < 0
    q_pre = k.build_quotes(DAY, games, EVENTS, MARKETS, "2026-11-14T00:45:00Z")[0]
    assert q_pre["pre_start"] is True
    assert q_pre["offset_to_scheduled_start_sec"] == -900.0
    assert q_pre["scheduled_start"] == "2026-11-14T01:00:00Z"

    # 2. Quoted after tipoff -> pre_start is False, offset > 0
    q_post = k.build_quotes(DAY, games, EVENTS, MARKETS, "2026-11-14T01:15:00Z")[0]
    assert q_post["pre_start"] is False
    assert q_post["offset_to_scheduled_start_sec"] == 900.0

    # 3. Missing tipoff -> pre_start is "unknown", offset is None
    from src.scheduler.capture_scheduler import compute_pre_start_attribute
    ps, off = compute_pre_start_attribute("2026-11-14T00:45:00Z", None)
    assert ps == "unknown" and off is None


def test_post_tip_preserves_prior_pre_start_quote(tmp_path):
    games = k.normalize_espn_games(ESPN, DAY)
    pre_tip_time = "2026-11-14T00:45:00Z"  # 15m prior to 01:00Z tip
    post_tip_time = "2026-11-14T01:30:00Z" # 30m after 01:00Z tip

    # Step 1: Pre-tip capture writes Jalen Smith line 14.5
    quotes_pre = k.build_quotes(DAY, games, EVENTS, MARKETS, pre_tip_time)
    assert len(quotes_pre) == 1 and quotes_pre[0]["line"] == 14.5
    assert quotes_pre[0]["pre_start"] is True
    # Point viewer dir to tmp_path by saving existing odds file
    odds_file = tmp_path / f"nba_odds_{DAY}.json"
    odds_file.write_text(json.dumps({
        "schema_version": "nba.v1",
        "feed_status": "live-unverified",
        "slate": DAY,
        "quotes": quotes_pre,
    }))

    # Step 2: In-game markets change line to 9.5
    ingame_markets = [
        market("CHIJSMITH25", "Jalen Smith", 10, "0.48", "0.52", "0.52"),  # floor_strike 9.5
    ]
    quotes_ingame = k.build_quotes(DAY, games, EVENTS, ingame_markets, post_tip_time)
    assert quotes_ingame[0]["line"] == 9.5
    assert quotes_ingame[0]["pre_start"] is False

    # Step 3: merge_quotes_with_guard must preserve pre-tip quote (14.5) rather than overwriting with 9.5
    merged = k.merge_quotes_with_guard(DAY, games, quotes_ingame, post_tip_time, viewer_dir=tmp_path)
    assert len(merged) == 1
    assert merged[0]["line"] == 14.5
    assert merged[0]["pre_start"] is True
    assert merged[0]["quoted_at"] == pre_tip_time

    # Step 4: If in-game markets settled and returned zero markets, pre-tip quote is still preserved
    merged_empty = k.merge_quotes_with_guard(DAY, games, [], post_tip_time, viewer_dir=tmp_path)
    assert len(merged_empty) == 1
    assert merged_empty[0]["line"] == 14.5
    assert merged_empty[0]["pre_start"] is True


def test_unknown_time_update_cannot_replace_prior_pre_start_quote(tmp_path):
    games = k.normalize_espn_games(ESPN, DAY)
    pre = k.build_quotes(DAY, games, EVENTS, MARKETS, "2026-11-14T00:45:00Z")[0]
    (tmp_path / f"nba_odds_{DAY}.json").write_text(json.dumps({"quotes": [pre]}))
    unknown = {**pre, "line": 9.5, "quoted_at": None, "pre_start": True}
    merged = k.merge_quotes_with_guard(
        DAY, games, [unknown], "2026-11-14T00:50:00Z", viewer_dir=tmp_path)
    assert len(merged) == 1
    assert merged[0]["line"] == 14.5
    assert merged[0]["quoted_at"] == "2026-11-14T00:45:00Z"
    assert merged[0]["pre_start"] is True


@pytest.mark.parametrize("legacy_quote_time", [None, "not-a-time"])
def test_legacy_capture_is_retained_as_research_after_tip(tmp_path, legacy_quote_time):
    games = k.normalize_espn_games(ESPN, DAY)
    pre_tip_time = "2026-11-14T00:45:00Z"
    post_tip_time = "2026-11-14T01:30:00Z"
    legacy = k.build_quotes(DAY, games, EVENTS, MARKETS, pre_tip_time)[0]
    legacy["quoted_at"] = legacy_quote_time
    legacy.pop("scheduled_start")
    legacy["pre_start"] = None
    (tmp_path / f"nba_odds_{DAY}.json").write_text(json.dumps({"quotes": [legacy]}))

    changed = k.build_quotes(DAY, games, EVENTS, [
        market("CHIJSMITH25", "Jalen Smith", 10, "0.48", "0.52", "0.52"),
    ], post_tip_time)
    changed.append({**changed[0], "player_id": "odds-nba-new-player", "quote_id": "post-tip-new-player"})
    merged = k.merge_quotes_with_guard(
        DAY, games, changed, post_tip_time, viewer_dir=tmp_path)

    assert len(merged) == 1
    assert merged[0]["line"] == 14.5
    assert merged[0]["observed_at"] == pre_tip_time
    assert merged[0]["quoted_at"] == legacy_quote_time
    assert merged[0]["scheduled_start"] == games[0]["tipoff_utc"]
    assert merged[0]["pre_start"] == "unknown"
    assert merged[0]["offset_to_scheduled_start_sec"] is None


def test_slate_fixture_produces_capture_plan_with_every_game_covered_before_start():
    from src.scheduler.capture_scheduler import generate_capture_plan, parse_utc_dt

    slate_games = [
        {"game_id": "nba-g1", "tipoff_utc": "2026-11-14T00:00:00Z"},  # 19:00 ET
        {"game_id": "nba-g2", "tipoff_utc": "2026-11-14T00:30:00Z"},  # 19:30 ET
        {"game_id": "nba-g3", "tipoff_utc": "2026-11-14T01:00:00Z"},  # 20:00 ET
        {"game_id": "nba-g4", "tipoff_utc": "2026-11-14T02:00:00Z"},  # 21:00 ET
        {"game_id": "nba-g5", "tipoff_utc": "2026-11-14T03:30:00Z"},  # 22:30 ET
    ]

    plan = generate_capture_plan(slate_games, lead_minutes=15)
    assert len(plan) == 5

    covered_ids = set()
    for slot in plan:
        cap_dt = parse_utc_dt(slot["planned_capture_utc"])
        tgt_dt = parse_utc_dt(slot["target_start_utc"])
        assert cap_dt < tgt_dt
        assert (tgt_dt - cap_dt).total_seconds() == 15 * 60
        covered_ids.update(slot["games_covered"])

    assert covered_ids == {g["game_id"] for g in slate_games}


def test_run_writes_nothing_without_quotes_and_publishes_through_the_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(k, "VIEWER", tmp_path)
    responses = {k.ESPN: ESPN}

    def fake_get(url, params=None):
        if url in responses:
            return responses[url]
        kind = url.rsplit("/", 1)[1]
        return {kind: EVENTS if kind == "events" else markets[0]}

    markets = [[MARKETS[3]]]  # placeholder only
    monkeypatch.setattr(k, "get_json", fake_get)
    assert k.run(DAY, 0.10) == 3
    assert not (tmp_path / f"nba_odds_{DAY}.json").exists()
    heartbeat = json.loads((tmp_path / "feeds" / "feed_heartbeat.json").read_text())
    assert heartbeat["sports"]["nba"]["last_success_at"] is None
    assert "empty/zero-event" in heartbeat["sports"]["nba"]["last_error"]

    markets[0] = MARKETS
    assert k.run(DAY, 0.10) == 0
    odds = json.loads((tmp_path / f"nba_odds_{DAY}.json").read_text())
    games = json.loads((tmp_path / f"nba_games_{DAY}.json").read_text())
    assert odds["source"] == "kalshi" and odds["total_quotes"] == 1 and odds["slate"] == DAY
    assert odds["quotes"][0]["quoted_at"] == odds["quotes"][0]["observed_at"]
    assert len(games) == 2 and (tmp_path / f"nba_odds_{DAY}.manifest.json").exists()

    monkeypatch.setattr(k, "get_json", lambda url, params=None: (_ for _ in ()).throw(OSError("down")))
    assert k.run(DAY, 0.10) == 5


def test_publisher_accepts_one_reference_book_and_refuses_mixed_or_unknown(tmp_path, monkeypatch):
    import publish_nba_market_slate as pub
    monkeypatch.setattr(pub, "VIEWER", tmp_path)
    monkeypatch.setattr(pub, "write_manifest", lambda now: None)
    games = k.normalize_espn_games(ESPN, DAY)
    (tmp_path / f"nba_games_{DAY}.json").write_text(json.dumps(games))
    quote = k.build_quotes(DAY, games, EVENTS, MARKETS, "2026-11-13T16:00:00Z")[0]

    def write(quotes):
        (tmp_path / f"nba_odds_{DAY}.json").write_text(json.dumps({
            "slate": DAY, "_fixture": False, "feed_status": "live-unverified",
            "updated_at": "2026-11-13T16:00:00Z", "total_quotes": len(quotes), "quotes": quotes}))

    write([quote])
    pub.publish(DAY)
    assert json.loads((tmp_path / f"nba_slate_{DAY}.json").read_text()) == []
    write([quote, {**quote, "quote_id": "x", "book": "FanDuel"}])
    with pytest.raises(ValueError, match="one reference book"):
        pub.publish(DAY)
    write([{**quote, "book": "Bovada"}])
    with pytest.raises(ValueError, match="unsupported book"):
        pub.publish(DAY)
