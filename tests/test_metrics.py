"""Parsing, formatting and threshold logic - no Qt widgets, no display needed."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from token_hud import collectors, theme  # noqa: E402
from token_hud.gauges import (  # noqa: E402
    fmt_commit_day,
    fmt_contributions,
    fmt_duration,
    fmt_tokens,
    fmt_usd,
)
from token_hud.hud import TICKER_SIZE, ticker_cells  # noqa: E402
from token_hud.model import HeadroomMetrics, QuotaMetrics, QuotaWindow, RtkMetrics, Snapshot  # noqa: E402


def test_snapshot_totals_sum_every_saving_source():
    snap = Snapshot(
        rtk=RtkMetrics(tokens_saved=42_803, ok=True),
        headroom=HeadroomMetrics(
            compression_tokens_saved=4_472,
            output_tokens_saved=15_262,
            compression_usd=0.013416,
            output_usd=0.22893,
            cache_usd=15.386685,
            ok=True,
        ),
    )
    assert snap.tokens_saved == 62_537
    assert snap.usd_saved == pytest.approx(15.629, abs=1e-3)
    assert [label for label, _v, _k in snap.breakdown] == ["rtk CLI", "headroom out", "compression"]


def test_quota_window_reports_free_percentage():
    assert QuotaWindow(utilization_pct=17.0).free_pct == 83.0
    assert QuotaWindow(utilization_pct=140.0).free_pct == 0.0


def test_ticker_size_is_806_by_44():
    assert TICKER_SIZE == (806, 44)
    assert TICKER_SIZE[1] == 44


def test_ticker_cells_place_seven_day_quota_left_of_rtk_ratio():
    seven = QuotaWindow(utilization_pct=42.0)
    snap = Snapshot(
        rtk=RtkMetrics(savings_pct=91.7, ok=True),
        quota=QuotaMetrics(
            five_hour=QuotaWindow(utilization_pct=17.0),
            seven_day=seven,
        ),
    )
    cells = ticker_cells(snap)
    labels = [label for _value, label, _color, _glow in cells]
    assert labels.index("quota 7 j") == labels.index("rtk ratio") - 1
    value, label, color, glow = next(cell for cell in cells if cell[1] == "quota 7 j")
    assert value == f"{seven.free_pct:.0f} % libre"
    assert label == "quota 7 j"
    assert color == theme.quota_color(seven.utilization_pct)
    assert glow is False


@pytest.mark.parametrize(
    "pct,expected",
    [(0.0, theme.CYAN), (69.9, theme.CYAN), (70.0, theme.ORANGE), (89.9, theme.ORANGE), (90.0, theme.RED)],
)
def test_quota_color_thresholds(pct, expected):
    assert theme.quota_color(pct) == expected


def test_formatters():
    assert fmt_tokens(62_537) == "62,5K"
    assert fmt_tokens(6_313_105) == "6,31 M"
    assert fmt_tokens(940) == "940"
    assert fmt_usd(15.629) == "15,63 $"
    assert fmt_duration(None) == "-"
    assert fmt_duration(12_075) == "3 h 21"
    assert fmt_duration(599_475) == "6 j 22 h"


def test_commit_tooltip_formatters():
    assert fmt_commit_day("2026-08-07") == "07/08/2026"
    assert fmt_commit_day("pas-une-date") == "pas-une-date"
    assert fmt_contributions(0) == "Aucune contribution"
    assert fmt_contributions(1) == "1 contribution"
    assert fmt_contributions(12) == "12 contributions"


def test_read_headroom_uses_lifetime_and_busiest_model(tmp_path, monkeypatch):
    payload = {
        "lifetime": {
            "requests": 226,
            "tokens_saved": 4472,
            "output_tokens_saved": 15262,
            "compression_savings_usd": 0.013416,
            "output_savings_usd": 0.22893,
            "cache_savings_usd": 15.386685,
            "cache_read_tokens": 5128895,
            "total_input_cost_usd": 17.512782,
        },
        "by_model": {
            "claude-sonnet-5": {"requests": 78},
            "claude-opus-5": {"requests": 173},
        },
    }
    path = tmp_path / "proxy_savings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(collectors, "PROXY_SAVINGS", path)

    metrics = collectors.read_headroom()
    assert metrics.ok and metrics.model == "claude-opus-5"
    assert metrics.output_tokens_saved == 15262
    assert metrics.input_cost_usd == pytest.approx(17.512782)


def test_read_quota_prefers_absolute_reset_timestamp(tmp_path, monkeypatch):
    payload = {
        "latest": {
            "five_hour": {"utilization_pct": 17.0, "resets_at": "2099-01-01T00:00:00Z", "seconds_to_reset": 1},
            "seven_day": {"utilization_pct": 2.0, "resets_at": None, "seconds_to_reset": 600.0},
            "extra_usage": {"used_credits_usd": 0.0, "monthly_limit_usd": 40.0},
        }
    }
    path = tmp_path / "subscription_state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(collectors, "SUBSCRIPTION_STATE", path)

    quota = collectors.read_quota()
    assert quota.ok
    assert quota.five_hour.seconds_to_reset > 1  # stale relative value ignored
    assert quota.seven_day.seconds_to_reset == 600.0
    assert quota.extra_limit_usd == 40.0


def test_collectors_degrade_to_empty_metrics_on_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(collectors, "PROXY_SAVINGS", tmp_path / "nope.json")
    monkeypatch.setattr(collectors, "SUBSCRIPTION_STATE", tmp_path / "nope2.json")
    assert collectors.read_headroom().ok is False
    assert collectors.read_quota().ok is False


def test_read_rtk_survives_broken_output(monkeypatch):
    class Proc:
        returncode = 0
        stdout = "not json"

    monkeypatch.setattr(collectors.subprocess, "run", lambda *a, **k: Proc())
    assert collectors.read_rtk() == RtkMetrics()


# --- commit heatmap ----------------------------------------------------------

def _commits(counts):
    from datetime import date, timedelta

    from token_hud.model import CommitMetrics

    today = date(2026, 8, 6)
    days = tuple(
        ((today - timedelta(days=len(counts) - 1 - i)).isoformat(), c)
        for i, c in enumerate(counts)
    )
    return CommitMetrics(days=days, source="github", ok=True)


def test_commit_totals_and_streaks():
    metrics = _commits([0, 5, 14, 6, 0, 3, 1])
    assert metrics.total == 29
    assert metrics.peak == 14
    assert metrics.streak == 2       # 3 then 1, stopped by the zero before them
    assert metrics.best_streak == 3  # 5, 14, 6


def test_commit_levels_use_quantiles_not_fixed_thresholds():
    metrics = _commits([0, 5, 14, 6, 3, 8, 7, 0, 5])
    assert metrics.level_of(0) == 0
    assert metrics.level_of(14) == 4          # the peak is the only glowing level
    assert metrics.level_of(3) < metrics.level_of(8)
    assert len({metrics.level_of(v) for v in (3, 5, 7, 8, 14)}) >= 3


def test_commit_metrics_without_activity_stay_at_level_zero():
    metrics = _commits([0] * 30)
    assert metrics.levels() == {}
    assert metrics.level_of(0) == 0
    assert metrics.streak == 0


def test_read_commits_falls_back_to_git_when_github_unavailable(monkeypatch):
    from dataclasses import replace

    local = replace(_commits([1, 2]), source="git")
    monkeypatch.setattr(collectors, "read_commits_github", lambda: None)
    monkeypatch.setattr(collectors, "read_commits_git", lambda: local)
    assert collectors.read_commits().source == "git"

    monkeypatch.setattr(collectors, "read_commits_github", lambda: _commits([3]))
    assert collectors.read_commits().source == "github"  # GitHub wins when it answers


def test_git_scan_roots_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv(collectors.GIT_ROOTS_ENV, raising=False)
    assert collectors.git_scan_roots() == collectors.DEFAULT_GIT_SCAN_ROOTS

    monkeypatch.setenv(collectors.GIT_ROOTS_ENV, os.pathsep.join([str(tmp_path), "~/code", " "]))
    assert collectors.git_scan_roots() == (tmp_path, Path.home() / "code")


def test_git_scan_depth_falls_back_when_unset_or_invalid(monkeypatch):
    monkeypatch.delenv(collectors.GIT_DEPTH_ENV, raising=False)
    assert collectors.git_scan_depth() == collectors.DEFAULT_GIT_SCAN_DEPTH

    monkeypatch.setenv(collectors.GIT_DEPTH_ENV, "nope")
    assert collectors.git_scan_depth() == collectors.DEFAULT_GIT_SCAN_DEPTH

    monkeypatch.setenv(collectors.GIT_DEPTH_ENV, "0")
    assert collectors.git_scan_depth() == 1  # a zero-deep scan would find nothing


def test_github_reader_returns_none_on_broken_payload(monkeypatch):
    class Proc:
        returncode = 0
        stdout = '{"data": {}}'

    monkeypatch.setattr(collectors.subprocess, "run", lambda *a, **k: Proc())
    assert collectors.read_commits_github() is None


def test_github_reader_maps_calendar_onto_the_rolling_window(monkeypatch):
    from datetime import date, timedelta

    today = date.today()
    payload = {
        "data": {"viewer": {"contributionsCollection": {"contributionCalendar": {"weeks": [
            {"contributionDays": [
                {"date": (today - timedelta(days=1)).isoformat(), "contributionCount": 4},
                {"date": today.isoformat(), "contributionCount": 2},
                {"date": (today - timedelta(days=400)).isoformat(), "contributionCount": 9},
            ]}
        ]}}}}
    }

    class Proc:
        returncode = 0
        stdout = json.dumps(payload)

    monkeypatch.setattr(collectors.subprocess, "run", lambda *a, **k: Proc())
    metrics = collectors.read_commits_github()
    assert metrics is not None
    assert len(metrics.days) == collectors.COMMIT_DAYS
    assert metrics.days[-1] == (today.isoformat(), 2)
    assert metrics.total == 6  # the 400-day-old day falls outside the window
