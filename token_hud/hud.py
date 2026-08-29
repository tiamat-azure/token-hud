"""The frameless HUD window: dashboard mode, collapsed ticker mode, tray control.

`HudWindow` holds a `QStackedWidget` with `DashboardView` and `TickerView`; double-click
toggles between them, drag moves the window, and both the mode and the position persist
through `QSettings`. It is the only consumer of `MetricsStore.snapshotReady`, and it
fans the snapshot out to both views regardless of which one is visible.

A one-second clock re-applies the last snapshot so countdowns keep ticking between polls,
and a looping animation drives the alert pulse when a quota window crosses the thresholds
in `theme`. Both are cosmetic: `apply()` must stay cheap, it runs every second.

Layout uses plain Qt widgets; everything inside them is hand-painted by `gauges`.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRectF, QSettings, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QMenu,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .gauges import (
    AreaChart,
    CommitHeatmap,
    CommitStrip,
    Donut,
    KpiTile,
    Legend,
    RadialGauge,
    ThinBar,
    fmt_duration,
    fmt_tokens,
    fmt_usd,
)
from .model import Snapshot
from .store import HISTORY_INTERVAL_S, MetricsStore

DASHBOARD_SIZE = (560, 380)
TICKER_SIZE = (806, 44)
DEFAULT_MODE = "ticker"

_PALETTE = {"cyan": theme.CYAN, "violet": theme.VIOLET, "green": theme.GREEN}


def _peak_of(deltas: list[float]) -> str:
    return f"pic {fmt_tokens(max(deltas))}" if deltas else ""


def ticker_cells(snap: Snapshot) -> list[tuple[str, str, QColor, bool]]:
    """Widget-free ticker readouts so tests can assert order without a QApplication."""
    five = snap.quota.five_hour
    seven = snap.quota.seven_day
    return [
        (fmt_tokens(snap.tokens_saved), "tok saved", theme.GREEN, True),
        (fmt_usd(snap.usd_saved), "économisé", theme.GREEN, False),
        (f"{five.free_pct:.0f} % libre", "quota 5 h", theme.quota_color(five.utilization_pct), False),
        (fmt_duration(five.seconds_to_reset), "avant reset", theme.ORANGE, False),
        (f"{seven.free_pct:.0f} % libre", "quota 7 j", theme.quota_color(seven.utilization_pct), False),
        (f"{snap.rtk.savings_pct:.1f} %".replace(".", ","), "rtk ratio", theme.VIOLET, False),
    ]


class _Header(QWidget):
    """One-line status strip: identity on the left, liveness on the right."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._right = "connexion…"
        self._right_color = theme.DIM2
        self._left = "◉ token hud"
        self.setFixedHeight(16)

    def set_status(self, left: str, right: str, color: QColor) -> None:
        self._left, self._right, self._right_color = left, right, color
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = theme.mono(6, spacing=16)
        theme.draw_text(painter, QRectF(0, 0, self.width() / 2, 16), self._left.upper(), theme.DIM2, font)
        theme.draw_text(
            painter, QRectF(self.width() / 2, 0, self.width() / 2, 16), self._right.upper(),
            self._right_color, font, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )


