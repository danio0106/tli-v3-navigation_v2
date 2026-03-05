## Plan: Versioned C++ Migration Roadmap

Deliver scanner + overlay performance gains quickly with low regression risk by migrating in staged releases: C++ data workers first, renderer migration second, full-C++ runway last. Every release has explicit gates and rollback criteria.

**Steps**
1. `v5.69.0` - Native foundation + flags
2. Deliverables:
3. Add `src/native/` skeleton (`CMakeLists.txt`, `python_bridge.cpp`, `scanner_worker.cpp`, `overlay_worker.cpp` stubs).
4. Add build workflow in `pyproject.toml` for pybind11/CMake on Windows.
5. Add runtime flags: `native_scanner_enabled`, `native_overlay_worker_enabled`, `native_bridge_mode=off|shadow|active`.
6. Add diagnostics panel/log fields for native status (loaded, thread alive, errors).
7. Gate criteria:
8. Native module imports successfully in venv.
9. App startup unaffected when native module missing (graceful fallback).
10. No behavior change with all native flags off.
11. Rollback: set `native_bridge_mode=off`.

12. `v5.70.0` - C++ scanner worker (position channel only)
13. Deliverables:
14. Implement 120 Hz native thread reading only player position chain.
15. Expose bridge APIs: `start_scanner`, `stop_scanner`, `get_latest_scanner_snapshot`, `get_scanner_metrics`.
16. Keep Python `PositionPoller` active in parallel shadow mode.
17. Gate criteria:
18. Scanner cadence >=120 Hz mean, jitter p95 <=1.5 ms.
19. Position parity with Python poller >=99.9% within tolerance.
20. Zero crash/hang over 30 min continuous run.
21. Rollback: disable `native_scanner_enabled`.

22. `v5.71.0` - C++ scanner expansion (events/portals minimum set)
23. Deliverables:
24. Add native reads for fields required by overlay and routing diagnostics: typed event primitives and portal markers including exit semantic flag.
25. Add schema versioning for snapshot payloads.
26. Implement shadow comparator for semantic mismatches.
27. Gate criteria:
28. >=99.5% agreement on numeric fields.
29. Zero critical semantic mismatches across 20 full cycles:
30. wrong event type, missing exit portal, impossible zero markers while Python has stable markers.
31. Rollback: native snapshots ignored, Python scanner authoritative.

32. `v5.72.0` - C++ overlay worker (marker packing)
33. Deliverables:
34. Add second native thread to coalesce/filter/pack render-ready marker batches from native scanner state.
35. Expose `get_latest_overlay_snapshot` and drop/stale counters.
36. Keep current Python overlay worker in parallel for comparison.
37. Gate criteria:
38. Snapshot age p95 <=25 ms.
39. No unbounded queue growth.
40. Marker count parity within accepted tolerance in shadow mode.
41. Rollback: `native_overlay_worker_enabled=false`.

42. `v5.73.0` - Qt app consumption cutover (data path)
43. Deliverables:
44. In `src/gui_qt/app.py`, switch 60 Hz `QTimer` feed to pull native overlay snapshot in active mode.
45. Remove heavy memory reads from Python overlay feed path when native active.
46. Keep tkinter overlay backend operational.
47. Gate criteria:
48. Overlay render >=60 FPS mean, frame p95 <=20 ms in active maps.
49. UI remains responsive under heavy marker load (no freeze).
50. Startup-to-first-overlay-frame <=1.2 s.
51. Rollback: `native_bridge_mode=shadow` or `off`.

52. `v5.74.0` - Shadow soak and parity hardening
53. Deliverables:
54. Run native scanner + overlay in shadow by default; Python remains authoritative.
55. Add automated cycle-level parity reports and mismatch classification in logs.
56. Add watchdog auto-fallback when native thread stalls or snapshot schema invalid.
57. Gate criteria:
58. >=4h soak with no deadlocks/leaks.
59. 0 critical mismatches across >=40 cycles.
60. Auto-fallback triggers correctly in fault-injection test.
61. Rollback: remain in shadow mode by default.

62. `v5.75.0` - Active mode default for scanner + overlay workers
63. Deliverables:
64. Make native scanner/overlay workers default in production profile.
65. Keep Python paths hot-standby behind config.
66. Add one-click runtime toggle in settings for emergency fallback.
67. Gate criteria:
68. 3 consecutive user validation sessions pass with no blocker regressions.
69. Performance gain confirmed over Python baseline in same maps.
70. Rollback: switch profile to Python-only instantly.

