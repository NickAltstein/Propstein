"""Slate integration for the NFL Anytime Touchdown model (Track A — Day 3).

Produces:
  1. `viewer/td_board_{season}_w{week}.json` — deterministic ranked TD board
     (one row per active player-game, excluding CONTEXT_MISSING games).
  2. Additive TD fields merged onto `viewer/slate_{season}_w{week}.json`
     without overwriting any existing receiving model field.

Every emitted TD board row passes through `enforce_td_fail_closed()`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from src.td_features import (
    DATA_DIR,
    build_td_features,
    enforce_td_fail_closed,
    extract_pbp_player_weeks,
    load_pbp,
)
from src.td_model import (
    DEFAULT_IMPLIED_TEAM_TOTAL,
    TouchdownModel,
)

TD_BOARD_SCHEMA_VERSION = "td-board.v1"

# Exact additive keys merged onto priced rows of viewer/slate_{season}_w{week}.json.
# None of these collide with ReceivingModel output fields.
TD_SLATE_ADDITIVE_FIELDS = (
    "td_mean",
    "p_anytime_td",
    "p_2plus_td",
    "p_first_td",
    "td_confidence_tier",
    "fair_odds_anytime",
    "fair_odds_2plus",
    "fair_odds_first",
    "td_role_surge_z",
    "td_vacated_rz_opps",
    "td_gate_status",
)


def _clean_json_val(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        if not np.isfinite(float(v)):
            return None
        return round(float(v), 6)
    return v


def _attach_week_totals(
    feats: pd.DataFrame,
    games: pd.DataFrame | None,
    season: int,
    week: int,
    exclude_games: set[str],
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Attach implied_team_total, implied_opp_total, and game_id for active games."""
    out = feats.copy()
    team_tot: dict[str, float] = {}
    opp_tot: dict[str, float] = {}
    team_gid: dict[str, str] = {}
    team_opp: dict[str, str] = {}

    if games is not None and not games.empty:
        g = games[(games["season"] == season) & (games["week"] == week)]
        for _, r in g.iterrows():
            gid = str(r.get("game_id") or "")
            if gid and gid in exclude_games:
                continue
            h, a = str(r["home_team"]), str(r["away_team"])
            team_gid[h] = gid
            team_gid[a] = gid
            team_opp[h] = a
            team_opp[a] = h
            tot, spr = r.get("total_line"), r.get("spread_line")
            if pd.notna(tot) and pd.notna(spr):
                tot_f, spr_f = float(tot), float(spr)
                home_imp = (tot_f + spr_f) / 2.0
                away_imp = (tot_f - spr_f) / 2.0
                team_tot[h] = home_imp
                opp_tot[h] = away_imp
                team_tot[a] = away_imp
                opp_tot[a] = home_imp

    if team_gid:
        out = out[out["team"].isin(team_gid.keys())].copy()
        out["game_id"] = out["team"].map(team_gid)
        out["opponent_team"] = out["team"].map(team_opp)
    else:
        out["game_id"] = out.get("game_id", "")

    out["implied_team_total"] = out["team"].map(team_tot).fillna(DEFAULT_IMPLIED_TEAM_TOTAL)
    out["implied_opp_total"] = out["team"].map(opp_tot).fillna(DEFAULT_IMPLIED_TEAM_TOTAL)
    return out.reset_index(drop=True), team_gid


