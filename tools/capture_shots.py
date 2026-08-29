"""Grab ticker and dashboard PNGs from the real HudWindow, offscreen.

Drives `HudWindow` with a frozen snapshot so `make shots` is deterministic on CI.
Does not start collectors (live clocks and GitHub would churn the pixels). Radial
gauges animate for 500 ms; we wait for that to settle before `QWidget.grab()`.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

# Isolate QSettings / history before Qt reads XDG paths.
ROOT = Path(__file__).resolve().parents[1]
_FONT_DIR = ROOT / "tools" / "fonts"
_FONT_FILES = (
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMono-Medium.ttf",
    "JetBrainsMono-SemiBold.ttf",
)
_xdg = Path(tempfile.mkdtemp(prefix="token-hud-shots-"))
os.environ.setdefault("XDG_CONFIG_HOME", str(_xdg / "config"))
os.environ.setdefault("XDG_DATA_HOME", str(_xdg / "data"))
os.environ.setdefault("XDG_CACHE_HOME", str(_xdg / "cache"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
os.environ.setdefault("QT_FONT_DPI", "96")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

_missing = [name for name in _FONT_FILES if not (_FONT_DIR / name).is_file()]
if _missing:
    raise SystemExit(f"vendored JetBrains Mono missing in {_FONT_DIR}: {', '.join(_missing)}")
_fc = _xdg / "fonts.conf"
# FONTCONFIG_FILE replaces the host config. Include /etc/fonts/fonts.conf for
# Fc aliases, then bind JetBrains Mono to the vendored TTFs and pin raster
# options so agent-host and ubuntu-24.04 CI hint the same way.
_fc.write_text(
    f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>{_FONT_DIR}</dir>
  <selectfont>
    <rejectfont>
      <glob>/usr/share/fonts/truetype/macos/*</glob>
      <glob>/usr/share/fonts/truetype/jetbrains-mono/*</glob>
    </rejectfont>
  </selectfont>
  <match target="font">
    <edit name="antialias" mode="assign"><bool>true</bool></edit>
    <edit name="hinting" mode="assign"><bool>false</bool></edit>
    <edit name="hintstyle" mode="assign"><const>hintnone</const></edit>
    <edit name="rgba" mode="assign"><const>none</const></edit>
    <edit name="lcdfilter" mode="assign"><const>lcdnone</const></edit>
    <edit name="autohint" mode="assign"><bool>false</bool></edit>
  </match>
</fontconfig>
""",
    encoding="utf-8",
)
os.environ["FONTCONFIG_FILE"] = str(_fc)

from PyQt6.QtCore import QEventLoop, QObject, QTimer, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from token_hud.hud import DASHBOARD_SIZE, TICKER_SIZE, HudWindow  # noqa: E402
from token_hud.model import (  # noqa: E402
    CommitMetrics,
    HeadroomMetrics,
    QuotaMetrics,
    QuotaWindow,
    RtkMetrics,
    Snapshot,
)

IMAGES = ROOT / "images"
GAUGE_SETTLE_MS = 650


class _ShotStore(QObject):
    """Duck-typed stand-in for MetricsStore: same signals/methods, no I/O."""

    snapshotReady = pyqtSignal(object)

    def __init__(self, snapshot: Snapshot, series: list[tuple[float, int]], rate: float) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._series = series
        self._rate = rate

    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    def series(self) -> list[tuple[float, int]]:
        return list(self._series)

    def rate_per_hour(self) -> float:
        return self._rate

    def save_history(self) -> None:
        return


def _commits() -> CommitMetrics:
    start = date(2026, 7, 8)
    counts = (
        0, 1, 0, 4, 2, 8, 3,
        0, 5, 14, 6, 0, 3, 1,
        2, 0, 7, 4, 9, 1, 0,
        3, 5, 2, 8, 0, 6, 4, 1, 2,
    )
    days = tuple(((start + timedelta(days=i)).isoformat(), count) for i, count in enumerate(counts))
    return CommitMetrics(days=days, source="github", ok=True)


def _snapshot() -> Snapshot:
    return Snapshot(
        rtk=RtkMetrics(commands=43, tokens_saved=42_803, savings_pct=91.7, ok=True),
        headroom=HeadroomMetrics(
            requests=226,
            compression_tokens_saved=4_472,
            output_tokens_saved=15_262,
            compression_usd=0.013416,
            output_usd=0.22893,
            cache_usd=15.386685,
            cache_read_tokens=5_128_895,
            input_cost_usd=17.512782,
            model="claude-opus-5",
            ok=True,
        ),
        quota=QuotaMetrics(
            five_hour=QuotaWindow(utilization_pct=17.0, seconds_to_reset=12_075),
            seven_day=QuotaWindow(utilization_pct=2.0, seconds_to_reset=599_475),
            extra_used_usd=0.0,
            extra_limit_usd=40.0,
            ok=True,
        ),
        commits=_commits(),
    )


def _series() -> list[tuple[float, int]]:
    origin = 1_700_000_000.0
    return [(origin + i * 15.0, 50_000 + i * 180) for i in range(24)]


def _wait(app: QApplication, ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _opaque_samples(pixmap: QPixmap) -> int:
    image = pixmap.toImage()
    hits = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            if image.pixelColor(x, y).alpha() > 8:
                hits += 1
    return hits


def _grab(window: HudWindow, snap: Snapshot, mode: str, size: tuple[int, int], app: QApplication) -> QPixmap:
    window.set_mode(mode)
    window.apply(snap)
    window.show()
    window.repaint()
    app.processEvents()
    _wait(app, GAUGE_SETTLE_MS)
    pixmap = window.grab()
    width, height = pixmap.width(), pixmap.height()
    if (width, height) != size:
        raise SystemExit(f"{mode} grab is {width}x{height}, expected {size[0]}x{size[1]}")
    samples = _opaque_samples(pixmap)
    if samples < 40:
        platform = os.environ.get("QT_QPA_PLATFORM", "?")
        raise SystemExit(
            f"{mode} grab looks blank ({samples} opaque samples, platform={platform}). "
            "Need a working offscreen/xvfb paint path; refusing to write a fake PNG."
        )
    return pixmap


def main() -> int:
    IMAGES.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setApplicationName("token-hud-shots")
    app.setOrganizationName("token-hud-shots")
    app.setQuitOnLastWindowClosed(False)

    snap = _snapshot()
    store = _ShotStore(snap, _series(), rate=2_400.0)
    window = HudWindow(store)
    window.apply(snap)

    ticker = _grab(window, snap, "ticker", TICKER_SIZE, app)
    ticker_path = IMAGES / "ticker.png"
    if not ticker.save(str(ticker_path), "PNG"):
        raise SystemExit(f"failed to write {ticker_path}")

    dashboard = _grab(window, snap, "dashboard", DASHBOARD_SIZE, app)
    dashboard_path = IMAGES / "dashboard.png"
    if not dashboard.save(str(dashboard_path), "PNG"):
        raise SystemExit(f"failed to write {dashboard_path}")

    platform = os.environ.get("QT_QPA_PLATFORM", "?")
    print(f"platform={platform} font-dpi={os.environ.get('QT_FONT_DPI')}")
    print(f"{ticker_path} {ticker.width()}x{ticker.height()}")
    print(f"{dashboard_path} {dashboard.width()}x{dashboard.height()}")
    # Skip Qt teardown: CommitTooltip can already be gone when hideEvent runs.
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
