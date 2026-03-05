# Qt Migration Plan (v5.67.0)

## Goal
Introduce a safe PySide6 GUI shell without changing map-cycle runtime logic.

## Safety Policy
- `src/core/*` logic remains unchanged.
- Qt is now the default backend.
- Existing tkinter GUI remains available as explicit override and fallback.

## How to Run
- Qt (default):
  - `python main.py`
- Legacy tkinter override:
  - PowerShell: `$env:TLI_GUI_BACKEND='tk'; python main.py`

## Completed
- Phase 1:
  - New Qt package at `src/gui_qt/`.
  - Sidebar + stacked pages shell.
  - Read-only `EngineBridge` status feed.
  - Hard fallback to tkinter if Qt import/runtime fails.
- Phase 2:
  - Dashboard behavior ported (controls + status + log panel).
  - Settings behavior ported (grouped fields + save/reset parity).
  - Card Priority behavior ported (filters + rank edit + scan + mappings panel).
- Phase 3:
  - Address Setup behavior ported (attach/rescan/FNamePool/probe + debug-ui gating).
  - Map Paths behavior ported (mode toggle, recording controls, auto tools, explorer controls, waypoint editor/actions).
- Phase 4:
  - Qt shell helper controls added: `Overlay ON/OFF` and `Calibrate Scale`.
  - Overlay feed wiring enabled for Qt `Map Paths` waypoint/grid updates.
- Phase 5:
  - Startup backend switched to Qt-by-default (`TLI_GUI_BACKEND` now defaults to `qt`).
  - Tkinter kept as explicit override (`TLI_GUI_BACKEND=tk`) and as hard fallback when Qt launch fails.

## Current Scope
- New Qt package at `src/gui_qt/`.
- Sidebar + stacked pages shell.
- Real pages: Dashboard, Address Setup, Map Paths, Settings, Card Priority.
- Read-only `EngineBridge` status feed.
- Hard fallback to tkinter if Qt import/runtime fails.

## What Phase 1 Does NOT Change
- BotEngine navigation/event/overlay logic.
- Existing tkinter tab implementations.
- Production control paths used by current stable UI.

## Next Phases
1. Execute extended real in-game soak validation (Qt default path) and tune any residual UI/runtime regressions.
