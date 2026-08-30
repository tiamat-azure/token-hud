"""Scout: measure ticker cell boxes, QFontMetrics, and painted ink (offscreen HUD).

Shares the capture_shots font/DPI pipeline so numbers match make shots.
Writes before/after PNGs plus a JSON report. Does not start collectors.
"""

from __future__ import annotations

# Capture pipeline must load before PyQt so FONTCONFIG/DPI match make shots.
# ruff: noqa: I001

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import capture_shots as shots  # noqa: E402

from PyQt6.QtCore import QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from token_hud import theme  # noqa: E402
from token_hud.gauges import fmt_duration, fmt_tokens, fmt_usd  # noqa: E402
from token_hud.hud import (  # noqa: E402
    TICKER_CELL_COUNT,
    TICKER_CELL_WIDTHS,
    TICKER_SEPARATOR_INSET,
    TICKER_SIZE,
    TICKER_STRIP_W,
    HudWindow,
    ticker_cell_box,
    ticker_cells,
    ticker_mono_advance_px,
    ticker_separator_x,
)
from token_hud.model import QuotaWindow, RtkMetrics, Snapshot  # noqa: E402

IMAGES = shots.IMAGES
ARTIFACTS = Path("/opt/cursor/artifacts")
INK_RGB_MIN = 40
VALUE_Y0, VALUE_Y1 = 6, 24


def _metrics_face() -> tuple[QFont, QFontMetricsF]:
    font = theme.mono(9, QFont.Weight.DemiBold)
    return font, QFontMetricsF(font)


def _label_face() -> tuple[QFont, QFontMetricsF]:
    font = theme.mono(6, spacing=14)
    return font, QFontMetricsF(font)


def _candidates() -> dict[str, list[str]]:
    """Value strings to size against, including the frozen shot snapshot."""
    snap = shots._snapshot()
    live = {label: value for value, label, _c, _g in ticker_cells(snap)}
    return {
        "tok saved": [live["tok saved"], fmt_tokens(9_990_000), fmt_tokens(99_900_000), fmt_tokens(940), "9,99 M"],
        "économisé": [live["économisé"], fmt_usd(15.629), fmt_usd(999.99), fmt_usd(9.99)],
        "quota 5 h": [live["quota 5 h"], "100 % libre", "0 % libre", "83 % libre"],
        "avant reset": [live["avant reset"], fmt_duration(12_075), fmt_duration(17_940), fmt_duration(599_475)],
        "quota 7 j": [live["quota 7 j"], "100 % libre", "0 % libre", "98 % libre"],
        "rtk ratio": [live["rtk ratio"], "100,0 %", "91,7 %", "0,0 %"],
    }


def _libre_snap() -> Snapshot:
    snap = shots._snapshot()
    return replace(
        snap,
        quota=replace(
            snap.quota,
            five_hour=QuotaWindow(utilization_pct=0.0, seconds_to_reset=12_075),
            seven_day=QuotaWindow(utilization_pct=0.0, seconds_to_reset=599_475),
        ),
        rtk=replace(snap.rtk, savings_pct=100.0),
    )


def _worst_snap() -> Snapshot:
    """Long strings used for slack: 99,90 M, 999,99 $, 100 % libre, 6 j 22 h, 100,0 %."""
    snap = _libre_snap()
    return replace(
        snap,
        rtk=RtkMetrics(commands=43, tokens_saved=99_900_000, savings_pct=100.0, ok=True),
        headroom=replace(
            snap.headroom,
            compression_tokens_saved=0,
            output_tokens_saved=0,
            compression_usd=0.0,
            output_usd=0.0,
            cache_usd=999.99,
        ),
        quota=replace(
            snap.quota,
            five_hour=QuotaWindow(utilization_pct=0.0, seconds_to_reset=599_475),
            seven_day=QuotaWindow(utilization_pct=0.0, seconds_to_reset=599_475),
        ),
    )


def _ink_right(image, x0: int, x1: int, y0: int = VALUE_Y0, y1: int = VALUE_Y1) -> int | None:
    """Rightmost painted-glyph x in [x0, x1) on the value band. None if empty."""
    right = None
    x0 = max(0, x0)
    x1 = min(image.width(), x1)
    y0 = max(0, y0)
    y1 = min(image.height(), y1)
    for y in range(y0, y1):
        for x in range(x1 - 1, x0 - 1, -1):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() < 40:
                continue
            if max(pixel.red(), pixel.green(), pixel.blue()) < INK_RGB_MIN:
                continue
            right = x if right is None else max(right, x)
            break
    return right


def _grab_ticker(window: HudWindow, snap: Snapshot, app: QApplication) -> QPixmap:
    # HudWindow's 1s clock re-applies store.snapshot; pin the store and stop timers
    # so a 100% libre grab cannot be overwritten mid-wait.
    window._clock.stop()
    window._keep_above.stop()
    window._store._snapshot = snap
    return shots._grab(window, snap, "ticker", TICKER_SIZE, app)


