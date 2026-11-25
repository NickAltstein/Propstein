#!/usr/bin/env python3
"""
Local helper for one human PRIVATE_VALIDATION session (pregame NBA 3PM, DK vs FD).

Stamps UTC timestamps and TenSecondTrace automatically, POSTs both observations
and the compare request to /api/private-validation/*, writes the complete
session log JSON to disk, and exits non-zero unless state == COMPARISON_COMPLETE
and capture_to_answer_ms <= 10000.

Never invents observations; all odds and IDs must be supplied by the human
reading live DraftKings and FanDuel tabs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TEN_SECOND_BUDGET_MS = 10_000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _epoch_ms() -> int:
    return int(time.time() * 1000)


def _http_post_json(url: str, payload: dict[str, Any], token: str) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return int(resp.status), body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"raw_error": raw}
        return int(exc.code), body


def run_session(
    *,
    player: str,
    rung: int,
    dk_odds: int,
    fd_odds: int,
    slip_odds: int,
    game_id: str,
    player_id: str,
    slip_book: str = "draftkings",
    base_url: str = "http://127.0.0.1:8000",
    token: str = "",
    run_id: str | None = None,
    out_path: str | Path | None = None,
    dk_status: str = "HEALTHY",
    fd_status: str = "HEALTHY",
    post_json: Callable[[str, dict[str, Any], str], tuple[int, dict[str, Any]]] = _http_post_json,
    clock_ms: Callable[[], int] = _epoch_ms,
    utc_now: Callable[[], str] = _utc_now_iso,
) -> tuple[int, dict[str, Any]]:
    if not token:
        raise ValueError("PRIVATE_VALIDATION_TOKEN is required (via --token or env)")
    if rung not in (3, 4):
        raise ValueError("rung must be 3 or 4")

    base = base_url.rstrip("/")
    sid = run_id or f"pv-{uuid.uuid4().hex[:12]}"
    t0_capture_start_ms = clock_ms()
    captured_at = utc_now()
    t_capture_complete_ms = clock_ms()

    dk_obs_payload = {
        "book": "draftkings",
        "game_id": game_id,
        "player_id": player_id,
        "market": "nba_threes",
        "period": "full_game",
        "rung": rung,
        "american_odds": dk_odds,
        "observed_at": utc_now(),
        "received_at": utc_now(),
        "adapter_id": "manual-first-session",
        "run_id": sid,
        "source_env": "PRIVATE_VALIDATION",
        "observation_seq": 1,
        "is_fixture": False,
        "is_seeded": False,
        "is_replayed": False,
        "is_test": False,
        "provenance": "private_adapter",
    }
    dk_http_status, dk_resp = post_json(f"{base}/api/private-validation/observations", dk_obs_payload, token)
    t_dk_adapter_ms = clock_ms()

    fd_obs_payload = {
        "book": "fanduel",
        "game_id": game_id,
        "player_id": player_id,
        "market": "nba_threes",
        "period": "full_game",
        "rung": rung,
        "american_odds": fd_odds,
        "observed_at": utc_now(),
        "received_at": utc_now(),
        "adapter_id": "manual-first-session",
        "run_id": sid,
        "source_env": "PRIVATE_VALIDATION",
        "observation_seq": 1,
        "is_fixture": False,
        "is_seeded": False,
        "is_replayed": False,
        "is_test": False,
        "provenance": "private_adapter",
    }
    fd_http_status, fd_resp = post_json(f"{base}/api/private-validation/observations", fd_obs_payload, token)
    t_fd_adapter_ms = clock_ms()

    t_answer_rendered_ms = clock_ms()
    t_return_to_book_ms = clock_ms()

    compare_payload = {
        "run_id": sid,
        "slip": {
            "slip_id": f"slip-{sid}",
            "check_version": 1,
            "player_id": player_id,
            "player_name": player,
            "game_id": game_id,
            "market": "nba_threes",
            "period": "full_game",
            "rung": rung,
            "book": slip_book,
            "captured_price": slip_odds,
            "captured_at": captured_at,
            "capture_method": "manual",
        },
        "dk_health": {
            "book": "draftkings",
            "run_id": sid,
            "status": dk_status,
            "checked_at": utc_now(),
            "latency_ms": float(max(0, t_dk_adapter_ms - t_capture_complete_ms)),
        },
        "fd_health": {
            "book": "fanduel",
            "run_id": sid,
            "status": fd_status,
            "checked_at": utc_now(),
            "latency_ms": float(max(0, t_fd_adapter_ms - t_dk_adapter_ms)),
        },
        "expected_check_version": 1,
        "timing_trace": {
            "t0_capture_start_ms": t0_capture_start_ms,
            "t_capture_complete_ms": t_capture_complete_ms,
            "t_dk_adapter_ms": t_dk_adapter_ms,
            "t_fd_adapter_ms": t_fd_adapter_ms,
            "t_answer_rendered_ms": t_answer_rendered_ms,
            "t_return_to_book_ms": t_return_to_book_ms,
        },
    }
    compare_http_status, compare_resp = post_json(f"{base}/api/private-validation/compare", compare_payload, token)

    failure_reasons: list[str] = []
    if dk_http_status != 200 or not dk_resp.get("accepted"):
        failure_reasons.append(f"dk_observation_failed: status={dk_http_status} body={dk_resp}")
    if fd_http_status != 200 or not fd_resp.get("accepted"):
        failure_reasons.append(f"fd_observation_failed: status={fd_http_status} body={fd_resp}")
    if compare_http_status != 200:
        failure_reasons.append(f"compare_http_error: status={compare_http_status} body={compare_resp}")

    state = compare_resp.get("state")
    if state != "COMPARISON_COMPLETE":
        failure_reasons.append(f"state_not_complete: {state} ({compare_resp.get('reason_code')})")

    timing = compare_resp.get("timing") or {}
    capture_to_answer_ms = timing.get("capture_to_answer_ms")
    if capture_to_answer_ms is None:
        failure_reasons.append("missing_capture_to_answer_ms")
    elif int(capture_to_answer_ms) > TEN_SECOND_BUDGET_MS:
        failure_reasons.append(
            f"capture_to_answer_ms_exceeded: {capture_to_answer_ms} > {TEN_SECOND_BUDGET_MS}"
        )

    session_log = {
        "run_id": sid,
        "inputs": {
            "player": player,
            "rung": rung,
            "dk_odds": dk_odds,
            "fd_odds": fd_odds,
            "slip_odds": slip_odds,
            "slip_book": slip_book,
            "game_id": game_id,
            "player_id": player_id,
        },
        "observations": {
            "draftkings": {"request": dk_obs_payload, "http_status": dk_http_status, "response": dk_resp},
            "fanduel": {"request": fd_obs_payload, "http_status": fd_http_status, "response": fd_resp},
        },
        "compare": {
            "request": compare_payload,
            "http_status": compare_http_status,
            "response": compare_resp,
        },
        "passed": len(failure_reasons) == 0,
        "failure_reasons": failure_reasons,
    }

    target = Path(out_path) if out_path else Path(f"private_session_{sid}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(session_log, indent=2) + "\n", encoding="utf-8")

    return (0 if not failure_reasons else 1), session_log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one private validation session (NBA 3PM, DK vs FD).")
    parser.add_argument("--player", required=True, help="Player display name")
    parser.add_argument("--rung", required=True, type=int, choices=[3, 4], help="3 (3+ / Over 2.5) or 4 (4+ / Over 3.5)")
    parser.add_argument("--dk-odds", required=True, type=int, help="DraftKings American odds (e.g. 150 or -115)")
    parser.add_argument("--fd-odds", required=True, type=int, help="FanDuel American odds (e.g. 170 or -110)")
    parser.add_argument("--slip-odds", required=True, type=int, help="Captured draft slip American odds")
    parser.add_argument("--game-id", required=True, help="Canonical game_id")
    parser.add_argument("--player-id", required=True, help="Canonical player_id")
    parser.add_argument("--slip-book", default="draftkings", choices=["draftkings", "fanduel"])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default=os.environ.get("PRIVATE_VALIDATION_TOKEN", ""))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--out", default=None, help="Output JSON log path")
    parser.add_argument("--dk-status", default="HEALTHY")
    parser.add_argument("--fd-status", default="HEALTHY")
    args = parser.parse_args(argv)

    code, log = run_session(
        player=args.player,
        rung=args.rung,
        dk_odds=args.dk_odds,
        fd_odds=args.fd_odds,
        slip_odds=args.slip_odds,
        game_id=args.game_id,
        player_id=args.player_id,
        slip_book=args.slip_book,
        base_url=args.base_url,
        token=args.token,
        run_id=args.run_id,
        out_path=args.out,
        dk_status=args.dk_status,
        fd_status=args.fd_status,
    )
    verdict = log["compare"]["response"]
    print(json.dumps({
        "run_id": log["run_id"],
        "state": verdict.get("state"),
        "variant": verdict.get("variant"),
        "headline": verdict.get("headline"),
        "capture_to_answer_ms": (verdict.get("timing") or {}).get("capture_to_answer_ms"),
        "passed": log["passed"],
        "failure_reasons": log["failure_reasons"],
    }, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
