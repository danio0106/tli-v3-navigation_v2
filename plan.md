## Plan: Native-Only Full-Throttle Roadmap

Hard pivot: native runtime becomes the only runtime path. No Python fallback, no shadow mode, no dual execution.

## Non-Negotiable Direction
1. Python scanner path is removed from production flow.
2. Python overlay worker path is removed from production flow.
3. Runtime startup must fail fast if required native components are unavailable.
4. Any milestone that cannot run native-only is considered incomplete.

## Baseline (Completed)
1. `v5.70.0` - Native-only runtime enforcement completed
2. `v5.71.0` - Native scanner bootstrap + 120 Hz position cache completed
3. Python scanner fallback removed from `NativeRuntimeManager`.
4. Config policy locked to strict native runtime/scanner enablement.
5. Runtime now fails fast when native scanner cannot initialize.

## Recently Completed
1. `v5.72.0` - Events and portals in native scanner.
2. `v5.73.0` - Native overlay worker cutover (Qt consumes engine snapshot cache, no app-side heavy reader).
3. `v5.74.0` - Native-only bot loop integration (engine no longer depends on scanner private fields in runtime path; critical reads now use scanner runtime API wrapper).
4. `v5.76.0` - Native guard/event feed hardening (required scanner API validation + corruption-resistant overlay/event feed filtering + public player-position API-only control loops).
5. `v5.77.0` - Qt Quick native renderer pilot for core overlay layers (player/path/portal/event/guard) consuming engine snapshots directly.
6. `v5.78.0` - Qt Quick full-layer completion (grid/nav-collision/debug layers + overlay LOD controls; Qt Quick is now the primary overlay runtime path).

## Next Versioned Steps (from v5.82.0)
1. `v5.83.0` - Native profiling implementation kickoff.
2. Deliverables:
3. Instrument top runtime hotspots selected in v5.81 backlog.
4. Collect baseline timings from real map-cycle runs.
5. Produce implementation-priority migration queue from measured bottlenecks.
6. Gate criteria:
7. Profiling artifacts are reproducible from one-click run instructions.
8. Next native module target is chosen from measured top bottleneck.

9. `v5.84.0` - Native overlay worker implementation.
11. Deliverables:
12. Add C++ overlay worker implementation (threaded) in native module path.
13. Move heavy overlay marker aggregation into native worker-backed feed.
14. Keep Qt feed contract (`get_overlay_snapshot()` shape) stable.
15. Gate criteria:
16. Runtime status reports overlay worker as implemented.
17. High-load overlays remain responsive without Python heavy-read worker.

## Newly Completed (Earlier)
1. `v5.81.0` - Phase A immediate stability fix.
2. Deliverables:
3. Fixed malformed overlay snapshot worker body in `src/core/bot_engine.py`.
4. Restored dropped-marker counters in snapshot payload (`dropped_event_markers`, `dropped_guard_markers`).
5. Verified core runtime file syntax is clean (`bot_engine.py` no parser errors).
6. Version bump completed to `v5.81.0`.
7. Gate criteria:
8. Overlay worker function compiles/loads correctly.
9. In-game smoke verification pending user run (attach + overlay ON).

## Newly Completed
1. `v5.82.0` - Qt overlay parity/visibility restoration pass.
2. Deliverables:
3. Player coordinates label readability upgraded (larger bold green text with dark halo).
4. Restored on-map labels for portal/event/guard/waypoint/entity markers in Qt Quick renderer.
5. Added minimap/radar panel to Qt Quick overlay (bottom-right).
6. Restored entity marker feed on Qt path via `BotEngine` snapshot (`entity_markers`) + app forwarding.
7. Nav-line anchoring corrected to player center in Qt payload.
8. Gate criteria:
9. User confirms readability improvement and marker/radar/nav-line parity in live map run.

## Relevant Files
- `src/core/native_runtime.py` - native module loading and scanner creation policy.
- `src/core/bot_engine.py` - strict native scanner wiring and lifecycle.
- `src/gui_qt/app.py` - native snapshot consumption path.
- `src/core/scanner.py` - migration source for logic moved into native side.
- `src/core/portal_detector.py` - portal/event semantics to mirror natively.
- `src/native/cpp/module.cpp` - pybind module entry points.
- `src/core/bot_engine.py` - engine-owned overlay snapshot worker (active Qt path).
- `pyproject.toml` - native build dependencies/workflow.
- `.github/copilot-instructions.md` - architecture and maintenance notes.
- `CHAT_LOG.md` - version-by-version implementation evidence.

## Verification
1. Build native module in clean venv and verify strict startup behavior.
2. Validate attach -> scan -> map run flow with native scanner only.
3. Validate overlay feed and event/portal markers with native snapshot only.
4. Run long stability sessions (>=4h) with native-only data path.
5. Confirm no runtime imports or calls reintroduce Python fallback scanner path.

