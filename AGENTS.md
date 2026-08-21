# Project decisions for agents

The following decisions are intentional. Do NOT silently revert them.

## Maintaining this file

Keep this file limited to information that is useful across nearly all future agent
sessions. Do not duplicate information already present in the codebase; instead, reference
the authoritative file or command. When updating this file, prefer revising or removing
existing entries over appending new ones. Keep this section intact and keep all entries
concise.

## Orientation

PyQt6 desktop widget showing how many tokens and dollars `rtk` and `headroom` saved. ~1.9
kLOC across 8 modules in `token_hud/`. Every module has a docstring stating its role -
read those before reading bodies.

Data flows one way:

```
collectors (worker threads) -> model (frozen dataclasses) -> store (timers + snapshot)
  -> hud (windows) -> gauges (custom paintEvent) -> theme (palette + paint helpers)
```

Read `token_hud/model.py` first when touching anything data-related: it is the contract
between the collectors and the UI, and it is short.

## Commands

`make help` lists everything. Use `make check` (ruff + pytest) before declaring work done.
Never call `python`/`pytest`/`ruff` directly - the project is uv-managed and the bare
interpreters have no PyQt6.

`make start` opens a real window in the background (PID in `.token-hud.pid`, logs in
`/tmp/token-hud.log`) and returns control immediately; `make stop` kills it. It needs a
display; in a headless session it fails at `QApplication`, which is expected and not a bug
to fix.

## Constraints that are easy to break

- **No I/O in the UI thread.** Every read goes through `collectors.job()` onto the
  `QThreadPool`. Adding a blocking call inside a widget or in `MetricsStore._apply`
  freezes the HUD.
- **Model objects are frozen.** Mutate via `dataclasses.replace` / `Snapshot.merged`,
  never by assignment. The UI relies on receiving a new object per update.
- **Collectors must never raise.** Every source is optional and may be absent; each reader
  returns a default-constructed metrics object on failure so the HUD degrades to zeros
  instead of dying.
- **One network hop only**, the GitHub commit calendar, at `COMMITS_INTERVAL_MS` (10 min).
  Do not add network calls to the fast timers. `read_commits` falls back to a local
  `git log` scan when the API is unavailable.
- **Colors and fonts come from `theme.py`.** No literal `QColor` in `gauges.py` or
  `hud.py`, apart from the few neutral white-alpha track/grid tints already there.
- **Gauges are hand-painted**, no stylesheets and no Qt Charts. New visuals subclass
  `_Panel` and implement `paintEvent`.

## Testing

`tests/test_metrics.py` covers the pure logic only: parsing in `collectors`, derived
properties in `model`, formatters in `gauges`. It never instantiates a widget or a
`QApplication`. Keep new logic testable that way - put it in `model.py` or in a free
function rather than inside a `paintEvent`.

## Environment

`TOKEN_HUD_GIT_ROOTS` (colon-separated) and `TOKEN_HUD_GIT_DEPTH` tune the git fallback
scan; see `collectors.git_scan_roots`. Window mode and position persist through
`QSettings("token-hud", "hud")`; the metric history lands in `store.history_path()`.

Under Wayland the HUD cannot stay on top, so `__main__._prefer_x11` forces
`QT_QPA_PLATFORM=xcb` unless the user set it. Do not remove that shim.

## Not to touch

`images/demo.gif` and the screenshots are generated - rebuild with `make gif`, never by
hand. `uv.lock` is likewise generated - change dependencies in `pyproject.toml` and let
`uv sync` rewrite it.