71. `v5.76.0` - Qt Quick renderer pilot (GPU path)
72. Deliverables:
73. Introduce opt-in Qt Quick overlay renderer (`QQuickWindow`/QML) consuming the same native snapshot schema.
74. Keep tkinter renderer as stable fallback.
75. Port core layers first: player/path/portals/events/guards.
76. Gate criteria:
77. Visual parity with current overlay semantics.
78. 60 FPS stable on pilot maps.
79. Click-through and focus-hide behavior correct on Windows.
80. Rollback: renderer backend switch back to tkinter.

81. `v5.77.0` - Qt Quick full parity + debug layers
82. Deliverables:
83. Port remaining layers (grid/nav-collision/debug annotations).
84. Add LOD/decimation for heavy debug markers.
85. Keep debug-heavy layers default-off in production profile.
86. Gate criteria:
87. No regressions in diagnostic workflows.
88. No frame-time spikes >33 ms p99 in heavy debug sessions.
89. Rollback: disable debug layers or renderer switch.

90. `v5.78.0` - Python scanner retirement preparation
91. Deliverables:
92. Mark Python scanner hot loops as legacy path; keep minimal compatibility wrappers.
93. Freeze native snapshot schema v1 and document migration rules.
94. Reduce duplicate Python polling when native active.
95. Gate criteria:
96. Native-only data path stable for >=2 weeks of normal testing.
97. No required feature still dependent on Python scanner internals.
98. Rollback: unfreeze wrappers and re-enable Python polling.

99. `v5.79.0` - Full-C++ runway checkpoint
100. Deliverables:
101. Decision checkpoint: continue hybrid or start broader C++ behavior migration.
102. If proceeding, prioritize next modules by profile data: portal detector internals, FightMgr-heavy scans, then selected navigation preprocessing.
103. Gate criteria:
104. Clear measured ROI and test coverage plan for each next module.
105. No simultaneous rewrite of behavior logic and transport in same release.

**Relevant files**
- `pyproject.toml` - native build integration.
- `src/gui_qt/app.py` - 60 Hz pull integration and backend switching.
- `src/gui/overlay.py` - fallback renderer parity reference.
- `src/core/position_poller.py` - baseline comparator for v5.70.0.
- `src/core/scanner.py` - parity source and gradual retirement target.
- `src/core/portal_detector.py` - parity-critical semantic source.
- `src/core/bot_engine.py` - lifecycle, fallback orchestration, mode flags.
- `src/utils/constants.py` - feature flags and KPI thresholds.
- `src/native/CMakeLists.txt` - native build target definitions.
- `src/native/python_bridge.cpp` - pybind module API.
- `src/native/scanner_worker.cpp` - 120 Hz producer.
- `src/native/overlay_worker.cpp` - packed snapshot producer.
- `.github/copilot-instructions.md` - document architecture and maintenance.
- `CHAT_LOG.md` - release-by-release evidence and decisions.

**Verification**
1. Build/import checks on Windows venv for every release that touches native code.
2. Per-release KPI report generated and attached to session logs.
3. Shadow-mode parity reports for scanner and overlay fields before any active cutover.
4. Long soak tests at v5.74.0 and v5.75.0 milestones.
5. Manual in-game validation for portal/event/guard overlays each cutover step.

**Decisions**
- Use hybrid migration as default strategy; full rewrite deferred.
- C++ workers migrate first because they target the main bottleneck with lowest behavioral risk.
- Renderer migration (Qt Quick) is sequenced after data-path stabilization.
- Python fallback remains mandatory until native path proves long-run stability.

**Global Rules (Must-Preserve Scanner Feature Parity)**
1. No active cutover unless all critical scanner-dependent features are present and parity-verified; missing any item blocks promotion.
2. Position polling parity: 120 Hz player `x/y` feed, stable chain reads, and equivalent consumer freshness for RTNav + overlay.
3. Typed event parity: Carjack/Sandlord classification, `is_target_event`, event coordinates, and lifecycle timing transitions must match Python semantics.
4. Portal parity: in-map portal markers, exit-portal semantic flag (`is_exit`), and transition-time resilience (stale FightMgr recovery behavior equivalent).
5. Guard-feed parity: Carjack guard positions/count feed used by overlay/event flow must remain non-regressive under active fight conditions.
6. Navigation data parity: nav-collision marker extraction used by grid composition, wall/grid signals required by pathfinding, and any scanner-provided nav priors must be functionally equivalent.
7. Zone/map-state parity: real zone name reads and map-state transitions consumed by bot loop must not regress.
8. Map selection safety parity (critical): scanner/memory data used by map-selection process must preserve current behavior guarantees, including card UI-open detection, card identity inputs, and active-card safety checks that prevent wrong-card clicks.
9. Diagnostics parity: portal debug artifacts, key scanner KPIs, and reliability counters required for triage must remain available (or native-equivalent) during migration.
10. Shadow-mode comparator is mandatory for all above fields; active mode allowed only after passing parity gates across multi-cycle real runs.