def build_td_predictions_for_week(
    player_weeks: pd.DataFrame,
    season: int,
    week: int,
    *,
    games: pd.DataFrame | None = None,
    exclude_games: Iterable[str] | None = None,
    injuries: list[dict] | None = None,
    min_train_rows: int = 30,
) -> pd.DataFrame:
    """Fit TouchdownModel strictly on prior weeks and predict active games for (season, week)."""
    excl_set = {str(gid) for gid in (exclude_games or []) if gid}
    pw = player_weeks.sort_values(["season", "week", "player_id"]).reset_index(drop=True)

    # Build strictly prior labeled training frames (season < S or season == S and week < W)
    prior_pw = pw[(pw["season"] < season) | ((pw["season"] == season) & (pw["week"] < week))]
    all_sw = sorted({(int(s), int(w)) for s, w in zip(prior_pw["season"], prior_pw["week"])})

    train_snaps: list[pd.DataFrame] = []
    for s, w in all_sw:
        actual = prior_pw[(prior_pw["season"] == s) & (prior_pw["week"] == w)][
            ["player_id", "team", "opponent_team", "total_tds"]
        ]
        if actual.empty:
            continue
        matchups = dict(zip(actual["team"].astype(str), actual["opponent_team"].astype(str)))
        f = build_td_features(prior_pw, target_season=s, target_week=w, target_matchups=matchups)
        if f.empty:
            continue
        f, _ = _attach_week_totals(f, games, s, w, exclude_games=set())
        joined = f.merge(actual[["player_id", "total_tds"]], on="player_id", how="inner")
        if not joined.empty:
            train_snaps.append(joined)

    if not train_snaps:
        return enforce_td_fail_closed(pd.DataFrame())

    train_df = pd.concat(train_snaps, ignore_index=True)
    if len(train_df) < min_train_rows:
        return enforce_td_fail_closed(pd.DataFrame())

    # Build target-week features strictly from prior_pw
    target_matchups: dict[str, str] = {}
    if games is not None and not games.empty:
        g_wk = games[(games["season"] == season) & (games["week"] == week)]
        for _, r in g_wk.iterrows():
            gid = str(r.get("game_id") or "")
            if gid and gid in excl_set:
                continue
            h, a = str(r["home_team"]), str(r["away_team"])
            target_matchups[h] = a
            target_matchups[a] = h

    eval_feats = build_td_features(
        prior_pw,
        target_season=season,
        target_week=week,
        target_matchups=target_matchups or None,
        injuries=injuries,
    )
    if eval_feats.empty:
        return enforce_td_fail_closed(pd.DataFrame())

    eval_feats, _ = _attach_week_totals(eval_feats, games, season, week, exclude_games=excl_set)
    if eval_feats.empty:
        return enforce_td_fail_closed(pd.DataFrame())

    model = TouchdownModel().fit(train_df, target_col="total_tds")
    preds = model.predict(eval_feats)
    preds = preds.sort_values(["p_anytime_td", "player_id"], ascending=[False, True]).reset_index(drop=True)
    return enforce_td_fail_closed(preds)


