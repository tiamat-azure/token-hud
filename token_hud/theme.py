"""Neon dark palette and small painting helpers shared by every gauge.

Single source of truth for colors, fonts and the quota alert thresholds (`WARN_PCT`,
`CRIT_PCT`); change a color here and the whole HUD follows. `mono()` picks the first
available font from `_MONO_CANDIDATES`, so it degrades gracefully on any machine.

The glow effect is a wide translucent pen stroked under the sharp one - see `glow_pen`
and `draw_arc`. There is no `QGraphicsEffect` anywhere; that keeps repaints cheap enough
for the once-a-second refresh.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen

# --- palette -----------------------------------------------------------------

BG = QColor(10, 13, 19, 219)
PANEL = QColor(255, 255, 255, 8)
LINE = QColor(255, 255, 255, 26)
TEXT = QColor(230, 237, 246)
DIM = QColor(139, 152, 173)
DIM2 = QColor(93, 105, 121)

CYAN = QColor(34, 211, 238)
VIOLET = QColor(167, 139, 250)
GREEN = QColor(57, 255, 136)
ORANGE = QColor(255, 176, 32)
RED = QColor(255, 77, 109)

# Contribution heatmap: empty day, then dark green up to a saturated neon peak.
COMMIT_LEVELS = (
    QColor(15, 26, 21),
    QColor(20, 83, 45),
    QColor(31, 138, 76),
    QColor(46, 224, 111),
    QColor(57, 255, 136),
)

# Quota thresholds: below WARN it stays cyan, then orange, then red.
WARN_PCT = 70.0
CRIT_PCT = 90.0


def quota_color(pct: float) -> QColor:
    if pct >= CRIT_PCT:
        return RED
    if pct >= WARN_PCT:
        return ORANGE
    return CYAN


def alpha(color: QColor, a: int) -> QColor:
    out = QColor(color)
    out.setAlpha(a)
    return out


# --- fonts -------------------------------------------------------------------

_MONO_CANDIDATES = ("JetBrains Mono", "Fira Code", "DejaVu Sans Mono", "Monospace")


def mono(size: int, weight: QFont.Weight = QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    families = set(QFontDatabase.families())
    family = next((f for f in _MONO_CANDIDATES if f in families), _MONO_CANDIDATES[-1])
    font = QFont(family, size)
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100 + spacing)
    return font


# --- painting ----------------------------------------------------------------

def glow_pen(color: QColor, width: float, spread: float = 3.0, a: int = 55) -> QPen:
    """Wide translucent pen painted under the crisp stroke to fake a neon halo."""
    pen = QPen(alpha(color, a), width + spread)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def draw_arc(
    painter: QPainter,
    rect: QRectF,
    start_deg: float,
    span_deg: float,
    color: QColor,
    width: float,
    *,
    glow: bool = True,
    round_cap: bool = True,
) -> None:
    cap = Qt.PenCapStyle.RoundCap if round_cap else Qt.PenCapStyle.FlatCap
    if glow:
        halo = glow_pen(color, width)
        halo.setCapStyle(cap)
        painter.setPen(halo)
        painter.drawArc(rect, int(start_deg * 16), int(span_deg * 16))
    pen = QPen(color, width)
    pen.setCapStyle(cap)
    painter.setPen(pen)
    painter.drawArc(rect, int(start_deg * 16), int(span_deg * 16))


def draw_track(painter: QPainter, rect: QRectF, width: float) -> None:
    pen = QPen(QColor(255, 255, 255, 18), width)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    painter.drawEllipse(rect)


def draw_text(
    painter: QPainter,
    rect: QRectF,
    text: str,
    color: QColor,
    font: QFont,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    *,
    glow: bool = False,
) -> None:
    painter.setFont(font)
    if glow:
        painter.setPen(alpha(color, 70))
        for offset in ((0.7, 0), (-0.7, 0), (0, 0.7), (0, -0.7)):
            painter.drawText(rect.translated(QPointF(*offset)), int(align), text)
    painter.setPen(color)
    painter.drawText(rect, int(align), text)
