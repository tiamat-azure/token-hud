"""Host-stable checks for promo shots: sizes and layout, not PNG bytes.

Qt/freetype rasterization (and libpng filters) drift across VMs even on the same
Ubuntu release, so `git diff` on `images/*.png` is not a useful stale check.
This script still requires a real capture: it reads the files `make shots` /
`make gif` just wrote, asserts contracted dimensions, and compares a coarse
average-hash of the working-tree PNGs to `HEAD` so a forgotten 720 px ticker or
a missing cell fails while antialiasing noise does not.

No QApplication. Pillow only.
"""

from __future__ import annotations

import struct
import subprocess
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "images"

# Keep in sync with token_hud.hud and tools/make_demo_gif.py - imported lazily
# in main() so `png_size` stays usable from tests without pulling Qt.
GIF_SIZE = (880, 520)
HASH_SIZE = 16
# 16x16 = 256 bits. Freetype/Qt hinting across VMs flips a slice of edge bits;
# a missing ticker cell or a 720-wide bar blows past this.
MAX_HAMMING = 72
MIN_UNIQUE_COLORS = 24


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", data[16:24])


def gif_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError(f"{path} is not a GIF")
    return struct.unpack("<HH", data[6:10])


def average_hash(image: Image.Image, size: int = HASH_SIZE) -> int:
    gray = image.convert("L").resize((size, size), Image.Resampling.BILINEAR)
    pixels = gray.tobytes()
    mean = sum(pixels) / len(pixels)
    bits = 0
    for index, value in enumerate(pixels):
        if value >= mean:
            bits |= 1 << index
    return bits


def hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def unique_colors(path: Path) -> int:
    colors = Image.open(path).convert("RGBA").getcolors(maxcolors=200_000)
    return 0 if colors is None else len(colors)


def _git_show(relpath: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"HEAD:{relpath}"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"HEAD has no {relpath} to compare against:\n{result.stderr.decode()}")
    return result.stdout


def _check_png(relpath: str, expected: tuple[int, int]) -> None:
    path = ROOT / relpath
    size = png_size(path)
    if size != expected:
        raise SystemExit(f"{relpath} is {size[0]}x{size[1]}, expected {expected[0]}x{expected[1]}")
    colors = unique_colors(path)
    if colors < MIN_UNIQUE_COLORS:
        raise SystemExit(f"{relpath} looks blank or fake ({colors} unique colors)")
    committed = Image.open(BytesIO(_git_show(relpath)))
    regenerated = Image.open(path)
    if committed.size != regenerated.size:
        raise SystemExit(
            f"{relpath} size {regenerated.size} does not match committed {committed.size} - commit a fresh make gif"
        )
    distance = hamming(average_hash(committed), average_hash(regenerated))
    print(f"{relpath} {size[0]}x{size[1]} colors={colors} aHashΔ={distance} (max {MAX_HAMMING})")
    if distance > MAX_HAMMING:
        raise SystemExit(
            f"{relpath} layout drifted from HEAD (average-hash hamming {distance} > {MAX_HAMMING}). "
            "Run `make gif` and commit the real HUD grabs."
        )


def main() -> int:
    from token_hud.hud import DASHBOARD_SIZE, TICKER_SIZE

    _check_png("images/ticker.png", TICKER_SIZE)
    _check_png("images/dashboard.png", DASHBOARD_SIZE)
    gif = IMAGES / "demo.gif"
    size = gif_size(gif)
    if size != GIF_SIZE:
        raise SystemExit(f"images/demo.gif is {size[0]}x{size[1]}, expected {GIF_SIZE[0]}x{GIF_SIZE[1]}")
    print(f"images/demo.gif {size[0]}x{size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
