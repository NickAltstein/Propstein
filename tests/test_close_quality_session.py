"""Fixture-only tests for the pre-game close-capture session, its log and coverage count."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

from src.close_quality_capture import load_captures_jsonl, normalize_kalshi_capture
import src.close_quality_session as session
from src.close_quality_session import (
    SessionState,
    due_targets,
    next_target_at,
    run_session,
    write_data_files,
)

REPO = Path(__file__).resolve().parents[1]
UTC = timezone.utc
START_A = datetime(2026, 11, 27, 1, 20, tzinfo=UTC)  # 8:20 PM ET
START_B = datetime(2026, 11, 27, 1, 50, tzinfo=UTC)


def _write_feed(root: Path) -> None:
    viewer = root / "viewer"
    viewer.mkdir(parents=True, exist_ok=True)
    quotes = [
        {"game_id": "2026_12_KC_BUF", "end_date": "2026-11-27T01:20:00Z", "over_token_id": "a-over", "under_token_id": "a-under"},
        {"game_id": "2026_12_KC_BUF", "end_date": "2026-11-27T01:20:00Z", "over_token_id": "a2-over", "under_token_id": ""},
        {"game_id": "2026_12_LV_CLE", "end_date": "2026-11-27T01:50:00Z", "over_token_id": "b-over", "under_token_id": "b-under"},
        {"game_id": "2026_12_DONE", "end_date": "2026-11-26T18:00:00Z", "over_token_id": "old", "under_token_id": ""},
    ]
    (viewer / "live_odds_feed.json").write_text(json.dumps({"quotes": quotes}), encoding="utf-8")


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


def _fake_collect(clock: FakeClock, fetch_seconds: float = 40.0, fail: set[str] | None = None):
    calls: list[dict] = []

    def collect(*, viewer_dir, prev_vols, now, window_min_before, window_min_after, only_starts):
        feed = json.loads((viewer_dir / "live_odds_feed.json").read_text(encoding="utf-8"))
        captures, errors, per_game = [], [], {}
        calls.append({"at": clock.now, "starts": sorted(only_starts)})
        for q in feed["quotes"]:
            start = datetime.fromisoformat(q["end_date"].replace("Z", "+00:00"))
            if start not in only_starts:
                continue
            for tok in (q["over_token_id"], q["under_token_id"]):
                if not tok:
                    continue
                key = ("polymarket", q["game_id"], start.isoformat())
                counts = per_game.setdefault(key, {"markets": 0, "errors": 0})
                counts["markets"] += 1
                clock.now += timedelta(seconds=fetch_seconds / 4)
                if fail and tok in fail:
                    counts["errors"] += 1
                    errors.append(f"POLYMARKET_FETCH_FAILED:{tok}:HTTP 503")
                    continue
                captures.append({
                    "schema_version": 2, "source": "polymarket", "market_id": tok,
                    "game_id": q["game_id"], "scheduled_start": start.isoformat(),
                    "captured_at": clock.now.isoformat(), "quoted_at": None,
                    "raw_payload_sha256": tok, "volume_total": None,
                })
        return captures, errors, per_game

    return collect, calls


def _run(tmp_path: Path, start_at: datetime, **kw):
    _write_feed(tmp_path)
    clock = FakeClock(start_at)
    collect, calls = _fake_collect(clock, fail=kw.pop("fail", None))
    published: list[bool] = []

    def publish(state, final):
        published.append(final)
        write_data_files(state, root=tmp_path, now=clock.now)

    state = SessionState()
    result = run_session(
        state=state, clock=clock, sleep=clock.sleep, refresh=lambda now: None,
        collect=collect, publish=publish, root=tmp_path,
        run_meta={"run_id": "123", "run_attempt": "1", "event": "workflow_dispatch"},
        log=lambda msg: None, **kw,
    )
    log = [json.loads(l) for l in (tmp_path / "data/close_capture_log.jsonl").read_text().splitlines()]
    return result, calls, published, log, clock


def test_due_and_next_targets_follow_each_start():
    now = START_A - timedelta(minutes=31)
    done: set[tuple[str, int]] = set()
    assert due_targets([START_A, START_B], now, done) == {START_A: [60]}
    assert next_target_at([START_A, START_B], now, done) == START_A - timedelta(minutes=30)
    done.add((START_A.isoformat(), 60))
    assert due_targets([START_A], now, done) == {}
    # A started game is never due, whatever is left undone.
    assert due_targets([START_A], START_A + timedelta(seconds=1), set()) == {}


def test_session_captures_every_offset_on_its_own_clock(tmp_path):
    result, calls, published, log, clock = _run(tmp_path, START_A - timedelta(minutes=95))
    assert result["reason"] == "idle"
    rounds_a = [c["at"] for c in calls if START_A in c["starts"]]
    assert [round((START_A - t).total_seconds() / 60, 2) for t in rounds_a] == [60, 30, 15, 11, 8]
    rounds_b = [c["at"] for c in calls if START_B in c["starts"]]
    # B's T-60 coincides with A's T-30 and is captured in the same round.
    assert [round((START_B - t).total_seconds() / 60, 2) for t in rounds_b] == [60, 30, 15, 11, 8]
    assert all(s <= session.REFRESH_SECONDS for s in clock.sleeps)
    assert published[-1] is True and published.count(True) == 1

    scheduled = [e for e in log if e["kind"] == "scheduled"]
    assert sorted((e["game_id"], e["markets_in_feed"]) for e in scheduled) == [
        ("2026_12_KC_BUF", 3), ("2026_12_LV_CLE", 2),
    ]
    rounds = [e for e in log if e["kind"] == "round" and e["game_id"] == "2026_12_KC_BUF"]
    assert [e["target_offsets_minutes"] for e in rounds] == [[60], [30], [15], [11], [8]]
    last = rounds[-1]
    assert last["markets_expected"] == 3 and last["markets_captured"] == 3
    assert last["late_seconds"] == 0.0
    assert 5 < last["minutes_before_start_last"] < 8
    assert last["run_id"] == "123"

    records, _, valid = load_captures_jsonl(tmp_path / "data/close_quality_captures.jsonl")
    assert valid and len(records) == 5 * 3 + 5 * 2


def test_late_start_captures_overdue_targets_at_once(tmp_path):
    result, calls, _, log, _ = _run(tmp_path, START_A - timedelta(minutes=25), offsets=(60, 30, 15, 11, 8))
    first = [e for e in log if e["kind"] == "round" and e["game_id"] == "2026_12_KC_BUF"][0]
    assert first["target_offsets_minutes"] == [60, 30]
    assert first["late_seconds"] == 5 * 60.0
    assert len([c for c in calls if START_A in c["starts"]]) == 4


def test_session_stays_down_when_nothing_is_near(tmp_path):
    result, calls, published, log, _ = _run(tmp_path, START_A - timedelta(hours=6))
    assert result["reason"] == "idle" and calls == []
    # It still records the games it saw, so a game nobody captures counts as missed.
    assert {e["kind"] for e in log} == {"scheduled"}


def test_max_age_stops_with_targets_left(tmp_path):
    result, calls, _, _, _ = _run(tmp_path, START_A - timedelta(minutes=65), max_session_min=20)
    assert result["reason"] == "max_age"
    assert result["next_target_at"] is not None


def test_fetch_errors_are_counted_per_game(tmp_path):
    _, _, _, log, _ = _run(tmp_path, START_A - timedelta(minutes=9), fail={"a-under"})
    row = [e for e in log if e["kind"] == "round" and e["game_id"] == "2026_12_KC_BUF"][0]
    assert row["markets_expected"] == 3 and row["markets_captured"] == 2 and row["fetch_errors"] == 1


def _cap(minute: int) -> dict:
    return normalize_kalshi_capture(
        market_id="K1", game_id="g", scheduled_start=START_A,
        market_payload={"market": {"volume_fp": "10"}},
        captured_at=START_A - timedelta(minutes=minute),
    )


def test_publish_rebuilds_from_main_and_stacks_byte_for_byte(tmp_path):
    main = tmp_path / "main"
    (main / "data").mkdir(parents=True)
    state = SessionState(pending_captures=[_cap(30)])
    write_data_files(state, root=main, now=START_A)
    base = (main / "data/close_quality_captures.jsonl").read_bytes()  # main after PR 1 merged

    # PR 2 opened while main == base; then the last PR is stacked on the same main.
    pr2, pr3 = tmp_path / "pr2", tmp_path / "pr3"
    shutil.copytree(main, pr2)
    shutil.copytree(main, pr3)
    state.pending_captures += [_cap(15)]
    write_data_files(state, root=pr2, now=START_A)
    status_before = (pr3 / "data/close_quality_capture_status.json").read_bytes()
    state.pending_captures += [_cap(8)]
    write_data_files(state, root=pr3, now=START_A, include_status=False)

    lines2 = (pr2 / "data/close_quality_captures.jsonl").read_bytes()
    lines3 = (pr3 / "data/close_quality_captures.jsonl").read_bytes()
    assert lines2.startswith(base) and lines3.startswith(lines2)
    assert (pr3 / "data/close_quality_capture_status.json").read_bytes() == status_before
    records, _, valid = load_captures_jsonl(pr3 / "data/close_quality_captures.jsonl")
    assert valid and len(records) == 3
    # Captures already on main leave the pending list.
    assert len(state.pending_captures) == 2


def test_coverage_counts_unlogged_misses_and_fresh_markets():
    from scripts.close_capture_coverage import coverage

    later = START_A + timedelta(hours=1)
    log = [
        {"kind": "scheduled", "source": "polymarket", "game_id": "A", "scheduled_start": START_A.isoformat(), "markets_in_feed": 4},
        {"kind": "scheduled", "source": "polymarket", "game_id": "B", "scheduled_start": START_B.isoformat(), "markets_in_feed": 2},
        {"kind": "round", "source": "polymarket", "game_id": "A", "scheduled_start": START_A.isoformat(),
         "markets_expected": 3, "late_seconds": 12.0},
    ]
    def cap(game, start, market, minutes_before):
        return {"schema_version": 2, "source": "polymarket", "game_id": game, "market_id": market,
                "scheduled_start": start.isoformat(), "captured_at": (start - timedelta(minutes=minutes_before)).isoformat()}
    caps = [
        cap("A", START_A, "m1", 8), cap("A", START_A, "m2", 30), cap("A", START_A, "m3", 4),
        cap("Z", START_A, "z1", 8),  # not in the log: left out unless asked
    ]
    res = coverage(log_entries=log, captures=caps, now=later)
    pm = res["by_source"]["polymarket"]
    assert (pm["games"], pm["games_covered"]) == (2, 1)
    assert (pm["markets"], pm["markets_fresh"]) == (6, 1)
    assert pm["missed_games"] == [f"B {START_B.isoformat()}"]
    assert pm["round_late_seconds_median"] == 12.0
    res_all = coverage(log_entries=log, captures=caps, now=later, include_unlogged=True)
    assert res_all["by_source"]["polymarket"]["games"] == 3
    # A game whose cutoff has not passed yet is not counted.
    early = coverage(log_entries=log, captures=caps, now=START_A - timedelta(minutes=30))
    assert early["by_source"] == {}


def _leader(tmp_path: Path, runs: dict[str, list[int]], handoff: str = "", fail: bool = False) -> str:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    lines = ["#!/usr/bin/env bash", "args=\"$*\""]
    if fail:
        lines.append("exit 1")
    for status, ids in runs.items():
        lines.append(f'case "$args" in *status={status}*) printf "%s\\n" {" ".join(map(str, ids)) or "\"\""}; exit 0;; esac')
    lines.append("exit 0")
    gh.write_text("\n".join(lines) + "\n")
    gh.chmod(0o755)
    out = tmp_path / "out.txt"
    out.write_text("")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "GITHUB_REPOSITORY": "o/r",
           "GITHUB_RUN_ID": "500", "GITHUB_OUTPUT": str(out), "HANDOFF_FROM": handoff}
    subprocess.run(["bash", str(REPO / "scripts/close_capture_leader.sh")], env=env, check=True, capture_output=True)
    return out.read_text().strip()


def test_leader_only_oldest_active_run_leads(tmp_path):
    assert _leader(tmp_path, {"in_progress": [500], "queued": []}) == "lead=true"
    assert _leader(tmp_path, {"in_progress": [400, 500], "queued": []}) == "lead=false"
    assert _leader(tmp_path, {"in_progress": [500], "queued": [450]}) == "lead=false"
    assert _leader(tmp_path, {"in_progress": [500, 600], "queued": []}) == "lead=true"
    assert _leader(tmp_path, {"in_progress": [400, 500], "queued": []}, handoff="400") == "lead=true"
    assert _leader(tmp_path, {}, fail=True) == "lead=true"
