"""Owns the polling timers, the current snapshot, and the persisted history.

One `QTimer` per source, each firing a `collectors.job()` onto the global `QThreadPool`;
results land back on the UI thread through the runnable's signal and are folded into a
single `Snapshot` re-emitted as `snapshotReady`. Sources are independent - a slow or
failing one never blocks the others.

The rolling token-saved history (`HISTORY_POINTS` points, one every `HISTORY_INTERVAL_S`)
backs the area chart and the per-hour rate, and is persisted to `history_path()` so the
chart is not empty on restart. Points older than `HISTORY_MAX_AGE_S` are dropped on load.

Keep this module free of blocking calls: everything it runs happens in the UI thread.
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path

from PyQt6.QtCore import QObject, QStandardPaths, QThreadPool, QTimer, pyqtSignal

from . import collectors
from .model import Snapshot

HISTORY_POINTS = 240  # 60 min at one point every 15 s
HISTORY_INTERVAL_S = 15.0
HISTORY_MAX_AGE_S = 6 * 3600


def history_path() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    directory = Path(base or Path.home() / ".local/share/token-hud")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "history.json"


class MetricsStore(QObject):
    """Fans collector results into a single snapshot and emits it to the UI."""

    snapshotReady = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._snapshot = Snapshot()
        self._pool = QThreadPool.globalInstance()
        self._history: deque[tuple[float, int]] = deque(maxlen=HISTORY_POINTS)
        self._last_history_ts = 0.0
        self._load_history()

        self._timers: list[QTimer] = []
        self._schedule(collectors.read_rtk, "rtk", collectors.RTK_INTERVAL_MS)
        self._schedule(collectors.read_headroom, "headroom", collectors.HEADROOM_INTERVAL_MS)
        self._schedule(collectors.read_quota, "quota", collectors.QUOTA_INTERVAL_MS)
        self._schedule(collectors.read_commits, "commits", collectors.COMMITS_INTERVAL_MS)

    # --- polling ------------------------------------------------------------

    def _schedule(self, fn, field: str, interval_ms: int) -> None:
        timer = QTimer(self)
        timer.setInterval(interval_ms)
        timer.timeout.connect(lambda: self._dispatch(fn, field))
        timer.start()
        self._timers.append(timer)
        self._dispatch(fn, field)

    def _dispatch(self, fn, field: str) -> None:
        runnable = collectors.job(fn)
        runnable.signals.done.connect(lambda value, f=field: self._apply(f, value))
        self._pool.start(runnable)

    def _apply(self, field: str, value) -> None:
        self._snapshot = self._snapshot.merged(**{field: value})
        self._record_history()
        self.snapshotReady.emit(self._snapshot)

    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    # --- history ------------------------------------------------------------

    def _record_history(self) -> None:
        total = self._snapshot.tokens_saved
        if total <= 0:
            return
        now = time.time()
        if now - self._last_history_ts < HISTORY_INTERVAL_S and self._history:
            # Keep the newest point fresh instead of appending a near-duplicate.
            self._history[-1] = (now, total)
            return
        self._history.append((now, total))
        self._last_history_ts = now

    def series(self) -> list[tuple[float, int]]:
        """Cumulative saved-token points, oldest first."""
        return list(self._history)

    def rate_per_hour(self) -> float:
        """Saved tokens per hour, measured over the retained history window."""
        points = self.series()
        if len(points) < 2:
            return 0.0
        (t0, v0), (t1, v1) = points[0], points[-1]
        elapsed = t1 - t0
        if elapsed < 60:
            return 0.0
        return (v1 - v0) * 3600.0 / elapsed

    def _load_history(self) -> None:
        path = history_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        cutoff = time.time() - HISTORY_MAX_AGE_S
        points = [
            (float(ts), int(value))
            for ts, value in raw.get("points", [])
            if float(ts) >= cutoff
        ]
        self._history.extend(points[-HISTORY_POINTS:])
        if self._history:
            self._last_history_ts = self._history[-1][0]

    def save_history(self) -> None:
        payload = {"version": 1, "points": [[ts, value] for ts, value in self._history]}
        try:
            history_path().write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass
