#!/usr/bin/env python3
"""Build browser, install, and extension icons from approved icon masters."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageEnhance


ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "assets" / "brand"
VIEWER = ROOT / "viewer"
PUBLIC = ROOT / "public"
TERMINAL = ROOT / "terminal" / "public" / "brand"
EXTENSION = ROOT / "extension" / "icons"


def load_master(name: str) -> Image.Image:
    image = Image.open(BRAND / name).convert("RGB")
    if image.width != image.height or image.width < 1024:
        raise ValueError(f"Icon master must be square and at least 1024px: {name}")
    return image


def resized(master: Image.Image, size: int) -> Image.Image:
    icon = master.resize((size, size), Image.Resampling.LANCZOS)
    if size <= 32:
        icon = ImageEnhance.Sharpness(icon).enhance(1.4)
    return icon


def save(master: Image.Image, size: int, names: tuple[str, ...], folders: tuple[Path, ...]) -> None:
    icon = resized(master, size)
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            icon.save(folder / name, format="PNG", optimize=True)


def save_svg(favicon: Image.Image) -> None:
    buffer = BytesIO()
    resized(favicon, 512).save(buffer, format="PNG", optimize=True)
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">'
        f'<image href="data:image/png;base64,{data}" width="512" height="512"/>'
        '</svg>\n'
    )
    for folder in (VIEWER, PUBLIC, ROOT):
        (folder / "favicon.svg").write_text(svg, encoding="utf-8")


def main() -> None:
    app = load_master("app-icon-master.png")
    maskable = load_master("maskable-icon-master.png")
    favicon = load_master("favicon-master.png")

    for size in (192, 512):
        save(app, size, (f"icon-{size}.png",), (VIEWER, PUBLIC, ROOT))
        save(maskable, size, (f"icon-maskable-{size}.png",), (VIEWER, PUBLIC, ROOT))
    save(app, 180, (
        "apple-touch-icon.png", "apple-touch-icon-180x180.png",
        "apple-touch-icon-precomposed.png", "apple-touch-icon-180x180-precomposed.png",
    ), (VIEWER, PUBLIC, ROOT))
    for size in (167, 152, 120):
        save(app, size, (f"apple-touch-icon-{size}x{size}.png",), (VIEWER, PUBLIC))
    save(favicon, 32, ("favicon-32x32.png", "favicon.png"), (VIEWER, PUBLIC, ROOT))
    save(favicon, 16, ("favicon-16x16.png",), (VIEWER, PUBLIC, ROOT))
    for folder in (VIEWER, PUBLIC, ROOT):
        favicon.save(folder / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    save_svg(favicon)

    save(app, 512, ("icon-512.png",), (TERMINAL,))
    save(app, 192, ("icon-192.png",), (TERMINAL,))
    save(app, 180, ("apple-touch-icon.png",), (TERMINAL,))
    save(favicon, 32, ("icon-32.png",), (TERMINAL,))
    for size in (16, 48, 128):
        save(favicon, size, (f"icon{size}.png",), (EXTENSION,))
    print("Generated PropStein icons from approved app, maskable, and favicon masters")


if __name__ == "__main__":
    main()
