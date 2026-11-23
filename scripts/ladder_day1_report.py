"""Day-one ladder capture coverage summary from orphan-branch `capture-data`."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Any

from src.ladder_capture import load_ladder_captures_jsonl

TEST_TICK_FILES = frozenset({
    "ticks/2026-11-19/tick_20261119T190620Z.jsonl.gz",
    "ticks/2026-11-19/tick_20261119T190722Z.jsonl.gz",
})


def summarize_capture_dir(capture_dir: Path, *, exclude_test_ticks: bool = True) -> dict[str, Any]:
    index_path = capture_dir / "index.json"
    if not index_path.exists():
        return {"ticks_committed": 0, "total_records": 0, "by_stat": {}, "by_offset_bucket": {}}

    index = json.loads(index_path.read_text(encoding="utf-8"))
    ticks = index.get("ticks") or []
    by_stat: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "OK": 0, "NO_BOOK": 0, "ONE_SIDED": 0})
    by_offset_ladders: dict[str, set[str]] = defaultdict(set)
    total_bytes = 0
    committed_ticks = 0

    for t in ticks:
        fname = str(t.get("file_name") or "")
        if exclude_test_ticks and fname in TEST_TICK_FILES:
            continue
        fpath = capture_dir / fname
        if not fpath.exists():
            continue
        committed_ticks += 1
        total_bytes += fpath.stat().st_size
        rows, _, _ = load_ladder_captures_jsonl(fpath, expected_initial_prev_hash=t.get("first_prev_record_hash"))
        for r in rows:
            stat = f"{r.get('sport', 'unknown')}:{r.get('stat_type', 'unknown')}"
            by_stat[stat]["total"] += 1
            st = str(r.get("status") or "")
            if st == "OK":
                by_stat[stat]["OK"] += 1
            elif st == "NO_BOOK":
                by_stat[stat]["NO_BOOK"] += 1
            else:
                by_stat[stat]["ONE_SIDED"] += 1
            off = r.get("offset_minutes")
            sid = str(r.get("snapshot_id") or "")
            if off is not None and sid:
                off_f = float(off)
                if 45.0 <= off_f <= 75.0:
                    by_offset_ladders["T-60 (45..75)"].add(sid)
                elif 22.5 <= off_f < 45.0:
                    by_offset_ladders["T-30 (22.5..45)"].add(sid)
                elif 10.0 <= off_f < 22.5:
                    by_offset_ladders["T-15 (10..22.5)"].add(sid)
                elif 0.0 <= off_f < 10.0:
                    by_offset_ladders["T-5 (0..10)"].add(sid)

    return {
        "ticks_committed": committed_ticks,
        "capture_data_bytes": total_bytes,
        "gaps_recorded": len(index.get("gaps") or []),
        "by_stat": dict(by_stat),
        "ladders_by_offset_bucket": {k: len(v) for k, v in sorted(by_offset_ladders.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize day-one ladder capture coverage.")
    parser.add_argument("--capture-dir", type=Path, default=Path("_capture_data"))
    args = parser.parse_args()
    print(json.dumps(summarize_capture_dir(args.capture_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
