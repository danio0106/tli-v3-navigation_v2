# Cleanup Proposal (v5.56.0)

## Scope
Focused on: map-cycle reliability, runtime latency, log I/O pressure, and repository hygiene.

## What was changed now (safe, low-risk)
1. Heavy diagnostics are gated by `runtime_debug_heavy_enabled` (default `False`).
   - Nav collision probe no longer runs by default.
   - Portal deep-debug no longer runs by default.
2. Input spam logs are off by default (`input_debug_logging=False`).
   - Removes high-frequency per-action key/mouse INFO lines in normal runs.
3. Memory read-failure log flood is throttled in `MemoryReader`.
   - Repeated failures are batched with suppression counts.

## Findings from latest run (`bot_20260304_220146.log`)
- Portal data is present and stable (`PortalDebug accepted>0` continuously).
- Explorer did not show successful hop-route selection logs (`A* no direct path; routing via portal ...`) in sampled sections.
- Main runtime pressure came from high-rate input logs + debug probe loops + repeated read-failure debug flood.

## High-confidence removal/archive candidates (do not affect runtime)
These are not referenced by runtime paths (`src/`, `main.py`) and are safe to move to an archive folder.

### Scripts (research/forensics utilities)
- `scripts/_probe_extract.py`
- `scripts/analyze_probe.py`
- `scripts/analyze_probe2.py`
- `scripts/capture_pirates_template.py`
- `scripts/carjack_quick_report.ps1`
- `scripts/dump_class_props.py`
- `scripts/find_card_arrays.py`
- `scripts/find_guard_track.ps1`
- `scripts/find_props_regex.py`
- `scripts/find_props_simple.py`
- `scripts/find_props_simple2.py`
- `scripts/find_ui_props.py`
- `scripts/find_ui_props2.py`
- `scripts/guard_evidence_report.ps1`
- `scripts/parse_card_probe.py`
- `scripts/parse_prev_probe.py`
- `scripts/read_card_slots.py`
- `scripts/test_search.py`

### Root-level research artifacts
- `array_analysis.txt`
- `final_structs.txt`
- `map_arrays.txt`
- `search_dump.py`
- `search_ui_arrays.py`
- `test_search.py`

### Debug data artifacts (can be moved out of repo)
- Entire `debug/` folder contents (local evidence snapshots only)

## Candidate UI simplifications (next phase, optional)
These are likely debug-heavy and can be hidden behind a `debug_ui_enabled` flag (default `False`) before full removal:
- `Address Setup` tab probe actions (`Probe Events`, `Probe Card Memory`)
- `Entity Scanner` tab
- Advanced card memory deep-probe paths

## Do NOT remove (core map-cycle)
- `MapSelector`, `RTNavigator`, `WallScanner`, `Pathfinder`, `PortalDetector`, `BotEngine`
- `Paths` tab (recording/manual assist)
- `Settings` tab essential runtime knobs
- `Card Priority` tab unless a hardcoded static map-card strategy replaces it fully

## Recommended next cleanup step (safe)
1. Create `archive/tools_legacy/` and move all listed `scripts/*` + root research `.txt/.py` there.
2. Remove `debug/` tracked artifacts from repo (keep local copies if needed).
3. Keep runtime code unchanged after move; verify startup + one explorer run.
