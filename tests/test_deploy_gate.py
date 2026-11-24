"""Offline tests for scripts/deploy_gate.py and scripts/check_live_feed.py."""
import io
import json
import urllib.error
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import check_live_feed as live
from scripts import deploy_gate as gate

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 24, 20, 0, tzinfo=timezone.utc)
HOUR = timedelta(minutes=60)
HOOK = "https://api.vercel.com/v1/integrations/deploy/prj_x/SECRETHOOK"


# ── classification ──────────────────────────────────────────────────────

@pytest.mark.parametrize("path,kind", [
    ("viewer/app.js", "code"),
    ("viewer/style.css", "code"),
    ("viewer/index.html", "code"),
    ("vercel.json", "code"),
    ("package.json", "code"),
    ("viewer/manifest.json", "code"),
    ("viewer/live_odds_feed.json", "data"),
    ("viewer/feeds/slate-index.json", "data"),
    ("viewer/api/lines/2026_w12/x.json", "data"),
    ("viewer/slate_projections.csv", "data"),
    ("viewer/news_watcher_state.json", "none"),
    ("data/polymarket_receipt_log.json", "none"),
    ("scripts/feed_age.py", "none"),
    ("tests/test_feed_age.py", "none"),
    (".github/workflows/odds_freshness.yml", "none"),
])
def test_classify_path(path, kind):
    assert gate.classify_path(path) == kind


def test_any_code_change_wins_over_data():
    assert gate.classify(["viewer/live_odds_feed.json", "viewer/app.js"]) == "code"
    assert gate.classify(["viewer/live_odds_feed.json", "data/x.json"]) == "data"
    assert gate.classify(["data/x.json", "scripts/a.py"]) == "none"
    assert gate.classify([]) == "none"


def test_served_paths_match_the_ignored_build_step():
    """The gate and scripts/vercel_ignore.sh must agree on what is served."""
    script = (ROOT / "scripts/vercel_ignore.sh").read_text()
    for prefix in gate.SERVED_PREFIXES:
        assert f" {prefix}" in script or f"{prefix} " in script, prefix
    for name in gate.NOT_SERVED:
        assert name in script, name


# ── decision ────────────────────────────────────────────────────────────

def _decide(kind, last_age=None, in_flight=False, force=False, sweep=False):
    last = None if last_age is None else NOW - last_age
    return gate.decide(kind, last_ready_at=last, in_flight=in_flight, now=NOW,
                       min_data_interval=HOUR, force=force, sweep=sweep)


def test_code_deploys_immediately_even_right_after_a_deploy():
    assert _decide("code", last_age=timedelta(minutes=2), in_flight=True)[0] is True


def test_data_only_is_throttled_to_the_interval():
    assert _decide("data", last_age=timedelta(minutes=59))[0] is False
    assert _decide("data", last_age=timedelta(minutes=60))[0] is True
    assert _decide("data", last_age=None)[0] is True


def test_data_only_waits_for_an_in_flight_deployment():
    deploy, reason = _decide("data", last_age=timedelta(hours=3), in_flight=True)
    assert deploy is False and "in flight" in reason


def test_hourly_sweep_deploys_data_the_throttle_left_behind():
    # Last data merge of the night lands 5 min after a deploy; nothing follows.
    assert _decide("data", last_age=timedelta(minutes=5))[0] is False
    deploy, reason = _decide("data", last_age=timedelta(minutes=5), sweep=True)
    assert deploy is True and "hourly sweep" in reason
    # The sweep still never stacks on an in-flight deploy or deploys nothing.
    assert _decide("data", last_age=timedelta(minutes=5), in_flight=True, sweep=True)[0] is False
    assert _decide("none", last_age=timedelta(hours=5), sweep=True)[0] is False


def test_nothing_served_never_deploys_unless_forced():
    assert _decide("none", last_age=timedelta(days=2))[0] is False
    assert _decide("none", last_age=timedelta(minutes=1), force=True)[0] is True


