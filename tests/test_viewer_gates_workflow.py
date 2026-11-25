"""Pin the wiring of .github/workflows/viewer-gates.yml (viewer screenshot/layout gates)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "viewer-gates.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_every_action_is_pinned_to_a_full_sha() -> None:
    uses = re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)", _text(), re.M)
    assert uses, "workflow uses no actions?"
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), f"not pinned to a full SHA: {ref}"


def test_full_history_for_pinned_data_commit() -> None:
    # The harness `git show`s scripts/visual/redesign-data-ref, an older commit.
    assert re.search(r"fetch-depth:\s*0\b", _text())


def test_read_only_token_and_no_persisted_credentials() -> None:
    text = _text()
    assert re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", text, re.M)
    assert "persist-credentials: false" in text


def test_runs_harness_against_base_and_blocks_only_on_regressions() -> None:
    text = _text()
    assert "scripts/visual/redesign-gates.mjs" in text
    assert "--baseline gates/base/report.json" in text
    assert "--axe" in text
    # The PR capture step must not swallow its exit code.
    pr_step = re.search(r"--baseline gates/base/report.json[^\n]*", text).group(0)
    assert "|| true" not in pr_step


def test_screenshots_are_uploaded_as_artifacts_even_on_failure() -> None:
    text = _text()
    upload = text[text.index("actions/upload-artifact@"):]
    assert "path: gates/" in upload
    assert re.search(r"if:\s*always\(\)\s*\n\s*uses:\s*actions/upload-artifact@", text)


def test_triggers_on_viewer_changes() -> None:
    text = _text()
    for path in ('"viewer/**"', '"scripts/visual/**"', '".github/workflows/viewer-gates.yml"'):
        assert path in text
