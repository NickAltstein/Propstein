"""Vercel's ignore command must never turn shallow history into a failed deploy."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vercel_ignore.sh"


def run(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def commit(repo: Path, name: str) -> str:
    run(repo, "add", ".")
    run(repo, "commit", "-m", name)
    return run(repo, "rev-parse", "HEAD")


def check(repo: Path, base: str, vercel_env: str | None = None) -> int:
    env = {**os.environ, "VERCEL_GIT_PREVIOUS_SHA": base}
    if vercel_env is None:
        env.pop("VERCEL_ENV", None)
    else:
        env["VERCEL_ENV"] = vercel_env
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=repo,
        env=env,
        capture_output=True, text=True, check=False,
    ).returncode


def test_missing_base_builds_and_data_only_commit_skips(tmp_path: Path):
    run(tmp_path, "init")
    run(tmp_path, "config", "user.name", "Test")
    run(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "viewer").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "viewer" / "index.html").write_text("v1")
    first = commit(tmp_path, "viewer")
    (tmp_path / "data" / "feed.json").write_text("{}")
    commit(tmp_path, "data only")
    assert check(tmp_path, first) == 0
    assert check(tmp_path, "deadbeef") == 1
    (tmp_path / "viewer" / "index.html").write_text("v2")
    second = run(tmp_path, "rev-parse", "HEAD")
    commit(tmp_path, "viewer changed")
    assert check(tmp_path, second) == 1
    assert check(tmp_path, second, vercel_env="preview") == 0
    assert check(tmp_path, "deadbeef", vercel_env="preview") == 0
    assert check(tmp_path, second, vercel_env="production") == 1
    assert check(tmp_path, "deadbeef", vercel_env="production") == 1
