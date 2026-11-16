"""Feed-age alerts: kill the feed, kill the poller, suppress an off day."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    assert crons == ["7 16 * * *", "23 17 * * *", "37 20 * * *", "53 22 * * *"]
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
