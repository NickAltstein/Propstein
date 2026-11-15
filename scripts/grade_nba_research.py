#!/usr/bin/env python3
"""Grade one night of NBA points research against final box scores.

    python3 scripts/grade_nba_research.py --date 2026-11-14 \
        [--research viewer/feeds/nba_model/nba_research_<date>.json] \
        [--dataset-dir src/nba_model/out] [--out viewer/feeds/grades]

The honest test is the market, not a win rate: for every quoted line the
grader scores the model's P(over) and the de-vigged market P(over) with the
same Brier rule. It also settles the model's side at the quoted price (1u,
American odds) for every row and for the 10 biggest model-vs-market gaps, so
"top 10" lists are tracked exactly as they were presented.

A player with no box-score row or 0 minutes is VOID. Writes
nba_points_<date>.json and upserts that date in nba_points_history.jsonl.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def name_key(value: str) -> str:
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s))


def implied(odds: float) -> float:
    return 100.0 / (odds + 100.0) if odds > 0 else -odds / (-odds + 100.0)


def win_units(odds: float) -> float:
    return odds / 100.0 if odds > 0 else 100.0 / -odds


def grade_record(rec: dict, actual: float | None) -> dict:
    po, pu = implied(rec["over_odds"]), implied(rec["under_odds"])
    p_mkt = po / (po + pu)
    p_model = float(rec["model_p_over_uncalibrated"])
    over_gap, under_gap = p_model - p_mkt, (1 - p_model) - (1 - p_mkt)
    side = "OVER" if over_gap >= under_gap else "UNDER"
    gap = max(over_gap, under_gap)
    odds = rec["over_odds"] if side == "OVER" else rec["under_odds"]
    out = {"player": rec["player"], "team": rec["team"], "line": rec["line"], "side": side,
           "odds": odds, "gap": round(gap, 4), "p_model_over": round(p_model, 4),
           "p_market_over": round(p_mkt, 4), "actual": actual}
    if actual is None:
        return {**out, "result": "VOID", "units": 0.0}
    went_over = actual > rec["line"]
    won = went_over if side == "OVER" else not went_over
    return {**out, "result": "WIN" if won else "LOSS", "units": round(win_units(odds) if won else -1.0, 4),
            "brier_model": round((p_model - went_over) ** 2, 4), "brier_market": round((p_mkt - went_over) ** 2, 4)}


def summarize(rows: list[dict]) -> dict:
    played = [r for r in rows if r["result"] != "VOID"]
    def record(rs):
        return {"n": len(rs), "wins": sum(r["result"] == "WIN" for r in rs),
                "losses": sum(r["result"] == "LOSS" for r in rs), "units": round(sum(r["units"] for r in rs), 3)}
    top10 = sorted(rows, key=lambda r: -r["gap"])[:10]
    n = len(played)
    bm = sum(r["brier_model"] for r in played) / n if n else None
    bk = sum(r["brier_market"] for r in played) / n if n else None
    return {"graded": n, "void": len(rows) - n,
            "brier_model": round(bm, 4) if n else None, "brier_market": round(bk, 4) if n else None,
            "model_beats_market": (bm < bk) if n else None,
            "all_rows": record(played), "top10_by_gap": {**record([r for r in top10 if r["result"] != "VOID"]),
                                                       "void": sum(r["result"] == "VOID" for r in top10)}}


def actuals_for(date: str, dataset_dir: Path) -> dict[tuple[str, str], float | None]:
    path = dataset_dir / "nba_player_games.parquet"
    df = pd.read_parquet(path) if path.exists() else pd.read_csv(dataset_dir / "nba_player_games.csv")
    day = df[df["game_date"].astype(str).str[:10] == date]
    if day.empty:
        raise SystemExit(f"no box scores for {date} in {dataset_dir} (run prepare through {date} first)")
    out = {}
    for r in day.to_dict("records"):
        played = not r.get("dnp") and float(r.get("minutes") or 0) > 0
        out[(name_key(r["player_display_name"]), r["team"])] = float(r["pts"]) if played else None
    return out


def upsert_history(path: Path, entry: dict) -> list[dict]:
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
    rows = [r for r in rows if r.get("date") != entry["date"]] + [entry]
    rows.sort(key=lambda r: r["date"])
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return rows


def cumulative(history: list[dict]) -> dict:
    n = sum(h["graded"] for h in history)
    if not n:
        return {"nights": len(history), "graded": 0}
    bm = sum(h["brier_model"] * h["graded"] for h in history if h["graded"]) / n
    bk = sum(h["brier_market"] * h["graded"] for h in history if h["graded"]) / n
    t = {k: sum(h["top10_by_gap"][k] for h in history) for k in ("wins", "losses")}
    a = {k: sum(h["all_rows"][k] for h in history) for k in ("wins", "losses")}
    return {"nights": len(history), "graded": n, "brier_model": round(bm, 4), "brier_market": round(bk, 4),
            "all_rows": {**a, "units": round(sum(h["all_rows"]["units"] for h in history), 3)},
            "top10_by_gap": {**t, "units": round(sum(h["top10_by_gap"]["units"] for h in history), 3)},
            "model_beats_market": bm < bk,
            "verdict": ("model Brier below market: worth a calibration review" if bm < bk else
                        "market more accurate than the model: no edge; board stays refused")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", required=True)
    ap.add_argument("--research", type=Path)
    ap.add_argument("--dataset-dir", type=Path, default=ROOT / "src" / "nba_model" / "out")
    ap.add_argument("--out", type=Path, default=ROOT / "viewer" / "feeds" / "grades")
    a = ap.parse_args(argv)
    research_path = a.research or ROOT / "viewer" / "feeds" / "nba_model" / f"nba_research_{a.date}.json"
    research = json.loads(research_path.read_text())
    if research.get("slate_date") != a.date:
        raise SystemExit(f"{research_path} is for {research.get('slate_date')}, not {a.date}")
    actual = actuals_for(a.date, a.dataset_dir)
    rows = [grade_record(r, actual.get((name_key(r["player"]), r["team"]))) for r in research["records"]]
    summary = summarize(rows)
    a.out.mkdir(parents=True, exist_ok=True)
    report = {"schema": "propstein.grade.nba_points.v1", "date": a.date, "model_id": research.get("model_id"),
              "calibrated": research.get("calibrated"), "quote_source": research.get("quote_source"),
              "availability_feed": research.get("availability_feed"), **summary, "rows": rows}
    (a.out / f"nba_points_{a.date}.json").write_text(json.dumps(report, indent=1) + "\n")
    history = upsert_history(a.out / "nba_points_history.jsonl", {"date": a.date, **summary})
    cum = cumulative(history)
    (a.out / "nba_points_summary.json").write_text(json.dumps(cum, indent=1) + "\n")
    t = summary["top10_by_gap"]
    print(f"[grade_nba] {a.date}: {summary['graded']} graded, {summary['void']} void | Brier model "
          f"{summary['brier_model']} vs market {summary['brier_market']} | top10 {t['wins']}-{t['losses']} "
          f"({t['units']:+.2f}u) | season: {cum['graded']} graded, Brier {cum.get('brier_model')} vs {cum.get('brier_market')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
