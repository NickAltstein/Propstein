"""Feed-age alerts: kill the feed, kill the poller, suppress an off day."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.feed_age import ROOT, alert_message, check, load_config, note_fetch

NOW = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
ERROR = ('[fetch] HTTP 401 from /odds: b\'{"message":"Usage quota has been reached",'
         '"error_code":"OUT_OF_USAGE_CREDITS"}\'')
EMPTY = "empty/zero-event response"
CFG = load_config(override={
    "degraded_after_hours": 2,
    "critical_after_hours": 12,
    "upcoming_window_hours": 24,
    "in_progress_hours": 4,
    "schedule_observation_max_hours": 30,
    "nfl_off_weekdays": [1, 2],
    "sports": ["nba", "nfl"],
})
NBA = dict(CFG, sports=["nba"])
NFL = dict(CFG, sports=["nfl"])


def stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_kill_the_feed_degrades_then_fails_closed(tmp_path):
    """Errors and empty bodies do not refresh the clock. 2h degrades, 12h fail-closes."""
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    success_at = NOW - timedelta(minutes=30)
    tip = stamp(NOW + timedelta(hours=3))
    note_fetch("nba", ok=True, quotes=10, schedule_state="pending", starts=[tip],
               path=path, now=success_at)
    note_fetch("nba", ok=True, quotes=0, path=path, now=NOW)
    row = json.loads(path.read_text())["sports"]["nba"]
    assert row["last_success_quotes"] == 10
    assert row["last_success_at"] == stamp(success_at)
    assert row["last_error"] == EMPTY
    note_fetch("nba", ok=False, quotes=0, error=ERROR, path=path, now=NOW)

    assert check(tmp_path, None, NOW, NBA)["sports"]["nba"]["level"] == "ok"

    degraded = check(tmp_path, None, NOW + timedelta(hours=2), NBA)
    assert degraded["sports"]["nba"]["level"] == "degraded"
    assert degraded["sports"]["nba"]["fail_closed"] is False
    assert "NBA" in degraded["alerts"][0] and "hours" in degraded["alerts"][0] and ERROR in degraded["alerts"][0]

    critical = check(tmp_path, None, NOW + timedelta(hours=12), NBA)
    nba = critical["sports"]["nba"]
    assert nba["level"] == "critical" and nba["fail_closed"] is True and nba["show_edges"] is False
    assert nba["stale_since"] == stamp(success_at)
    assert "NBA" in critical["alerts"][0] and "12.5 hours" in critical["alerts"][0] and ERROR in critical["alerts"][0]


def test_zero_quote_file_does_not_reset_age(tmp_path):
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    tip = stamp(NOW + timedelta(hours=2))
    note_fetch("nba", ok=True, quotes=8, schedule_state="pending", starts=[tip],
               path=path, now=NOW - timedelta(hours=20))
    note_fetch("nba", ok=False, quotes=0, error=ERROR, path=path, now=NOW - timedelta(hours=19))
    (tmp_path / "nba_odds_2026-11-16.json").write_text(json.dumps({
        "updated_at": stamp(NOW), "_fixture": False, "total_quotes": 0}))
    report = check(tmp_path, None, NOW, NBA)
    assert report["sports"]["nba"]["level"] == "critical"
    assert report["sports"]["nba"]["age_hours"] >= 20
    assert ERROR in report["alerts"][0]


def test_kill_the_poller_still_alerts(tmp_path):
    """The checker only reads the last success. It does not run a fetch."""
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nba", ok=True, quotes=8, path=path, now=NOW - timedelta(hours=20))
    note_fetch("nba", ok=False, quotes=0, error="poller stopped", schedule_state="none",
               path=path, now=NOW - timedelta(hours=40))
    before = json.loads(path.read_text())["sports"]["nba"]["last_success_at"]
    report = check(tmp_path, None, NOW, NBA)
    after = json.loads(path.read_text())["sports"]["nba"]["last_success_at"]
    assert before == after
    assert report["sports"]["nba"]["level"] == "critical"
    assert any("NBA" in line and "poller stopped" in line for line in report["alerts"])


def test_no_slate_pending_does_not_alert(tmp_path):
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nba", ok=True, quotes=4, path=path, now=NOW - timedelta(hours=100))
    note_fetch("nba", ok=False, quotes=0, error="should not page", schedule_state="none",
               starts=[], path=path, now=NOW)
    report = check(tmp_path, None, NOW, NBA)
    assert report["sports"]["nba"]["level"] == "suppressed"
    assert report["sports"]["nba"]["fail_closed"] is False
    assert report["alerts"] == []


def test_nfl_tuesday_is_suppressed_without_a_game_in_the_window(tmp_path):
    tuesday = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=True, quotes=2, path=path, now=tuesday - timedelta(hours=30))
    report = check(tmp_path, None, tuesday, NFL)
    assert report["sports"]["nfl"]["level"] == "suppressed"
    assert report["alerts"] == []


def test_thresholds_come_from_config(tmp_path):
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nba", ok=True, quotes=3, schedule_state="pending",
               starts=[stamp(NOW + timedelta(hours=2))],
               path=path, now=NOW - timedelta(hours=1, minutes=30))
    report = check(tmp_path, None, NOW, dict(NBA, degraded_after_hours=1))
    assert report["sports"]["nba"]["level"] == "degraded"
    assert "1.5 hours" in alert_message({
        "level": "degraded", "sport": "nba", "age_hours": 1.5, "last_error": "x"})


def test_watcher_workflow_is_not_the_poller():
    text = (ROOT / ".github" / "workflows" / "feed_age_watch.yml").read_text(encoding="utf-8")
    assert "scripts/feed_age.py" in text
    assert "fetch_nba_odds.py" not in text
    assert "fetch_nfl_props_sweep.py" not in text
    assert "the-odds-api.com" not in text
    assert "FEED_AGE_UPTIME_URL" in text
    assert "ALARM_WEBHOOK_URL" in text
    assert "https://" not in text


def test_nba_daily_schedule_avoids_the_top_and_bottom_of_the_hour():
    text = (ROOT / ".github" / "workflows" / "nba_daily.yml").read_text(encoding="utf-8")
    assert "group: nba-daily-market-slate" in text
    assert "cancel-in-progress: false" in text
    crons = [line.split("'", 2)[1] for line in text.splitlines() if line.strip().startswith("- cron:")]
    assert crons == [
        "7 16 * * *", "23 17 * * *", "37 20 * * *", "53 22 * * *",
        "43 23 * * *", "17 0 * * *", "43 0 * * *", "43 1 * * *", "43 2 * * *", "13 3 * * *"
    ]
    for expr in crons:
        minute = int(expr.split()[0])
        assert minute % 30 != 0


def test_scheduled_jobs_do_not_call_the_odds_api():
    workflows = Path(ROOT / ".github" / "workflows")
    for path in workflows.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "the-odds-api.com" not in text, path.name
        assert "fetch_nba_odds.py" not in text, path.name
        assert "fetch_nfl_props_sweep.py" not in text, path.name
        assert "ODDS_API_KEY" not in text, path.name


def _nfl_viewer(tmp_path, default, slates, files):
    (tmp_path / "feeds").mkdir(parents=True, exist_ok=True)
    (tmp_path / "feeds" / "slate-index.json").write_text(json.dumps({
        "generated_at": "2026-11-17T05:10:00Z",
        "sports": {"nfl": {"default_slate": default, "slates": slates}}}))
    for name, payload in files.items():
        (tmp_path / name).write_text(json.dumps(payload))


def _between_weeks(tmp_path):
    # W10's last game is Monday 2026-11-16 -> gap opens Tue 00:00 ET (05:00 UTC).
    _nfl_viewer(tmp_path, None,
                {"2026_w10": {"games": "games_2026_w10.json", "slate": "slate_2026_w10.json",
                              "historical": True}},
                {"games_2026_w10.json": [{"gameday": "2026-11-16"}]})


def test_nfl_between_weeks_is_reported_and_suppressed_until_ingest_plus_buffer(tmp_path):
    _between_weeks(tmp_path)
    tue_morning = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
    report = check(tmp_path, {"sports": {}}, tue_morning, NFL)
    nfl = report["sports"]["nfl"]
    assert nfl["level"] == "suppressed"
    assert nfl["slate_state"]["state"] == "NO_CURRENT_SLATE"
    assert nfl["slate_state"]["since"] == "2026-11-17T05:00:00Z"
    assert nfl["slate_state"]["alarm_after"] == "2026-11-17T17:00:00Z"
    assert report["alerts"] == []


def test_nfl_between_weeks_pages_once_overdue(tmp_path):
    _between_weeks(tmp_path)
    late = datetime(2026, 11, 17, 17, 30, tzinfo=timezone.utc)
    report = check(tmp_path, {"sports": {}}, late, NFL)
    nfl = report["sports"]["nfl"]
    assert nfl["level"] == "critical" and nfl["slate_state"]["overdue"] is True
    assert "[NO_CURRENT_SLATE]" in report["alerts"][0] and "overdue" in report["alerts"][0]


def test_nfl_buffer_comes_from_config(tmp_path):
    _between_weeks(tmp_path)
    late = datetime(2026, 11, 17, 17, 30, tzinfo=timezone.utc)
    report = check(tmp_path, {"sports": {}}, late, dict(NFL, no_current_slate_buffer_hours=12))
    assert report["sports"]["nfl"]["level"] == "suppressed"


def test_nfl_live_slate_with_no_rows_pages_immediately(tmp_path):
    _nfl_viewer(tmp_path, "2026_w11",
                {"2026_w11": {"games": "games_2026_w11.json", "slate": "slate_2026_w11.json"}},
                {"games_2026_w11.json": [{"gameday": "2026-11-20"}], "slate_2026_w11.json": []})
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=True, quotes=40, path=path, now=NOW - timedelta(minutes=10))
    report = check(tmp_path, None, NOW, NFL)
    nfl = report["sports"]["nfl"]
    assert nfl["level"] == "critical" and nfl["slate_state"]["state"] == "NO_SLATE_ROWS"
    assert "[NO_SLATE_ROWS]" in report["alerts"][0]


def test_nfl_context_gated_slate_reports_reasons_without_paging(tmp_path):
    gated = {"game_id": "2026_11_A_B", "matchup": "A @ B", "context_status": "CONTEXT_MISSING",
             "position": "CONTEXT_MISSING", "excl_reason": "CONTEXT_MISSING: spread_close"}
    _nfl_viewer(tmp_path, "2026_w11",
                {"2026_w11": {"games": "games_2026_w11.json", "slate": "slate_2026_w11.json"}},
                {"games_2026_w11.json": [{"gameday": "2026-11-17"}], "slate_2026_w11.json": [gated]})
    report = check(tmp_path, {"sports": {}}, NOW, NFL)
    nfl = report["sports"]["nfl"]
    assert nfl["level"] == "suppressed" and report["alerts"] == []
    assert nfl["slate_state"]["state"] == "SLATE_BLOCKED"
    assert nfl["slate_state"]["exclusions"][0]["reason"] == "CONTEXT_MISSING: spread_close"


def test_note_fetch_records_slate_state_and_collisions(tmp_path):
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=False, quotes=0, error="NO_CURRENT_SLATE: between weeks", path=path, now=NOW,
               slate={"state": "NO_CURRENT_SLATE", "detail": "between weeks", "overdue": False},
               collisions=2)
    row = json.loads(path.read_text())["sports"]["nfl"]
    assert row["slate_state"]["state"] == "NO_CURRENT_SLATE"
    assert row["line_collisions_excluded"] == 2


def test_unparsed_markets_raise_a_parse_alarm_without_changing_the_level(tmp_path):
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=True, quotes=40, path=path, now=NOW - timedelta(minutes=5),
               schedule_state="pending", starts=[stamp(NOW + timedelta(hours=3))],
               counters={"skipped_unparsed": 2, "skipped_no_book": 5,
                         "unparsed_market_ids": ["m1", "m2"]})
    report = check(tmp_path, None, NOW, NFL)
    nfl = report["sports"]["nfl"]
    assert nfl["level"] == "ok"
    assert nfl["fetch_counters"]["skipped_no_book"] == 5
    assert len(report["alerts"]) == 1
    assert "PARSE ALARM" in report["alerts"][0] and "skipped 2" in report["alerts"][0]
    assert "m1, m2" in report["alerts"][0]


def test_no_book_alone_is_reported_not_alarmed(tmp_path):
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=True, quotes=40, path=path, now=NOW - timedelta(minutes=5),
               schedule_state="pending", starts=[stamp(NOW + timedelta(hours=3))],
               counters={"skipped_unparsed": 0, "skipped_no_book": 9})
    report = check(tmp_path, None, NOW, NFL)
    assert report["alerts"] == []
    assert report["sports"]["nfl"]["fetch_counters"]["skipped_no_book"] == 9


def test_parse_alarm_clears_on_the_next_clean_or_skipped_run(tmp_path):
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=True, quotes=40, path=path, now=NOW - timedelta(minutes=20),
               counters={"skipped_unparsed": 1, "unparsed_market_ids": ["m1"]})
    note_fetch("nfl", ok=False, quotes=0, error="between weeks", path=path,
               now=NOW - timedelta(minutes=5), counters={})
    row = json.loads(path.read_text())["sports"]["nfl"]
    assert "fetch_counters" not in row


GATED_ROW = {"game_id": "2026_11_A_B", "matchup": "A @ B", "context_status": "CONTEXT_MISSING",
             "position": "CONTEXT_MISSING", "excl_reason": "CONTEXT_MISSING: spread_close"}


def _blocked_viewer(tmp_path, generated_at="2026-11-16T11:00:00Z"):
    _nfl_viewer(tmp_path, "2026_w11",
                {"2026_w11": {"games": "games_2026_w11.json", "slate": "slate_2026_w11.json"}},
                {"games_2026_w11.json": [{"gameday": "2026-11-17"}], "slate_2026_w11.json": [GATED_ROW]})
    index = json.loads((tmp_path / "feeds" / "slate-index.json").read_text())
    index["generated_at"] = generated_at
    (tmp_path / "feeds" / "slate-index.json").write_text(json.dumps(index))


BLOCKED = {"state": "SLATE_BLOCKED", "detail": "gated", "slate_key": "2026_w11",
           "exclusions": [{"matchup": "A @ B", "reason": "CONTEXT_MISSING: spread_close"}]}


def test_state_since_holds_while_the_state_holds_and_resets_when_it_changes(tmp_path):
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    t0 = NOW - timedelta(hours=5)
    note_fetch("nfl", ok=False, quotes=0, error="b", path=path, now=t0, slate=BLOCKED)
    note_fetch("nfl", ok=False, quotes=0, error="b", path=path, now=NOW, slate=BLOCKED)
    row = json.loads(path.read_text())["sports"]["nfl"]["slate_state"]
    assert row["state_since"] == stamp(t0) and row["observed_at"] == stamp(NOW)
    note_fetch("nfl", ok=True, quotes=9, path=path, now=NOW + timedelta(minutes=15),
               slate={"state": "OK", "slate_key": "2026_w11"})
    note_fetch("nfl", ok=False, quotes=0, error="b", path=path, now=NOW + timedelta(minutes=30), slate=BLOCKED)
    row = json.loads(path.read_text())["sports"]["nfl"]["slate_state"]
    assert row["state_since"] == stamp(NOW + timedelta(minutes=30))


def test_state_since_resets_for_a_new_slate_with_the_same_state(tmp_path):
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=False, quotes=0, path=path, now=NOW - timedelta(days=7), slate=BLOCKED)
    note_fetch("nfl", ok=False, quotes=0, path=path, now=NOW, slate=dict(BLOCKED, slate_key="2026_w12"))
    assert json.loads(path.read_text())["sports"]["nfl"]["slate_state"]["state_since"] == stamp(NOW)


def test_blocked_slate_pages_only_after_the_limit(tmp_path):
    _blocked_viewer(tmp_path)
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=False, quotes=0, path=path, now=NOW - timedelta(hours=11), slate=BLOCKED)
    report = check(tmp_path, None, NOW, NFL)
    nfl = report["sports"]["nfl"]
    assert nfl["level"] == "suppressed" and report["alerts"] == []
    assert nfl["slate_state"]["blocked_hours"] == 11.0
    assert nfl["slate_state"]["blocked_since_source"] == "poller heartbeat"

    late = check(tmp_path, None, NOW + timedelta(hours=2), NFL)
    assert late["sports"]["nfl"]["level"] == "suppressed", "the feed level stays unchanged"
    assert len(late["alerts"]) == 1
    line = late["alerts"][0]
    assert "BLOCKED SLATE" in line and "'2026_w11'" in line and "13.0h" in line
    assert "A @ B: CONTEXT_MISSING: spread_close" in line


def test_blocked_slate_limit_comes_from_config(tmp_path):
    _blocked_viewer(tmp_path)
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=False, quotes=0, path=path, now=NOW - timedelta(hours=5), slate=BLOCKED)
    assert check(tmp_path, None, NOW, dict(NFL, blocked_slate_alarm_hours=4))["alerts"]


def test_blocked_slate_still_pages_when_the_poller_never_confirmed_it(tmp_path):
    # Poller down: no heartbeat slate state. slate-index generated_at is the lower bound.
    _blocked_viewer(tmp_path, generated_at=stamp(NOW - timedelta(hours=20)))
    report = check(tmp_path, {"sports": {}}, NOW, NFL)
    st = report["sports"]["nfl"]["slate_state"]
    assert st["blocked_overdue"] is True and st["blocked_since_source"].startswith("slate-index")
    assert any("BLOCKED SLATE" in a for a in report["alerts"])


def test_blocked_slate_ignores_a_heartbeat_for_a_different_slate(tmp_path):
    _blocked_viewer(tmp_path, generated_at=stamp(NOW - timedelta(hours=1)))
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=False, quotes=0, path=path, now=NOW - timedelta(days=3),
               slate=dict(BLOCKED, slate_key="2026_w10"))
    report = check(tmp_path, None, NOW, NFL)
    assert report["sports"]["nfl"]["slate_state"]["blocked_hours"] == 1.0
    assert report["alerts"] == []


def _blocked_with_kickoff(tmp_path, kickoff_utc: datetime):
    _blocked_viewer(tmp_path, generated_at=stamp(NOW - timedelta(hours=1)))
    et = kickoff_utc.astimezone(ZoneInfo("America/New_York"))
    (tmp_path / "games_2026_w11.json").write_text(json.dumps([
        {"gameday": et.strftime("%Y-%m-%d"), "gametime": et.strftime("%H:%M")}]))


def test_blocked_slate_pages_early_when_a_kickoff_is_near(tmp_path):
    # Blocked for only 1h, kickoff in 3h: inside the 4h window, so page now.
    _blocked_with_kickoff(tmp_path, NOW + timedelta(hours=3))
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=False, quotes=0, path=path, now=NOW - timedelta(hours=1), slate=BLOCKED)
    report = check(tmp_path, None, NOW, NFL)
    st = report["sports"]["nfl"]["slate_state"]
    assert st["blocked_overdue"] is True and st["blocked_reasons"] == ["kickoff"]
    assert st["blocked_next_kickoff"] == stamp(NOW + timedelta(hours=3))
    assert len(report["alerts"]) == 1 and "next kickoff" in report["alerts"][0]


def test_blocked_slate_with_a_distant_kickoff_waits_for_the_duration_limit(tmp_path):
    _blocked_with_kickoff(tmp_path, NOW + timedelta(hours=10))
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=False, quotes=0, path=path, now=NOW - timedelta(hours=1), slate=BLOCKED)
    report = check(tmp_path, None, NOW, NFL)
    assert report["alerts"] == []
    assert report["sports"]["nfl"]["slate_state"]["blocked_next_kickoff"] is None


def test_kickoff_already_past_does_not_count_as_near(tmp_path):
    _blocked_with_kickoff(tmp_path, NOW - timedelta(minutes=30))
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=False, quotes=0, path=path, now=NOW - timedelta(hours=1), slate=BLOCKED)
    assert check(tmp_path, None, NOW, NFL)["alerts"] == []


def test_kickoff_window_comes_from_config(tmp_path):
    _blocked_with_kickoff(tmp_path, NOW + timedelta(hours=6))
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=False, quotes=0, path=path, now=NOW - timedelta(hours=1), slate=BLOCKED)
    assert check(tmp_path, None, NOW, NFL)["alerts"] == []
    assert check(tmp_path, None, NOW, dict(NFL, blocked_slate_kickoff_hours=8))["alerts"]


def test_both_reasons_are_reported_together(tmp_path):
    _blocked_with_kickoff(tmp_path, NOW + timedelta(hours=2))
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=False, quotes=0, path=path, now=NOW - timedelta(hours=13), slate=BLOCKED)
    st = check(tmp_path, None, NOW, NFL)["sports"]["nfl"]["slate_state"]
    assert st["blocked_reasons"] == ["duration", "kickoff"]


def test_watcher_passes_the_webhook_to_the_feed_age_step():
    """The BLOCKED SLATE, parse and staleness alarms page from feed_age.py
    itself; without the secret on that step they would land in the job log only."""
    yml = (ROOT / ".github" / "workflows" / "feed_age_watch.yml").read_text()
    step = yml[yml.index("- name: Check feed age"):]
    step = step[:step.index("\n      - name:", 1)] if "\n      - name:" in step[1:] else step
    assert "ALARM_WEBHOOK_URL: ${{ secrets.ALARM_WEBHOOK_URL }}" in step
    assert "scripts/feed_age.py --check" in step


def _long_block(tmp_path, blocked_hours=13):
    _blocked_viewer(tmp_path, generated_at=stamp(NOW - timedelta(hours=1)))
    path = tmp_path / "feeds" / "feed_heartbeat.json"
    note_fetch("nfl", ok=False, quotes=0, path=path, now=NOW - timedelta(hours=blocked_hours), slate=BLOCKED)


def _blocked_lines(report):
    return [a for a in report["alerts"] if "BLOCKED SLATE" in a]


def test_blocked_page_fires_once_then_holds_between_reminders(tmp_path):
    _long_block(tmp_path)
    first = check(tmp_path, None, NOW, NFL, prior_status={"sports": {}})
    assert len(_blocked_lines(first)) == 1
    assert first["sports"]["nfl"]["blocked_page"]["decision"] == "first page"
    # 30 min later, same reason: held, no alert (so no webhook and no failed job).
    second = check(tmp_path, None, NOW + timedelta(minutes=30), NFL, prior_status=first)
    assert _blocked_lines(second) == []
    page = second["sports"]["nfl"]["blocked_page"]
    assert page["last_paged_at"] == stamp(NOW) and page["decision"].startswith("held")
    # Still held at 2h59m (carried forward from the held run's record).
    third = check(tmp_path, None, NOW + timedelta(hours=2, minutes=59), NFL, prior_status=second)
    assert _blocked_lines(third) == []
    # Reminder at 3h.
    fourth = check(tmp_path, None, NOW + timedelta(hours=3), NFL, prior_status=third)
    assert len(_blocked_lines(fourth)) == 1
    assert fourth["sports"]["nfl"]["blocked_page"]["decision"].startswith("reminder")


def test_blocked_page_repeats_at_once_when_the_reason_changes(tmp_path):
    _long_block(tmp_path)
    first = check(tmp_path, None, NOW, NFL, prior_status={"sports": {}})
    assert first["sports"]["nfl"]["blocked_page"]["reasons"] == ["duration"]
    # A kickoff moves inside the window 30 min later: duration -> duration+kickoff.
    soon = NOW + timedelta(minutes=30) + timedelta(hours=3)
    et = soon.astimezone(ZoneInfo("America/New_York"))
    (tmp_path / "games_2026_w11.json").write_text(json.dumps([
        {"gameday": et.strftime("%Y-%m-%d"), "gametime": et.strftime("%H:%M")}]))
    second = check(tmp_path, None, NOW + timedelta(minutes=30), NFL, prior_status=first)
    assert len(_blocked_lines(second)) == 1
    assert second["sports"]["nfl"]["blocked_page"]["decision"].startswith("reason changed")


def test_a_cleared_block_forgets_its_page_so_the_next_block_pages_fresh(tmp_path):
    _long_block(tmp_path)
    first = check(tmp_path, None, NOW, NFL, prior_status={"sports": {}})
    (tmp_path / "slate_2026_w11.json").write_text(json.dumps([
        {"player_id": "00-1", "player_display_name": "A", "game_id": "2026_11_A_B"}]))
    cleared = check(tmp_path, None, NOW + timedelta(minutes=30), NFL, prior_status=first)
    assert "blocked_page" not in cleared["sports"]["nfl"]
    (tmp_path / "slate_2026_w11.json").write_text(json.dumps([GATED_ROW]))
    again = check(tmp_path, None, NOW + timedelta(hours=1), NFL, prior_status=cleared)
    assert len(_blocked_lines(again)) == 1


def test_main_reads_the_last_status_file_to_hold_repeats(tmp_path, monkeypatch):
    from scripts import feed_age
    _long_block(tmp_path)
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(NFL))
    status = tmp_path / "feeds" / "feed_status.json"
    sent = []
    monkeypatch.setattr(feed_age, "deliver", lambda alerts, *a, **k: sent.append(list(alerts)) or [])
    args = ["--check", "--viewer", str(tmp_path), "--config", str(cfg_path), "--status-out", str(status)]
    assert feed_age.main(args + ["--now", stamp(NOW)]) == 1
    assert json.loads(status.read_text())["sports"]["nfl"]["blocked_page"]["last_paged_at"] == stamp(NOW)
    assert feed_age.main(args + ["--now", stamp(NOW + timedelta(minutes=30))]) == 0
    assert [a for a in sent[1] if "BLOCKED SLATE" in a] == []
