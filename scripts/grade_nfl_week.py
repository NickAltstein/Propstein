#!/usr/bin/env python3
"""Grade one NFL week of model projections against results and the market.

    python3 scripts/grade_nfl_week.py --season 2026 --week 9 [--stats data/stats_2026.parquet]

For every projected player (viewer/slate_<season>_w<week>.json, eligible rows
with model parameters) and market (receptions, receiving yards):
  * accuracy: mean absolute error of the model mean, and how often the
    result landed over the model's fair line (a calibrated median -> ~50%);
  * vs market: where a Polymarket quote exists before kickoff
    (viewer/api/lines/<player>/<market>.json), the model's P(over) from the
    published NegBin-Gamma fit and the de-vigged market P(over) are scored
    with the same Brier rule, and the model's side is settled at that price.
Actual stats come from nflverse weekly player stats (downloaded if missing).
A projected player with no stat row is VOID (did not play).
Writes viewer/feeds/grades/nfl_<season>_w<week>.json and upserts
nfl_history.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from model import ReceivingModel  # noqa: E402

ALPHA, K = 0.1097, 1.555  # published fit (src/freeze_slate.py, viewer/js/sports/nfl.js)
STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.parquet"
MARKETS = {"receptions": ("receptions", "fair_line_rec", "receptions.json"),
           "receiving_yards": ("receiving_yards", "fair_line_yards", "receiving-yards.json")}
ET = ZoneInfo("America/New_York")


def implied(odds: float) -> float:
    return 100.0 / (odds + 100.0) if odds > 0 else -odds / (-odds + 100.0)


def win_units(odds: float) -> float:
    return odds / 100.0 if odds > 0 else 100.0 / -odds


def model_p_over(m: ReceivingModel, market: str, mu_rec: float, mu_ypr: float, line: float) -> float:
    return float(m.p_rec_over(mu_rec, line) if market == "receptions" else m.p_yards_over(mu_rec, mu_ypr, line))


def pregame_quote(player_id: str, file: str, kickoff: datetime, lines_dir: Path) -> dict | None:
    path = lines_dir / player_id / file
    if not path.exists():
        return None
    series = json.loads(path.read_text()).get("series", {})
    best = None
    for book, quotes in series.items():
        for q in quotes:
            try:
                t = datetime.fromisoformat(str(q["t"]).replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if t < kickoff and q.get("over") and q.get("under") and (best is None or t > best["t"]):
                best = {**q, "t": t, "book": book}
    return best


def grade(season: int, week: int, stats: pd.DataFrame, root: Path = ROOT) -> dict:
    viewer = root / "viewer"
    slate = json.loads((viewer / f"slate_{season}_w{week}.json").read_text())
    games = {g["game_id"]: g for g in json.loads((viewer / f"games_{season}_w{week}.json").read_text())}
    wk = stats[(stats["season"] == season) & (stats["week"] == week)]
    if wk.empty:
        raise SystemExit(f"no nflverse stats for {season} week {week} yet")
    actual = wk.set_index("player_id")[["receptions", "receiving_yards"]].to_dict("index")
    m = ReceivingModel(None, None, ALPHA, K, None, None, [])
    rows = []
    for r in slate:
        if r.get("excl_reason") or not r.get("mu_rec") or not r.get("mu_ypr"):
            continue
        g = games.get(r.get("game_id"), {})
        try:
            kickoff = datetime.fromisoformat(f"{g['gameday']}T{g['gametime']}").replace(tzinfo=ET)
        except (KeyError, ValueError):
            continue
        a = actual.get(r["player_id"])
        for market, (stat, fair_key, file) in MARKETS.items():
            mu_rec, mu_ypr = float(r["mu_rec"]), float(r["mu_ypr"])
            mean = mu_rec if market == "receptions" else mu_rec * mu_ypr
            fair = r.get(fair_key)
            row = {"player": r.get("player_display_name"), "player_id": r["player_id"], "team": r.get("team"),
                   "market": market, "model_mean": round(mean, 2), "fair_line": fair}
            if a is None:
                rows.append({**row, "result": "VOID"})
                continue
            x = float(a[stat] or 0)
            row.update(actual=x, abs_error=round(abs(x - mean), 2),
                       over_fair=None if fair is None else x > float(fair))
            q = pregame_quote(r["player_id"], file, kickoff, viewer / "api" / "lines")
            if q:
                line = float(q["line"])
                po, pu = implied(q["over"]), implied(q["under"])
                p_mkt = po / (po + pu)
                p_mod = model_p_over(m, market, mu_rec, mu_ypr, line)
                over = x > line
                side = "OVER" if p_mod - p_mkt >= (1 - p_mod) - (1 - p_mkt) else "UNDER"
                odds = q["over"] if side == "OVER" else q["under"]
                won = over if side == "OVER" else not over
                row.update(quote={"book": q["book"], "line": line, "over": q["over"], "under": q["under"],
                                  "observed_at": q["t"].isoformat()},
                           p_model_over=round(p_mod, 4), p_market_over=round(p_mkt, 4), side=side,
                           gap=round(abs(p_mod - p_mkt), 4), result="WIN" if won else "LOSS",
                           units=round(win_units(odds) if won else -1.0, 4),
                           brier_model=round((p_mod - over) ** 2, 4), brier_market=round((p_mkt - over) ** 2, 4))
            else:
                row["result"] = "NO_QUOTE"
            rows.append(row)
    return {"season": season, "week": week, "rows": rows, **summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    out = {}
    for market in MARKETS:
        rs = [r for r in rows if r["market"] == market]
        played = [r for r in rs if r["result"] != "VOID"]
        quoted = [r for r in played if r["result"] in ("WIN", "LOSS")]
        fair = [r for r in played if r.get("over_fair") is not None]
        n_q = len(quoted)
        out[market] = {
            "projected": len(rs), "void": len(rs) - len(played),
            "mae": round(sum(r["abs_error"] for r in played) / len(played), 2) if played else None,
            "over_fair_rate": round(sum(r["over_fair"] for r in fair) / len(fair), 3) if fair else None,
            "quoted": n_q,
            "brier_model": round(sum(r["brier_model"] for r in quoted) / n_q, 4) if n_q else None,
            "brier_market": round(sum(r["brier_market"] for r in quoted) / n_q, 4) if n_q else None,
            "record": {"wins": sum(r["result"] == "WIN" for r in quoted),
                       "losses": sum(r["result"] == "LOSS" for r in quoted),
                       "units": round(sum(r["units"] for r in quoted), 3)},
        }
    return {"markets": out}


def finished_ungraded_weeks(out: Path, now: datetime, root: Path = ROOT) -> list[tuple[int, int]]:
    """(season, week) slates whose last game kicked off 12h+ ago and have no grade yet."""
    index = json.loads((root / "viewer" / "feeds" / "slate-index.json").read_text())
    hist = out / "nfl_history.jsonl"
    done = {(r["season"], r["week"]) for r in (json.loads(l) for l in hist.read_text().splitlines() if l.strip())} \
        if hist.exists() else set()
    weeks = []
    for cfg in index["sports"]["nfl"]["slates"].values():
        season, week = cfg.get("season"), cfg.get("week")
        if not (season and week) or (season, week) in done:
            continue
        games_path = root / "viewer" / str(cfg.get("games", ""))
        if not games_path.is_file() or not (root / "viewer" / f"slate_{season}_w{week}.json").exists():
            continue
        kicks = [datetime.fromisoformat(f"{g['gameday']}T{g['gametime']}").replace(tzinfo=ET)
                 for g in json.loads(games_path.read_text()) if g.get("gameday") and g.get("gametime")]
        if kicks and (now - max(kicks)).total_seconds() >= 12 * 3600:
            weeks.append((season, week))
    return sorted(weeks)


def load_stats(season: int, path: Path | None, refresh: bool) -> pd.DataFrame:
    stats_path = path or ROOT / "data" / f"stats_{season}.parquet"
    if refresh or not stats_path.exists():
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(STATS_URL.format(season=season), stats_path)
    return pd.read_parquet(stats_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--auto", action="store_true", help="grade every finished, ungraded slate (fresh stats)")
    ap.add_argument("--stats", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "viewer" / "feeds" / "grades")
    a = ap.parse_args(argv)
    if a.auto:
        todo = finished_ungraded_weeks(a.out, datetime.now(ET))
        if not todo:
            print("[grade_nfl] nothing to grade (no finished, ungraded week)")
            return 0
        code = 0
        for season, week in todo:
            stats = load_stats(season, a.stats, refresh=True)
            if stats[(stats["season"] == season) & (stats["week"] == week)].empty:
                print(f"[grade_nfl] {season} W{week}: nflverse stats not published yet; will retry")
                continue
            code |= main(["--season", str(season), "--week", str(week), "--out", str(a.out)]
                         + (["--stats", str(a.stats)] if a.stats else []))
        return code
    if not (a.season and a.week):
        ap.error("--season and --week are required (or --auto)")
    report = grade(a.season, a.week, load_stats(a.season, a.stats, refresh=False))
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / f"nfl_{a.season}_w{a.week}.json").write_text(json.dumps(
        {"schema": "propstein.grade.nfl.v1", "model": f"negbin-gamma alpha={ALPHA} k={K}", **report}, indent=1, default=str) + "\n")
    hist = a.out / "nfl_history.jsonl"
    rows = [json.loads(l) for l in hist.read_text().splitlines() if l.strip()] if hist.exists() else []
    rows = [r for r in rows if (r["season"], r["week"]) != (a.season, a.week)]
    rows.append({"season": a.season, "week": a.week, "markets": report["markets"]})
    rows.sort(key=lambda r: (r["season"], r["week"]))
    hist.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    for market, s in report["markets"].items():
        print(f"[grade_nfl] {a.season} W{a.week} {market}: {s['projected']} projected, {s['void']} void, "
              f"MAE {s['mae']}, over-fair {s['over_fair_rate']}, quoted {s['quoted']} | Brier model "
              f"{s['brier_model']} vs market {s['brier_market']} | {s['record']['wins']}-{s['record']['losses']} "
              f"({s['record']['units']:+.2f}u)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
