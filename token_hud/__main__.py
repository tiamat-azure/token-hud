"""Entry point: `python -m token_hud`."""

from __future__ import annotations

import os
import signal
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from .hud import HudWindow, tray_icon
from .store import MetricsStore


def _prefer_x11() -> None:
    """Wayland has no protocol for "keep above": the HUD sinks behind fullscreen
    terminals and stops receiving clicks. XWayland honours the stay-on-top hint, so
    use it unless the platform plugin was chosen explicitly."""
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    if os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def main() -> int:
    _prefer_x11()
    app = QApplication(sys.argv)
    app.setApplicationName("token-hud")
    app.setOrganizationName("token-hud")
    app.setQuitOnLastWindowClosed(False)  # the tray keeps the process alive

    store = MetricsStore()
    window = HudWindow(store)
    window.show()

    tray = tray_icon(app, window)
    tray.show()

    app.aboutToQuit.connect(store.save_history)

    # Let Ctrl+C through: Python signal handlers only run between Qt events.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    heartbeat = QTimer()
    heartbeat.start(300)
    heartbeat.timeout.connect(lambda: None)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
