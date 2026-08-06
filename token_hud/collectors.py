"""Data collection. Every read happens in a QThreadPool worker, never in the UI thread.

Sources:
  rtk gain -f json                        -> RtkMetrics
  ~/.headroom/proxy_savings.json          -> HeadroomMetrics
  ~/.headroom/subscription_state.json     -> QuotaMetrics
  gh api graphql (fallback: git log)      -> CommitMetrics

Only the commit calendar touches the network, and only every 10 minutes.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from .model import CommitMetrics, HeadroomMetrics, QuotaMetrics, QuotaWindow, RtkMetrics

HEADROOM_DIR = Path.home() / ".headroom"
PROXY_SAVINGS = HEADROOM_DIR / "proxy_savings.json"
SUBSCRIPTION_STATE = HEADROOM_DIR / "subscription_state.json"

RTK_INTERVAL_MS = 30_000
HEADROOM_INTERVAL_MS = 5_000
QUOTA_INTERVAL_MS = 15_000
COMMITS_INTERVAL_MS = 600_000  # GitHub call is the only network hop - keep it rare

COMMIT_DAYS = 30

# The offline fallback walks the filesystem, so where it walks is the user's call:
# TOKEN_HUD_GIT_ROOTS is a path-separated list, TOKEN_HUD_GIT_DEPTH caps the descent.
GIT_ROOTS_ENV = "TOKEN_HUD_GIT_ROOTS"
GIT_DEPTH_ENV = "TOKEN_HUD_GIT_DEPTH"
DEFAULT_GIT_SCAN_ROOTS = (Path.home() / "workspaces", Path.home() / "src", Path.home() / "projects")
DEFAULT_GIT_SCAN_DEPTH = 3


def git_scan_roots() -> tuple[Path, ...]:
    raw = os.environ.get(GIT_ROOTS_ENV, "").strip()
    if not raw:
        return DEFAULT_GIT_SCAN_ROOTS
    return tuple(Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip())


def git_scan_depth() -> int:
    try:
        return max(1, int(os.environ.get(GIT_DEPTH_ENV, "")))
    except ValueError:
        return DEFAULT_GIT_SCAN_DEPTH


class _Signals(QObject):
    done = pyqtSignal(object)


class _Job(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.signals = _Signals()

    def run(self) -> None:  # pragma: no cover - thread entry point
        try:
            result = self._fn()
        except Exception:
            result = None
        if result is not None:
            self.signals.done.emit(result)


def job(fn) -> _Job:
    return _Job(fn)


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# --- rtk ---------------------------------------------------------------------

def read_rtk() -> RtkMetrics:
    try:
        proc = subprocess.run(
            ["rtk", "gain", "-f", "json"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return RtkMetrics()
    if proc.returncode != 0 or not proc.stdout.strip():
        return RtkMetrics()
    try:
        summary = json.loads(proc.stdout)["summary"]
    except (ValueError, KeyError, TypeError):
        return RtkMetrics()
    return RtkMetrics(
        commands=int(summary.get("total_commands", 0)),
        tokens_saved=int(summary.get("total_saved", 0)),
        savings_pct=float(summary.get("avg_savings_pct", 0.0)),
        ok=True,
    )


# --- headroom ----------------------------------------------------------------

def read_headroom() -> HeadroomMetrics:
    if not PROXY_SAVINGS.exists():
        return HeadroomMetrics()
    try:
        data = _load_json(PROXY_SAVINGS)
    except (OSError, ValueError):
        return HeadroomMetrics()

    lifetime = data.get("lifetime", {})
    # Label the HUD with the model that actually drove the traffic, not the provider.
    by_model = data.get("by_model", {}) or {}
    busiest = max(by_model.items(), key=lambda kv: kv[1].get("requests", 0), default=("", {}))
    return HeadroomMetrics(
        requests=int(lifetime.get("requests", 0)),
        compression_tokens_saved=int(lifetime.get("tokens_saved", 0)),
        output_tokens_saved=int(lifetime.get("output_tokens_saved", 0)),
        compression_usd=float(lifetime.get("compression_savings_usd", 0.0)),
        output_usd=float(lifetime.get("output_savings_usd", 0.0)),
        cache_usd=float(lifetime.get("cache_savings_usd", 0.0)),
        cache_read_tokens=int(lifetime.get("cache_read_tokens", 0)),
        input_cost_usd=float(lifetime.get("total_input_cost_usd", 0.0)),
        model=busiest[0],
        ok=True,
    )


# --- quota -------------------------------------------------------------------

def _seconds_to_reset(window: dict) -> float | None:
    """Prefer the absolute reset timestamp: `seconds_to_reset` ages with the file."""
    resets_at = window.get("resets_at")
    if resets_at:
        try:
            when = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00"))
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
        except ValueError:
            pass
    raw = window.get("seconds_to_reset")
    return float(raw) if raw is not None else None


def read_quota() -> QuotaMetrics:
    if not SUBSCRIPTION_STATE.exists():
        return QuotaMetrics()
    try:
        latest = _load_json(SUBSCRIPTION_STATE).get("latest", {})
    except (OSError, ValueError):
        return QuotaMetrics()
    if not latest:
        return QuotaMetrics()

    polled_at = time.time()
    windows = {}
    for key in ("five_hour", "seven_day"):
        raw = latest.get(key, {}) or {}
        windows[key] = QuotaWindow(
            utilization_pct=float(raw.get("utilization_pct") or 0.0),
            seconds_to_reset=_seconds_to_reset(raw),
            polled_at=polled_at,
        )

    extra = latest.get("extra_usage", {}) or {}
    return QuotaMetrics(
        five_hour=windows["five_hour"],
        seven_day=windows["seven_day"],
        extra_used_usd=float(extra.get("used_credits_usd") or 0.0),
        extra_limit_usd=float(extra.get("monthly_limit_usd") or 0.0),
        ok=True,
    )


# --- commits -----------------------------------------------------------------

_GH_QUERY = """
query {
  viewer {
    contributionsCollection {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def _window() -> list[date]:
    today = datetime.now().date()
    return [today - timedelta(days=COMMIT_DAYS - 1 - i) for i in range(COMMIT_DAYS)]


def _as_days(counts: dict[str, int], source: str) -> CommitMetrics:
    days = tuple((day.isoformat(), int(counts.get(day.isoformat(), 0))) for day in _window())
    return CommitMetrics(days=days, source=source, ok=True)


def read_commits_github() -> CommitMetrics | None:
    """The profile calendar itself: every repo, private included. Needs `gh auth`."""
    try:
        proc = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={_GH_QUERY}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        calendar = json.loads(proc.stdout)["data"]["viewer"]["contributionsCollection"][
            "contributionCalendar"
        ]
    except (ValueError, KeyError, TypeError):
        return None

    counts = {
        day["date"]: day["contributionCount"]
        for week in calendar.get("weeks", [])
        for day in week.get("contributionDays", [])
    }
    return _as_days(counts, "github")


def _git_repos() -> list[Path]:
    repos: list[Path] = []
    max_depth = git_scan_depth()
    for root in git_scan_roots():
        if not root.is_dir():
            continue
        for git_dir in root.glob("/".join(["*"] * max_depth + [".git"])):
            repos.append(git_dir.parent)
        for depth in range(1, max_depth):
            for git_dir in root.glob("/".join(["*"] * depth + [".git"])):
                repos.append(git_dir.parent)
    return sorted(set(repos))


def read_commits_git() -> CommitMetrics:
    """Offline fallback: local clones only, so it under-reports by construction."""
    try:
        email = subprocess.run(
            ["git", "config", "--get", "user.email"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        email = ""

    counts: dict[str, int] = {}
    since = (datetime.now().date() - timedelta(days=COMMIT_DAYS)).isoformat()
    for repo in _git_repos():
        command = ["git", "-C", str(repo), "log", f"--since={since}", "--pretty=%cs"]
        if email:
            command.insert(4, f"--author={email}")
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines():
            day = line.strip()
            if day:
                counts[day] = counts.get(day, 0) + 1
    return _as_days(counts, "git")


def read_commits() -> CommitMetrics:
    return read_commits_github() or read_commits_git()
