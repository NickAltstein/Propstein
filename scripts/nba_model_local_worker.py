#!/usr/bin/env python3
"""nba_model local worker — the residential-IP leg of the free topology.

NBA's official feeds block datacenter IPs (empirically confirmed
2026-11-03: stats.nba.com blackholes cloud hosts including GitHub Actions
runners; cdn.nba.com Akamai-403s them — see src/nba_model/DECISIONS.md
D7). This script runs the same pregame research pipeline as Actions,
from a residential IP (your machine, or a self-hosted runner pointed at
it), and commits the artifacts to viewer/feeds/nba_model/ — the exact
commit the workflow would have made. Git stays the cache layer; Vercel
serves it statically.

Usage:
  python3 scripts/nba_model_local_worker.py                 # run + commit
  python3 scripts/nba_model_local_worker.py --push          # run + commit + push
  python3 scripts/nba_model_local_worker.py --season-start 2026-10-20

Schedule it after the daily FanDuel feed is committed, e.g. 13:00 ET:
  0 13 * * * cd /path/to/Propstein && python3 scripts/nba_model_local_worker.py --push

Exit codes mirror the CLI: 0 ok (gates REFUSED is the honest outcome);
any provider or identity-join failure exits non-zero and
commits NOTHING (fail-closed, no partial artifacts).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "src" / "nba_model" / "out"
FEED = REPO / "viewer" / "feeds" / "nba_model"
SEASON_START_DEFAULT = "2026-10-20"

ARTIFACTS = [
    "nba_board_*.json",
    "nba_board_history.jsonl",
    "nba_research_*.json",
    "nba_dataset_meta.json",
]


def run(cmd: list, env: dict) -> int:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], cwd=str(REPO), env=env).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-start", default=SEASON_START_DEFAULT,
                    help="NBA season opening night, ET (YYYY-MM-DD)")
    ap.add_argument("--days", type=int, default=45,
                    help="rolling window length in days (bounded runtime)")
    ap.add_argument("--push", action="store_true", help="git push after committing")
    ap.add_argument("--sync-main", action="store_true",
                    help="first reset this checkout to origin/main (dedicated worktree only; "
                         "refuses if it has uncommitted changes)")
    args = ap.parse_args()
    if args.sync_main:
        status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True).stdout.strip()
        if status:
            print("--sync-main: checkout has uncommitted changes; refusing to reset it.", file=sys.stderr)
            return 5
        for cmd in (["fetch", "-q", "origin", "main"], ["checkout", "-q", "-B", "nba-worker", "origin/main"]):
            if subprocess.run(["git", "-C", str(REPO), *cmd]).returncode != 0:
                print(f"--sync-main: git {' '.join(cmd)} failed.", file=sys.stderr)
                return 5
    if args.push:
        branch = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        if branch not in ("main", "nba-worker"):
            print(f"--push from branch {branch!r} would publish it to main; use --sync-main "
                  "in a dedicated worktree (scripts/install_nba_worker.sh).", file=sys.stderr)
            return 5

    today = datetime.now(ZoneInfo("America/New_York")).date()
    end = today - timedelta(days=1)   # data through yesterday ET
    start = max(date.fromisoformat(args.season_start),
                end - timedelta(days=max(0, args.days - 1)))
    window = (start.isoformat(), end.isoformat())
    print(f"NBA model window: {window[0]} .. {window[1]} (ET, stats.nba.com free path)")

    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}

    if run([sys.executable, "src/nba_model/cli.py", "prepare",
            "--provider", "nbacom", "--start", window[0], "--end", window[1]],
           env) != 0:
        print("prepare failed — fail-closed, nothing committed.", file=sys.stderr)
        return 5

    # 1. Grade yesterday's research against the box scores just fetched.
    yesterday = end.isoformat()
    research_y = FEED / f"nba_research_{yesterday}.json"
    if research_y.exists():
        if run([sys.executable, "scripts/grade_nba_research.py", "--date", yesterday,
                "--research", str(research_y), "--dataset-dir", str(OUT)], env) != 0:
            print("grading failed; continuing with today's research (grades not updated).", file=sys.stderr)
    else:
        print(f"no research for {yesterday}; nothing to grade.")

    slate = today.isoformat()
    # 2. Fresh availability at run time (ESPN); the daily slate job's copy may be hours old.
    injuries = OUT / f"nba_injuries_{slate}.json"
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        sys.path.insert(0, str(REPO / "src"))
        import json as _json
        from publish_nba_market_slate import injury_report
        games_today = _json.loads((REPO / "viewer" / f"nba_games_{slate}.json").read_text())
        injuries.write_text(_json.dumps(injury_report(slate, games_today, ""), indent=1) + "\n")
    except Exception as exc:  # noqa: BLE001 - availability is additive
        print(f"ESPN availability unavailable ({exc}); projecting without it.", file=sys.stderr)
        injuries.unlink(missing_ok=True)
    games = REPO / "viewer" / f"nba_games_{slate}.json"
    odds = REPO / "viewer" / f"nba_odds_{slate}.json"
    if not games.exists() or not odds.exists():
        print(f"today's schedule/odds are missing for {slate}; nothing committed.", file=sys.stderr)
        return 5
    # publish_board appends to out/nba_board_history.jsonl. Start from the
    # committed public chain each run; out/ is gitignored and may be empty or
    # contain a previous failed attempt.
    OUT.mkdir(parents=True, exist_ok=True)
    public_history = FEED / "nba_board_history.jsonl"
    working_history = OUT / "nba_board_history.jsonl"
    if public_history.exists():
        shutil.copyfile(public_history, working_history)
    else:
        working_history.unlink(missing_ok=True)

    code = run([sys.executable, "scripts/build_nba_pregame_research.py",
                "--dataset-dir", str(OUT), "--games", str(games), "--odds", str(odds),
                "--out", str(OUT), "--date", slate]
               + (["--injuries", str(injuries)] if injuries.exists() else []), env)
    if code != 0:
        print("pregame research join failed — nothing committed.", file=sys.stderr)
        return code

    FEED.mkdir(parents=True, exist_ok=True)
    copied = 0
    for pattern in ARTIFACTS:
        for artifact in OUT.glob(pattern):
            target = FEED / artifact.name
            target.write_bytes(artifact.read_bytes())
            copied += 1
    print(f"staged {copied} artifact(s) -> {FEED.relative_to(REPO)}")
    if copied == 0:
        print("no artifacts produced — nothing to commit.", file=sys.stderr)
        return 5

    git = ["git", "-C", str(REPO)]
    if run(git + ["add", "viewer/feeds/nba_model", "viewer/feeds/grades"], env) != 0:
        return 1
    dirty = subprocess.run(git + ["diff", "--staged", "--quiet"], env=env).returncode != 0
    if not dirty:
        print("No artifact changes — nothing to commit.")
        return 0
    if run(git + ["commit", "-m",
                  f"chore(data): NBA model local-worker refresh {slate} [skip ci]"],
           env) != 0:
        return 1
    if args.push:
        if run(git + ["pull", "--rebase", "origin", "main"], env) != 0:
            return 1
        return run(git + ["push", "origin", "HEAD:main"], env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
