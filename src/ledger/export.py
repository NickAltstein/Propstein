"""JSONL mirror stub.

Merkle roots and OpenTimestamps anchoring are a follow-up. This writer only
copies ledger events. It does not anchor them.
"""

from __future__ import annotations

import json
from pathlib import Path


def _quote_mirror_rows(conn) -> list[dict]:
    specs = (
        ("entry", "intended_quotes", "line", "quoted_at"),
        ("entry", "placements", "achieved_line", "placed_at"),
        ("close", "closing_references", "line", "observed_at"),
    )
    mirrored = []
    with conn.cursor() as cur:
        for role, table, line_col, when_col in specs:
            cur.execute(
                f"""
                SELECT id::text AS id, {line_col} AS line, quote_source, {when_col} AS observed_at
                FROM {table}
                ORDER BY created_at, id
                """
            )
            for row in cur.fetchall():
                mirrored.append({
                    "record": "quote",
                    "role": role,
                    "id": row["id"],
                    "line": float(row["line"]) if row["line"] is not None else None,
                    "quote_source": row["quote_source"] or "unknown",
                    "observed_at": row["observed_at"].isoformat() if row["observed_at"] is not None else None,
                })
    return mirrored


def export_ledger_jsonl(conn, dest: Path) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT passport_id::text AS passport_id, seq, event_type, prev_hash,
                   integrity_hash, created_at_canonical
            FROM ledger_events
            ORDER BY created_at, passport_id, seq
            """
        )
        rows = cur.fetchall()
    quotes = _quote_mirror_rows(conn)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({
                "passport_id": row["passport_id"],
                "seq": row["seq"],
                "event_type": row["event_type"],
                "prev_hash": row["prev_hash"],
                "integrity_hash": row["integrity_hash"],
                "created_at": row["created_at_canonical"],
            }, sort_keys=True) + "\n")
        for row in quotes:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return {
        "path": str(dest),
        "event_count": len(rows),
        "quote_count": len(quotes),
        "anchoring": "not_implemented",
    }
