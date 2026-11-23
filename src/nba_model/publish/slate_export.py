"""Export fail-closed viewer/nba_slate_{date}.json and update viewer/feeds/slate-index.json.

Consumes the research artifact produced by `scripts/build_nba_pregame_research.py`
and emits a deterministic, fail-closed public NBA Points + Minutes slate while
leaving `sports.nfl` in `slate-index.json` untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_nba_slate_rows(research_payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transform nba_research_{date}.json rows into canonical fail-closed viewer slate rows."""
    raw_rows = research_payload if isinstance(research_payload, list) else (
        research_payload.get("rows")
        or research_payload.get("projections")
        or research_payload.get("picks")
        or []
    )
    out: list[dict[str, Any]] = []
    for r in raw_rows:
        explain = r.get("explain") if isinstance(r.get("explain"), dict) else {}
        min_hat = r.get("minutes_hat", explain.get("minutes_hat"))
        sd_min = r.get("sd_minutes", explain.get("sd_minutes", 4.0))
        min_p10 = r.get("minutes_p10")
        min_p90 = r.get("minutes_p90")
        if min_hat is not None:
            mh = float(min_hat)
            sdm = float(sd_min or 4.0)
            if min_p10 is None:
                min_p10 = round(max(0.0, mh - 1.2816 * sdm), 2)
            if min_p90 is None:
                min_p90 = round(min(48.0, mh + 1.2816 * sdm), 2)

        proj_pts = r.get("mean_pts", r.get("proj_points"))
        fair_pts = r.get("fair_line", r.get("fair_line_points"))
        if proj_pts is None:
            continue

        row_dict: dict[str, Any] = {
            "player_id": str(r.get("player_id") or ""),
            "player_display_name": str(r.get("player_display_name") or r.get("player_name") or ""),
            "team": str(r.get("team") or ""),
            "opponent": str(r.get("opponent") or r.get("opponent_team") or ""),
            "game_id": str(r.get("game_id") or ""),
            "position": str(r.get("position") or "G"),
            "minutes_p10": round(float(min_p10), 2) if min_p10 is not None else None,
            "minutes_hat": round(float(min_hat), 2) if min_hat is not None else None,
            "minutes_p90": round(float(min_p90), 2) if min_p90 is not None else None,
            "sd_minutes": round(float(sd_min), 2) if sd_min is not None else None,
            "role_surge": bool(r.get("role_surge", False)),
            "role_surge_reason": str(r.get("role_surge_reason") or ""),
            "proj_points": round(float(proj_pts), 2),
            "fair_line_points": round(float(fair_pts), 1) if fair_pts is not None else round(float(proj_pts), 1),
            "sd_points": round(float(r.get("sd_pts", r.get("sd_points", 5.0))), 2),
            "market": "points",
            # Fail-closed invariants (EDGE_GATE.md NBA Kalshi CLV gate)
            "actionable": False,
            "edge_pct": None,
            "kelly_stake": 0.0,
            "gate_status": "RESEARCH_ONLY_UNVERIFIED",
        }
        # Pass through additive multi-stat markets (REB, AST, 3PM, PRA, PR, PA, RA) when present
        for m in ("rebounds", "assists", "threes", "pra", "pr", "pa", "ra"):
            for prefix in ("proj_", "sd_", "fair_line_", "p_over_fair_"):
                k = f"{prefix}{m}"
                if r.get(k) is not None:
                    row_dict[k] = round(float(r[k]), 4 if prefix == "p_over_fair_" else 2)
        if r.get("p_over_fair_points") is not None:
            row_dict["p_over_fair_points"] = round(float(r["p_over_fair_points"]), 4)
        out.append(row_dict)

    out.sort(key=lambda row: (-float(row["proj_points"]), row["player_display_name"]))
    return out


def export_nba_slate_and_index(
    research_path: Path,
    date_str: str,
    viewer_dir: Path,
) -> dict[str, Any]:
    """Write viewer/nba_slate_{date}.json and update sports.nba in viewer/feeds/slate-index.json."""
    research_payload = json.loads(Path(research_path).read_text(encoding="utf-8"))
    rows = build_nba_slate_rows(research_payload)

    viewer_dir = Path(viewer_dir)
    viewer_dir.mkdir(parents=True, exist_ok=True)
    slate_filename = f"nba_slate_{date_str}.json"
    slate_path = viewer_dir / slate_filename
    slate_path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    index_path = viewer_dir / "feeds" / "slate-index.json"
    index_updated = False
    if index_path.exists():
        idx = json.loads(index_path.read_text(encoding="utf-8"))
        sports = idx.setdefault("sports", {})
        nba = sports.setdefault("nba", {})
        slates = nba.setdefault("slates", {})
        existing = slates.get(date_str) if isinstance(slates.get(date_str), dict) else {}
        slates[date_str] = {
            **existing,
            "slate": slate_filename,
            "modelContextStatus": "AVAILABLE" if rows else "UNAVAILABLE",
            "actionLocked": False,
            "marketOnly": False if rows else True,
        }
        index_path.write_text(json.dumps(idx, indent=2) + "\n", encoding="utf-8")
        index_updated = True

    return {
        "slate_path": str(slate_path),
        "row_count": len(rows),
        "index_updated": index_updated,
    }
