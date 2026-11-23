"""Regression test for nba_model.cli.load_world date derivation when --start is omitted."""

from __future__ import annotations

import types
from pathlib import Path
import sys

_SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import nba_model.cli as nba_cli


def test_load_world_derives_start_from_date_when_start_omitted(monkeypatch):
    captured = {}

    class DummyProvider:
        def fetch_range(self, dates):
            captured["dates"] = dates
            return {"rows": [], "games": []}

    monkeypatch.setattr(nba_cli, "build_provider", lambda args: DummyProvider())
    args = types.SimpleNamespace(provider="nbacom", start=None, end=None, date="2026-11-22", days=10)
    world = nba_cli.load_world(args)
    assert world["odds"] == []
    assert world["injuries"] == []
    assert captured["dates"][0] == "2026-11-12"
    assert captured["dates"][-1] == "2026-11-22"
    assert len(captured["dates"]) == 11
