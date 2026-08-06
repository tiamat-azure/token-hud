# ⚡ Token HUD

![Ticker to dashboard and back](images/demo.gif)

**Neon, Grafana-flavoured desktop widget for Claude Code economics.** Three
questions at a glance: how many tokens `rtk` and `headroom` saved you, what that
is worth in dollars, and how much of your usage window is left before the reset.

Frameless, always on top, draggable, no taskbar entry. Two modes, one code base:
a full **dashboard** (560x380) and a collapsed **ticker** bar (720x44) that lives
quietly along an edge of the screen. Double-click switches between them, as in
the animation above.

![Dashboard](images/dashboard.png)

## 🚀 Run

Managed with [uv](https://docs.astral.sh/uv/), driven through a Makefile.

```sh
make dev    # create .venv, install runtime + dev dependencies
make run    # launch the HUD
make check  # ruff + pytest, no display needed
```

`make help` lists every target. Without make: `uv sync` then
`uv run python -m token_hud`, or install the `token-hud` script.

Right-click the widget, or use the tray icon, for show/hide, mode switch, quit.
Position and mode are remembered across restarts, and so is the saved-token
series behind the trend panel.

## 🔌 Data sources

Everything is read locally except the commit calendar, the only network hop, and
it runs once every 10 minutes.

| Metric | Source |
| --- | --- |
| Tokens saved, savings ratio, command count | `rtk gain -f json` (30 s) |
| Compression + output savings, cache savings, spend, model | `~/.headroom/proxy_savings.json` (5 s) |
| 5 h / 7 d usage windows, extra credits | `~/.headroom/subscription_state.json` (15 s) |
| Rolling 30-day commit calendar | `gh api graphql` -> `contributionsCollection`, fallback `git log` (600 s) |

Every read runs in a `QThreadPool` worker; the UI thread only receives a
`Snapshot`. A missing or malformed source degrades to empty metrics and a red
liveness dot - it never raises.

Two upstream quirks worked around: `five_hour.limit` is reported as `0`, so
absolute "tokens remaining" is not derivable and the HUD shows **percentage used
+ time to reset** instead; and `seconds_to_reset` ages with the file, so
`resets_at` wins and the countdown is recomputed every second.

## 🟩 Commit heatmap

A GitHub-style rolling month: one column per calendar week, one row per weekday,
dark green to neon green. Levels are **quantile** cut-offs like GitHub's own
graph - a fixed scale would flatten a spiky month - and only the top level gets a
halo, otherwise the whole grid glows and the hierarchy disappears. In the ticker,
44 px cannot hold seven rows, so it degrades to one bar per day with height *and*
colour carrying the count.

`gh api graphql` reproduces the profile page exactly, private repos included. If
`gh` is missing or unauthenticated, `git log` over local clones takes over - it
under-reports by construction, so the panel labels itself `local`. That fallback
walks the filesystem, so where it walks is configurable:

```sh
TOKEN_HUD_GIT_ROOTS="$HOME/code:/srv/repos" TOKEN_HUD_GIT_DEPTH=2 make run
```

It defaults to `~/workspaces`, `~/src` and `~/projects`, three levels deep, and
only ever runs `git log`.

![Ticker](images/ticker.png)

## 🚨 Alerts

![Alert state](images/alert.png)

The window border is cyan below 70 % usage, solid orange from 70 %, and pulses
red from 90 % (`theme.WARN_PCT` / `theme.CRIT_PCT`). Quota gauges follow the same
scale.

## 🗂️ Layout

```
token_hud/
  __main__.py    entry point, tray, Ctrl+C handling
  hud.py         frameless window, dashboard + ticker views, tray menu
  gauges.py      QPainter widgets: KPI tile, donut, radial gauge, area, thin bar
  store.py       timers, thread pool fan-in, persisted history
  collectors.py  rtk + headroom + quota readers
  model.py       frozen dataclasses passed to the UI
  theme.py       palette, fonts, glow/arc painting helpers
tests/           parsing, thresholds, formatters, graceful degradation
```

## ⚠️ Known limits

- Wayland compositors may ignore `WindowStaysOnTopHint` and programmatic moves.
  Run under XWayland (`QT_QPA_PLATFORM=xcb`) if the widget will not stay on top.
- `rtk cc-economics` is currently broken upstream (ccusage: `missing field
  'month'`) and is deliberately not used.