def _strip_box(window: HudWindow) -> dict:
    geom = window.ticker.strip.geometry()
    return {
        "x": geom.x(),
        "y": geom.y(),
        "w": geom.width(),
        "h": geom.height(),
        "STRIP_W": TICKER_STRIP_W,
        "strip_separator_x": window.width() - TICKER_STRIP_W - 24,
    }


def _measure_cells(pixmap: QPixmap, snap: Snapshot) -> list[dict]:
    image = pixmap.toImage()
    n = TICKER_CELL_COUNT
    width, height = TICKER_SIZE
    face, fm = _metrics_face()
    _labelf, label_fm = _label_face()
    rows = []
    for index, (value, label, _color, _glow) in enumerate(ticker_cells(snap)):
        x, _y, w, h = ticker_cell_box(width, height, index, n)
        inner = float(w - TICKER_SEPARATOR_INSET)
        sep = ticker_separator_x(width, index + 1, n) if index + 1 < n else width - TICKER_STRIP_W - 24
        advance = fm.horizontalAdvance(value)
        tight = fm.tightBoundingRect(value)
        formula = ticker_mono_advance_px(value)
        label_adv = label_fm.horizontalAdvance(label.upper())
        # Clip the scan to the inner rect so the next cell's glyphs cannot leak in.
        scan_right = min(x + int(inner) + 1, sep)
        ink_x = _ink_right(image, x, scan_right)
        ink_w = None if ink_x is None else ink_x - x + 1
        slack = None if ink_w is None else inner - ink_w
        gap = None if ink_x is None else sep - ink_x
        rows.append(
            {
                "index": index,
                "label": label,
                "value": value,
                "box_x": x,
                "box_w": w,
                "box_h": h,
                "inner_w": inner,
                "separator_x": sep,
                "separator_inset": TICKER_SEPARATOR_INSET,
                "metrics_px": round(advance, 2),
                "tight_px": round(tight.width(), 2),
                "formula_px": round(formula, 2),
                "label_metrics_px": round(label_adv, 2),
                "ink_right": ink_x,
                "ink_px": ink_w,
                "slack_px": None if slack is None else round(slack, 2),
                "gap_to_sep_px": gap,
                "font": f"{face.family()} {face.pointSize()}pt weight={int(face.weight())}",
            }
        )
    return rows


def _candidate_table(fm: QFontMetricsF) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for label, strings in _candidates().items():
        seen: set[str] = set()
        rows = []
        for text in strings:
            if text in seen:
                continue
            seen.add(text)
            rows.append(
                {
                    "text": text,
                    "metrics_px": round(fm.horizontalAdvance(text), 2),
                    "tight_px": round(fm.tightBoundingRect(text).width(), 2),
                    "formula_px": round(ticker_mono_advance_px(text), 2),
                }
            )
        rows.sort(key=lambda row: row["metrics_px"], reverse=True)
        out[label] = rows
    return out