def format_td_board_payload(
    preds: pd.DataFrame,
    season: int,
    week: int,
    *,
    excluded_games: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build deterministic JSON-serializable payload for viewer/td_board_{season}_w{week}.json."""
    rows: list[dict[str, Any]] = []
    for _, r in preds.iterrows():
        rows.append({
            "player_id": str(r["player_id"]),
            "player_name": str(r.get("player_name") or ""),
            "team": str(r["team"]),
            "opponent_team": str(r.get("opponent_team") or ""),
            "game_id": str(r.get("game_id") or ""),
            "position": str(r.get("position") or ""),
            "td_mean": _clean_json_val(r["td_mean"]),
            "p_anytime_td": _clean_json_val(r["p_anytime_td"]),
            "p_2plus_td": _clean_json_val(r["p_2plus_td"]),
            "p_first_td": _clean_json_val(r["p_first_td"]),
            "team_td_share": _clean_json_val(r.get("team_td_share")),
            "confidence_tier": str(r.get("confidence_tier") or "THIN_SAMPLE"),
            "fair_odds_anytime": _clean_json_val(r.get("fair_odds_anytime")),
            "fair_odds_2plus": _clean_json_val(r.get("fair_odds_2plus")),
            "fair_odds_first": _clean_json_val(r.get("fair_odds_first")),
            "role_surge_z": _clean_json_val(r.get("role_surge_z", 0.0)),
            "vacated_rz_opps_team": _clean_json_val(r.get("vacated_rz_opps_team", 0.0)),
            "teammate_out_flag": int(r.get("teammate_out_flag", 0)),
            "games_prior": int(r.get("games_prior", 0)),
            # Fail-closed invariants
            "actionable": False,
            "edge_pct": None,
            "kelly_stake": 0.0,
            "gate_status": "RESEARCH_ONLY_UNVERIFIED",
        })

    return {
        "schema_version": TD_BOARD_SCHEMA_VERSION,
        "season": int(season),
        "week": int(week),
        "excluded_games": sorted({str(g) for g in (excluded_games or []) if g}),
        "gate_status": "RESEARCH_ONLY_UNVERIFIED",
        "actionable_count": 0,
        "row_count": len(rows),
        "rows": rows,
    }


def enrich_slate_rows_with_td(
    slate_rows: list[dict[str, Any]],
    td_board_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add TD fields to priced slate rows without modifying any existing receiving field."""
    by_pid = {str(r["player_id"]): r for r in td_board_rows if r.get("player_id")}
    enriched: list[dict[str, Any]] = []

    for row in slate_rows:
        out_row = dict(row)
        # Never attach TD probabilities to CONTEXT_MISSING unpriced rows
        if out_row.get("context_status") == "CONTEXT_MISSING":
            enriched.append(out_row)
            continue

        pid = str(out_row.get("player_id") or "")
        td = by_pid.get(pid)
        if td is not None:
            additions = {
                "td_mean": td["td_mean"],
                "p_anytime_td": td["p_anytime_td"],
                "p_2plus_td": td["p_2plus_td"],
                "p_first_td": td["p_first_td"],
                "td_confidence_tier": td["confidence_tier"],
                "fair_odds_anytime": td["fair_odds_anytime"],
                "fair_odds_2plus": td["fair_odds_2plus"],
                "fair_odds_first": td["fair_odds_first"],
                "td_role_surge_z": td["role_surge_z"],
                "td_vacated_rz_opps": td["vacated_rz_opps_team"],
                "td_gate_status": td["gate_status"],
            }
            for k, v in additions.items():
                if k not in out_row:
                    out_row[k] = v
        enriched.append(out_row)

    return enriched


def write_td_board_and_enrich_slate(
    player_weeks: pd.DataFrame,
    season: int,
    week: int,
    viewer_dir: Path,
    *,
    games: pd.DataFrame | None = None,
    exclude_games: Iterable[str] | None = None,
    injuries: list[dict] | None = None,
    min_train_rows: int = 30,
) -> dict[str, Any]:
    """Generate viewer/td_board_{season}_w{week}.json and enrich viewer/slate_{season}_w{week}.json."""
    preds = build_td_predictions_for_week(
        player_weeks,
        season,
        week,
        games=games,
        exclude_games=exclude_games,
        injuries=injuries,
        min_train_rows=min_train_rows,
    )
    payload = format_td_board_payload(preds, season, week, excluded_games=exclude_games)

    viewer_dir = Path(viewer_dir)
    viewer_dir.mkdir(parents=True, exist_ok=True)
    td_board_path = viewer_dir / f"td_board_{season}_w{week}.json"
    td_board_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    slate_path = viewer_dir / f"slate_{season}_w{week}.json"
    enriched_count = 0
    if slate_path.exists():
        slate_rows = json.loads(slate_path.read_text(encoding="utf-8"))
        if isinstance(slate_rows, list):
            enriched = enrich_slate_rows_with_td(slate_rows, payload["rows"])
            enriched_count = sum(1 for r in enriched if "p_anytime_td" in r)
            slate_path.write_text(json.dumps(enriched, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "td_board_path": str(td_board_path),
        "td_rows": payload["row_count"],
        "slate_enriched_rows": enriched_count,
    }


def run_td_slate_pipeline(
    season: int,
    week: int,
    viewer_dir: Path,
    *,
    games: pd.DataFrame | None = None,
    exclude_games: Iterable[str] | None = None,
    injuries_path: Path | None = None,
    data_dir: str = DATA_DIR,
) -> dict[str, Any] | None:
    """Thin autopilot entrypoint: loads local PBP parquets (season-1, season) if present."""
    seasons_to_load = [yr for yr in (season - 1, season) if (Path(data_dir) / f"play_by_play_{yr}.parquet").exists()]
    if not seasons_to_load:
        return None

    pbp = load_pbp(seasons_to_load, data_dir=data_dir)
    pw = extract_pbp_player_weeks(pbp)

    injuries: list[dict] = []
    if injuries_path is not None and Path(injuries_path).exists():
        raw_inj = json.loads(Path(injuries_path).read_text(encoding="utf-8"))
        if isinstance(raw_inj, list):
            injuries = raw_inj
        elif isinstance(raw_inj, dict):
            injuries = raw_inj.get("injuries") or raw_inj.get("players") or []

    return write_td_board_and_enrich_slate(
        pw,
        season,
        week,
        viewer_dir=viewer_dir,
        games=games,
        exclude_games=exclude_games,
        injuries=injuries,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate NFL TD board and enrich slate JSON.")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--viewer-dir", type=Path, default=Path("viewer"))
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--exclude-game", action="append", default=[])
    args = parser.parse_args()

    games_path = Path(args.data_dir) / "games.csv"
    games = pd.read_csv(games_path) if games_path.exists() else None
    inj_path = args.viewer_dir / f"injuries_{args.season}_w{args.week}.json"

    res = run_td_slate_pipeline(
        args.season,
        args.week,
        args.viewer_dir,
        games=games,
        exclude_games=args.exclude_game,
        injuries_path=inj_path if inj_path.exists() else None,
        data_dir=args.data_dir,
    )
    if res is None:
        print(f"[td_slate] skipped: no play_by_play_{args.season-1}/{args.season}.parquet in {args.data_dir}")
    else:
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
