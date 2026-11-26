"""Pre-game close-capture session: capture each game at fixed offsets on the runner's own clock.

The one-shot capture (`src.close_quality_capture`) only lands near T-5 when
some trigger happens to fire at the right minute. GitHub's schedule drops
most ticks, and the Vercel Schedules backstop fires on a fixed 15-minute grid
that is not anchored to any game. Here one workflow run stays up through the
pre-game window instead: any trigger that lands before a game starts a
session, and the session itself waits until each game's target offsets
(T-60, T-30, T-15, T-11, T-8 by default) and captures that start group then.
Three of those offsets fall inside the 15 minutes before the T-5 cutoff, so a
single failed fetch round does not lose the close.

Every capture round appends one line per game to `data/close_capture_log.jsonl`
(planned time, actual start/finish, first and last captured_at, markets
expected vs captured, fetch errors, run id). The first time a session sees a
game in the feeds it also logs a `scheduled` line, so a game that never gets a
capture round still counts as a miss. `scripts/close_capture_coverage.py`
computes coverage from the log and the capture records.

Publishing keeps the existing auto-merge data-PR path. A session has at most
one data PR open at a time; captures wait in memory until the previous PR has
merged, and every PR is rebuilt from the newest main, so the hash-chained
capture file never forks. Only one session runs at a time (the workflow's
leader step), and a session that reaches its maximum age hands over to a
successor run with `workflow_dispatch`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.close_quality_capture import (
    GameKey,
    _last_volume_by_market,
    _parse_utc,
    append_captures,
    capture_record_id,
    collect_live_captures,
    load_captures_jsonl,
    upcoming_games,
)

TARGET_OFFSETS_MIN: tuple[int, ...] = (60, 30, 15, 11, 8)
LOOKAHEAD_MIN = 150.0  # stay up while the next target is this close
SCHEDULE_HORIZON_MIN = 24 * 60.0  # log games this far ahead as `scheduled`
MAX_SESSION_MIN = 300.0  # then hand over to a successor run
REFRESH_SECONDS = 300.0  # re-read the feeds at least this often while waiting
FINAL_PR_WAIT_SECONDS = 25 * 60.0
PR_POLL_SECONDS = 30.0

LOG_SCHEMA = "propwire.close_capture_log.v1"
CAPTURES_PATH = Path("data/close_quality_captures.jsonl")
STATUS_PATH = Path("data/close_quality_capture_status.json")
LOG_PATH = Path("data/close_capture_log.jsonl")
VIEWER_DIR = Path("viewer")
WORKFLOW_FILE = "close_quality_capture.yml"
PR_SLUG = "close-quality-capture"
PR_MESSAGE = "chore(data): append close-quality captures [vercel skip]"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def log_entry_id(entry: Mapping[str, Any]) -> str:
    """Identity of a log line: a game is `scheduled` once; a round is unique by its start time."""
    identity = {k: entry.get(k) for k in ("kind", "source", "game_id", "scheduled_start")}
    if entry.get("kind") == "round":
        identity["started_at"] = entry.get("started_at")
        identity["run_id"] = entry.get("run_id")
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def load_log_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            ids.add(str(json.loads(raw).get("entry_id")))
    return ids


def append_log_entries(path: Path, entries: Iterable[Mapping[str, Any]]) -> int:
    """Append entries whose `entry_id` is not in the file yet; returns how many were written."""
    seen = load_log_ids(path)
    lines: list[str] = []
    for entry in entries:
        if entry["entry_id"] in seen:
            continue
        seen.add(entry["entry_id"])
        lines.append(json.dumps(entry, sort_keys=True, separators=(",", ":")))
    if lines:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
    return len(lines)


def due_targets(
    starts: Iterable[datetime],
    now: datetime,
    done: set[tuple[str, int]],
    offsets: Sequence[int] = TARGET_OFFSETS_MIN,
) -> dict[datetime, list[int]]:
    """Start groups with a target offset that has arrived and not been captured, before start."""
    due: dict[datetime, list[int]] = {}
    for start in starts:
        if start <= now:
            continue
        for off in offsets:
            if (_iso(start), off) in done:
                continue
            if start - timedelta(minutes=off) <= now:
                due.setdefault(start, []).append(off)
    return {s: sorted(v, reverse=True) for s, v in due.items()}


def next_target_at(
    starts: Iterable[datetime],
    now: datetime,
    done: set[tuple[str, int]],
    offsets: Sequence[int] = TARGET_OFFSETS_MIN,
) -> datetime | None:
    upcoming = [
        start - timedelta(minutes=off)
        for start in starts
        if start > now
        for off in offsets
        if (_iso(start), off) not in done and start - timedelta(minutes=off) > now
    ]
    return min(upcoming) if upcoming else None


def round_log_entries(
    *,
    due: Mapping[datetime, Sequence[int]],
    started_at: datetime,
    finished_at: datetime,
    captures: Sequence[Mapping[str, Any]],
    per_game: Mapping[GameKey, Mapping[str, int]],
    run_meta: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """One `round` line per game captured in this round."""
    by_game: dict[GameKey, list[datetime]] = {}
    for cap in captures:
        key = (str(cap.get("source")), str(cap.get("game_id")), str(cap.get("scheduled_start")))
        captured = _parse_utc(cap.get("captured_at"))
        if captured is not None:
            by_game.setdefault(key, []).append(captured)
    due_by_iso = {_iso(s): list(v) for s, v in due.items()}
    entries: list[dict[str, Any]] = []
    for key in sorted(set(per_game) | set(by_game)):
        source, game_id, start_iso = key
        start_dt = _parse_utc(start_iso)
        offsets = due_by_iso.get(start_iso, [])
        planned = start_dt - timedelta(minutes=min(offsets)) if (start_dt and offsets) else None
        times = sorted(by_game.get(key, []))
        counts = per_game.get(key, {})
        entry: dict[str, Any] = {
            "schema": LOG_SCHEMA,
            "kind": "round",
            "source": source,
            "game_id": game_id,
            "scheduled_start": start_iso,
            "target_offsets_minutes": offsets,
            "planned_at": _iso(planned) if planned else None,
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "late_seconds": round((started_at - planned).total_seconds(), 1) if planned else None,
            "markets_expected": int(counts.get("markets", 0)),
            "markets_captured": len(times),
            "fetch_errors": int(counts.get("errors", 0)),
            "first_captured_at": _iso(times[0]) if times else None,
            "last_captured_at": _iso(times[-1]) if times else None,
            "minutes_before_start_last": (
                round((start_dt - times[-1]).total_seconds() / 60.0, 2) if (times and start_dt) else None
            ),
            **{k: run_meta.get(k) for k in ("run_id", "run_attempt", "event")},
        }
        entry["entry_id"] = log_entry_id(entry)
        entries.append(entry)
    return entries


def scheduled_log_entry(key: GameKey, markets: int, seen_at: datetime, run_meta: Mapping[str, Any]) -> dict[str, Any]:
    source, game_id, start_iso = key
    entry: dict[str, Any] = {
        "schema": LOG_SCHEMA,
        "kind": "scheduled",
        "source": source,
        "game_id": game_id,
        "scheduled_start": start_iso,
        "first_seen_at": _iso(seen_at),
        "markets_in_feed": markets,
        "run_id": run_meta.get("run_id"),
    }
    entry["entry_id"] = log_entry_id(entry)
    return entry


@dataclass
class SessionState:
    pending_captures: list[dict[str, Any]] = field(default_factory=list)
    pending_log: list[dict[str, Any]] = field(default_factory=list)
    last_errors: list[str] = field(default_factory=list)
    done: set[tuple[str, int]] = field(default_factory=set)
    rounds: int = 0


def write_data_files(
    state: SessionState,
    *,
    root: Path,
    now: datetime,
    include_status: bool = True,
) -> None:
    """Write main's files plus everything pending, and drop pending items main already has."""
    captures_path = root / CAPTURES_PATH
    status_path = root / STATUS_PATH
    log_path = root / LOG_PATH
    existing, _, _ = load_captures_jsonl(captures_path)
    on_main = {str(r.get("record_id")) for r in existing}
    state.pending_captures = [c for c in state.pending_captures if capture_record_id(c) not in on_main]
    logged = load_log_ids(log_path)
    state.pending_log = [e for e in state.pending_log if e["entry_id"] not in logged]

    status_before = status_path.read_bytes() if status_path.exists() else None
    append_captures(
        jsonl_path=captures_path,
        status_path=status_path,
        captures=state.pending_captures,
        fetch_errors=state.last_errors,
        now=now,
    )
    if not include_status:
        if status_before is None:
            status_path.unlink(missing_ok=True)
        else:
            status_path.write_bytes(status_before)
    append_log_entries(log_path, state.pending_log)


