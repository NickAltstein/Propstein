#!/usr/bin/env python3
"""Guard: viewer PRs may not introduce new colour values or change the logo/icons.

The allow-list (scripts/visual/colour-guard.json) is every colour literal in the
viewer's CSS, HTML and JS on main as of 2026-11-24, plus a SHA-256 of every logo,
icon and manifest file. tests/test_viewer_colour_guard.py fails when the working
tree uses a colour value that is not on the list, or when a logo/icon file changes.

    python3 scripts/visual/colour_guard.py --check          # what the test runs
    python3 scripts/visual/colour_guard.py --write <ref>    # rebuild from a git ref

Rebuilding the list (`--write`) is a colour/brand change: only with the user's
explicit approval, in its own commit.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIST = ROOT / "scripts" / "visual" / "colour-guard.json"

SOURCE_RE = re.compile(r"^viewer/(?:.*\.css|.*\.html|[^/]+\.js|js/.*\.js|r/.*\.js|widget/.*\.js)$")
BRAND_RE = re.compile(
    r"^viewer/(?:favicon[^/]*|apple-touch-icon[^/]*|icon-[^/]*|propstein-[^/]*\.(?:png|svg|webp)|"
    r"og-image\.png|manifest\.json|site\.webmanifest|league-logos/.*|widget/.*\.(?:png|svg))$"
)
HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")
FUNC = re.compile(r"\b(?:rgba?|hsla?)\(\s*[^)]*\)", re.I)


def norm_hex(value: str) -> str:
    v = value.lower()[1:]
    if len(v) in (3, 4):
        v = "".join(c * 2 for c in v)
    return "#" + v


def norm_func(value: str) -> str:
    name, args = value.split("(", 1)
    parts = re.split(r"[\s,/]+", args.rstrip(")").strip())
    out = []
    for p in parts:
        if re.fullmatch(r"-?\d*\.?\d+", p):
            out.append(f"{float(p):g}")
        else:
            out.append(p.lower())
    name = name.lower().rstrip("a")
    return f"{name}({','.join(out)})"


def colours(text: str) -> set[str]:
    found = {norm_hex(m.group(0)) for m in HEX.finditer(text)}
    found |= {norm_func(m.group(0)) for m in FUNC.finditer(text) if "var(" not in m.group(0) and "${" not in m.group(0)}
    return found


def tracked(ref: str | None) -> list[str]:
    cmd = ["git", "ls-tree", "-r", "--name-only", ref, "viewer"] if ref else ["git", "ls-files", "viewer"]
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True).stdout.split()


def read(path: str, ref: str | None) -> bytes:
    if ref:
        return subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, check=True, capture_output=True).stdout
    return (ROOT / path).read_bytes()


def snapshot(ref: str | None) -> dict:
    files = tracked(ref)
    cols: set[str] = set()
    for path in files:
        if SOURCE_RE.match(path):
            cols |= colours(read(path, ref).decode("utf-8", "replace"))
    brand = {p: hashlib.sha256(read(p, ref)).hexdigest() for p in files if BRAND_RE.match(p)}
    return {"colours": sorted(cols), "brand_files": dict(sorted(brand.items()))}


def problems() -> list[str]:
    allowed = json.loads(LIST.read_text(encoding="utf-8"))
    now = snapshot(None)
    out = [f"new colour value not used on main: {c}" for c in now["colours"] if c not in set(allowed["colours"])]
    for path, digest in allowed["brand_files"].items():
        got = now["brand_files"].get(path)
        if got is None:
            out.append(f"logo/icon file removed: {path}")
        elif got != digest:
            out.append(f"logo/icon file changed: {path}")
    for path in now["brand_files"]:
        if path not in allowed["brand_files"]:
            out.append(f"logo/icon file added: {path}")
    return out


def main() -> int:
    if "--write" in sys.argv:
        ref = sys.argv[sys.argv.index("--write") + 1]
        data = snapshot(ref)
        data = {"_doc": f"Colour values and logo/icon hashes on {ref}. Changing this file is a brand/colour change: user approval only.", **data}
        LIST.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {LIST.relative_to(ROOT)}: {len(data['colours'])} colours, {len(data['brand_files'])} brand files")
        return 0
    errs = problems()
    for e in errs:
        print("ERROR:", e)
    if not errs:
        print("No new colour values; logo and icon files unchanged.")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
