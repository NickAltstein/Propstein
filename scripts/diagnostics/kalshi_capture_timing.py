#!/usr/bin/env python3
"""kalshi_capture_timing.py - Diagnostic script for quote capture timing offsets.

Computes quote offset = quoted_at - scheduled_start in UTC for:
1. Kalshi NBA stored feeds (viewer/nba_odds_*.json joined to viewer/nba_games_*.json)
2. Polymarket NFL line-history series (viewer/api/lines/*/*.json joined to viewer/games_2026_w*.json)

Also audits git history for in-place file overwrites where later post-tip runs
overwrote earlier pre-tip quotes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent.parent
VIEWER = ROOT / "viewer"
ET = ZoneInfo("America/New_York")


def parse_iso(ts_str: str) -> datetime:
    return datetime.fromisoformat(str(ts_str).replace("Z", "+00:00")).astimezone(timezone.utc)


def analyze_kalshi_odds() -> tuple[list[dict], list[dict]]:
    """Analyze all stored Kalshi NBA feeds against their scheduled tipoff."""
    runs = []
    all_quotes = []

    files = sorted(VIEWER.glob("nba_odds_*.json"))
    for file_path in files:
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("source") != "kalshi":
            continue

        slate = data.get("slate")
        updated_at = data.get("updated_at")
        run_dt = parse_iso(updated_at) if updated_at else None

        games_path = VIEWER / f"nba_games_{slate}.json"
        tip_map = {}
        if games_path.is_file():
            try:
                games = json.loads(games_path.read_text(encoding="utf-8"))
                for g in games:
                    if g.get("game_id") and g.get("tipoff_utc"):
                        tip_map[g["game_id"]] = parse_iso(g["tipoff_utc"])
            except Exception:
                pass

        quotes = data.get("quotes") or []
        pre_count = 0
        post_count = 0
        unknown_count = 0
        games_covered = set()
        offsets = []

        for q in quotes:
            gid = q.get("game_id")
            if gid:
                games_covered.add(gid)
            obs_str = q.get("observed_at") or updated_at
            if not obs_str or gid not in tip_map:
                unknown_count += 1
                all_quotes.append({
                    "source": "kalshi",
                    "file": file_path.name,
                    "quote_id": q.get("quote_id"),
                    "game_id": gid,
                    "quoted_at": parse_iso(obs_str) if obs_str else None,
                    "scheduled_start": tip_map.get(gid),
                    "offset_hours": None,
                    "status": "unknown",
                })
                continue

            quoted_at = parse_iso(obs_str)
            start = tip_map[gid]
            offset_sec = (quoted_at - start).total_seconds()
            offset_h = offset_sec / 3600.0
            offsets.append(offset_h)

            status = "pre_start" if offset_sec < 0 else "post_start"
            if offset_sec < 0:
                pre_count += 1
            else:
                post_count += 1

            all_quotes.append({
                "source": "kalshi",
                "file": file_path.name,
                "quote_id": q.get("quote_id"),
                "game_id": gid,
                "quoted_at": quoted_at,
                "scheduled_start": start,
                "offset_hours": offset_h,
                "status": status,
            })

        runs.append({
            "source": "kalshi",
            "file": file_path.name,
            "slate": slate,
            "run_time": updated_at,
            "total_quotes": len(quotes),
            "games_covered": len(games_covered),
            "pre_start": pre_count,
            "post_start": post_count,
            "unknown": unknown_count,
            "min_offset_h": min(offsets) if offsets else None,
            "median_offset_h": sorted(offsets)[len(offsets) // 2] if offsets else None,
            "max_offset_h": max(offsets) if offsets else None,
        })

    return runs, all_quotes


def analyze_polymarket_line_history() -> tuple[list[dict], list[dict]]:
    """Analyze Polymarket NFL line-history captures near game start."""
    # Find kickoff of 2026_09_BUF_MIN
    buf_min_start = None
    games_w9_path = VIEWER / "games_2026_w9.json"
    if games_w9_path.is_file():
        games_w9 = json.loads(games_w9_path.read_text(encoding="utf-8"))
        for g in games_w9:
            if g.get("game_id") == "2026_09_BUF_MIN":
                gameday = g.get("gameday")
                gametime = g.get("gametime")
                if gameday and gametime:
                    buf_min_start = datetime.strptime(
                        f"{gameday} {gametime}", "%Y-%m-%d %H:%M"
                    ).replace(tzinfo=ET).astimezone(timezone.utc)
                break

    # Find players on 2026_09_BUF_MIN
    buf_min_players = set()
    slate_w9_path = VIEWER / "slate_2026_w9.json"
    if slate_w9_path.is_file():
        slate_w9 = json.loads(slate_w9_path.read_text(encoding="utf-8"))
        for r in slate_w9:
            if r.get("game_id") == "2026_09_BUF_MIN" and r.get("player_id"):
                buf_min_players.add(r["player_id"])

    lines_dir = VIEWER / "api" / "lines"
    quotes = []
    offsets = []
    if lines_dir.is_dir() and buf_min_start:
        for p_dir in lines_dir.glob("*"):
            pid = p_dir.name
            if pid not in buf_min_players:
                continue
            for m_file in p_dir.glob("*.json"):
                try:
                    data = json.loads(m_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                for pt in data.get("series", {}).get("polymarket", []):
                    t_str = pt.get("t")
                    if not t_str:
                        continue
                    t_dt = parse_iso(t_str)
                    diff_h = (t_dt - buf_min_start).total_seconds() / 3600.0
                    # Check window [-24h, +8h]
                    if -24.0 <= diff_h <= 8.0:
                        status = "pre_start" if diff_h < 0 else "post_start"
                        offsets.append(diff_h)
                        quotes.append({
                            "source": "polymarket",
                            "file": f"api/lines/{pid}/{m_file.name}",
                            "quote_id": f"{pid}_{m_file.stem}_{t_str}",
                            "game_id": "2026_09_BUF_MIN",
                            "quoted_at": t_dt,
                            "scheduled_start": buf_min_start,
                            "offset_hours": diff_h,
                            "status": status,
                        })

    pre_count = sum(1 for q in quotes if q["status"] == "pre_start")
    post_count = sum(1 for q in quotes if q["status"] == "post_start")
    runs = []
    if quotes:
        runs.append({
            "source": "polymarket",
            "file": "viewer/api/lines/*/*.json (2026_09_BUF_MIN)",
            "slate": "2026-w09",
            "run_time": "2026-11-10T02:47:16Z",
            "total_quotes": len(quotes),
            "games_covered": 1,
            "pre_start": pre_count,
            "post_start": post_count,
            "unknown": 0,
            "min_offset_h": min(offsets) if offsets else None,
            "median_offset_h": sorted(offsets)[len(offsets) // 2] if offsets else None,
            "max_offset_h": max(offsets) if offsets else None,
        })
    return runs, quotes


def check_git_overwrites() -> list[dict]:
    """Inspect git commits on viewer/nba_odds_2026-11-13.json to detect pre-tip quote overwrites."""
    cmd = [
        "git", "log", "--pretty=format:%h|%ad|%s", "--date=iso-strict",
        "--", "viewer/nba_odds_2026-11-13.json"
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        return []

    commits = []
    for line in res.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "msg": parts[2]})

    # Load file at each commit and check quotes
    history = []
    for c in reversed(commits):
        show_cmd = ["git", "show", f"{c['sha']}:viewer/nba_odds_2026-11-13.json"]
        s_res = subprocess.run(show_cmd, cwd=ROOT, capture_output=True, text=True)
        if s_res.returncode != 0:
            continue
        try:
            d = json.loads(s_res.stdout)
            q_list = d.get("quotes", [])
            history.append({
                "sha": c["sha"],
                "date": c["date"],
                "updated_at": d.get("updated_at"),
                "total_quotes": len(q_list),
                "sample_quotes": [
                    {"quote_id": q.get("quote_id"), "observed_at": q.get("observed_at"), "line": q.get("line")}
                    for q in q_list[:3]
                ],
            })
        except Exception:
            continue
    return history


def format_table(runs: list[dict]) -> str:
    lines = []
    header = (
        f"{'Source':<12} | {'Slate / Run':<24} | {'Run Time (UTC)':<20} | "
        f"{'Covered':<7} | {'Pre':<5} | {'Post':<5} | {'Unk':<5} | {'Offset Range (hours)':<25}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in runs:
        min_o = f"{r['min_offset_h']:+.2f}" if r["min_offset_h"] is not None else "N/A"
        med_o = f"{r['median_offset_h']:+.2f}" if r["median_offset_h"] is not None else "N/A"
        max_o = f"{r['max_offset_h']:+.2f}" if r["max_offset_h"] is not None else "N/A"
        rng = f"[{min_o}, {med_o}, {max_o}]"
        line = (
            f"{r['source']:<12} | {r['slate']:<24} | {str(r['run_time']):<20} | "
            f"{r['games_covered']:<7} | {r['pre_start']:<5} | {r['post_start']:<5} | "
            f"{r['unknown']:<5} | {rng:<25}"
        )
        lines.append(line)
    return "\n".join(lines)


def plot_offsets(quotes: list[dict], out_path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available; skipping plot image generation", file=sys.stderr)
        return

    kalshi_quotes = [q for q in quotes if q["source"] == "kalshi" and q["offset_hours"] is not None]
    poly_quotes = [q for q in quotes if q["source"] == "polymarket" and q["offset_hours"] is not None]

    fig, (ax_hist, ax_strip) = plt.subplots(
        2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [1.5, 1]}
    )

    all_offsets = [q["offset_hours"] for q in kalshi_quotes + poly_quotes]
    bins = 30
    ax_hist.hist(
        [q["offset_hours"] for q in kalshi_quotes if q["offset_hours"] < 0],
        bins=15, color="#10b981", alpha=0.8, label="Kalshi Pre-Start (< 0h)"
    )
    ax_hist.hist(
        [q["offset_hours"] for q in kalshi_quotes if q["offset_hours"] >= 0],
        bins=20, color="#ef4444", alpha=0.8, label="Kalshi Post-Start (>= 0h)"
    )
    if poly_quotes:
        ax_hist.hist(
            [q["offset_hours"] for q in poly_quotes if q["offset_hours"] >= 0],
            bins=5, color="#f97316", alpha=0.8, label="Polymarket Post-Kickoff"
        )

    ax_hist.axvline(0, color="#6b7280", linestyle="--", linewidth=1.5, label="Scheduled Start (0h)")
    ax_hist.set_ylabel("Quote Count", fontsize=11, fontweight="bold")
    ax_hist.set_title("Distribution of Quote Capture Offsets vs Scheduled Game Start", fontsize=13, fontweight="bold")
    ax_hist.legend(loc="upper right", frameon=True)
    ax_hist.grid(axis="y", linestyle=":", alpha=0.5)

    # Strip plot
    groups = {}
    for q in kalshi_quotes:
        groups.setdefault(f"Kalshi: {q['file'].replace('nba_odds_', '').replace('.json', '')}", []).append(q["offset_hours"])
    if poly_quotes:
        groups["Polymarket: 2026_09_BUF_MIN"] = [q["offset_hours"] for q in poly_quotes]

    y_labels = list(groups.keys())
    for idx, (label, y_offsets) in enumerate(groups.items()):
        colors = ["#10b981" if off < 0 else "#ef4444" for off in y_offsets]
        ax_strip.scatter(y_offsets, [idx] * len(y_offsets), color=colors, alpha=0.6, edgecolors="none", s=25)

    ax_strip.axvline(0, color="#6b7280", linestyle="--", linewidth=1.5)
    ax_strip.set_yticks(range(len(y_labels)))
    ax_strip.set_yticklabels(y_labels, fontsize=10)
    ax_strip.set_xlabel("Offset to Scheduled Game Start (Hours, UTC)", fontsize=11, fontweight="bold")
    ax_strip.grid(axis="x", linestyle=":", alpha=0.5)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Plot saved to: {out_path}")


def main() -> int:
    kalshi_runs, kalshi_quotes = analyze_kalshi_odds()
    poly_runs, poly_quotes = analyze_polymarket_line_history()
    all_runs = kalshi_runs + poly_runs
    all_quotes = kalshi_quotes + poly_quotes

    print("================================================================================")
    print("KALSHI & POLYMARKET CAPTURE TIMING DIAGNOSTIC")
    print("================================================================================\n")
    print(format_table(all_runs))
    print()

    total_k = sum(r["total_quotes"] for r in kalshi_runs)
    post_k = sum(r["post_start"] for r in kalshi_runs)
    pre_k = sum(r["pre_start"] for r in kalshi_runs)
    print(f"Total Stored Kalshi Quotes: {total_k}")
    print(f"  Pre-Start Quotes:  {pre_k} ({pre_k / total_k * 100:.1f}%)")
    print(f"  Post-Start Quotes: {post_k} ({post_k / total_k * 100:.1f}%)")

    total_p = sum(r["total_quotes"] for r in poly_runs)
    post_p = sum(r["post_start"] for r in poly_runs)
    print(f"\nPolymarket Quotes (2026_09_BUF_MIN window): {total_p}")
    print(f"  Post-Kickoff Quotes: {post_p} ({post_p / total_p * 100:.1f}%)")

    print("\n--------------------------------------------------------------------------------")
    print("FILE OVERWRITE AUDIT (Git History for viewer/nba_odds_2026-11-13.json)")
    print("--------------------------------------------------------------------------------")
    overwrites = check_git_overwrites()
    for h in overwrites:
        print(f"Commit {h['sha']} ({h['date']}) | updated_at: {h['updated_at']} | quotes: {h['total_quotes']}")
        for sq in h["sample_quotes"]:
            print(f"   quote {sq['quote_id']} line={sq['line']} observed_at={sq['observed_at']}")

    out_img = ROOT / "docs" / "images" / "kalshi-capture-offsets.png"
    plot_offsets(all_quotes, out_img)

    return 0


if __name__ == "__main__":
    sys.exit(main())
