"""Tests for Step 1: verify_receipt alias, README hash-only copy, and stale public/ copy guard."""

from __future__ import annotations

from pathlib import Path

from scripts.check_public_sync import check_public_sync

ROOT = Path(__file__).resolve().parents[1]


def test_public_matches_viewer_and_has_no_win_rate_or_roi():
    assert check_public_sync() == []


def test_readme_describes_verify_receipt_as_hash_check_only():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "verify_receipt" in readme
    assert "Hash check only" in readme
    assert "does not price or evaluate slips" in readme
    assert "Evaluates multi-leg DFS Pick'em" not in readme


def test_mcp_server_exposes_verify_receipt_with_verify_slip_alias():
    mcp_src = (ROOT / "src" / "mcp-server.ts").read_text(encoding="utf-8")
    assert 'name: "verify_receipt"' in mcp_src
    assert 'name: "verify_slip"' in mcp_src
    assert 'case "verify_receipt":' in mcp_src
    assert 'case "verify_slip":' in mcp_src
    assert "Hash check only" in mcp_src