@pytest.mark.parametrize("rule,expected", [
    (True, True), (False, False),
    ({"**": False, "main": True}, True),
    ({"**": False}, False),
    ({"*": False, "main": False}, False),
])
def test_auto_deploys_enabled_reads_vercel_json(tmp_path, rule, expected):
    p = tmp_path / "vercel.json"
    p.write_text(json.dumps({"git": {"deploymentEnabled": rule}}))
    assert gate.auto_deploys_enabled(str(p)) is expected


def test_repo_vercel_json_still_deploys_main_automatically():
    # Automatic deploys are turned off in a follow-up, after the hook test.
    assert gate.auto_deploys_enabled(str(ROOT / "vercel.json")) is True


# ── hook, verification, alarm ───────────────────────────────────────────

class _Resp(io.BytesIO):
    def __init__(self, body=b"{}", status=201):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code):
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
    return opener


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 500])
def test_hook_failures_never_include_the_hook_url(code):
    with pytest.raises(gate.GateError) as exc:
        gate.call_hook(HOOK, opener=_http_error(code))
    assert f"HTTP {code}" in str(exc.value)
    assert "SECRETHOOK" not in str(exc.value) and "vercel.com" not in str(exc.value)
    if code == 429:
        assert "rate limited" in str(exc.value)


def test_unreachable_hook_never_includes_the_url():
    def opener(req, timeout=None):
        raise urllib.error.URLError(f"cannot reach {req.full_url}")
    with pytest.raises(gate.GateError) as exc:
        gate.call_hook(HOOK, opener=opener)
    assert "SECRETHOOK" not in str(exc.value)


def test_wait_for_new_deployment_finds_one_created_after_the_call():
    after = NOW
    polls = iter([[{"createdAt": int((NOW - HOUR).timestamp() * 1000)}],
                  [{"createdAt": int((NOW + timedelta(seconds=8)).timestamp() * 1000), "state": "QUEUED"}]])
    clock = iter([0, 0, 1, 2])
    dep = gate.wait_for_new_deployment(lambda: next(polls), after=after, timeout_s=60, interval_s=0,
                                       sleep=lambda s: None, clock=lambda: next(clock))
    assert dep["state"] == "QUEUED"


def test_wait_for_new_deployment_times_out():
    old = [{"createdAt": int((NOW - HOUR).timestamp() * 1000)}]
    t = iter(range(100))
    assert gate.wait_for_new_deployment(lambda: old, after=NOW, timeout_s=3, interval_s=0,
                                        sleep=lambda s: None, clock=lambda: next(t)) is None


# ── run() end to end, all I/O stubbed ───────────────────────────────────

def _env(**extra):
    env = {"VERCEL_API_TOKEN": "tok", "VERCEL_PROJECT_ID": "prj", "VERCEL_TEAM_ID": "team",
           "VERCEL_DEPLOY_HOOK_URL": HOOK, "ALARM_WEBHOOK_URL": "https://hooks.example/x"}
    env.update(extra)
    return env


def _args(tmp_path, auto=False, **kw):
    vj = tmp_path / "vercel.json"
    vj.write_text(json.dumps({"git": {"deploymentEnabled": auto}}))
    base = dict(head="HEAD", min_data_interval=60.0, verify_timeout=1.0, force=False,
                sweep=False, dry_run=False, vercel_json=str(vj))
    base.update(kw)
    return Namespace(**base)


@pytest.fixture
def stubs(monkeypatch):
    state = {"hook_calls": 0, "alarms": [], "paths": ["viewer/app.js"],
             "last": {"meta": {"githubCommitSha": "abc123"}, "ready": int((NOW - HOUR * 2).timestamp() * 1000),
                      "state": "READY"},
             "new_dep": True}

    def fake_list(token, project, team, *, state=None, limit=10, opener=None):
        if state == "READY":
            return [stubs_state["last"]]
        if stubs_state["hook_calls"] and stubs_state["new_dep"]:
            return [{"createdAt": int(datetime.now(timezone.utc).timestamp() * 1000) + 1000,
                     "state": "QUEUED", "meta": {"githubCommitSha": "def456"}}]
        return [stubs_state["last"]]

    stubs_state = state
    monkeypatch.setattr(gate, "list_production", fake_list)
    monkeypatch.setattr(gate, "changed_paths", lambda base, head="HEAD": state["paths"])
    monkeypatch.setattr(gate, "call_hook", lambda url: state.__setitem__("hook_calls", state["hook_calls"] + 1))
    monkeypatch.setattr(gate, "send_alarm", lambda msg, url: state["alarms"].append(msg))
    monkeypatch.setattr(gate.time, "sleep", lambda s: None)
    return state