class DashboardView(QWidget):
    """Full 560x380 surface: KPI row, donut + trend, quota gauges."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.header = _Header()
        self.kpi_tokens = KpiTile("tokens sauvés", theme.GREEN, glow=True)
        self.kpi_usd = KpiTile("$ économisés", theme.GREEN)
        self.kpi_ratio = KpiTile("efficacité rtk", theme.CYAN)
        self.donut = Donut()
        self.legend = Legend()
        self.heatmap = CommitHeatmap()
        self.area = AreaChart()
        self.gauge_5h = RadialGauge("fenêtre 5 h")
        self.gauge_7d = RadialGauge("fenêtre 7 j")
        self.extra = ThinBar("crédits extra", theme.ORANGE)

        # The legend only needs three rows, so the heatmap reuses the empty space
        # below it and the dashboard keeps its 560x380 footprint.
        self.legend.setFixedHeight(72)
        side_column = QWidget()
        side_layout = QVBoxLayout(side_column)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(6)
        side_layout.addWidget(self.legend, 0)
        side_layout.addWidget(self.heatmap, 1)

        donut_box = QWidget()
        donut_layout = QHBoxLayout(donut_box)
        donut_layout.setContentsMargins(8, 8, 8, 8)
        donut_layout.setSpacing(8)
        donut_layout.addWidget(self.donut, 0)
        donut_layout.addWidget(side_column, 1)
        donut_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._donut_box = donut_box

        grid = QGridLayout()
        grid.setSpacing(9)
        grid.addWidget(self.kpi_tokens, 0, 0)
        grid.addWidget(self.kpi_usd, 0, 1)
        grid.addWidget(self.kpi_ratio, 0, 2)
        grid.addWidget(donut_box, 1, 0, 1, 2)
        grid.addWidget(self.area, 1, 2)
        grid.addWidget(self.gauge_5h, 2, 0)
        grid.addWidget(self.gauge_7d, 2, 1)
        grid.addWidget(self.extra, 2, 2)
        grid.setRowStretch(1, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 12)
        root.setSpacing(9)
        root.addWidget(self.header)
        root.addLayout(grid)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self._donut_box.geometry())
        painter.setPen(QPen(theme.LINE, 1))
        painter.setBrush(theme.PANEL)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

    def apply(self, snap: Snapshot, deltas: list[float], rate_per_hour: float) -> None:
        model = snap.headroom.model or "claude"
        live = f"{snap.headroom.requests} req · maj 5 s" if snap.headroom.ok else "sources indisponibles"
        self.header.set_status(
            f"◉ token hud · {model}", f"● {live}",
            theme.GREEN if snap.headroom.ok else theme.RED,
        )

        trend = f"▲ +{fmt_tokens(rate_per_hour)} / h" if rate_per_hour > 0 else "tendance en cours"
        self.kpi_tokens.set_value(fmt_tokens(snap.tokens_saved), trend)
        self.kpi_usd.set_value(fmt_usd(snap.usd_saved), f"vs {fmt_usd(snap.headroom.input_cost_usd)} dépensés")
        self.kpi_ratio.set_value(
            f"{snap.rtk.savings_pct:.1f} %".replace(".", ","),
            f"{snap.rtk.commands} commandes" if snap.rtk.ok else "rtk indisponible",
        )

        segments = [(label, value, _PALETTE[key]) for label, value, key in snap.breakdown]
        self.donut.set_segments(segments)
        self.legend.set_rows(
            [(label, fmt_tokens(value), color) for label, value, color in segments if value > 0],
            f"cache lu : {fmt_tokens(snap.headroom.cache_read_tokens)} tok",
        )

        self.heatmap.set_commits(snap.commits)
        self.area.set_series(deltas, _peak_of(deltas))
        self.gauge_5h.set_value(
            snap.quota.five_hour.utilization_pct,
            f"{fmt_duration(snap.quota.five_hour.seconds_to_reset)} avant reset",
        )
        self.gauge_7d.set_value(
            snap.quota.seven_day.utilization_pct,
            f"reset dans {fmt_duration(snap.quota.seven_day.seconds_to_reset)}",
        )
        limit = snap.quota.extra_limit_usd
        self.extra.set_value(
            f"{snap.quota.extra_used_usd:.0f} / {limit:.0f} $",
            snap.quota.extra_used_usd / limit if limit else 0.0,
        )


class TickerView(QWidget):
    """Collapsed 806x44 bar: six readouts (including 7-day quota), then the commit strip.

    The strip is suffixed rather than substituted - the existing indicators keep
    their slots, they only get narrower.
    """

    STRIP_W = 150

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cells: list[tuple[str, str, QColor, bool]] = []
        self._quota_pct = 0.0
        self.strip = CommitStrip(self)

    def resizeEvent(self, event) -> None:
        self.strip.setGeometry(
            self.width() - self.STRIP_W - 14, 6, self.STRIP_W, self.height() - 14
        )
        super().resizeEvent(event)

    def apply(self, snap: Snapshot, *_ignored) -> None:
        self.strip.set_commits(snap.commits)
        self._quota_pct = snap.quota.five_hour.utilization_pct
        self._cells = ticker_cells(snap)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        theme.draw_text(
            painter, QRectF(14, 0, 90, self.height()), "◉ TOKEN HUD", theme.CYAN,
            theme.mono(8, QFont.Weight.DemiBold, spacing=18), glow=True,
        )
        x = 112.0
        cells_w = self.width() - 130 - self.STRIP_W - 10
        cell_w = max(78.0, cells_w / max(1, len(self._cells)))
        for index, (value, label, color, glow) in enumerate(self._cells):
            if index:
                painter.setPen(QPen(theme.LINE, 1))
                painter.drawLine(int(x - 10), 10, int(x - 10), self.height() - 10)
            theme.draw_text(
                painter, QRectF(x, 7, cell_w, 16), value, color,
                theme.mono(9, QFont.Weight.DemiBold), glow=glow,
            )
            theme.draw_text(
                painter, QRectF(x, 24, cell_w, 12), label.upper(), theme.DIM2, theme.mono(6, spacing=14)
            )
            x += cell_w

        separator_x = self.width() - self.STRIP_W - 24
        painter.setPen(QPen(theme.LINE, 1))
        painter.drawLine(separator_x, 10, separator_x, self.height() - 10)

        bar = QRectF(112, self.height() - 5, cells_w, 2.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 16))
        painter.drawRoundedRect(bar, 1.5, 1.5)
        if self._quota_pct > 0:
            fill = QRectF(bar)
            fill.setWidth(max(3.0, bar.width() * min(self._quota_pct, 100.0) / 100.0))
            painter.setBrush(theme.quota_color(self._quota_pct))
            painter.drawRoundedRect(fill, 1.5, 1.5)


class HudWindow(QWidget):
    """Frameless, always-on-top, draggable. Double-click switches dashboard <-> ticker."""

    modeChanged = pyqtSignal(str)

    def __init__(self, store: MetricsStore) -> None:
        super().__init__()
        self._store = store
        self._drag_offset: QPoint | None = None
        self._alert_level = 0
        self._settings = QSettings("token-hud", "hud")

        self.setWindowTitle("Token HUD")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Showing without activating keeps the focus on the terminal underneath, but the
        # window manager then never re-raises us above a freshly focused fullscreen client.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.dashboard = DashboardView()
        self.ticker = TickerView()
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.dashboard)
        self.stack.addWidget(self.ticker)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        self._pulse = QVariantAnimation(self)
        self._pulse.setDuration(1400)
        self._pulse.setStartValue(0.0)
        self._pulse.setEndValue(1.0)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(lambda _v: self.update())
        self._pulse_value = 0.0
        self._pulse.valueChanged.connect(self._on_pulse)

        store.snapshotReady.connect(self.apply)
        self._clock = QTimer(self)  # keeps the countdowns ticking between polls
        self._clock.setInterval(1000)
        self._clock.timeout.connect(lambda: self.apply(store.snapshot))
        self._clock.start()

        # Fullscreen terminals (herdr) get stacked above "keep above" windows by most
        # window managers, so re-assert the stacking order periodically.
        self._keep_above = QTimer(self)
        self._keep_above.setInterval(2000)
        self._keep_above.timeout.connect(self._raise_above)
        self._keep_above.start()

        self.set_mode(self._settings.value("mode", DEFAULT_MODE, str))
        self._restore_position()

    # --- state --------------------------------------------------------------

    def _on_pulse(self, value) -> None:
        self._pulse_value = float(value)

    def apply(self, snap: Snapshot) -> None:
        deltas = self._deltas()
        rate = self._store.rate_per_hour()
        self.dashboard.apply(snap, deltas, rate)
        self.ticker.apply(snap, deltas, rate)
        self._update_alert(snap)

    def _deltas(self) -> list[float]:
        """Gaps between sessions would spike the chart, so only contiguous pairs count."""
        points = self._store.series()
        max_gap = HISTORY_INTERVAL_S * 3
        return [
            max(0.0, b[1] - a[1])
            for a, b in zip(points, points[1:], strict=False)
            if b[0] - a[0] <= max_gap
        ]

    def _update_alert(self, snap: Snapshot) -> None:
        pct = max(snap.quota.five_hour.utilization_pct, snap.quota.seven_day.utilization_pct)
        level = 2 if pct >= theme.CRIT_PCT else 1 if pct >= theme.WARN_PCT else 0
        if level == self._alert_level:
            return
        self._alert_level = level
        if level == 2:
            self._pulse.start()
        else:
            self._pulse.stop()
            self._pulse_value = 0.0
        self.update()

    def set_mode(self, mode: str) -> None:
        mode = mode if mode in ("dashboard", "ticker") else "dashboard"
        if mode == "dashboard":
            self.stack.setCurrentWidget(self.dashboard)
            self.setFixedSize(*DASHBOARD_SIZE)
        else:
            self.stack.setCurrentWidget(self.ticker)
            self.setFixedSize(*TICKER_SIZE)
        self._settings.setValue("mode", mode)
        self.modeChanged.emit(mode)

    @property
    def mode(self) -> str:
        return "dashboard" if self.stack.currentWidget() is self.dashboard else "ticker"

    def toggle_mode(self) -> None:
        self.set_mode("ticker" if self.mode == "dashboard" else "dashboard")

    # --- window chrome ------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.BG)
        painter.drawRoundedRect(rect, 14, 14)

        border = {0: theme.alpha(theme.CYAN, 56), 1: theme.alpha(theme.ORANGE, 150)}.get(
            self._alert_level, theme.alpha(theme.RED, int(120 + 110 * self._pulse_value))
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, 1.4))
        painter.drawRoundedRect(rect, 14, 14)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        self._settings.setValue("pos", self.pos())
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        self.toggle_mode()
        event.accept()

    def contextMenuEvent(self, event) -> None:
        menu = build_menu(self, tray_context=False)
        menu.exec(event.globalPos())

    def _raise_above(self) -> None:
        if self.isVisible():
            self.raise_()

    def _restore_position(self) -> None:
        pos = self._settings.value("pos")
        # A stale off-screen position leaves the HUD invisible and unclickable, which
        # is what a Wayland session records because it reports bogus global positions.
        if isinstance(pos, QPoint) and self.screen().availableGeometry().contains(pos):
            self.move(pos)
        else:
            self.move(self._default_position())

    def _default_position(self) -> QPoint:
        """Top of the screen, horizontally centred."""
        screen = self.screen().availableGeometry()
        return QPoint(screen.center().x() - self.width() // 2, screen.top() + 24)

    def closeEvent(self, event) -> None:
        self._store.save_history()
        super().closeEvent(event)


def build_menu(window: HudWindow, *, tray_context: bool) -> QMenu:
    """Shared menu for the tray icon and the widget's own right-click."""
    from PyQt6.QtWidgets import QApplication

    menu = QMenu()
    if tray_context:
        toggle = QAction("Masquer le HUD" if window.isVisible() else "Afficher le HUD", menu)
        toggle.triggered.connect(lambda: window.setVisible(not window.isVisible()))
        menu.addAction(toggle)
    switch = QAction(
        "Mode ticker (compact)" if window.mode == "dashboard" else "Mode dashboard (complet)", menu
    )
    switch.triggered.connect(window.toggle_mode)
    menu.addAction(switch)
    menu.addSeparator()
    quit_action = QAction("Quitter", menu)
    quit_action.triggered.connect(QApplication.quit)
    menu.addAction(quit_action)
    return menu


def tray_icon(parent, window: HudWindow) -> QSystemTrayIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(theme.alpha(theme.CYAN, 70))
    painter.drawEllipse(4, 4, 24, 24)
    painter.setBrush(theme.CYAN)
    painter.drawEllipse(10, 10, 12, 12)
    painter.end()

    tray = QSystemTrayIcon(QIcon(pixmap), parent)
    tray.setToolTip("Token HUD - économies headroom + rtk")

    def refresh_menu(*_args) -> None:
        tray.setContextMenu(build_menu(window, tray_context=True))

    refresh_menu()
    window.modeChanged.connect(refresh_menu)
    tray.activated.connect(
        lambda reason: window.setVisible(not window.isVisible())
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    return tray
