"""Host-stable checks for promo shots: sizes and layout, not PNG/GIF bytes.

Qt/freetype rasterization (and libpng filters) drift across VMs even on the same
Ubuntu release, so `git diff` on `images/` is not a useful stale check.
This script still requires a real capture: it reads the files `make shots` /
`make gif` just wrote, asserts contracted dimensions, and compares a coarse
average-hash of the working-tree PNGs and GIF frame 0 to `HEAD`.

No QApplication. Pillow only.
"""

from __future__ import annotations

import struct
import subprocess
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

# Keep in sync with token_hud.hud and tools/make_demo_gif.py - imported lazily
# in main() so `png_size` stays usable from tests without pulling Qt.
GIF_SIZE = (880, 520)
HASH_SIZE = 16
# Dashboard/GIF only. The ticker is gated by the quota-7j cell crop: a missing
# weekly cell scored global Δ≈13–62, under any Hamming that still survives
# freetype drift.
MAX_HAMMING = 72
# 16x16 of the 86×44 `quota 7 j` box. Blur is Δ≈2; five cells stretched into
# six or rtk painted in that slot is Δ≈50–70.
MAX_CELL_HAMMING = 32
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


def unique_colors(image: Image.Image) -> int:
    colors = image.convert("RGBA").getcolors(maxcolors=200_000)
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


def _frame0(image: Image.Image) -> Image.Image:
    image.seek(0)
    return image.convert("RGBA")


def ticker_quota_7j_box(window_width: int, window_height: int) -> tuple[int, int, int, int]:
    from token_hud.hud import quota_7j_cell_index, ticker_cell_box, ticker_cells
    from token_hud.model import Snapshot

    n_cells = len(ticker_cells(Snapshot()))
    return ticker_cell_box(window_width, window_height, quota_7j_cell_index(), n_cells)


def crop_ticker_quota_7j(image: Image.Image) -> Image.Image:
    x, y, w, h = ticker_quota_7j_box(*image.size)
    return image.crop((x, y, x + w, y + h))


def _check_quota_7j_cell(committed: Image.Image, regenerated: Image.Image) -> None:
    expected = crop_ticker_quota_7j(committed)
    actual = crop_ticker_quota_7j(regenerated)
    box = ticker_quota_7j_box(*regenerated.size)
    colors = unique_colors(actual)
    if colors < MIN_UNIQUE_COLORS:
        raise SystemExit(f"quota 7 j cell {box} looks blank ({colors} unique colors)")
    distance = hamming(average_hash(expected), average_hash(actual))
    print(
        f"images/ticker.png quota 7 j cell {box[0]},{box[1]} {box[2]}x{box[3]} "
        f"colors={colors} aHashΔ={distance} (max {MAX_CELL_HAMMING})"
    )
    if distance > MAX_CELL_HAMMING:
        raise SystemExit(
            f"quota 7 j cell drifted from HEAD (average-hash hamming {distance} > {MAX_CELL_HAMMING}). "
            "The weekly-quota readout is missing or the five other cells were stretched into its slot."
        )


def _check_layout(relpath: str, expected: tuple[int, int], size_of, *, cell_gate: bool = False) -> None:
    path = ROOT / relpath
    size = size_of(path)
    if size != expected:
        raise SystemExit(f"{relpath} is {size[0]}x{size[1]}, expected {expected[0]}x{expected[1]}")
    regenerated = _frame0(Image.open(path))
    colors = unique_colors(regenerated)
    if colors < MIN_UNIQUE_COLORS:
        raise SystemExit(f"{relpath} looks blank or fake ({colors} unique colors)")
    committed = _frame0(Image.open(BytesIO(_git_show(relpath))))
    if committed.size != regenerated.size:
        raise SystemExit(
            f"{relpath} size {regenerated.size} does not match committed {committed.size} - commit a fresh make gif"
        )
    distance = hamming(average_hash(committed), average_hash(regenerated))
    print(f"{relpath} {size[0]}x{size[1]} colors={colors} aHashΔ={distance} (max {MAX_HAMMING})")
    if cell_gate:
        _check_quota_7j_cell(committed, regenerated)
        return
    if distance > MAX_HAMMING:
        raise SystemExit(
            f"{relpath} layout drifted from HEAD (average-hash hamming {distance} > {MAX_HAMMING}). "
            "Run `make gif` and commit the real HUD grabs."
        )


def main() -> int:
    from token_hud.hud import DASHBOARD_SIZE, TICKER_SIZE

    _check_layout("images/ticker.png", TICKER_SIZE, png_size, cell_gate=True)
    _check_layout("images/dashboard.png", DASHBOARD_SIZE, png_size)
    _check_layout("images/demo.gif", GIF_SIZE, gif_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
