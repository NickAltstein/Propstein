#!/usr/bin/env python3
"""Fail when public/ presentation files drift from viewer/ or surface forbidden win-rate/ROI copy."""

from __future__ import annotations

import filecmp
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "viewer"
PUBLIC = ROOT / "public"

SYNCED_FILES = (
    "app.js",
    "audit.html",
    "circuit_breaker_config.json",
    "index.html",
    "ledger.html",
    "ledger/index.html",
    "manifest.json",
    "site.webmanifest",
    "style.css",
    "sw.js",
    "verify.html",
)

FORBIDDEN_PUBLIC_COPY_RE = re.compile(r"Win Rate:|Realized ROI / P&L|win_rate_pct|realized_roi_pct")


def check_public_sync() -> list[str]:
    errors: list[str] = []
    for rel in SYNCED_FILES:
        v_path = VIEWER / rel
        p_path = PUBLIC / rel
        if not v_path.exists():
            errors.append(f"missing viewer source: {rel}")
            continue
        if not p_path.exists():
            errors.append(f"missing public mirror: {rel}")
            continue
        if not filecmp.cmp(v_path, p_path, shallow=False):
            errors.append(f"stale public copy differs from viewer/: public/{rel}")

    for surface in ("app.js", "index.html", "audit.html", "ledger.html", "ledger/index.html", "verify.html"):
        p_path = PUBLIC / surface
        v_path = VIEWER / surface
        for label, path in (("public", p_path), ("viewer", v_path)):
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="ignore")
                match = FORBIDDEN_PUBLIC_COPY_RE.search(text)
                if match:
                    errors.append(f"forbidden win-rate/ROI copy '{match.group(0)}' in {label}/{surface}")
    return errors


if __name__ == "__main__":
    errs = check_public_sync()
    if errs:
        for err in errs:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    print("public/ matches viewer/ and contains no win-rate/ROI copy.")
