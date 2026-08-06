"""Custom-painted neon gauges: KPI tile, donut, radial gauge, area chart, thin bar.

No stylesheets and no Qt Charts - every widget subclasses `_Panel` and draws itself in
`paintEvent` with the palette and helpers from `theme`. Never hardcode a `QColor` here.

Each gauge takes already-computed values through a `set_*` method and only formats and
draws them; derivation belongs in `model`. The `fmt_*` helpers at the top are pure and
unit-tested.

Sizing is driven by the parent layout, so paint relative to `self.rect()` rather than to
the fixed sizes in `hud`.
"""

from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from . import theme
from .model import CommitMetrics


def fmt_tokens(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} M".replace(".", ",")
    if value >= 10_000:
        return f"{value / 1000:.1f}K".replace(".", ",")
    return f"{int(value):,}".replace(",", " ")


def fmt_usd(value: float) -> str:
    return f"{value:.2f} $".replace(".", ",")


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(0, int(seconds))
    if seconds >= 86_400:
        return f"{seconds // 86_400} j {seconds % 86_400 // 3600:02d} h"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d}"


class _Panel(QWidget):
    """Rounded translucent tile used as the background of most gauges."""

    def __init__(self, parent: QWidget | None = None, *, framed: bool = True) -> None:
        super().__init__(parent)
        self._framed = framed
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def _paint_panel(self, painter: QPainter) -> None:
        if not self._framed:
            return
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(theme.LINE, 1))
        painter.setBrush(theme.PANEL)
        painter.drawRoundedRect(rect, 10, 10)


