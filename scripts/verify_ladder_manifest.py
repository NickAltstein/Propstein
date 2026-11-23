"""Verify weekly ladder archive manifest and orphan-branch tick index hash-chain continuity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from src.ladder_capture import load_ladder_captures_jsonl


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest_entry(
    *,
    week_id: str,
    jsonl_path: Path,
    expected_initial_prev_hash: str | None = None,
) -> dict[str, Any]:
    """Summarize one weekly JSONL/JSONL.GZ file for data/ladder_archives_manifest.json."""
    rows, last_hash, chain_valid = load_ladder_captures_jsonl(
        jsonl_path, expected_initial_prev_hash=expected_initial_prev_hash
    )
    if not chain_valid or not rows:
        raise ValueError(f"cannot build manifest entry for invalid or empty file: {jsonl_path}")
    return {
        "week_id": week_id,
        "file_name": jsonl_path.name,
        "sha256": file_sha256(jsonl_path),
        "record_count": len(rows),
        "first_record_id": rows[0]["record_id"],
        "last_record_id": rows[-1]["record_id"],
        "first_prev_record_hash": rows[0].get("prev_record_hash"),
        "last_record_hash": last_hash,
    }


def _verify_entries(
    entries: list[Mapping[str, Any]],
    *,
    base_dir: Path | None,
) -> dict[str, Any]:
    expected_prev: str | None = None
    files_verified = 0
    for idx, entry in enumerate(entries):
        if entry.get("first_prev_record_hash") != expected_prev:
            return {
                "valid": False,
                "error": f"chain break at index {idx} ({entry.get('file_name')})",
                "entries_checked": idx,
            }
        if base_dir is not None:
            fpath = base_dir / str(entry["file_name"])
            if fpath.exists():
                actual_sha = file_sha256(fpath)
                if actual_sha != entry.get("sha256"):
                    return {
                        "valid": False,
                        "error": f"sha256 mismatch for {fpath.name}",
                        "entries_checked": idx,
                    }
                rows, last_hash, chain_valid = load_ladder_captures_jsonl(
                    fpath, expected_initial_prev_hash=expected_prev
                )
                if not chain_valid or len(rows) != entry.get("record_count"):
                    return {
                        "valid": False,
                        "error": f"chain or count mismatch for {fpath.name}",
                        "entries_checked": idx,
                    }
                if rows[0]["record_id"] != entry.get("first_record_id") or rows[-1]["record_id"] != entry.get("last_record_id"):
                    return {
                        "valid": False,
                        "error": f"record_id boundary mismatch for {fpath.name}",
                        "entries_checked": idx,
                    }
                if last_hash != entry.get("last_record_hash"):
                    return {
                        "valid": False,
                        "error": f"last_record_hash mismatch for {fpath.name}",
                        "entries_checked": idx,
                    }
                files_verified += 1
        expected_prev = entry.get("last_record_hash")

    return {
        "valid": True,
        "entries_checked": len(entries),
        "weeks_checked": len(entries),
        "files_verified": files_verified,
        "last_record_hash": expected_prev,
    }


def verify_manifest(
    manifest: Mapping[str, Any],
    *,
    archive_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify cross-week hash-chain linkage in manifest and validate local files if present."""
    archives = manifest.get("archives")
    if not isinstance(archives, list):
        return {"valid": False, "error": "manifest.archives must be a list", "weeks_checked": 0}
    return _verify_entries(archives, base_dir=archive_dir)


def verify_capture_index(
    index: Mapping[str, Any],
    *,
    capture_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify cross-tick hash-chain linkage in orphan-branch `index.json`."""
    ticks = index.get("ticks")
    if not isinstance(ticks, list):
        return {"valid": False, "error": "index.ticks must be a list", "entries_checked": 0}
    res = _verify_entries(ticks, base_dir=capture_dir)
    if not res["valid"]:
        return res
    if ticks and res["last_record_hash"] != index.get("last_record_hash"):
        return {"valid": False, "error": "index.last_record_hash mismatch", "entries_checked": len(ticks)}
    return res


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify ladder archive manifest or capture-data index.")
    parser.add_argument("--manifest", type=Path, default=Path("data/ladder_archives_manifest.json"))
    parser.add_argument("--archive-dir", type=Path, default=Path("data"))
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--capture-dir", type=Path, default=Path("_capture_data"))
    args = parser.parse_args()

    if args.index is not None:
        if not args.index.exists():
            print(json.dumps({"valid": True, "entries_checked": 0, "files_verified": 0, "note": "no index yet"}))
            return
        index = json.loads(args.index.read_text(encoding="utf-8"))
        res = verify_capture_index(index, capture_dir=args.capture_dir)
    else:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        res = verify_manifest(manifest, archive_dir=args.archive_dir)

    print(json.dumps(res))
    if not res["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