def _annotate(pixmap: QPixmap, cells: list[dict], strip: dict, title: str) -> QPixmap:
    """Copy of the ticker with cell boxes, separator ticks, and ink marks."""
    scale = 3
    src = pixmap.toImage()
    out = QPixmap(src.width() * scale, src.height() * scale + 52)
    out.fill(QColor(8, 10, 14))
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    painter.drawImage(
        0,
        0,
        src.scaled(
            src.width() * scale,
            src.height() * scale,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        ),
    )
    for cell in cells:
        x = cell["box_x"] * scale
        w = cell["box_w"] * scale
        painter.setPen(QPen(QColor(255, 220, 80, 200), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(x, 0, w - 1, src.height() * scale - 1)
        sep = cell["separator_x"] * scale
        painter.setPen(QPen(QColor(255, 70, 90, 230), 2))
        painter.drawLine(sep, 0, sep, src.height() * scale)
        if cell["ink_right"] is not None:
            ink = cell["ink_right"] * scale
            painter.setPen(QPen(QColor(80, 255, 160, 230), 2))
            painter.drawLine(ink, 0, ink, src.height() * scale)
        painter.setPen(QColor(230, 237, 246))
        painter.drawText(
            QRectF(x + 2, src.height() * scale + 4, w, 20),
            f"{cell['box_w']}px slack {cell['slack_px']}",
        )
        painter.drawText(QRectF(x + 2, src.height() * scale + 22, w, 20), cell["label"])
    sx = strip["x"] * scale
    painter.setPen(QPen(QColor(120, 200, 255, 220), 1))
    painter.drawRect(sx, strip["y"] * scale, strip["w"] * scale - 1, strip["h"] * scale - 1)
    painter.setPen(QColor(180, 210, 230))
    painter.drawText(QRectF(4, src.height() * scale + 4, 400, 40), title)
    painter.end()
    return out


def _save(pixmap: QPixmap, rel: Path) -> Path:
    IMAGES.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = IMAGES / rel
    if not pixmap.save(str(path), "PNG"):
        raise SystemExit(f"failed to write {path}")
    artifact = ARTIFACTS / rel.name
    pixmap.save(str(artifact), "PNG")
    return path


def _zoom_quota(pixmap: QPixmap, cells: list[dict]) -> QPixmap:
    quota = [c for c in cells if c["label"] in ("quota 5 h", "quota 7 j")]
    x0 = min(c["box_x"] for c in quota) - 4
    x1 = max(c["box_x"] + c["box_w"] for c in quota) + 8
    crop = pixmap.copy(x0, 0, x1 - x0, pixmap.height())
    return QPixmap.fromImage(
        crop.toImage().scaled(
            crop.width() * 4,
            crop.height() * 4,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
    )


def measure(phase: str) -> dict:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("token-hud-cell-scout")
    app.setOrganizationName("token-hud-shots")
    app.setQuitOnLastWindowClosed(False)
    shots._register_fonts()

    face, fm = _metrics_face()
    store = shots._ShotStore(shots._snapshot(), shots._series(), rate=2_400.0)
    window = HudWindow(store)
    window.apply(shots._snapshot())

    snap = shots._snapshot()
    libre = _libre_snap()
    worst = _worst_snap()

    pix_snap = _grab_ticker(window, snap, app)
    pix_libre = _grab_ticker(window, libre, app)
    pix_worst = _grab_ticker(window, worst, app)
    strip = _strip_box(window)

    cells_libre = _measure_cells(pix_libre, libre)
    cells_snap = _measure_cells(pix_snap, snap)
    cells_worst = _measure_cells(pix_worst, worst)

    prefix = f"ticker-cell-widths-{phase}"
    _save(pix_libre, Path(f"{prefix}.png"))
    _save(pix_snap, Path(f"{prefix}-snapshot.png"))
    annotated = _annotate(
        pix_libre, cells_libre, strip, f"{phase} 100% libre  {TICKER_SIZE[0]}x{TICKER_SIZE[1]}"
    )
    _save(annotated, Path(f"{prefix}-annotated.png"))
    _save(_zoom_quota(pix_libre, cells_libre), Path(f"{prefix}-quota-zoom.png"))
    _save(pix_worst, Path(f"{prefix}-worst.png"))
    worst_ann = _annotate(pix_worst, cells_worst, strip, f"{phase} worst-case strings")
    _save(worst_ann, Path(f"{prefix}-worst-annotated.png"))

    report = {
        "phase": phase,
        "TICKER_SIZE": list(TICKER_SIZE),
        "TICKER_STRIP_W": TICKER_STRIP_W,
        "TICKER_CELL_COUNT": TICKER_CELL_COUNT,
        "TICKER_CELL_WIDTHS": list(TICKER_CELL_WIDTHS),
        "font": f"{face.family()} {face.pointSize()}pt DemiBold",
        "strip": strip,
        "candidates": _candidate_table(fm),
        "cells_100pct_libre": cells_libre,
        "cells_snapshot": cells_snap,
        "cells_worst": cells_worst,
        "bar": {
            "y": TICKER_SIZE[1] - 5,
            "h": 2.5,
            "note": "2.5px fill uses five_hour.utilization_pct only",
        },
    }
    report_path = IMAGES / f"{prefix}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (ARTIFACTS / report_path.name).write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    # Keep the window alive: destroying HudWindow runs CommitStrip.hideEvent after
    # CommitTooltip is already gone (same reason capture_shots uses os._exit).
    report["_window"] = window
    return report


def _print_table(report: dict) -> None:
    print(f"phase={report['phase']} TICKER_SIZE={report['TICKER_SIZE']} STRIP_W={report['TICKER_STRIP_W']}")
    print(f"font={report['font']}")
    print(
        f"{'cell':<14} {'x':>5} {'w':>5} {'inner':>6} {'sep':>5} {'string':<14} "
        f"{'metrics':>8} {'ink':>6} {'slack':>6} {'gap':>5}"
    )
    for cell in report["cells_100pct_libre"]:
        print(
            f"{cell['label']:<14} {cell['box_x']:5d} {cell['box_w']:5d} {cell['inner_w']:6.1f} "
            f"{cell['separator_x']:5d} {cell['value']:<14} {cell['metrics_px']:8.2f} "
            f"{cell['ink_px']!s:>6} {cell['slack_px']!s:>6} {cell['gap_to_sep_px']!s:>5}"
        )
    print("strip", report["strip"])
    print("candidate advances:")
    for label, rows in report["candidates"].items():
        best = rows[0]
        print(
            f"  {label}: worst {best['text']!r} metrics={best['metrics_px']} "
            f"tight={best['tight_px']} formula={best['formula_px']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("before", "after"), default="before")
    args = parser.parse_args()
    report = measure(args.phase)
    _print_table(report)
    print(f"wrote {IMAGES / f'ticker-cell-widths-{args.phase}.png'}")
    sys.stdout.flush()
    # Skip Qt teardown: CommitTooltip can already be gone when hideEvent runs.
    import os

    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