def test_code_change_calls_the_hook_and_confirms_the_deployment(tmp_path, stubs):
    assert gate.run(_args(tmp_path), env=_env()) == 0
    assert stubs["hook_calls"] == 1 and stubs["alarms"] == []


def test_report_only_while_automatic_deploys_are_on(tmp_path, stubs, capsys):
    assert gate.run(_args(tmp_path, auto={"**": False, "main": True}), env=_env()) == 0
    assert stubs["hook_calls"] == 0
    assert "report only" in capsys.readouterr().out


def test_force_tests_the_hook_even_while_automatic_deploys_are_on(tmp_path, stubs):
    stubs["paths"] = []
    assert gate.run(_args(tmp_path, auto=True, force=True), env=_env()) == 0
    assert stubs["hook_calls"] == 1


def test_hook_accepted_but_no_deployment_pages(tmp_path, stubs):
    stubs["new_dep"] = False
    assert gate.run(_args(tmp_path), env=_env()) == 1
    assert len(stubs["alarms"]) == 1
    assert "no production deployment appeared" in stubs["alarms"][0]
    assert "SECRETHOOK" not in stubs["alarms"][0]


def test_hook_rejection_pages(tmp_path, stubs, monkeypatch):
    def rejected(url):
        raise gate.GateError("deploy hook returned HTTP 429 (rate limited)")
    monkeypatch.setattr(gate, "call_hook", rejected)
    assert gate.run(_args(tmp_path), env=_env()) == 1
    assert "HTTP 429 (rate limited)" in stubs["alarms"][0]


def test_missing_hook_secret_pages_only_when_a_deploy_was_needed(tmp_path, stubs):
    stubs["paths"] = ["scripts/a.py"]
    assert gate.run(_args(tmp_path), env=_env(VERCEL_DEPLOY_HOOK_URL="")) == 0
    stubs["paths"] = ["viewer/app.js"]
    assert gate.run(_args(tmp_path), env=_env(VERCEL_DEPLOY_HOOK_URL="")) == 1
    assert "VERCEL_DEPLOY_HOOK_URL is not set" in stubs["alarms"][0]


def test_data_only_inside_the_hour_skips_without_calling_the_hook(tmp_path, stubs):
    stubs["paths"] = ["viewer/live_odds_feed.json"]
    stubs["last"]["ready"] = int((datetime.now(timezone.utc) - timedelta(minutes=20)).timestamp() * 1000)
    assert gate.run(_args(tmp_path), env=_env()) == 0
    assert stubs["hook_calls"] == 0


def test_unknown_last_deploy_commit_deploys(tmp_path, stubs, monkeypatch):
    monkeypatch.setattr(gate, "changed_paths", lambda base, head="HEAD": None)
    assert gate.run(_args(tmp_path), env=_env()) == 0
    assert stubs["hook_calls"] == 1


def test_vercel_api_failure_pages(tmp_path, stubs, monkeypatch):
    def down(*a, **k):
        raise gate.GateError("Vercel deployments API returned HTTP 403")
    monkeypatch.setattr(gate, "list_production", down)
    assert gate.run(_args(tmp_path), env=_env()) == 1
    assert "HTTP 403" in stubs["alarms"][0]


GATE_YML = ROOT / ".github/workflows/deploy_gate.yml"


def _code(yml: str) -> str:
    return "\n".join(l for l in yml.splitlines() if not l.lstrip().startswith("#"))