class KpiTile(_Panel):
    """Label + big value + subtitle. The value can glow for emphasis."""

    def __init__(self, label: str, color: QColor, *, glow: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._color = color
        self._glow = glow
        self._value = "-"
        self._sub = ""
        self.setMinimumHeight(58)

    def set_value(self, value: str, sub: str = "") -> None:
        if (value, sub) != (self._value, self._sub):
            self._value, self._sub = value, sub
            self.update()

    def set_color(self, color: QColor) -> None:
        if color != self._color:
            self._color = color
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_panel(painter)
        w = self.width() - 20
        theme.draw_text(
            painter, QRectF(10, 6, w, 12), self._label.upper(), theme.DIM2,
            theme.mono(6, spacing=16),
        )
        theme.draw_text(
            painter, QRectF(10, 18, w, 24), self._value, self._color,
            theme.mono(15, QFont.Weight.DemiBold), glow=self._glow,
        )
        if self._sub:
            theme.draw_text(painter, QRectF(10, 42, w, 12), self._sub, theme.DIM, theme.mono(7))


class Donut(_Panel):
    """Saved-token split. Caps at three segments - beyond that it stops being readable."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, framed=False)
        self._segments: list[tuple[str, int, QColor]] = []
        self._total = 0
        self.setMinimumSize(112, 112)

    def set_segments(self, segments: list[tuple[str, int, QColor]]) -> None:
        self._segments = [s for s in segments if s[1] > 0]
        self._total = sum(s[1] for s in self._segments)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        width = max(4.0, side * 0.10)
        rect = QRectF(
            (self.width() - side) / 2 + width, (self.height() - side) / 2 + width,
            side - 2 * width, side - 2 * width,
        )
        theme.draw_track(painter, rect, width)

        angle = 90.0  # 12 o'clock, clockwise
        for _label, value, color in self._segments:
            span = -360.0 * value / self._total if self._total else 0.0
            theme.draw_arc(painter, rect, angle, span, color, width, round_cap=False)
            angle += span

        theme.draw_text(
            painter, rect.adjusted(0, -rect.height() * 0.06, 0, -rect.height() * 0.06),
            fmt_tokens(self._total), theme.TEXT, theme.mono(11, QFont.Weight.DemiBold),
            Qt.AlignmentFlag.AlignCenter,
        )
        theme.draw_text(
            painter, rect.adjusted(0, rect.height() * 0.26, 0, rect.height() * 0.26),
            "TOKENS", theme.DIM2, theme.mono(6, spacing=20), Qt.AlignmentFlag.AlignCenter,
        )


class Legend(_Panel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, framed=False)
        self._rows: list[tuple[str, str, QColor]] = []
        self._footer = ""

    def set_rows(self, rows: list[tuple[str, str, QColor]], footer: str = "") -> None:
        self._rows, self._footer = rows, footer
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = 2.0
        for label, value, color in self._rows:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(0, y + 5, 7, 7), 2, 2)
            theme.draw_text(painter, QRectF(13, y, self.width() - 60, 16), label, theme.DIM, theme.mono(7))
            theme.draw_text(
                painter, QRectF(self.width() - 60, y, 60, 16), value, theme.TEXT, theme.mono(7),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
            y += 18
        if self._footer:
            theme.draw_text(painter, QRectF(0, y + 4, self.width(), 14), self._footer, theme.DIM2, theme.mono(6))


class RadialGauge(_Panel):
    """One bounded 0-100 value (a quota window), animated and threshold-coloured."""

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._sub = "-"
        self._pct = 0.0
        self._shown = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(500)
        self._anim.valueChanged.connect(self._on_anim)
        self.setMinimumHeight(58)

    def _on_anim(self, value) -> None:
        self._shown = float(value)
        self.update()

    def set_value(self, pct: float, sub: str) -> None:
        self._sub = sub
        if abs(pct - self._pct) > 0.01:
            self._anim.stop()
            self._anim.setStartValue(self._shown)
            self._anim.setEndValue(float(pct))
            self._anim.start()
            self._pct = pct
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_panel(painter)
        color = theme.quota_color(self._shown)
        side = min(self.height() - 10, 48)
        rect = QRectF(9, (self.height() - side) / 2, side, side)
        width = max(3.0, side * 0.11)
        inner = rect.adjusted(width / 2, width / 2, -width / 2, -width / 2)
        theme.draw_track(painter, inner, width)
        theme.draw_arc(painter, inner, 90.0, -360.0 * min(self._shown, 100.0) / 100.0, color, width)
        theme.draw_text(
            painter, inner, f"{self._shown:.0f}%", theme.TEXT,
            theme.mono(8, QFont.Weight.DemiBold), Qt.AlignmentFlag.AlignCenter,
        )
        text_x = rect.right() + 9
        w = self.width() - text_x - 8
        theme.draw_text(
            painter, QRectF(text_x, self.height() / 2 - 15, w, 13), self._label.upper(),
            theme.DIM2, theme.mono(6, spacing=16),
        )
        theme.draw_text(painter, QRectF(text_x, self.height() / 2 - 1, w, 14), self._sub, theme.TEXT, theme.mono(7))


class ThinBar(_Panel):
    """Compact bounded ratio - used for the extra-credit envelope."""

    def __init__(self, label: str, color: QColor, parent=None) -> None:
        super().__init__(parent)
        self._label, self._color = label, color
        self._value_text, self._ratio = "-", 0.0
        self.setMinimumHeight(58)

    def set_value(self, text: str, ratio: float) -> None:
        self._value_text, self._ratio = text, max(0.0, min(1.0, ratio))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_panel(painter)
        w = self.width() - 20
        theme.draw_text(painter, QRectF(10, 6, w, 12), self._label.upper(), theme.DIM2, theme.mono(6, spacing=16))
        theme.draw_text(painter, QRectF(10, 19, w, 20), self._value_text, theme.TEXT, theme.mono(11, QFont.Weight.DemiBold))
        track = QRectF(10, self.height() - 14, w, 5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 18))
        painter.drawRoundedRect(track, 2.5, 2.5)
        if self._ratio > 0:
            fill = QRectF(track)
            fill.setWidth(max(3.0, track.width() * self._ratio))
            painter.setBrush(theme.alpha(self._color, 70))
            painter.drawRoundedRect(fill.adjusted(-1, -1, 1, 1), 3, 3)
            painter.setBrush(self._color)
            painter.drawRoundedRect(fill, 2.5, 2.5)


class AreaChart(_Panel):
    """Saved tokens per interval over the retained history - the only trend view."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._points: list[float] = []
        self._title = "SAUVÉS / 15 S"
        self._peak = ""
        self.setMinimumHeight(86)

    def set_series(self, deltas: list[float], peak: str) -> None:
        self._points = deltas
        self._peak = peak
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_panel(painter)
        theme.draw_text(painter, QRectF(10, 6, self.width() - 95, 12), self._title, theme.DIM2, theme.mono(6, spacing=16))
        if self._peak:
            theme.draw_text(
                painter, QRectF(self.width() - 85, 6, 75, 12), self._peak, theme.CYAN, theme.mono(6),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )

        plot = QRectF(10, 22, self.width() - 20, self.height() - 32)
        painter.setPen(QPen(QColor(255, 255, 255, 14), 1))
        for i in range(1, 4):
            y = plot.top() + plot.height() * i / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        if len(self._points) < 2:
            theme.draw_text(
                painter, plot, "collecte de l'historique…", theme.DIM2, theme.mono(7),
                Qt.AlignmentFlag.AlignCenter,
            )
            return

        peak = max(self._points) or 1.0
        step = plot.width() / (len(self._points) - 1)
        coords = [
            QPointF(plot.left() + i * step, plot.bottom() - (v / peak) * plot.height() * 0.92)
            for i, v in enumerate(self._points)
        ]

        area = QPainterPath(QPointF(coords[0].x(), plot.bottom()))
        for point in coords:
            area.lineTo(point)
        area.lineTo(coords[-1].x(), plot.bottom())
        area.closeSubpath()
        gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
        gradient.setColorAt(0.0, theme.alpha(theme.CYAN, 115))
        gradient.setColorAt(1.0, theme.alpha(theme.CYAN, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawPath(area)

        line = QPainterPath(coords[0])
        for point in coords[1:]:
            line.lineTo(point)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(theme.glow_pen(theme.CYAN, 2.0, spread=3.5, a=60))
        painter.drawPath(line)
        pen = QPen(theme.CYAN, 2.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(line)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.alpha(theme.CYAN, 70))
        painter.drawEllipse(coords[-1], 5, 5)
        painter.setBrush(theme.CYAN)
        painter.drawEllipse(coords[-1], 2.6, 2.6)


class CommitHeatmap(_Panel):
    """GitHub-style rolling month: one column per calendar week, one row per weekday.

    Only the top level glows - lighting every cell would flatten the hierarchy.
    """

    CELL = 10
    GAP = 3
    LABEL_W = 11
    DAY_LABELS = ("L", "", "M", "", "V", "", "D")

    def __init__(self, parent=None) -> None:
        super().__init__(parent, framed=False)
        self._commits = CommitMetrics()
        self.setMinimumHeight(7 * self.CELL + 6 * self.GAP + 17)

    def set_commits(self, commits: CommitMetrics) -> None:
        self._commits = commits
        self.setToolTip(
            f"{commits.total} contributions sur 30 j · pic {commits.peak}"
            + (" · source git local" if commits.source == "git" else "")
        )
        self.update()

    def _columns(self) -> list[list[tuple[str, int] | None]]:
        """Pad the first week so weekdays line up with the real calendar."""
        days = list(self._commits.days)
        if not days:
            return []
        offset = date.fromisoformat(days[0][0]).weekday()  # Monday = 0
        cells: list[tuple[str, int] | None] = [None] * offset + days
        while len(cells) % 7:
            cells.append(None)
        return [cells[i : i + 7] for i in range(0, len(cells), 7)]

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        columns = self._columns()
        if not columns:
            theme.draw_text(
                painter, QRectF(0, 0, self.width(), self.height()), "github indisponible",
                theme.DIM2, theme.mono(7), Qt.AlignmentFlag.AlignCenter,
            )
            return

        header = QRectF(0, 0, self.width(), 12)
        theme.draw_text(painter, header, "COMMITS · 30 J", theme.DIM2, theme.mono(6, spacing=16))
        right = f"{self._commits.total} · pic {self._commits.peak}"
        if self._commits.source == "git":
            right += " · local"
        theme.draw_text(
            painter, header, right, theme.GREEN, theme.mono(6),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        top = 18.0
        painter.setPen(Qt.PenStyle.NoPen)
        for row, label in enumerate(self.DAY_LABELS):
            if not label:
                continue
            theme.draw_text(
                painter, QRectF(0, top + row * (self.CELL + self.GAP), self.LABEL_W, self.CELL),
                label, theme.DIM2, theme.mono(6),
            )

        for col, week in enumerate(columns):
            x = self.LABEL_W + col * (self.CELL + self.GAP)
            for row, day in enumerate(week):
                if day is None:
                    continue
                y = top + row * (self.CELL + self.GAP)
                level = self._commits.level_of(day[1])
                color = theme.COMMIT_LEVELS[level]
                rect = QRectF(x, y, self.CELL, self.CELL)
                if level == 4:  # halo reserved for the peak
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(theme.alpha(color, 60))
                    painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 4, 4)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(rect, 2.5, 2.5)


class CommitStrip(QWidget):
    """Ticker variant: one bar per day, height *and* colour carry the count.

    A 7-row grid does not fit in a 44 px bar, so the weekly structure is traded
    for density - the peak stays instantly findable.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._commits = CommitMetrics()

    def set_commits(self, commits: CommitMetrics) -> None:
        self._commits = commits
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        label = QRectF(0, 0, self.width(), 11)
        theme.draw_text(painter, label, "COMMITS 30 J", theme.DIM2, theme.mono(6, spacing=14))
        days = self._commits.days
        if not days:
            return
        theme.draw_text(
            painter, label, f"{self._commits.total} · pic {self._commits.peak}", theme.GREEN,
            theme.mono(6), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        top, height = 13.0, max(10.0, self.height() - 14.0)
        peak = self._commits.peak or 1
        gap = 2.0
        width = max(2.0, (self.width() - gap * (len(days) - 1)) / len(days))
        painter.setPen(Qt.PenStyle.NoPen)
        for index, (_day, count) in enumerate(days):
            level = self._commits.level_of(count)
            bar_h = 3.0 if count <= 0 else 4.0 + (height - 4.0) * count / peak
            rect = QRectF(index * (width + gap), top + height - bar_h, width, bar_h)
            if level == 4:
                painter.setBrush(theme.alpha(theme.COMMIT_LEVELS[4], 70))
                painter.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), 2.5, 2.5)
            painter.setBrush(theme.COMMIT_LEVELS[level])
            painter.drawRoundedRect(rect, 1.5, 1.5)