## Decisions
- Architecture is now native-first and native-required.
- Reliability is achieved by hardening native components, not by fallback paths.
- Migration sequencing remains versioned, but each milestone is native-only complete before moving forward.

## Global Rules (Native-Only Runtime)
1. No Python fallback in scanner or overlay runtime paths.
2. No shadow comparison mode in production runtime path.
3. Any missing native component is a startup failure, not a downgrade trigger.
4. Map selection safety logic must remain fully functional while moving data sourcing to native.
5. Event/portal/guard semantics must remain intact under native-only sourcing.
6. All new runtime reads are added to native path first; Python implementation is not the source of truth.
7. Runtime config must not expose fallback toggles that re-enable Python scanner paths.

## Remediation Plan (Fix All 6 Gaps)

Goal in plain terms:
- Make the app stable again first.
- Replace bridge/stub native parts with real native scanner + native overlay worker.
- Keep bot behavior the same (or safer) while migrating.

### Phase A - Immediate Stability Fix (Issue #1)
1. Fix malformed overlay worker function body in `src/core/bot_engine.py`.
2. Re-run syntax + import checks on core runtime files.
3. Smoke test: app starts, can attach, overlay ON does not crash.
4. Exit criteria:
- no parser/syntax errors,
- no startup failure in normal launch,
- overlay snapshot thread starts/stops cleanly.

### Phase B - Remove Native Scanner Bridge Stub (Issue #2)
1. Replace `src/native/cpp/module.cpp:create_scanner(...)` bridge implementation (currently returns Python `UE4Scanner`) with a true native scanner object.
2. Implement required runtime API methods in native scanner backend used by `NativeRuntimeManager` strict contract:
- chain scan/fnamepool/gobjects,
- player position + zone reads,
- typed events + monster/entity reads,
- carjack truck/guards,
- interactive items + boss room,
- minimap visited positions,
- nav collision markers,
- fightmgr pointer + object lookup + cancel lifecycle.
3. Keep method names/signatures stable so existing engine logic does not need broad rewrites.
4. Exit criteria:
- `create_scanner(...)` no longer imports Python scanner,
- runtime map-cycle works with native scanner implementation only.

### Phase C - Implement Real Native Overlay Worker (Issues #3 and #4)
1. Add C++ overlay worker implementation (threaded) in native module path.
2. Move heavy overlay marker aggregation into native worker-backed feed.
3. Keep Qt feed contract unchanged (`get_overlay_snapshot()` shape remains stable) so UI migration risk stays low.
4. Update runtime status reporting so `overlay_worker` no longer shows `not_implemented`.
5. Exit criteria:
- overlay worker is native-backed,
- high-load overlays remain responsive,
- no Python-only heavy overlay scan loop required for runtime.

### Phase D - Align Documentation and Operational Truth (Issue #5)
1. Update `src/native/README.md` to reflect strict-native architecture and current startup behavior.
2. Remove stale statements about opt-in mode and Python fallback.
3. Add a short operator section: build, verify, and failure meaning in plain language.
4. Exit criteria:
- docs match real runtime behavior,
- no contradictory migration guidance remains.

### Phase E - Add Behavior Parity Validation (Issue #6)
1. Expand validation beyond "method exists":
- add behavior checks for critical reads (position, zone, event types, guards, portals, nav markers).
2. Add side-by-side debug harness for controlled test mode only (not production fallback):
- compare native outputs vs reference expectations/log baselines.
3. Add reliability thresholds and fail-fast policy when native output is invalid/out-of-range.
4. Exit criteria:
- parity checks pass for core gameplay cycle,
- regressions are detected early by automated checks.

### Versioned Delivery Sequence
1. `v5.81.0` - Phase A complete (stability fix) + migration scaffolding for Phase B.
2. `v5.82.0` - Qt overlay parity/visibility restoration pass (readability + labels + radar + nav-line anchoring).
3. `v5.83.0` - Phase B first native scanner runtime slice complete (native scanner object + C++-owned `read_player_xy`; remaining runtime APIs delegated via injected backend during transition).
4. `v5.84.0` - Phase C native overlay worker integration complete.
5. `v5.85.0` - Phase B full scanner completion + doc alignment (Phase D).
6. `v5.86.0` - Phase E parity/reliability harness + gate criteria enforcement.

### Non-Developer Test Checklist (What You Will Do)
1. Start bot and confirm it opens without immediate errors.
2. Attach to game and confirm coordinates/zone update.
3. Run one full map cycle and note:
- did it finish,
- did overlay markers appear correctly,
- any freeze/crash.
4. Run 5 consecutive maps and report:
- total successful runs,
- where it failed (if any),
- whether performance feels smoother/same/worse.
5. After final phase, run long session (target 4h) and report stability only (no code decisions needed).
