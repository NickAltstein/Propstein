"""Viewer PRs must not add colour values or change the logo/icons (scripts/visual/colour_guard.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("colour_guard", ROOT / "scripts" / "visual" / "colour_guard.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def test_no_new_colours_and_brand_files_unchanged() -> None:
    assert guard.problems() == []


def test_colour_normalisation_catches_respelled_values() -> None:
    assert guard.colours("a{color:#FFF;b:#ffffff}") == {"#ffffff"}
    assert guard.colours("x{border:1px solid rgba(255, 255, 255, .06)}") == guard.colours("y{c:rgba(255,255,255,0.06)}")
    assert "#123456" in guard.colours("<rect fill='#123456'>")
