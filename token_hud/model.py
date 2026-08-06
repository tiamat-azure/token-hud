"""Immutable value objects passed from the collectors to the UI thread.

Every dataclass here is frozen and has defaults for all fields: a collector that fails
returns a default-constructed instance, so the UI always renders, worst case as zeros.
Update through `dataclasses.replace` or `Snapshot.merged`, never by assignment - the
widgets rely on receiving a fresh object to know something changed.

`Snapshot` aggregates the four sources and exposes the derived values the gauges read
(`tokens_saved`, `usd_saved`, `breakdown`). Put new arithmetic here rather than in a
`paintEvent`: this module is the part covered by `tests/test_metrics.py`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class RtkMetrics:
    commands: int = 0
    tokens_saved: int = 0
    savings_pct: float = 0.0
    ok: bool = False


@dataclass(frozen=True)
class HeadroomMetrics:
    requests: int = 0
    compression_tokens_saved: int = 0
    output_tokens_saved: int = 0
    compression_usd: float = 0.0
    output_usd: float = 0.0
    cache_usd: float = 0.0
    cache_read_tokens: int = 0
    input_cost_usd: float = 0.0
    model: str = ""
    ok: bool = False


@dataclass(frozen=True)
class QuotaWindow:
    """`limit` is unreliable upstream (often 0), so the widget shows percentages."""

    utilization_pct: float = 0.0
    seconds_to_reset: float | None = None
    polled_at: float = 0.0

    @property
    def free_pct(self) -> float:
        return max(0.0, 100.0 - self.utilization_pct)


@dataclass(frozen=True)
class QuotaMetrics:
    five_hour: QuotaWindow = field(default_factory=QuotaWindow)
    seven_day: QuotaWindow = field(default_factory=QuotaWindow)
    extra_used_usd: float = 0.0
    extra_limit_usd: float = 0.0
    ok: bool = False


@dataclass(frozen=True)
class CommitMetrics:
    """Rolling 30-day contribution calendar, oldest day first."""

    days: tuple[tuple[str, int], ...] = ()  # (ISO date, commit count)
    source: str = ""  # "github" | "git" - shown when the fallback is in use
    ok: bool = False

    @property
    def total(self) -> int:
        return sum(count for _date, count in self.days)

    @property
    def peak(self) -> int:
        return max((count for _date, count in self.days), default=0)

    @property
    def streak(self) -> int:
        """Consecutive active days ending on the most recent day."""
        run = 0
        for _date, count in reversed(self.days):
            if count <= 0:
                break
            run += 1
        return run

    @property
    def best_streak(self) -> int:
        best = run = 0
        for _date, count in self.days:
            run = run + 1 if count > 0 else 0
            best = max(best, run)
        return best

    def levels(self) -> dict[int, int]:
        """Quantile cut-offs, GitHub-style: a fixed scale would flatten a spiky month."""
        active = sorted(count for _date, count in self.days if count > 0)
        if not active:
            return {}
        def quantile(ratio: float) -> int:
            return active[min(len(active) - 1, int(len(active) * ratio))]
        return {1: quantile(0.25), 2: quantile(0.55), 3: quantile(0.85)}

    def level_of(self, count: int) -> int:
        if count <= 0:
            return 0
        cuts = self.levels()
        if not cuts:
            return 0
        if count <= cuts[1]:
            return 1
        if count <= cuts[2]:
            return 2
        if count <= cuts[3]:
            return 3
        return 4


@dataclass(frozen=True)
class Snapshot:
    rtk: RtkMetrics = field(default_factory=RtkMetrics)
    headroom: HeadroomMetrics = field(default_factory=HeadroomMetrics)
    quota: QuotaMetrics = field(default_factory=QuotaMetrics)
    commits: CommitMetrics = field(default_factory=CommitMetrics)
    updated_at: float = field(default_factory=time.time)

    # --- derived ------------------------------------------------------------

    @property
    def tokens_saved(self) -> int:
        return (
            self.rtk.tokens_saved
            + self.headroom.compression_tokens_saved
            + self.headroom.output_tokens_saved
        )

    @property
    def usd_saved(self) -> float:
        h = self.headroom
        return h.compression_usd + h.output_usd + h.cache_usd

    @property
    def breakdown(self) -> list[tuple[str, int, str]]:
        """(label, tokens, palette key) - drives the donut and its legend."""
        h = self.headroom
        return [
            ("rtk CLI", self.rtk.tokens_saved, "cyan"),
            ("headroom out", h.output_tokens_saved, "violet"),
            ("compression", h.compression_tokens_saved, "green"),
        ]

    def merged(self, **changes) -> Snapshot:
        return replace(self, updated_at=time.time(), **changes)