def test_workflow_never_traces_and_reads_the_hook_from_secrets():
    yml = _code(GATE_YML.read_text())
    assert "set -x" not in yml
    assert "VERCEL_DEPLOY_HOOK_URL: ${{ secrets.VERCEL_DEPLOY_HOOK_URL }}" in yml
    assert "branches: [main]" in yml
    assert "contents: read" in yml


def test_gate_runs_on_push_and_an_hourly_sweep():
    yml = _code(GATE_YML.read_text())
    assert "cron: '41 * * * *'" in yml
    assert '"$EVENT" = "schedule"' in yml and "--sweep" in yml


def test_gate_job_holds_its_secrets_in_a_main_only_environment():
    yml = _code(GATE_YML.read_text())
    assert "environment: deploy-gate" in yml


def test_gate_never_runs_for_pull_requests():
    yml = _code(GATE_YML.read_text())
    assert "pull_request" not in yml, "fork PRs must never run with the deploy secrets"


def test_every_action_in_the_gate_is_pinned_to_a_full_sha():
    import re
    uses = re.findall(r"uses:\s*(\S+)", _code(GATE_YML.read_text()))
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


def test_no_other_workflow_references_the_vercel_api_token():
    for wf in (ROOT / ".github/workflows").glob("*.y*ml"):
        if wf.name != "deploy_gate.yml":
            assert "VERCEL_API_TOKEN" not in wf.read_text(), wf.name


# ── live-site freshness ─────────────────────────────────────────────────

def test_live_feed_older_than_the_limit_alarms_and_names_the_deploy_lag():
    alarm, msg = live.evaluate({"updated_at": "2026-11-24T12:00:00Z"},
                               {"updated_at": "2026-11-24T19:45:00Z"}, NOW, 6)
    assert alarm is True
    assert "LIVE FEED STALE" in msg and "8.0h old" in msg
    assert "repo feed is 7.8h newer" in msg


def test_fresh_live_feed_passes():
    alarm, msg = live.evaluate({"updated_at": "2026-11-24T19:30:00Z"},
                               {"updated_at": "2026-11-24T19:30:00Z"}, NOW, 6)
    assert alarm is False and "deploy path" not in msg


@pytest.mark.parametrize("state,checked", [
    ("NO_CURRENT_SLATE", False), ("SLATE_BLOCKED", False),
    ("OK", True), ("NO_SLATE_ROWS", True), ("UNKNOWN", True),
])
def test_live_check_activity_comes_from_slate_states_only(monkeypatch, state, checked):
    monkeypatch.setattr(live.slate_state, "classify", lambda viewer, now=None: {
        "state": state, "detail": "d"})
    active, reason = live.slate_active(Path("."), NOW)
    assert active is checked and reason.startswith(state)


def test_live_check_skips_between_weeks(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(live, "slate_active", lambda viewer, now: (False, "NO_CURRENT_SLATE: between weeks"))
    monkeypatch.setattr(live, "fetch_live", lambda url: pytest.fail("must not fetch"))
    assert live.main(["--viewer", str(tmp_path)]) == 0
    assert "NO_CURRENT_SLATE" in capsys.readouterr().out


def test_unreachable_live_site_alarms(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(live, "slate_active", lambda viewer, now: (True, "active"))

    def down(url):
        raise urllib.error.URLError("timed out")
    monkeypatch.setattr(live, "fetch_live", down)
    monkeypatch.setattr(live, "send_alarm", lambda msg, url: sent.append(msg))
    (tmp_path / "live_odds_feed.json").write_text('{"updated_at": "2026-11-24T19:00:00Z"}')
    assert live.main(["--viewer", str(tmp_path)]) == 1
    assert "LIVE FEED CHECK FAILED" in sent[0]


def test_live_check_runs_in_the_watcher_even_when_feed_age_fails():
    yml = (ROOT / ".github/workflows/feed_age_watch.yml").read_text()
    step = yml[yml.index("Check the live site's odds feed"):]
    assert "if: always()" in step and "check_live_feed.py" in step
