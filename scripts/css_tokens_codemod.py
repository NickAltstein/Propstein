#!/usr/bin/env python3
"""Move literal CSS values in the viewer stylesheets onto viewer/tokens.css.

Idempotent: values already written as var(--...) are left alone, so it is safe
to re-run after anyone adds new CSS.

    python3 scripts/css_tokens_codemod.py           # rewrite in place
    python3 scripts/css_tokens_codemod.py --check   # exit 1 if anything would change

Rules (px values; `!important` is preserved):
  font, font-size  (shorthand: the size part only)
                 <11.75 -> --fs-1 (11)   <13 -> --fs-2 (12)   <15 -> --fs-3 (14)
                 <17.75 -> --fs-4 (16)   <24 -> --fs-5 (20)   <31 -> --fs-6 (28)
                 else   -> --fs-7 (36).  clamp()/em/%/0 are left alone.
  border-radius  each px part: 0 stays 0; 1-3 -> --r-1; 4-5 -> --r-2;
                 6-998 -> --r-3; >=999 -> --r-full.  50% (circles) stays.
  padding/margin/gap  each px part >=4: snapped to the nearest 4px step (ties
                 round up) and written as --sp-N when a token exists. 1-3px
                 nudges and negative values stay literal.
  transition     durations -> --dur-1 (<=150ms) / --dur-2; timing keywords and
                 cubic-bezier() -> --ease-out.
  Colours, including border/hairline alphas, are never rewritten (no colour changes).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "viewer/style.css",
    "viewer/terminal-ui.css",
    "viewer/refresh-2026.css",
    "viewer/design-system.css",
    "viewer/feed-status.css",
    "viewer/ufc.css",
]

FS_STEPS = [(11.75, 1), (13, 2), (15, 3), (17.75, 4), (24, 5), (31, 6)]
SP_TOKENS = {4: 1, 8: 2, 12: 3, 16: 4, 20: 5, 24: 6, 32: 8, 40: 10, 48: 12}


def fs_token(px: float) -> str:
    for limit, step in FS_STEPS:
        if px < limit:
            return f"var(--fs-{step})"
    return "var(--fs-7)"


def r_token(px: float) -> str:
    if px == 0:
        return "0"
    if px < 4:
        return "var(--r-1)"
    if px < 6:
        return "var(--r-2)"
    if px < 999:
        return "var(--r-3)"
    return "var(--r-full)"


def sp_value(px: float) -> str:
    if px < 4:
        return f"{px:g}px"
    snapped = int((px + 2) // 4 * 4)
    return f"var(--sp-{SP_TOKENS[snapped]})" if snapped in SP_TOKENS else f"{snapped}px"


PX = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)px\b")


def split_important(value: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)(\s*!important)?\s*$", value, re.S)
    return m.group(1), m.group(2) or ""


def rewrite_decl(prop: str, value: str) -> str:
    body, imp = split_important(value)
    if prop == "font":
        # Shorthand: only the size part (e.g. `600 10px/1 var(--mono)`) moves onto a token.
        new = re.sub(r"(?<![\w.(-])(\d+(?:\.\d+)?)px(?=/|\s)", lambda m: fs_token(float(m.group(1))), body, count=1)
        return new + imp if new != body else value
    if "var(" in body or "calc(" in body or "clamp(" in body or "min(" in body or "max(" in body:
        return value
    if prop == "font-size":
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)px\s*", body)
        return f" {fs_token(float(m.group(1)))}{imp}" if m else value
    if prop == "border-radius":
        parts = body.split()
        if not parts or any(not re.fullmatch(r"\d+(?:\.\d+)?px|0", p) for p in parts):
            return value
        return " " + " ".join(r_token(0 if p == "0" else float(p[:-2])) for p in parts) + imp
    if prop in {"padding", "margin", "gap", "row-gap", "column-gap", "padding-top", "padding-bottom",
                "padding-left", "padding-right", "margin-top", "margin-bottom", "margin-left",
                "margin-right", "padding-inline", "padding-block", "margin-inline", "margin-block"}:
        parts = body.split()
        if not parts or any(not re.fullmatch(r"\d+(?:\.\d+)?px|0|auto", p) for p in parts):
            return value
        out = [p if p in {"0", "auto"} else sp_value(float(p[:-2])) for p in parts]
        return " " + " ".join(out) + imp
    if prop in {"transition", "transition-duration"}:
        def dur(m: re.Match) -> str:
            n, unit = float(m.group(1)), m.group(2)
            ms = n * 1000 if unit == "s" else n
            return "var(--dur-1)" if ms <= 150 else "var(--dur-2)"
        new = re.sub(r"(?<![\w.(-])(\d*\.?\d+)(ms|s)\b", dur, body)
        new = re.sub(r"cubic-bezier\([^)]*\)|\bease-in-out\b|\bease-out\b|\bease-in\b|\bease\b|\blinear\b", "var(--ease-out)", new)
        return " " + new.strip() + imp if new != body else value
    # Borders/hairlines are deliberately NOT rewritten: collapsing their alphas onto one
    # token would change border and divider colour (user rule: no colour changes).
    return value


DECL = re.compile(r"(?<![\w-])(font-size|font|border-radius|padding(?:-[a-z]+)?|margin(?:-[a-z]+)?|gap|row-gap|column-gap|transition(?:-duration)?)\s*:([^;{}]*)(?=[;}])")


def rewrite(css: str) -> str:
    # Only touch declarations, never selectors or custom-property definitions.
    return DECL.sub(lambda m: f"{m.group(1)}:{rewrite_decl(m.group(1), m.group(2))}", css)


def main() -> int:
    check = "--check" in sys.argv
    changed = []
    for rel in FILES:
        path = ROOT / rel
        before = path.read_text()
        after = rewrite(before)
        if after != before:
            changed.append(rel)
            if not check:
                path.write_text(after)
    if check and changed:
        print("literal values not on tokens:", ", ".join(changed))
        return 1
    print(("would change: " if check else "rewrote: ") + (", ".join(changed) or "nothing"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
