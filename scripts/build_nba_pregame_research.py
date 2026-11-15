#!/usr/bin/env python3
"""Join the NBA points model to today's published schedule and FanDuel lines.

This writes research projections and the existing fail-closed board decision.
It never turns a one-book, uncalibrated slate into recommended plays.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from nba_model.features.form import add_rolling_form  # noqa: E402
from nba_model.features.injuries import add_availability_adjustments  # noqa: E402
from nba_model.model.distribution import PointsDistribution  # noqa: E402
from nba_model.model.projector import NBAPointsProjector  # noqa: E402
from nba_model.publish.board import build_board, publish_board  # noqa: E402


def name_key(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in plain.casefold() if c.isalnum())


def load_dataset(directory: Path) -> pd.DataFrame:
    parquet = directory / "nba_player_games.parquet"
    csv = directory / "nba_player_games.csv"
    if parquet.exists():
        return pd.read_parquet(parquet)
    if csv.exists():
        return pd.read_csv(csv)
    raise ValueError(f"no prepared player-game dataset in {directory}")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pregame_rows(history: pd.DataFrame, games: list[dict], quotes: list[dict], slate: str) -> tuple[pd.DataFrame, list[dict], dict]:
    """Resolve each quote to one historical player on a team in that game."""
    played = history[(~history["dnp"]) & (history["game_date"] < slate)].copy()
    played = played.sort_values(["player_id", "game_date", "game_id"])
    latest = played.drop_duplicates("player_id", keep="last")
    by_name: dict[str, list[dict]] = {}
    for row in latest.to_dict("records"):
        by_name.setdefault(name_key(str(row["player_display_name"])), []).append(row)
    games_by_id = {g["game_id"]: g for g in games if g.get("gameday") == slate}
    rows: list[dict] = []
    remapped: list[dict] = []
    reasons = {"unmatched_identity": 0, "ambiguous_identity": 0, "wrong_game": 0, "duplicate_quote": 0}
    seen = set()
    for q in quotes:
        if q.get("market") != "points":
            continue
        game = games_by_id.get(q.get("game_id"))
        if not game:
            reasons["wrong_game"] += 1
            continue
        teams = {game["home_team"], game["away_team"]}
        matches = [r for r in by_name.get(name_key(str(q.get("player_name", ""))), []) if r["team"] in teams]
        if not matches:
            reasons["unmatched_identity"] += 1
            continue
        if len(matches) != 1:
            reasons["ambiguous_identity"] += 1
            continue
        player = matches[0]
        key = (player["player_id"], game["game_id"])
        if key in seen:
            reasons["duplicate_quote"] += 1
            continue
        seen.add(key)
        ph = played[played["player_id"] == player["player_id"]]
        team = player["team"]
        rows.append({
            "player_id": player["player_id"],
            "player_display_name": player["player_display_name"],
            "team": team,
            "opponent": game["away_team"] if team == game["home_team"] else game["home_team"],
            "game_id": game["game_id"],
            "game_date": slate,
            "position": player["position"],
            "dnp": False,
            "form5_ppm": float(ph["ppm"].tail(5).mean()),
            "form20_ppm": float(ph["ppm"].tail(20).mean()),
            "availability_mult": 1.0,
            "var_inflation": 1.0,
        })
        remapped.append({**q, "player_id": player["player_id"]})
    return pd.DataFrame(rows), remapped, reasons


#: ESPN status -> the availability vocabulary nba_model.features.injuries uses.
ESPN_STATUS = {"out": "out", "suspension": "suspended", "suspended": "suspended",
               "doubtful": "doubtful", "questionable": "questionable",
               "day-to-day": "questionable", "game time decision": "gtd"}


def availability_rows(report: dict | None, history: pd.DataFrame, games: list[dict], slate: str) -> tuple[list[dict] | None, dict]:
    """ESPN injury report -> nba_model availability rows (player_id, team, game_id, report_status).

    Names are resolved to one dataset player on that team (ambiguous -> skipped).
    An Out player only triggers the teammate minutes boost when he played in one
    of his team's last two games: a long absence is already in his teammates'
    recent minutes, so boosting again would double count it.
    """
    stats = {"entries": 0, "matched": 0, "out_recent": 0, "out_long_term": 0, "uncertain": 0}
    if not report or not report.get("injuries"):
        return None, stats
    played = history[(~history["dnp"]) & (history["game_date"] < slate)]
    latest = played.sort_values(["game_date", "game_id"]).drop_duplicates("player_id", keep="last")
    by_key: dict[tuple[str, str], list[dict]] = {}
    for row in latest.to_dict("records"):
        by_key.setdefault((name_key(str(row["player_display_name"])), row["team"]), []).append(row)
    team_games = {team: sorted(played.loc[played["team"] == team, "game_id"].unique(),
                               key=lambda g: played.loc[played["game_id"] == g, "game_date"].iloc[0])[-2:]
                  for team in played["team"].unique()}
    game_by_team = {}
    for g in games:
        if g.get("gameday") == slate:
            game_by_team[g["home_team"]] = g["game_id"]
            game_by_team[g["away_team"]] = g["game_id"]
    rows = []
    for entry in report["injuries"]:
        status = ESPN_STATUS.get(str(entry.get("report_status", "")).lower())
        team = entry.get("team")
        if status is None or team not in game_by_team:
            continue
        stats["entries"] += 1
        hits = by_key.get((name_key(str(entry.get("player_name", ""))), team), [])
        if len(hits) != 1:
            continue
        stats["matched"] += 1
        pid = hits[0]["player_id"]
        if status in ("out", "suspended"):
            recent = played[(played["player_id"] == pid) & (played["game_id"].isin(team_games.get(team, [])))]
            if recent.empty:
                stats["out_long_term"] += 1
                status = "out_long_term"  # not projected; no teammate boost
            else:
                stats["out_recent"] += 1
        else:
            stats["uncertain"] += 1
        rows.append({"player_id": pid, "team": team, "game_id": game_by_team[team], "report_status": status})
    return rows, stats


def run(dataset_dir: Path, games_file: Path, odds_file: Path, out_dir: Path, slate: str,
        injuries_file: Path | None = None) -> dict:
    history = load_dataset(dataset_dir)
    meta = json.loads((dataset_dir / "nba_dataset_meta.json").read_text())
    games = json.loads(games_file.read_text())
    feed = json.loads(odds_file.read_text())
    quotes = feed.get("quotes", [])
    if meta.get("all_fixture") or not str(meta.get("source", "")).startswith("stats_nba_com"):
        raise ValueError("real official box-score history required; fixture input refused")
    if meta.get("max_game_date", "") >= slate:
        raise ValueError("history reaches slate date; refusing possible outcome leakage")
    if feed.get("slate") != slate or feed.get("_fixture") or not quotes:
        raise ValueError("real quote feed for the requested slate required")
    if any(q.get("book") not in ("FanDuel", "Kalshi") or q.get("_fixture") for q in quotes):
        raise ValueError("this research bridge accepts only real FanDuel or Kalshi quotes")

    test, mapped_quotes, exclusions = pregame_rows(history, games, quotes, slate)
    if test.empty:
        raise ValueError("no quote players resolved to historical players on today's teams")
    report = json.loads(injuries_file.read_text()) if injuries_file and injuries_file.exists() else None
    avail, avail_stats = availability_rows(report, history, games, slate)
    if avail:
        test = add_availability_adjustments(test, avail)
        # long-term absences: not projected, but never a teammate boost
        long_term = {r["player_id"] for r in avail if r["report_status"] == "out_long_term"}
        test.loc[test["player_id"].isin(long_term), "availability_mult"] = 0.0
    ruled_out = set(test.loc[test["availability_mult"] == 0.0, "player_id"]) if avail else set()
    if ruled_out:
        exclusions["ruled_out"] = len(ruled_out)
        test = test[~test["player_id"].isin(ruled_out)].copy()
        mapped_quotes = [q for q in mapped_quotes if q["player_id"] not in ruled_out]
    if test.empty:
        raise ValueError("every quoted player is ruled out")
    train = add_rolling_form(history[history["game_date"] < slate].copy())
    preds = NBAPointsProjector().fit(train).predict(test, history=train)
    by_quote = {(q["player_id"], q["game_id"]): q for q in mapped_quotes}
    records = []
    for p in preds.to_dict("records"):
        q = by_quote[(p["player_id"], p["game_id"])]
        dist = PointsDistribution(mean=p["mean_pts"], sd=p["sd_pts"])
        records.append({
            "player": p["player_display_name"], "team": p["team"], "opponent": p["opponent"],
            "game_id": p["game_id"], "line": q["line"],
            "over_odds": q["over_odds"], "under_odds": q["under_odds"],
            "quote_observed_at": q["observed_at"], "quote_verified": False,
            "model_mean_points": p["mean_pts"], "model_sd_points": p["sd_pts"],
            "model_fair_line": p["fair_line"],
            "model_p_over_uncalibrated": round(dist.p_over(q["line"]), 4),
            "model_p_under_uncalibrated": round(dist.p_under(q["line"]), 4),
            "below_minutes_floor": p["below_minutes_floor"], "model_components": p["explain"],
        })

    # No scored, independently quoted walk-forward sample exists. The board
    # must continue to REFUSE, even though research projections can be shown.
    wf = {"n": 0, "uncalibrated": True}
    board = build_board(preds, mapped_quotes, wf, meta)
    board["slate_date"] = slate
    if board["status"] != "REFUSED":
        raise RuntimeError("unexpected qualified board from uncalibrated one-book input")
    out_dir.mkdir(parents=True, exist_ok=True)
    board_path, receipt = publish_board(board, out_dir)
    artifact = {
        "schema": "propstein.nba_pregame_research.v1", "status": "RESEARCH_ONLY",
        "slate_date": slate, "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_id": "nba-points-v1", "calibrated": False, "qualified_plays": 0,
        "recommendations": [], "source": meta["source"],
        "boxscores_through": meta["max_game_date"], "quote_source": "Kalshi exchange" if quotes[0].get("book") == "Kalshi" else "FanDuel via The Odds API",
        "input_sha256": {
            "player_games": file_sha256(dataset_dir / (
                "nba_player_games.parquet" if (dataset_dir / "nba_player_games.parquet").exists()
                else "nba_player_games.csv")),
            "schedule": file_sha256(games_file), "quotes": file_sha256(odds_file),
        },
        "quote_feed_updated_at": feed.get("updated_at"), "quote_count": len(quotes),
        "independent_books": 1, "independent_books_required": 2,
        "availability_feed": (f"{report.get('source', 'injury report')} as of {report.get('as_of')}: "
                              f"{avail_stats['matched']}/{avail_stats['entries']} slate entries matched, "
                              f"{avail_stats['out_recent']} newly out (teammate boost), "
                              f"{avail_stats['out_long_term']} long-term out, {avail_stats['uncertain']} questionable"
                              if avail else "not supplied; no injury adjustment applied"),
        "matched_count": len(records), "excluded": exclusions,
        "board_status": board["status"], "board_reasons": board["reasons"],
        "board_receipt_sha256": receipt, "records": records,
    }
    research_path = out_dir / f"nba_research_{slate}.json"
    research_path.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    print(f"{slate}: {len(records)}/{len(quotes)} matched; {board['status']} board ({board_path.name})")
    for reason in board["reasons"]:
        print(f"GATE: {reason}")
    print(f"research: {research_path}")
    return artifact


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset-dir", type=Path, required=True)
    ap.add_argument("--games", type=Path, required=True)
    ap.add_argument("--odds", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--injuries", type=Path, help="viewer/nba_injuries_<date>.json (ESPN availability)")
    a = ap.parse_args()
    run(a.dataset_dir, a.games, a.odds, a.out, a.date, a.injuries)