def run_session(
    *,
    state: SessionState,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    refresh: Callable[[datetime], None],
    collect: Callable[..., tuple[list[dict[str, Any]], list[str], dict[GameKey, dict[str, int]]]],
    publish: Callable[[SessionState, bool], Any],
    root: Path,
    run_meta: Mapping[str, Any],
    offsets: Sequence[int] = TARGET_OFFSETS_MIN,
    lookahead_min: float = LOOKAHEAD_MIN,
    max_session_min: float = MAX_SESSION_MIN,
    refresh_seconds: float = REFRESH_SECONDS,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Wait for each game's target offsets and capture at them until nothing is near."""
    started = clock()
    max_offset = max(offsets)
    reason = "idle"
    next_at: datetime | None = None
    while True:
        refresh(clock())
        now = clock()
        games = upcoming_games(root / VIEWER_DIR, now, SCHEDULE_HORIZON_MIN)
        logged = load_log_ids(root / LOG_PATH) | {e["entry_id"] for e in state.pending_log}
        for key, markets in sorted(games.items()):
            entry = scheduled_log_entry(key, markets, now, run_meta)
            if entry["entry_id"] not in logged:
                state.pending_log.append(entry)
                logged.add(entry["entry_id"])

        starts = {_parse_utc(k[2]) for k in games}
        starts.discard(None)
        due = due_targets(starts, now, state.done, offsets)
        if due:
            existing, _, _ = load_captures_jsonl(root / CAPTURES_PATH)
            prev_vols = _last_volume_by_market([*existing, *state.pending_captures], now)
            round_start = clock()
            captures, errors, per_game = collect(
                viewer_dir=root / VIEWER_DIR,
                prev_vols=prev_vols,
                now=round_start,
                window_min_before=max_offset + 15.0,
                window_min_after=0.0,
                only_starts=set(due),
            )
            round_end = clock()
            state.pending_captures.extend(captures)
            state.last_errors = list(errors)
            state.pending_log.extend(round_log_entries(
                due=due, started_at=round_start, finished_at=round_end,
                captures=captures, per_game=per_game, run_meta=run_meta,
            ))
            for start, offs in due.items():
                for off in offs:
                    state.done.add((_iso(start), off))
            state.rounds += 1
            log(json.dumps({
                "round": state.rounds,
                "starts": sorted(_iso(s) for s in due),
                "captured": len(captures),
                "errors": len(errors),
                "seconds": round((round_end - round_start).total_seconds(), 1),
            }))
            publish(state, False)
            continue

        next_at = next_target_at(starts, now, state.done, offsets)
        if next_at is None or (next_at - now).total_seconds() / 60.0 > lookahead_min:
            reason = "idle"
            break
        if (now - started).total_seconds() / 60.0 >= max_session_min:
            reason = "max_age"
            break
        sleep(max(1.0, min((next_at - now).total_seconds(), refresh_seconds)))

    publish(state, True)
    return {
        "reason": reason,
        "rounds": state.rounds,
        "next_target_at": _iso(next_at) if next_at else None,
        "pending_captures": len(state.pending_captures),
    }


# --------------------------------------------------------------------------
# GitHub Actions wiring: git refresh, data PRs, successor dispatch.


def _run(cmd: Sequence[str], *, env: Mapping[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(list(cmd), env=full_env, check=check, capture_output=True, text=True)


def _feed_paths(now: datetime) -> list[Path]:
    et_day = now.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    return [
        VIEWER_DIR / "live_odds_feed.json",
        VIEWER_DIR / f"nba_odds_{et_day}.json",
        CAPTURES_PATH,
        STATUS_PATH,
        LOG_PATH,
    ]


class GitHubSession:
    """Refresh inputs from origin/main and publish through scripts/open_automerge_data_pr.sh."""

    def __init__(self, *, publish_enabled: bool, repo: str, log: Callable[[str], None] = print):
        self.publish_enabled = publish_enabled
        self.repo = repo
        self.log = log
        self.outstanding_pr: int | None = None
        self.publish_failed = False

    def fetch_main(self) -> bool:
        res = _run(["git", "fetch", "--no-tags", "--quiet", "origin", "main"], check=False)
        if res.returncode != 0:
            self.log(f"::warning::git fetch origin main failed; using the files already on disk ({res.stderr.strip()[:200]})")
            return False
        return True

    def refresh(self, now: datetime) -> None:
        """Bring the feeds and data files to main's newest version without touching code."""
        if not self.fetch_main():
            return
        for path in _feed_paths(now):
            res = _run(["git", "show", f"FETCH_HEAD:{path.as_posix()}"], check=False)
            if res.returncode == 0:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(res.stdout, encoding="utf-8")
            else:
                # Not on main (yet): drop any local copy so main stays the base.
                path.unlink(missing_ok=True)

    def _pr_open(self, number: int) -> bool:
        res = _run(
            ["gh", "api", f"repos/{self.repo}/pulls/{number}", "--jq", ".state"],
            env={"GH_TOKEN": os.environ.get("DATA_BOT_TOKEN", "")},
            check=False,
        )
        if res.returncode != 0:
            self.log(f"::warning::Could not read data PR #{number}; treating it as still open.")
            return True
        return res.stdout.strip() == "open"

    def publish(self, state: SessionState, final: bool) -> None:
        now = datetime.now(timezone.utc)
        if not self.publish_enabled:
            write_data_files(state, root=Path("."), now=now)
            return
        stacked = False
        if self.outstanding_pr is not None and self._pr_open(self.outstanding_pr):
            if not final:
                self.log(f"Data PR #{self.outstanding_pr} still open; holding {len(state.pending_captures)} captures for the next round.")
                return
            deadline = time.monotonic() + FINAL_PR_WAIT_SECONDS
            while time.monotonic() < deadline and self._pr_open(self.outstanding_pr):
                time.sleep(PR_POLL_SECONDS)
            # Still open: open the last PR on the same main. Its capture and
            # log lines start with the open PR's lines byte for byte, so it
            # merges cleanly after it; the status file is left alone because
            # both PRs would rewrite it.
            stacked = self._pr_open(self.outstanding_pr)
        if not self.fetch_main():
            self.publish_failed = True
            return
        _run(["git", "checkout", "--quiet", "-f", "-B", "close-capture-session", "FETCH_HEAD"])
        write_data_files(state, root=Path("."), now=now, include_status=not stacked)
        paths = [CAPTURES_PATH, LOG_PATH] + ([] if stacked else [STATUS_PATH])
        _run(["git", "add", "--", *[p.as_posix() for p in paths if p.exists()]])
        if _run(["git", "diff", "--cached", "--quiet"], check=False).returncode == 0:
            return
        res = _run(
            ["bash", "scripts/open_automerge_data_pr.sh", PR_SLUG, PR_MESSAGE],
            env={"GH_TOKEN": os.environ.get("DATA_BOT_TOKEN", "")},
            check=False,
        )
        sys.stdout.write(res.stdout)
        if res.returncode != 0:
            self.log(f"::error::Opening the close-capture data PR failed: {res.stderr.strip()[:500]}")
            self.publish_failed = True
            return
        match = re.search(r"/pull/(\d+)", res.stdout)
        self.outstanding_pr = int(match.group(1)) if match else None

    def dispatch_successor(self, run_id: str) -> None:
        res = _run(
            ["gh", "workflow", "run", WORKFLOW_FILE, "--ref", "main", "-f", f"handoff_from={run_id}"],
            env={"GH_TOKEN": os.environ.get("GITHUB_TOKEN", "")},
            check=False,
        )
        if res.returncode == 0:
            self.log("Dispatched the successor session.")
        else:
            self.log(f"::warning::Successor dispatch failed; the 15-minute triggers will start the next session. {res.stderr.strip()[:200]}")


def main() -> int:
    publish_enabled = os.environ.get("CLOSE_CAPTURE_PUBLISH", "0") == "1"
    max_minutes = float(os.environ.get("CLOSE_CAPTURE_MAX_MINUTES") or MAX_SESSION_MIN)
    run_meta = {
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "event": os.environ.get("GITHUB_EVENT_NAME"),
    }
    gh = GitHubSession(publish_enabled=publish_enabled, repo=os.environ.get("GITHUB_REPOSITORY", "NickAltstein/Propstein"))
    result = run_session(
        state=SessionState(),
        clock=lambda: datetime.now(timezone.utc),
        sleep=time.sleep,
        refresh=gh.refresh,
        collect=collect_live_captures,
        publish=gh.publish,
        root=Path("."),
        run_meta=run_meta,
        max_session_min=max_minutes,
    )
    print(json.dumps(result))
    if result["reason"] == "max_age" and result["next_target_at"] and publish_enabled and run_meta["run_id"]:
        gh.dispatch_successor(str(run_meta["run_id"]))
    return 1 if gh.publish_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
