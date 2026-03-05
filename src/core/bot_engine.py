import json
import os
import math
import time
import threading
from collections import deque
from typing import Optional, Callable, Dict, Any

from src.core.memory_reader import MemoryReader
from src.core.game_state import GameState
from src.core.address_manager import AddressManager
from src.core.window_manager import WindowManager
from src.core.input_controller import InputController
from src.core.path_recorder import PathRecorder
from src.core.waypoint import Waypoint
from src.core.map_selector import MapSelector
from src.core.scanner import UE4Scanner
from src.core.portal_detector import PortalDetector
from src.core.screen_capture import ScreenCapture
from src.core.hex_calibrator import HexCalibrator
from src.core.scale_calibrator import ScaleCalibrator
from src.core.wall_scanner import WallScanner, WallPoint
from src.core.pathfinder import Pathfinder
from src.core.rt_navigator import RTNavigator
from src.core.position_poller import PositionPoller
from src.core.card_database import CardDatabase
from src.core.memory_card_selector import MemoryCardSelector
from src.core.native_runtime import NativeRuntimeManager
from src.utils.constants import (
    BotState, GAME_PROCESS_NAME, CARD_SLOTS, EVENT_PROXIMITY_TRIGGER_UNITS,
    WALL_GRID_CELL_SIZE, WALL_GRID_HALF_SIZE,
    ZONE_WATCHER_EXIT_THRESHOLD, MINIMAP_SCAN_SKIP_THRESHOLD,
    MAP_EXPLORER_POSITION_SAMPLE_DIST, MAP_EXPLORER_POSITION_POLL_S,
    MAP_EXPLORER_POSITION_FLUSH_EVERY, MAP_EXPLORER_POSITION_FLUSH_S,
    MAP_EXPLORER_FRONTIER_ESTIMATE_MULTIPLIER,
    VISITED_CELL_WALKABLE_RADIUS,
    CARJACK_STRONGBOX_SEARCH_RADIUS_SQ,
    CARJACK_BOUNTY_UI_TEMPLATE_PATH,
    CARJACK_BOUNTY_UI_MATCH_THRESHOLD,
    CARJACK_BOUNTY_UI_SEARCH_REGION,
    CARJACK_BOUNTY_UI_CLICK_POSITIONS,
    CHARACTER_CENTER,
    EXIT_PORTAL_TEMPLATE_PATH, EXIT_PORTAL_MATCH_THRESHOLD,
    EXIT_PORTAL_SEARCH_REGION,
    HARDCODED_MAP_FINAL_DESTINATIONS,
    HARDCODED_MAP_PORTALS,
)

# Bot-controlled runtime tick cadence (not user-configurable in UI).
BOT_MAIN_LOOP_DELAY_S = 0.050
from src.utils.config_manager import ConfigManager
from src.utils.logger import log


class BotEngine:
    def __init__(self):
        self.config = ConfigManager()
        self._native_runtime = NativeRuntimeManager(self.config)
        self._native_runtime.initialize()
        self.memory = MemoryReader()
        self.addresses = AddressManager()
        self.game_state = GameState(self.memory, self.addresses)
        self._pos_poller = PositionPoller(self.game_state)  # single shared 120 Hz position reader
        self.window = WindowManager()
        input_mode = self.config.get("input_mode", "hardware")
        self.input = InputController(input_mode=input_mode)
        self.input.debug_input = bool(self.config.get("input_debug_logging", False))

        self._screen_capture = ScreenCapture(self.window)
        self._hex_calibrator = HexCalibrator(self._screen_capture)
        self._scale_calibrator = ScaleCalibrator(self.window, self.input)

        self._path_recorder = PathRecorder(self.game_state)
        self._card_database = CardDatabase()  # reads data/card_database.json (priority + texture mappings)
        self._map_selector = MapSelector(self.input, self.game_state, self.config, self._screen_capture, self._hex_calibrator)
        self._helper_rt_nav: Optional[RTNavigator] = None  # lazy; for manual nav + helpers
        self._manual_waypoints: Optional[list] = None
        self._manual_event_checker = None
        self._manual_event_handler = None

        self._state_lock = threading.Lock()
        self._state = BotState.IDLE
        self._previous_state = BotState.IDLE
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._paused = False
        self._stop_in_progress = False
        self._demo_mode = False
        self._state_callbacks: list = []
        self._scanner = None
        self._portal_detector = None

        self._maps_completed = 0
        self._start_time = 0.0
        self._navigation_completed = False
        self._handled_event_addrs: set = set()
        self.last_scan_failed: bool = False  # set True when scan_dump_chain fails — indicates outdated offsets

        # ── Cycle diagnostics (map-entry -> map-complete/error) ──────────
        self._cycle_active = False
        self._cycle_started_at = 0.0
        self._cycle_map_name = ""
        self._cycle_durations_s = deque(maxlen=50)
        self._cycle_total_count = 0
        self._cycle_success_count = 0
        self._cycle_fail_count = 0
        self._cycle_abort_count = 0
        self._cycle_best_s = float("inf")
        self._cycle_worst_s = 0.0
        self._last_cycle_duration_s = 0.0
        self._last_cycle_status = ""

        # Auto-navigation components (lazy-initialised on first map entry in auto mode)
        self._wall_scanner: Optional[WallScanner] = None
        self._pathfinder: Pathfinder = Pathfinder()
        self._pathfinder.set_wall_model_mode(self.config.get("wall_model_mode", "hybrid"))
        self._rt_navigator: Optional[RTNavigator] = None

        # Map explorer (independent of the main bot loop — triggered from the GUI)
        self._map_explorer = None           # MapExplorer instance while running
        self._explorer_thread: Optional[threading.Thread] = None
        self._explorer_stop_in_progress = False

        # Carjack bounty UI detection cache (lazy-loaded template).
        self._carjack_bounty_template = None
        self._carjack_bounty_template_loaded = False

        self._zone_change_detected = False
        self._last_valid_position = (0.0, 0.0)
        self._consecutive_read_failures = 0
        self._rescan_in_progress = False
        self._current_zone_name = ""

        # Debug overlay reference — set by the GUI when overlay is toggled on.
        # Passed to RTNavigator so it can feed live A* paths for visualisation.
        self._debug_overlay = None
        self._current_map_name = self.config.get("current_map", "")
        self._zone_watcher_active = False  # set True when zone-watcher thread is running
        self._overlay_zone_fname = ""
        self._overlay_zone_english = ""
        self._overlay_snapshot_lock = threading.Lock()
        self._overlay_snapshot: Dict[str, Any] = {
            "portal_positions": [],
            "event_markers": [],
            "guard_markers": [],
            "entity_markers": [],
            "nav_collision_markers": [],
            "dropped_event_markers": 0,
            "dropped_guard_markers": 0,
            "updated_at": 0.0,
        }
        self._overlay_worker_stop = threading.Event()
        self._overlay_worker_thread: Optional[threading.Thread] = None

        self._entering_phase = ""
        self._entering_start = 0.0
        self._entering_gworld_before = 0
        self._entering_last_log = 0.0

        self._state_handlers = {
            BotState.IDLE: self._handle_idle,
            BotState.STARTING: self._handle_starting,
            BotState.IN_HIDEOUT: self._handle_hideout,
            BotState.OPENING_MAP: self._handle_opening_map,
            BotState.SELECTING_MAP: self._handle_selecting_map,
            BotState.ENTERING_MAP: self._handle_entering_map,
            BotState.IN_MAP: self._handle_in_map,
            BotState.NAVIGATING: self._handle_navigating,
            BotState.RETURNING: self._handle_returning,
            BotState.MAP_COMPLETE: self._handle_map_complete,
            BotState.ERROR: self._handle_error,
        }

    @property
    def state(self) -> BotState:
        with self._state_lock:
            return self._state

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def is_paused(self) -> bool:
        with self._state_lock:
            return self._paused

    @property
    def path_recorder(self) -> Optional[PathRecorder]:
        return getattr(self, "_path_recorder", None)

    def _get_helper_rt_nav(self) -> RTNavigator:
        """Return a helper RTNavigator without spawning competing nav loops.

        If primary map navigation or explorer navigation is already running,
        reuse that active RTNavigator instead of creating a second instance.
        This avoids issuing an extra right-click (which toggles follow mode off)
        and prevents concurrent mouse-steering loops fighting each other.
        """
        # Reuse active primary auto navigator when available.
        if self._rt_navigator and self._rt_navigator.is_running:
            return self._rt_navigator

        # Reuse active explorer navigator when available.
        explorer_rt = getattr(self, "_explorer_rt_nav", None)
        if explorer_rt and explorer_rt.is_running:
            return explorer_rt

        rt = self._helper_rt_nav
        if rt is None:
            rt = RTNavigator(
                game_state       = self.game_state,
                input_ctrl       = self.input,
                pathfinder       = self._pathfinder,
                config           = self.config,
                pos_poller       = self._pos_poller,
                scale_calibrator = self._scale_calibrator,
            )
            if self._debug_overlay:
                rt.set_overlay(self._debug_overlay)
            if self._current_map_name:
                rt.set_map_name(self._current_map_name)
            self._helper_rt_nav = rt
        return rt

    @property
    def scanner(self) -> Optional[UE4Scanner]:
        return getattr(self, "_scanner", None)

    @property
    def card_database(self) -> CardDatabase:
        return self._card_database

    @property
    def scale_calibrator(self):
        return getattr(self, "_scale_calibrator", None)

    def set_debug_overlay(self, overlay):
        """Store overlay reference.  Passed to RTNavigator for live A* path display."""
        self._debug_overlay = overlay
        if overlay is not None:
            self._start_overlay_snapshot_worker()
        else:
            self._stop_overlay_snapshot_worker()

    def _resolve_overlay_current_map(self) -> str:
        scanner = self._scanner
        if scanner:
            try:
                internal = scanner.read_real_zone_name()
                if internal:
                    if internal != self._overlay_zone_fname:
                        self._overlay_zone_fname = internal
                        self._overlay_zone_english = internal
                        mapping_file = os.path.join("data", "zone_name_mapping.json")
                        if os.path.exists(mapping_file):
                            try:
                                with open(mapping_file, "r", encoding="utf-8") as f:
                                    mapping = json.load(f)
                                english = mapping.get(internal, "")
                                if english:
                                    self._overlay_zone_english = english
                            except Exception:
                                pass
                    return self._overlay_zone_english
            except Exception:
                pass
        return self.config.get("current_map", "hideout")

    def _collect_overlay_snapshot_data(self) -> Dict[str, Any]:
        portal_positions = []
        event_markers = []
        guard_markers = []
        entity_markers = []
        nav_collision_markers = []
        dropped_event_markers = 0
        dropped_guard_markers = 0

        def _valid_world_xy(x: Any, y: Any) -> bool:
            try:
                xf = float(x)
                yf = float(y)
            except Exception:
                return False
            if not (math.isfinite(xf) and math.isfinite(yf)):
                return False
            return abs(xf) <= 120000.0 and abs(yf) <= 120000.0

        portal_det = self._portal_detector
        if portal_det:
            try:
                if hasattr(portal_det, "get_portal_markers"):
                    portal_positions = portal_det.get_portal_markers() or []
                elif hasattr(portal_det, "get_portal_positions"):
                    portal_positions = portal_det.get_portal_positions() or []
            except Exception:
                portal_positions = []

        try:
            current_map = self._resolve_overlay_current_map()
            hardcoded = HARDCODED_MAP_PORTALS.get(current_map, []) or []
            existing = {
                (int(round(float(p.get("x", 0.0)))), int(round(float(p.get("y", 0.0)))))
                for p in portal_positions if isinstance(p, dict)
            }
            for hp in hardcoded:
                key = (int(round(float(hp.get("x", 0.0)))), int(round(float(hp.get("y", 0.0)))))
                if key in existing:
                    continue
                portal_positions.append({
                    "x": float(hp.get("x", 0.0)),
                    "y": float(hp.get("y", 0.0)),
                    "is_exit": bool(hp.get("is_exit", False)),
                })
                existing.add(key)
        except Exception:
            pass

        # Keep one portal marker per rounded (x,y), preferring exit semantics.
        try:
            dedup = {}
            for p in portal_positions:
                if not isinstance(p, dict):
                    continue
                x = float(p.get("x", 0.0))
                y = float(p.get("y", 0.0))
                key = (int(round(x)), int(round(y)))
                prev = dedup.get(key)
                if prev is None:
                    dedup[key] = dict(p)
                    continue
                if (not bool(prev.get("is_exit", False))) and bool(p.get("is_exit", False)):
                    dedup[key] = dict(p)
            portal_positions = list(dedup.values())
        except Exception:
            pass

        scanner = self._scanner
        if scanner:
            try:
                events = scanner.get_typed_events() or []
                for e in events:
                    if abs(e.position[0]) <= 1.0 and abs(e.position[1]) <= 1.0:
                        continue
                    if not _valid_world_xy(e.position[0], e.position[1]):
                        dropped_event_markers += 1
                        continue
                    event_markers.append({
                        "x": e.position[0],
                        "y": e.position[1],
                        "type": e.event_type,
                        "wave": e.wave_counter,
                        "guards": -1,
                        "guard_classes": "",
                        "is_target": e.is_target_event,
                    })
            except Exception:
                event_markers = []

            try:
                raw_guards = scanner.get_carjack_guard_positions() or []
                for g in raw_guards:
                    gx = g.get("x", 0.0)
                    gy = g.get("y", 0.0)
                    if not _valid_world_xy(gx, gy):
                        dropped_guard_markers += 1
                        continue
                    guard_markers.append(
                        {
                            "x": gx,
                            "y": gy,
                            "abp": g.get("abp", ""),
                            "score": 0.0,
                            "dist_truck": g.get("dist_truck", -1.0),
                        }
                    )
            except Exception:
                guard_markers = []

            try:
                # Keep overlay entities lightweight: only alive monsters near the player.
                monsters = scanner.get_monster_entities() or []
                px, py = self._pos_poller.get_pos()
                near_alive: List[Tuple[float, Any]] = []
                for m in monsters:
                    if int(getattr(m, "bvalid", 0) or 0) == 0:
                        continue
                    mx = float(m.position[0])
                    my = float(m.position[1])
                    if not _valid_world_xy(mx, my):
                        continue
                    if abs(px) > 1.0 or abs(py) > 1.0:
                        d2 = (mx - px) * (mx - px) + (my - py) * (my - py)
                    else:
                        d2 = 0.0
                    near_alive.append((d2, m))
                near_alive.sort(key=lambda it: it[0])

                for _, m in near_alive[:120]:
                    cls = str(getattr(m, "class_name", "") or "")
                    if cls:
                        cls = cls.split(".")[-1]
                    entity_markers.append(
                        {
                            "x": float(m.position[0]),
                            "y": float(m.position[1]),
                            "name": cls or "EMonster",
                        }
                    )
            except Exception:
                entity_markers = []

            try:
                if self.config.get("nav_collision_overlay_enabled", False):
                    raw_markers = scanner.get_nav_collision_markers() or []
                    show_raw = bool(self.config.get("nav_collision_overlay_show_raw", True))
                    show_inflated = bool(self.config.get("nav_collision_overlay_inflate_debug", False))
                    try:
                        inflate_u = float(self.config.get("nav_collision_grid_inflate_u", 0.0) or 0.0)
                    except Exception:
                        inflate_u = 0.0

                    if show_raw:
                        for m in raw_markers:
                            mm = dict(m)
                            mm["overlay_style"] = "raw"
                            mm["overlay_label"] = ""
                            nav_collision_markers.append(mm)

                    if show_inflated and inflate_u > 0.0:
                        for m in raw_markers:
                            mm = dict(m)
                            mm["extent_x"] = max(1.0, float(m.get("extent_x", 0.0)) + inflate_u)
                            mm["extent_y"] = max(1.0, float(m.get("extent_y", 0.0)) + inflate_u)
                            mm["overlay_style"] = "inflated"
                            mm["overlay_label"] = "NAV+"
                            nav_collision_markers.append(mm)
            except Exception:
                nav_collision_markers = []

        return {
            "portal_positions": portal_positions,
            "event_markers": event_markers,
            "guard_markers": guard_markers,
            "entity_markers": entity_markers,
            "nav_collision_markers": nav_collision_markers,
            "dropped_event_markers": dropped_event_markers,
            "dropped_guard_markers": dropped_guard_markers,
            "updated_at": time.monotonic(),
        }

    def _start_overlay_snapshot_worker(self):
        if self._overlay_worker_thread and self._overlay_worker_thread.is_alive():
            self._native_runtime.set_overlay_worker_state("native:engine-overlay-snapshot", True)
            return

        self._overlay_worker_stop.clear()

        def _worker_loop():
            interval_s = 0.20
            while not self._overlay_worker_stop.is_set():
                t0 = time.monotonic()
                try:
                    if self.memory.is_attached and self._scanner:
                        snapshot = self._collect_overlay_snapshot_data()
                        with self._overlay_snapshot_lock:
                            self._overlay_snapshot = snapshot
                except Exception:
                    pass

                sleep_for = interval_s - (time.monotonic() - t0)
                if sleep_for > 0.001:
                    time.sleep(sleep_for)

        self._overlay_worker_thread = threading.Thread(
            target=_worker_loop,
            daemon=True,
            name="EngineOverlaySnapshotWorker",
        )
        self._overlay_worker_thread.start()
        self._native_runtime.set_overlay_worker_state("native:engine-overlay-snapshot", True)

    def _stop_overlay_snapshot_worker(self):
        self._overlay_worker_stop.set()
        t = self._overlay_worker_thread
        if t and t.is_alive():
            t.join(timeout=0.5)
        self._overlay_worker_thread = None
        self._native_runtime.set_overlay_worker_state("native:engine-overlay-snapshot", False)

    def get_overlay_snapshot(self) -> Dict[str, Any]:
        with self._overlay_snapshot_lock:
            return {
                "portal_positions": list(self._overlay_snapshot.get("portal_positions", [])),
                "event_markers": list(self._overlay_snapshot.get("event_markers", [])),
                "guard_markers": list(self._overlay_snapshot.get("guard_markers", [])),
                "entity_markers": list(self._overlay_snapshot.get("entity_markers", [])),
                "nav_collision_markers": list(self._overlay_snapshot.get("nav_collision_markers", [])),
                "dropped_event_markers": int(self._overlay_snapshot.get("dropped_event_markers", 0) or 0),
                "dropped_guard_markers": int(self._overlay_snapshot.get("dropped_guard_markers", 0) or 0),
                "updated_at": float(self._overlay_snapshot.get("updated_at", 0.0) or 0.0),
            }

    @property
    def portal_detector(self) -> Optional[PortalDetector]:
        return self._portal_detector

    @property
    def map_selector(self) -> MapSelector:
        return self._map_selector

    @property
    def screen_capture(self) -> ScreenCapture:
        return self._screen_capture

    @property
    def hex_calibrator(self) -> HexCalibrator:
        return self._hex_calibrator

    @property
    def stats(self) -> dict:
        elapsed = time.time() - self._start_time if self._start_time else 0
        portal_info = ""
        if self._portal_detector:
            portal_info = self._portal_detector.get_status_text()
        map_step = ""
        if self._map_selector.current_step:
            map_step = self._map_selector.current_step.name
        with self._state_lock:
            state_name = self._state.name
            maps = self._maps_completed
            avg_cycle = (sum(self._cycle_durations_s) / len(self._cycle_durations_s)) if self._cycle_durations_s else 0.0
            success_rate = (self._cycle_success_count / self._cycle_total_count * 100.0) if self._cycle_total_count else 0.0
        native_status = self._native_runtime.get_status_snapshot()
        native_label = native_status.get("scanner_backend", "native:uninitialized")
        native_error = str(native_status.get("last_error", "") or "").strip()
        native_metrics = native_status.get("scanner_metrics", {}) or {}
        return {
            "state": state_name,
            "maps_completed": maps,
            "runtime": elapsed,
            "attached": self.memory.is_attached,
            "portal_status": portal_info,
            "map_selection_step": map_step,
            "calibrated": self._map_selector.is_calibrated,
            "avg_cycle_time_s": avg_cycle,
            "last_cycle_time_s": self._last_cycle_duration_s,
            "cycle_success_rate_pct": success_rate,
            "cycle_total": self._cycle_total_count,
            "native_runtime_enabled": bool(native_status.get("runtime_enabled", False)),
            "native_module_loaded": bool(native_status.get("module_loaded", False)),
            "native_module_name": native_status.get("module_name", ""),
            "native_scanner_backend": native_status.get("scanner_backend", "native:uninitialized"),
            "native_overlay_backend": native_status.get("overlay_backend", "native:pending"),
            "native_overlay_worker_alive": bool(native_status.get("overlay_worker_alive", False)),
            "native_error": native_error,
            "native_status_label": native_label,
            "native_scanner_hz": float(native_metrics.get("hz", 0.0) or 0.0),
            "native_scanner_jitter_ms": float(native_metrics.get("jitter_ms", 0.0) or 0.0),
            "native_scanner_stale_frames": int(native_metrics.get("stale_frames", 0) or 0),
            "native_scanner_age_ms": float(native_metrics.get("age_ms", 0.0) or 0.0),
        }

    def _create_scanner(self, log_prefix: str = "[Scanner]"):
        return self._native_runtime.create_scanner(
            self.memory,
            self.addresses,
            lambda msg_s: log.info(f"{log_prefix} {msg_s}"),
        )

    def _read_player_xy_runtime(self) -> tuple:
        """Return player XY from active runtime scanner API only."""
        scanner = self._scanner
        if scanner and hasattr(scanner, "read_player_xy"):
            try:
                pos = scanner.read_player_xy()
                if pos is not None:
                    x, y = float(pos[0]), float(pos[1])
                    return x, y
            except Exception:
                pass
        return 0.0, 0.0

    def _cycle_begin(self):
        """Mark start of a map cycle (highest-priority KPI: cycle time)."""
        self._cycle_active = True
        self._cycle_started_at = time.time()
        self._cycle_map_name = self.config.get("current_map", "") or self._current_map_name or ""

    def _cycle_end(self, status: str):
        """Finalize cycle diagnostics and emit a compact KPI summary log."""
        if not self._cycle_active or self._cycle_started_at <= 0.0:
            return

        dur = max(0.0, time.time() - self._cycle_started_at)
        self._cycle_active = False
        self._cycle_started_at = 0.0

        self._cycle_total_count += 1
        if status == "success":
            self._cycle_success_count += 1
            self._cycle_durations_s.append(dur)
            self._cycle_best_s = min(self._cycle_best_s, dur)
            self._cycle_worst_s = max(self._cycle_worst_s, dur)
        elif status == "failed":
            self._cycle_fail_count += 1
        else:
            self._cycle_abort_count += 1

        self._last_cycle_duration_s = dur
        self._last_cycle_status = status

        avg_s = (sum(self._cycle_durations_s) / len(self._cycle_durations_s)) if self._cycle_durations_s else 0.0
        success_rate = (self._cycle_success_count / self._cycle_total_count * 100.0) if self._cycle_total_count else 0.0
        best_s = 0.0 if self._cycle_best_s == float("inf") else self._cycle_best_s

        log.info(
            "[CycleKPI] "
            f"map='{self._cycle_map_name or 'unknown'}' "
            f"cycle={dur:.1f}s "
            f"avg={avg_s:.1f}s "
            f"best={best_s:.1f}s "
            f"worst={self._cycle_worst_s:.1f}s "
            f"success={self._cycle_success_count}/{self._cycle_total_count} ({success_rate:.1f}%) "
            f"fail={self._cycle_fail_count} abort={self._cycle_abort_count} "
            f"status={status}"
        )

    def add_state_callback(self, callback: Callable):
        self._state_callbacks.append(callback)

    def _init_memory_card_selector(self):
        """Create MemoryCardSelector and attach it to MapSelector.

        Called whenever scanner gets fnamepool + gobjects resolved
        (same conditions as PortalDetector initialization).
        """
        if self._scanner and self._scanner.fnamepool_addr and self._scanner.gobjects_addr:
            try:
                mcs = MemoryCardSelector(self.memory, self._scanner, self._card_database)
                self._map_selector.set_memory_card_selector(mcs)
                log.info("Memory card selector initialized and attached to MapSelector")
            except Exception as e:
                log.warning(f"Failed to init memory card selector: {e}")

    def _configure_scanner_probes(self, scanner: Optional[UE4Scanner]) -> None:
        if not scanner:
            return
        heavy_debug = bool(self.config.get("runtime_debug_heavy_enabled", False))
        enabled = heavy_debug and bool(self.config.get("nav_collision_probe_enabled", False))
        try:
            interval_s = float(self.config.get("nav_collision_probe_interval_s", 2.0) or 2.0)
        except Exception:
            interval_s = 2.0
        scanner.set_nav_collision_probe(enabled, interval_s)

    def _create_portal_detector(self, scanner: UE4Scanner) -> PortalDetector:
        heavy_debug = bool(self.config.get("runtime_debug_heavy_enabled", False))
        debug_enabled = heavy_debug and bool(self.config.get("portal_debug_enabled", False))
        try:
            summary_interval_s = float(self.config.get("portal_debug_summary_interval_s", 5.0) or 5.0)
        except Exception:
            summary_interval_s = 5.0
        strict_class_check = bool(self.config.get("portal_debug_strict_class_check", False))
        try:
            max_entries = int(self.config.get("portal_debug_max_entries_per_tick", 60) or 60)
        except Exception:
            max_entries = 60
        return PortalDetector(
            self.memory,
            scanner,
            debug_enabled=debug_enabled,
            debug_summary_interval_s=summary_interval_s,
            debug_strict_class_check=strict_class_check,
            debug_max_entries_per_tick=max_entries,
        )

    def _set_state(self, new_state: BotState):
        with self._state_lock:
            if new_state != self._state:
                self._previous_state = self._state
                self._state = new_state
                old = self._previous_state
            else:
                return
        log.info(f"State: {old.name} -> {new_state.name}")
        for cb in self._state_callbacks:
            try:
                cb(old, new_state)
            except Exception as e:
                log.error(f"State callback error: {e}")

    def attach_to_game(self) -> tuple:
        process_name = self.config.get("game_process", GAME_PROCESS_NAME)
        if self.memory.is_attached:
            log.info("Already attached to game process")
            return (True, "Already connected")
        success, error_msg = self.memory.attach_with_reason(process_name)
        if success:
            log.info(f"Attached to {process_name}")
            pid = self.memory.process_id
            if pid:
                self.window.set_target_pid(pid)
            window_title = self.config.get("game_window")
            self.window.find_window(window_title)
            if self.window.hwnd:
                self.input.set_target_window(self.window.hwnd)

            valid, msg = self.game_state.validate_addresses()
            if valid:
                log.info(f"Saved addresses valid - skipping scan: {msg}")
                if not self._scanner:
                    self._scanner = self._create_scanner("[Scanner]")
                    saved_addr = self.addresses.get_address("player_x")
                    if saved_addr and saved_addr.get("base_offset", 0) != 0:
                        modules = self.memory.list_modules()
                        if modules:
                            gworld_static = modules[0][1] + saved_addr["base_offset"]
                            ptr = self.memory.read_value(gworld_static, "ulong")
                            if ptr and 0x10000 < ptr < 0x7FFFFFFFFFFF:
                                self._scanner.set_cached_gworld_static(gworld_static)
                                import threading
                                def _deferred_extras():
                                    try:
                                        if not self.memory.is_attached:
                                            return
                                        self._scanner.scan_fnamepool(modules[0][1], modules[0][2])
                                        self._scanner.scan_gobjects(modules[0][1], modules[0][2])
                                        fnamepool_addr = getattr(self._scanner, "fnamepool_addr", 0)
                                        gobjects_addr = getattr(self._scanner, "gobjects_addr", 0)
                                        if fnamepool_addr and gobjects_addr:
                                            self._portal_detector = self._create_portal_detector(self._scanner)
                                            log.info("Portal detector initialized (deferred)")
                                            self._init_memory_card_selector()
                                    except Exception as exc:
                                        log.warning(f"[Attach] Deferred scanner extras failed: {exc}")
                                    finally:
                                        for cb in getattr(self, '_deferred_scan_callbacks', []):
                                            try:
                                                cb()
                                            except Exception:
                                                pass
                                threading.Thread(target=_deferred_extras, daemon=True, name="DeferredExtras").start()
                self._configure_scanner_probes(self._scanner)
            else:
                log.info("Saved addresses invalid - running dump chain scan...")
                scanner = self._create_scanner("[Scanner]")
                result = scanner.scan_dump_chain(use_cache=False)
                if result.success:
                    log.info(f"Dump chain resolved: ({result.player_x:.1f}, {result.player_y:.1f}, {result.player_z:.1f})")
                    self._scanner = scanner
                    self._configure_scanner_probes(self._scanner)
                    self.last_scan_failed = False
                    if result.fnamepool_addr and result.gobjects_addr:
                        self._portal_detector = self._create_portal_detector(scanner)
                        log.info("Portal detector initialized")
                        self._init_memory_card_selector()
                else:
                    log.warning("Dump chain scan failed - addresses may need manual configuration")
                    self.last_scan_failed = True

            try:
                self.game_state.update()
                pos = self.game_state.player.position
                if pos and (pos.x != 0 or pos.y != 0):
                    zone_map = self.detect_map_from_zone_name()
                    if zone_map:
                        self.config.set("current_map", zone_map)
                        log.info(f"[Attach] Detected map from FName: '{zone_map}'")
                    else:
                        self.detect_map_from_position_and_update(pos.x, pos.y)
            except Exception:
                pass

            self._pos_poller.start()   # single 120 Hz position reader — all consumers call get_pos()
            self._start_zone_watcher()
            return (True, f"Connected to {process_name}")
        else:
            log.error(f"Failed to attach: {error_msg}")
            return (False, error_msg)

    def start(self) -> bool:
        with self._state_lock:
            if self._running:
                log.warning("Bot already running")
                return False
            self._running = True
            self._paused = False
        self._start_time = time.time()
        self._set_state(BotState.STARTING)

        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()

        log.info("Bot started")
        return True

    def stop(self):
        with self._state_lock:
            if self._stop_in_progress:
                # Even during duplicate stop requests, keep explorer cancel as a
                # best-effort safety path so autonomous movement is not left alive.
                try:
                    self.stop_map_explorer()
                except Exception:
                    pass
                log.info("Stop already in progress — ignoring duplicate stop request")
                return
            self._stop_in_progress = True
            self._running = False
            self._paused = False
            self._demo_mode = False
            self._start_time = 0.0

        if self._cycle_active:
            self._cycle_end("aborted")

        try:
            # Always stop explorer first; it can run independently of main bot loop.
            self.stop_map_explorer()

            if self._helper_rt_nav:
                self._helper_rt_nav.cancel()
            if self._rt_navigator:
                self._rt_navigator.cancel()
            self._map_selector.cancel()
            if self._path_recorder.is_recording:
                self._path_recorder.stop_recording()
            if self._portal_detector:
                self._portal_detector.stop_polling()
            if self._scanner:
                self._scanner.cancel()  # flushes + closes MovData CSV writer thread
            self._set_state(BotState.STOPPING)

            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5.0)

            self.game_state.reset()
            self._set_state(BotState.IDLE)
            log.info("Bot stopped")
        finally:
            with self._state_lock:
                self._stop_in_progress = False

    def pause(self):
        with self._state_lock:
            self._paused = not self._paused
            paused = self._paused
            prev = self._previous_state
        if paused:
            if self._helper_rt_nav:
                self._helper_rt_nav.cancel()
            if self._rt_navigator:
                self._rt_navigator.cancel()
            self._set_state(BotState.PAUSED)
            log.info("Bot paused")
        else:
            self._set_state(prev)
            log.info("Bot resumed")

    def start_demo(self) -> bool:
        if self._running:
            return False
        self._demo_mode = True
        self.game_state.enable_demo_mode()
        return self.start()

    def _main_loop(self):
        loop_delay = BOT_MAIN_LOOP_DELAY_S

        while self._running:
            if self._paused:
                time.sleep(0.1)
                continue

            try:
                self.game_state.update()

                if not self._demo_mode and not self._rescan_in_progress and self._state != BotState.ENTERING_MAP:
                    self._check_zone_change()
                    if self._zone_change_detected:
                        self._handle_zone_change_rescan()
                        continue

                handler = self._state_handlers.get(self._state)
                if handler:
                    handler()
                else:
                    log.warning(f"No handler for state: {self._state.name}")

            except Exception as e:
                log.error(f"Bot loop error: {e}")
                self._set_state(BotState.ERROR)

            time.sleep(loop_delay)

        log.info("Main loop exited")

    def _check_zone_change(self):
        x, y = self._read_player_xy_runtime()

        if x != 0.0 or y != 0.0:
            self._consecutive_read_failures = 0
            self._last_valid_position = (x, y)
        else:
            self._consecutive_read_failures += 1
            if self._consecutive_read_failures >= 5:
                log.info("Zone change detected - pointer chains broken")
                self._zone_change_detected = True

    def _handle_zone_change_rescan(self, retry_count=0):
        self._rescan_in_progress = True
        self._zone_change_detected = False
        self._consecutive_read_failures = 0
        try:
            if not self._running:
                return

            log.info("Zone change detected - re-resolving dump chain...")
            log.info("Waiting 3 seconds for new level to load...")
            # Invalidate FightMgr cache immediately so entity scanner uses the new
            # map's instance rather than reading stale data from the previous map.
            if self._scanner:
                self._scanner.clear_fightmgr_cache()
            for _ in range(6):
                if not self._running:
                    return
                time.sleep(0.5)

            if not self._scanner:
                self._scanner = self._create_scanner("[Re-scan]")

            result = self._scanner.scan_dump_chain(use_cache=True)

            if not self._running:
                return

            if result.success:
                log.info(f"Re-scan SUCCESS: ({result.player_x:.1f}, {result.player_y:.1f})")

                zone = self._scanner.read_real_zone_name()
                if zone:
                    log.info(f"Current zone: {zone}")
                    self._current_zone_name = zone
                    self.game_state.set_zone_name(zone)

                if zone and self._is_hideout_zone(zone):
                    self._set_state(BotState.IN_HIDEOUT)
                elif zone:
                    self._set_state(BotState.IN_MAP)
                else:
                    log.warning("Could not determine zone name - defaulting to IN_HIDEOUT")
                    self._set_state(BotState.IN_HIDEOUT)
            else:
                log.error("Re-scan FAILED - dump chain could not resolve")
                if retry_count < 3 and self._running:
                    log.info(f"Will retry in 5 seconds... (attempt {retry_count + 2}/4)")
                    for _ in range(10):
                        if not self._running:
                            return
                        time.sleep(0.5)
                    self._rescan_in_progress = False
                    self._handle_zone_change_rescan(retry_count=retry_count + 1)
                    return
                else:
                    log.error("Re-scan failed after all retries")
                    self._set_state(BotState.ERROR)
        finally:
            self._rescan_in_progress = False

    def _handle_idle(self):
        pass

    def _handle_starting(self):
        if self._demo_mode:
            log.info("Running in DEMO mode (simulated game state)")
            self._set_state(BotState.IN_HIDEOUT)
            return

        process_name = self.config.get("game_process", GAME_PROCESS_NAME)

        if not self.memory.is_attached:
            if not self.memory.attach(process_name):
                log.error("Cannot attach to game process")
                self._set_state(BotState.ERROR)
                return
            pid = self.memory.process_id
            if pid:
                self.window.set_target_pid(pid)

        window_title = self.config.get("game_window")
        self.window.find_window(window_title)
        if self.window.hwnd:
            self.input.set_target_window(self.window.hwnd)

        if not self._scanner:
            self._scanner = self._create_scanner("[Scanner]")

        # Ensure scanner-backed runtime services are available even when the app
        # is restarted while already inside a map (attach -> start in-map path).
        self._ensure_runtime_services()

        valid, msg = self.game_state.validate_addresses()
        if not valid:
            log.info("No valid addresses - running auto-scan...")
            result = self._scanner.scan_dump_chain(use_cache=False)
            if not result.success:
                log.error("Auto-scan failed - cannot find player coordinates")
                log.error("Make sure the game is fully loaded and you are in-game")
                self._set_state(BotState.ERROR)
                return
            log.info(f"Auto-scan success: ({result.player_x:.1f}, {result.player_y:.1f})")
        else:
            if msg.startswith("WARNING"):
                log.warning(f"Address validation: {msg}")
            else:
                log.info(f"Address validation: {msg}")
            self._scanner.scan_dump_chain(use_cache=False)
            log.info("GWorld cached for zone transition detection")

        self.game_state.update()

        zone = ""
        if self._scanner:
            zone = self._scanner.read_real_zone_name()
            if zone:
                self._current_zone_name = zone
                self.game_state.set_zone_name(zone)
                log.info(f"Current zone: {zone}")

        # Resolve current map name early so IN_MAP startup has stable context
        # for wall scan, calibration, and path loading after restart-in-map.
        if zone and not self._is_hideout_zone(zone):
            detected = self.detect_map_from_zone_name()
            if detected:
                self.config.set("current_map", detected)
            else:
                x, y = self._read_player_xy_runtime()
                if x != 0.0 or y != 0.0:
                    self.detect_map_from_position_and_update(x, y)

        if zone and self._is_hideout_zone(zone):
            self._set_state(BotState.IN_HIDEOUT)
        elif zone:
            self._set_state(BotState.IN_MAP)
        elif self.game_state.map.is_in_hideout:
            self._set_state(BotState.IN_HIDEOUT)
        elif self.game_state.map.is_in_map:
            self._set_state(BotState.IN_MAP)
        else:
            self._set_state(BotState.IN_HIDEOUT)

    def _ensure_runtime_services(self):
        """Best-effort startup init for scanner-dependent runtime services.

        This is important when the app is restarted while the character is
        already inside a map. In that scenario, we still want event scans,
        portal detection, and overlay-backed map services to be available on the
        first IN_MAP tick.
        """
        if not self._scanner:
            return

        try:
            modules = self.memory.list_modules() if self.memory.is_attached else []
            if modules:
                module_base, module_size = modules[0][1], modules[0][2]
                if not self._scanner.fnamepool_addr:
                    self._scanner.scan_fnamepool(module_base, module_size)
                if not self._scanner.gobjects_addr:
                    self._scanner.scan_gobjects(module_base, module_size)
        except Exception as e:
            log.debug(f"[Startup] Runtime service pre-scan skipped: {e}")

        try:
            if self._scanner.fnamepool_addr and self._scanner.gobjects_addr and not self._portal_detector:
                self._portal_detector = self._create_portal_detector(self._scanner)
                log.info("Portal detector initialized")
                self._init_memory_card_selector()
        except Exception as e:
            log.debug(f"[Startup] Portal detector init skipped: {e}")

    def _handle_hideout(self):
        self._navigation_completed = False
        log.info("In hideout - starting map selection sequence")
        if self._demo_mode:
            time.sleep(1.0)
            self._set_state(BotState.SELECTING_MAP)
            return
        self._set_state(BotState.SELECTING_MAP)

    def _handle_opening_map(self):
        self._set_state(BotState.SELECTING_MAP)

    def _handle_selecting_map(self):
        if self._demo_mode:
            from src.utils.constants import MAP_NAMES
            import random
            map_name = random.choice(MAP_NAMES)
            self.config.set("current_map", map_name)
            log.info(f"[DEMO] Selected map: {map_name}")
            time.sleep(1.0)
            self._set_state(BotState.ENTERING_MAP)
            return

        self._map_selector.set_step_callback(None)
        if not self._map_selector.is_calibrated:
            log.info("Calibrating hexagon positions...")
            self._map_selector.calibrate(debug=True)
        result = self._map_selector.execute_map_selection()
        if result:
            selected_idx = self._map_selector._last_selected
            if selected_idx >= 0 and selected_idx in CARD_SLOTS:
                map_name = CARD_SLOTS[selected_idx]["name"]
            else:
                map_name = "Unknown"
            self.config.set("current_map", map_name)
            log.info(f"Map selection + portal open complete — map: {map_name}")
            self._entering_phase = ""
            self._set_state(BotState.ENTERING_MAP)
        else:
            if not self._running:
                return
            log.error("Map selection failed")
            self._set_state(BotState.ERROR)

    def _handle_entering_map(self):
        if self._demo_mode:
            log.info("[DEMO] Entering map...")
            time.sleep(1.5)
            self.game_state._map.is_in_hideout = False
            self.game_state._map.is_in_map = True
            self._set_state(BotState.IN_MAP)
            return

        if not self._scanner:
            self._scanner = self._create_scanner("[Scanner]")

        if not self._entering_phase:
            self._entering_gworld_before = self._scanner.get_gworld_ptr()
            if self._entering_gworld_before == 0:
                log.info("No cached GWorld — running scan before zone transition detection")
                result = self._scanner.scan_dump_chain(use_cache=False)
                if result.success:
                    self._entering_gworld_before = self._scanner.get_gworld_ptr()
                    zone = self._scanner.read_real_zone_name()
                    if zone:
                        self._current_zone_name = zone
                        self.game_state.set_zone_name(zone)
                    if zone and not self._is_hideout_zone(zone):
                        map_name = self.config.get("current_map", "")
                        if map_name and result.player_x != 0.0:
                            self._record_starting_position(map_name, result.player_x, result.player_y)
                        self._entering_phase = ""
                        self._set_state(BotState.IN_MAP)
                        return
                    log.info(f"Scan succeeded, zone='{zone}', monitoring for transition...")
                else:
                    log.error("Cannot establish GWorld baseline for zone transition")
                    self._set_state(BotState.ERROR)
                    return
            self._entering_phase = "waiting_for_load"
            self._entering_start = time.time()
            self._entering_last_log = 0.0
            self._entering_popup_start = 3.0
            self._entering_popup_attempts = 0
            log.info(f"Waiting for zone transition... (GWorld=0x{self._entering_gworld_before:X})")
            return

        elapsed = time.time() - self._entering_start
        timeout = 30.0

        if elapsed > timeout:
            log.error(f"ENTERING_MAP timeout ({timeout:.0f}s) in phase '{self._entering_phase}'")
            self._entering_phase = ""
            self._set_state(BotState.ERROR)
            return

        if self._entering_phase == "waiting_for_load":
            chain_ok = self._scanner.check_chain_valid()
            gworld_now = self._scanner.get_gworld_ptr()
            gworld_changed = (self._entering_gworld_before > 0 and
                              gworld_now > 0 and
                              gworld_now != self._entering_gworld_before)

            if gworld_changed:
                log.info(f"GWorld changed: 0x{self._entering_gworld_before:X} -> 0x{gworld_now:X} ({elapsed:.1f}s)")
                self._entering_phase = "rescan"
            elif not chain_ok:
                log.info(f"Pointer chain broken (loading screen) ({elapsed:.1f}s)")
                self._entering_phase = "waiting_for_level"
            else:
                popup_start = getattr(self, '_entering_popup_start', 3.0)
                popup_attempts = getattr(self, '_entering_popup_attempts', 0)
                if popup_attempts < 3 and elapsed >= popup_start:
                    self._entering_popup_attempts = popup_attempts + 1
                    log.info(f"No zone transition after {elapsed:.1f}s — popup check #{popup_attempts + 1}")
                    if self._map_selector.check_and_dismiss_tip_popup():
                        log.info("Tip popup was blocking — reset transition timer")
                        self._entering_start = time.time()
                        self._entering_last_log = 0.0
                        self._entering_popup_attempts = 3
                    else:
                        self._entering_popup_start = popup_start + 2.0
                if elapsed - self._entering_last_log >= 5.0:
                    self._entering_last_log = elapsed
                    log.info(f"Waiting for zone transition... ({elapsed:.0f}s)")

        elif self._entering_phase == "waiting_for_level":
            chain_ok = self._scanner.check_chain_valid()
            if chain_ok:
                log.info(f"Pointer chain recovered ({elapsed:.1f}s)")
                self._entering_phase = "rescan"
            elif elapsed - self._entering_last_log >= 3.0:
                self._entering_last_log = elapsed
                log.info(f"Loading... ({elapsed:.0f}s)")

        elif self._entering_phase == "rescan":
            log.info("Re-resolving dump chain after zone transition...")
            result = self._scanner.scan_dump_chain(use_cache=True)
            if result.success:
                log.info(f"Re-scan SUCCESS: ({result.player_x:.1f}, {result.player_y:.1f})")
                zone = self._scanner.read_real_zone_name()
                if zone:
                    self._current_zone_name = zone
                    self.game_state.set_zone_name(zone)
                    log.info(f"Zone FName after transition: {zone}")

                self._entering_phase = ""
                if zone and self._is_hideout_zone(zone):
                    log.info("Arrived in hideout (unexpected after Open Portal)")
                    self.config.set("current_map", "hideout")
                    self._set_state(BotState.IN_HIDEOUT)
                else:
                    map_name = self.detect_map_from_zone_name()
                    if map_name:
                        current = self.config.get("current_map", "")
                        if map_name != current:
                            self.config.set("current_map", map_name)
                            log.info(f"[ZoneMap] Set current map to '{map_name}' via FName")
                    else:
                        map_name = self.config.get("current_map", "")
                        if not map_name or map_name == "hideout":
                            self.detect_map_from_position_and_update(result.player_x, result.player_y)
                            map_name = self.config.get("current_map", "")
                        else:
                            self._learn_zone_name_mapping(map_name)
                    if not map_name:
                        map_name = self.config.get("current_map", "")
                    if map_name and map_name != "hideout":
                        self._record_starting_position(map_name, result.player_x, result.player_y)
                    log.info(f"Entered map successfully ({elapsed:.1f}s)")
                    self._cycle_begin()
                    self._set_state(BotState.IN_MAP)
            else:
                log.warning("Re-scan failed, waiting for level to stabilize...")
                self._entering_phase = "waiting_for_level"

    def _handle_in_map(self):
        if self._demo_mode:
            log.info("[DEMO] In map - simulating navigation")
            time.sleep(2.0)
            self._cycle_begin()
            self._navigation_completed = True
            self._set_state(BotState.RETURNING)
            return

        if not self._cycle_active:
            self._cycle_begin()

        if self._portal_detector and not self._portal_detector.is_polling:
            self._portal_detector.find_fightmgr()
            self._portal_detector.start_polling()

        # Auto-scan events in background so they are logged and visible on overlay
        if self._scanner:
            threading.Thread(target=self._scan_events_on_entry, daemon=True,
                             name="EventEntryScan").start()

        self._current_map_name = self.config.get("current_map", "")
        nav_mode = self.config.get("nav_mode", "manual")

        # Sync calibrator so _steer_direct uses the correct per-map axis matrix
        if self._scale_calibrator and self._current_map_name:
            self._scale_calibrator.set_current_map(self._current_map_name)

        # ── Always scan walkable area on map entry (both nav modes) ─────
        # Reads MinimapSaveObject.Records.Pos in the background.
        # Cache hit  → builds A* grid instantly from wall_data.json.
        # Cache miss → reads memory, saves cache for future runs.
        # In manual mode the grid is unused for navigation but the cache
        # is built silently so auto mode works well on the first switch.
        if self._scanner:
            self._start_wall_scan_background(self._current_map_name)

        # ── Auto-navigation mode ────────────────────────────────────────
        if nav_mode == "auto" and self._scanner:
            self._handled_event_addrs = set()
            # Build RTNavigator — 60 Hz real-time navigation brain
            _, event_handler = self._make_event_callbacks()
            auto_behavior = (self.config.get("auto_behavior", "rush_events") or "rush_events").strip().lower()
            self._rt_navigator = RTNavigator(
                game_state          = self.game_state,
                input_ctrl          = self.input,
                pathfinder          = self._pathfinder,
                scanner             = self._scanner,
                portal_detector     = self._portal_detector,
                event_handler_fn    = event_handler,
                boss_locate_fn      = self._get_boss_position,
                portal_entered_fn   = self._auto_nav_portal_entered,
                find_portal_icon_fn = self._find_exit_portal_icon_pos,
                config              = self.config,
                behavior            = auto_behavior,
                pos_poller          = self._pos_poller,
                scale_calibrator    = self._scale_calibrator,
            )
            # Wire overlay + map name for learned walls & A* path display
            if self._debug_overlay:
                self._rt_navigator.set_overlay(self._debug_overlay)
            if self._current_map_name:
                self._rt_navigator.set_map_name(self._current_map_name)
            self._set_state(BotState.NAVIGATING)
            return

        # ── Manual navigation mode (recorded path) ──────────────────────
        if self._current_map_name:
            waypoints = self._path_recorder.load_path(self._current_map_name)
            if waypoints:
                # Reset per-map event tracking and store for _handle_navigating
                self._handled_event_addrs = set()
                self._manual_waypoints = waypoints
                if self._scanner:
                    checker, handler = self._make_event_callbacks()
                    self._manual_event_checker = checker
                    self._manual_event_handler = handler
                else:
                    self._manual_event_checker = None
                    self._manual_event_handler = None
                self._set_state(BotState.NAVIGATING)
                return

        log.warning("No map path loaded - skipping to map complete")
        self._navigation_completed = False
        self._set_state(BotState.MAP_COMPLETE)

    def _scan_events_on_entry(self):
        """Background task: wait briefly then scan and log all map events."""
        time.sleep(2.0)  # allow server to register events after zone load
        try:
            events = self._scanner.get_typed_events() if self._scanner else []
            if events:
                log.info(f"[Events] Map entry scan: {len(events)} event(s) found")
                for e in events:
                    tag = "TARGET" if e.is_target_event else "other"
                    log.info(
                        f"[Events]  [{tag}] {e.event_type} | "
                        f"spawn=0x{e.cfg_id:X} | "
                        f"pos=({e.position[0]:.0f}, {e.position[1]:.0f}) | "
                        f"wave={e.wave_counter} bvalid={e.bvalid}"
                    )
            else:
                log.info("[Events] Map entry scan: no events found yet "
                         "(some events load on approach — will be picked up during navigation)")
        except Exception as ex:
            log.debug(f"[Events] Entry scan error: {ex}")

    def _build_navigation_grid_for_map(self,
                                       ws: WallScanner,
                                       map_name: str,
                                       cx: float,
                                       cy: float,
                                       runtime_points: Optional[list]) -> Optional[object]:
        """Build runtime-only walkable grid from sampled/map-cached points."""
        runtime_points = runtime_points or []
        runtime_walk = [pt for pt in runtime_points if getattr(pt, "pt_type", "walkable") == "walkable"]
        merged = runtime_walk
        apply_blocked = False

        if not merged:
            return None

        nav_markers = []
        if self._scanner and self.config.get("nav_collision_grid_blocking_enabled", True):
            try:
                nav_markers = self._scanner.get_nav_collision_markers() or []
            except Exception:
                nav_markers = []

        try:
            nav_inflate = float(self.config.get("nav_collision_grid_inflate_u", 0.0) or 0.0)
        except Exception:
            nav_inflate = 0.0

        grid = ws.build_walkable_grid(
            merged,
            cx,
            cy,
            half_size=WALL_GRID_HALF_SIZE,
            cell_size=WALL_GRID_CELL_SIZE,
            apply_blocked_points=apply_blocked,
            nav_collision_markers=nav_markers,
            nav_collision_inflate_u=nav_inflate,
        )
        return grid

    def _start_wall_scan_background(self, map_name: str, force_rescan: bool = False):
        """Background task: build A* walkable-area grid from MinimapSaveObject.

        PRIMARY PATH — MinimapSaveObject.Records.Pos (reliable memory reading):
          Reads all world positions previously visited in this map from the live
          MinimapSaveObject GObjects singleton.  Builds an INVERTED grid (all
          blocked → visited circles opened as walkable) and installs it in
          self._pathfinder.

          MinimapSaveObject.Pos accumulates positions as the player explores the
          map minimap.  Sampling rate is approximately 1 position per second of
          movement (~150–300 world units per sample at normal run speed).  The
          cache must be updated throughout the map run, not just on first entry.

        force_rescan=False (default / map entry):
          Cache hit → load JSON instantly, build grid, return.  Fast path used
          when the cache already has good coverage from a previous run.
        force_rescan=True (periodic retry / accumulation pass):
          Always reads MinimapSaveObject regardless of cache state.  If the live
          array has MORE entries than the cache, the cache is updated and the A*
          grid is rebuilt.  Used by the zone watcher to grow the cache throughout
          the current map run as the player explores new areas.

        CACHE MISS — reads MinimapSaveObject; ~0.5–2s depending on position count.
        FALLBACK — if MinimapSaveObject returns 0 positions (game hasn't populated
          the array yet), pathfinder grid is left unset / unchanged and RTNavigator
          falls back to direct navigation.
        """
        raw_zone = self._current_zone_name  # e.g. 'YJ_XieDuYuZuo200'

        def _run():
            try:
                if not self._scanner:
                    log.warning("[WallScan] Scanner not available — skipping background scan")
                    return
                if self._wall_scanner is None:
                    self._wall_scanner = WallScanner(self._scanner)
                ws = self._wall_scanner

                # ── Cache hit (fast path — only when not force-rescanning) ───
                cached = ws.load_wall_data(map_name) if map_name else None
                if cached and not force_rescan:
                    log.info(f"[WallScan] Cache hit: {len(cached)} visited-position points for '{map_name}'")
                    cx, cy = self._read_player_xy_runtime()
                    grid = self._build_navigation_grid_for_map(ws, map_name, cx, cy, cached)
                    if grid is not None:
                        self._pathfinder.set_grid(grid)
                        log.info(f"[WallScan] A* grid ready from cache: {grid}")
                    else:
                        log.info(f"[WallScan] Cache exists for '{map_name}' but no grid was composed")
                    return

                # ── Read MinimapSaveObject (cache miss or force rescan) ──────
                old_count = len(cached) if cached else 0
                if old_count:
                    log.info(
                        f"[WallScan] Re-scanning MinimapSaveObject for '{map_name}' "
                        f"(cache has {old_count} pts, checking for new positions)"
                    )
                else:
                    log.info(
                        f"[WallScan] No cache for '{map_name}' — "
                        f"reading MinimapSaveObject (zone='{raw_zone}')"
                    )
                t0 = time.time()
                points = ws.scan_from_minimap_records(map_name, raw_zone)
                elapsed = time.time() - t0

                if not points:
                    if old_count:
                        log.info(
                            f"[WallScan] Re-scan: MinimapSaveObject empty for '{map_name}' "
                            f"in {elapsed:.2f}s — keeping {old_count} cached pts"
                        )
                        cx, cy = self._read_player_xy_runtime()
                        grid = self._build_navigation_grid_for_map(ws, map_name, cx, cy, cached)
                        if grid is not None:
                            self._pathfinder.set_grid(grid)
                    else:
                        log.info(
                            f"[WallScan] MinimapSaveObject returned 0 positions in {elapsed:.2f}s "
                            f"— map '{map_name}' not yet visited or GObjects not ready; "
                            f"trying atlas/fail-open composition"
                        )
                        cx, cy = self._read_player_xy_runtime()
                        grid = self._build_navigation_grid_for_map(ws, map_name, cx, cy, [])
                        if grid is not None:
                            self._pathfinder.set_grid(grid)
                            log.info(f"[WallScan] A* grid ready from atlas/fallback: {grid}")
                        else:
                            log.info("[WallScan] No atlas/fallback grid available — RTNavigator will use direct navigation")
                    log.flush()
                    return

                new_count = len(points)
                if old_count and new_count <= old_count:
                    log.info(
                        f"[WallScan] Re-scan: {new_count} positions "
                        f"(cache already has {old_count}) — no update needed for '{map_name}'"
                    )
                    log.flush()
                    return

                # Save and rebuild grid (new data available)
                ws.save_wall_data(map_name, points)
                if old_count:
                    log.info(
                        f"[WallScan] Cache grown: {old_count} → {new_count} "
                        f"(+{new_count - old_count} new positions) for '{map_name}'"
                    )
                else:
                    log.info(f"[WallScan] Saved {new_count} visited-position points for '{map_name}'")

                cx, cy = self._read_player_xy_runtime()
                grid = self._build_navigation_grid_for_map(ws, map_name, cx, cy, points)
                if grid is not None:
                    self._pathfinder.set_grid(grid)
                    log.info(f"[WallScan] A* grid {'updated' if old_count else 'ready'}: {grid}")
                else:
                    log.info(f"[WallScan] Grid composition returned empty for '{map_name}'")
                log.flush()

            except Exception as exc:
                log.error(f"[WallScan] Background scan failed: {exc}", exc_info=True)
                log.flush()

        threading.Thread(target=_run, daemon=True, name="WallScan").start()

    def _scan_walkable_on_exit(self):
        """Background task: re-read MinimapSaveObject just before leaving a map.

        Called at the start of _handle_returning so that every world position
        accumulated during this bot run is captured.  Unlike the entry scan,
        this always reads from memory (ignoring the cache) so newly explored
        areas are merged in.  The cache is updated only when the fresh read
        returns more points than the current cache, keeping data monotonically
        growing.

        Runs on a daemon background thread.  _navigate_to_boss() and the portal
        walk provide ample time for the read to complete before zone change.
        """
        map_name = self._current_map_name
        raw_zone = self._current_zone_name

        def _run():
            try:
                if not self._scanner or not map_name:
                    return
                if self._wall_scanner is None:
                    self._wall_scanner = WallScanner(self._scanner)
                ws = self._wall_scanner

                log.info(
                    f"[WallScan] Exit scan started for '{map_name}' (zone='{raw_zone}') "
                    f"— capturing positions accumulated this run"
                )
                points = ws.scan_from_minimap_records(map_name, raw_zone)
                if not points:
                    log.info(
                        f"[WallScan] Exit scan: no positions returned for '{map_name}' "
                        f"— MinimapSaveObject may be empty for this zone"
                    )
                    log.flush()
                    return

                # Only overwrite cache when new read is richer than what is stored
                cached = ws.load_wall_data(map_name) or []
                old_count = len(cached)
                new_count = len(points)

                if new_count > old_count:
                    ws.save_wall_data(map_name, points)
                    log.info(
                        f"[WallScan] Exit scan: cache updated {old_count} → {new_count} "
                        f"(+{new_count - old_count} new positions) for '{map_name}'"
                    )
                else:
                    log.info(
                        f"[WallScan] Exit scan: {new_count} positions "
                        f"(cache already has {old_count}) — no update needed for '{map_name}'"
                    )
                log.flush()

            except Exception as exc:
                log.error(f"[WallScan] Exit scan failed: {exc}", exc_info=True)
                log.flush()

        threading.Thread(target=_run, daemon=True, name="WallScanExit").start()

    def _start_zone_watcher(self):
        """Background daemon: auto-scans walkable area on map entry — no F9 needed.

        Starts from attach_to_game() so it is always active whenever the bot is
        attached, regardless of whether the bot state machine is running.

        Designed for manual-play sessions (overlay-only, F9 never pressed).  When
        the bot IS running, its own entry/exit scans handle things; the zone watcher
        fires an additional safety-net exit scan all the same.

        Scan triggers:
          1. Immediately on new map zone entry — loads cache (fast path) then
             falls through to the periodic accumulation loop.
          2. Every 0.5 s while in map — reads MinimapSaveObject (cached ptr, ~1ms),
             saves to wall_data.json and rebuilds A* grid whenever position count
             grows.  Up to MAX_RETRIES (300 = 150 s) per map entry.
          3. Once when zone transitions to loading screen / hideout — final grab
             while MinimapSaveObject may still be alive in /Engine/Transient.

        NOTE: boss detection (scan_boss_room) is a completely separate, real-time
        GObjects scan and has nothing to do with the periodic interval here.
        """
        if self._zone_watcher_active:
            return
        self._zone_watcher_active = True

        POLL_S      = 2.0   # zone poll interval (seconds)
        RETRY_S     = 0.5   # re-scan interval to accumulate new positions
        # MinimapSaveObject.Records is populated from the game's save file on
        # map load — it reflects PREVIOUS-SESSION data, not live positions.
        # The game only flushes the current session's positions back to the
        # TMap on map exit.  5 retries (≈10 s) is enough to catch the initial
        # load; further re-scans will find no new data until the next entry.
        MAX_RETRIES = 5     # up to 10 s of initial scans per map entry

        def _run():
            last_zone          = ""
            last_map_name      = ""
            entered_at         = 0.0
            last_scan_at       = 0.0
            retry_count        = 0
            consecutive_nonmap = 0   # debounce: must see non-map N times before exit
            pos_sampler_stop:  Optional[threading.Event] = None

            try:
                while self.memory.is_attached:
                    time.sleep(POLL_S)
                    try:
                        if not self._scanner or not self._scanner.gobjects_addr:
                            continue

                        zone    = self._scanner.read_zone_name()
                        mapping = self._load_zone_name_mapping()

                        # ── Loading screen / hideout / UI overlay ────────────
                        is_hideout_or_loading = (
                            not zone
                            or "UIMain" in zone
                            or mapping.get(zone) == "Embers Rest"
                        )
                        if is_hideout_or_loading:
                            consecutive_nonmap += 1
                            # Debounce: opening inventory or brief UI flicker
                            # causes zone to read as UIMainLevelV2 for one or two
                            # polls.  Only treat as a real exit after
                            # ZONE_WATCHER_EXIT_THRESHOLD consecutive non-map reads.
                            if consecutive_nonmap < ZONE_WATCHER_EXIT_THRESHOLD:
                                continue
                            # Stop position sampler
                            if pos_sampler_stop:
                                pos_sampler_stop.set()
                                pos_sampler_stop = None
                            if last_map_name:
                                log.info(
                                    f"[ZoneWatcher] Zone exited → final scan "
                                    f"attempt for '{last_map_name}'"
                                )
                                self._current_zone_name = last_zone
                                self._current_map_name  = last_map_name
                                self._scan_walkable_on_exit()
                                log.flush()
                            last_zone          = zone or ""
                            last_map_name      = ""
                            last_scan_at       = 0.0
                            retry_count        = 0
                            consecutive_nonmap = 0
                            self._current_zone_name = ""
                            # Flush FightMgr cache on map exit: the previous map's
                            # instance is no longer valid in the hideout / loading screen.
                            if self._scanner:
                                self._scanner.clear_fightmgr_cache()
                            continue

                        # Back in a map zone — reset non-map counter
                        consecutive_nonmap = 0

                        map_name = mapping.get(zone, "")
                        if not map_name:
                            last_zone = zone
                            continue

                        now = time.monotonic()

                        # ── New map zone entered ──────────────────────────────
                        if zone != last_zone:
                            last_zone     = zone
                            last_map_name = map_name
                            entered_at    = now
                            last_scan_at  = 0.0
                            retry_count   = 0
                            self._current_zone_name = zone
                            log.info(
                                f"[ZoneWatcher] Entered '{map_name}' "
                                f"— auto-scanning walkable area + starting position sampler"
                            )
                            # New map → new FightMgr instance: flush the cached ptr so
                            # get_monster_entities() and get_typed_events() re-resolve it
                            # via GObjects instead of reading the previous map's instance.
                            if self._scanner:
                                self._scanner.clear_fightmgr_cache()
                            self._start_wall_scan_background(map_name)
                            last_scan_at = now
                            # Start direct position sampler for this map
                            if pos_sampler_stop:
                                pos_sampler_stop.set()
                            pos_sampler_stop = threading.Event()
                            _stop = pos_sampler_stop
                            threading.Thread(
                                target=self._run_zone_position_sampler,
                                args=(map_name, _stop),
                                daemon=True, name="PosSampler",
                            ).start()
                            log.flush()
                            continue

                        # ── Same map: initial scans to load any MinimapSaveObject
                        # historical data (legacy — kept for backward compatibility).
                        # The direct position sampler above is the primary source.
                        if (last_scan_at > 0
                                and retry_count < MAX_RETRIES
                                and now - last_scan_at >= RETRY_S):
                            cached_data = WallScanner._load_json()
                            cached_count = len(cached_data.get(map_name, []))
                            # Skip MinimapSaveObject retries when the cache already
                            # has sufficient walkable data — MinimapSaveObject always
                            # returns 0 now (confirmed broken), and the PosSampler
                            # thread handles all new point accumulation.
                            if cached_count >= MINIMAP_SCAN_SKIP_THRESHOLD:
                                log.info(
                                    f"[ZoneWatcher] Cache for '{map_name}' has "
                                    f"{cached_count} pts — skipping MinimapSaveObject "
                                    f"retries, direct sampler is active"
                                )
                                log.flush()
                                retry_count = MAX_RETRIES + 1  # exhaust retries
                            else:
                                retry_count += 1
                                log.info(
                                    f"[ZoneWatcher] Scan #{retry_count} for "
                                    f"'{map_name}' ({now - entered_at:.0f}s in map, "
                                    f"cache={cached_count} pts)"
                                )
                                self._start_wall_scan_background(map_name, force_rescan=True)
                                last_scan_at = now
                                log.flush()
                        elif last_scan_at > 0 and retry_count == MAX_RETRIES:
                            log.info(
                                f"[ZoneWatcher] MinimapSaveObject scans done for "
                                f"'{map_name}' — direct sampler is active"
                            )
                            log.flush()
                            retry_count += 1  # prevent repeated logging

                    except Exception:
                        pass
            finally:
                if pos_sampler_stop:
                    pos_sampler_stop.set()
                self._zone_watcher_active = False

        threading.Thread(target=_run, daemon=True, name="ZoneWatcher").start()
        log.info("[ZoneWatcher] Started — will auto-scan walkable area on map entry")

    def _handle_navigating(self):
        nav_mode = self.config.get("nav_mode", "manual")

        # ── Auto-navigation mode ────────────────────────────────────────────
        if nav_mode == "auto" and self._rt_navigator is not None:
            log.info("[RTNav] Starting autonomous map run (60 Hz)")

            def cancel_check():
                return not self._running or self._paused

            result = self._rt_navigator.run_phases(cancel_fn=cancel_check)
            self._rt_navigator = None  # discard after run

            if not self._running:
                return
            if result:
                log.info("[AutoNav] Run complete — portal entry confirmed")
                self._navigation_completed = True
                self._set_state(BotState.MAP_COMPLETE)
            else:
                log.error("[AutoNav] Run failed or cancelled")
                self._navigation_completed = False
                self._set_state(BotState.ERROR)
            return

        # ── Manual navigation mode (recorded path via RTNavigator) ───────────
        wps = getattr(self, "_manual_waypoints", None)
        if wps:
            rt = self._get_helper_rt_nav()
            if self._current_map_name:
                rt.set_map_name(self._current_map_name)
            if self._debug_overlay:
                rt.set_overlay(self._debug_overlay)

            def cancel_check():
                return not self._running or self._paused

            result = rt.navigate_waypoints(
                wps,
                cancel_fn=cancel_check,
                event_checker=getattr(self, "_manual_event_checker", None),
                event_handler=getattr(self, "_manual_event_handler", None),
            )
            rt.stop()
            self._manual_waypoints = None

            if not self._running:
                return
            if result:
                log.info("Navigation complete")
                if self._scanner:
                    self._handle_map_events()
                self._navigation_completed = True
                self._set_state(BotState.RETURNING)
            else:
                log.error("Navigation failed")
                self._navigation_completed = False
                self._set_state(BotState.ERROR)
        else:
            log.warning("No waypoints - skipping to map complete")
            self._navigation_completed = False
            self._set_state(BotState.MAP_COMPLETE)

    def _auto_nav_portal_entered(self) -> bool:
        try:
            self.game_state.update()
            return self.game_state.map.is_in_hideout or (not self.game_state.map.is_in_map)
        except Exception:
            return False

    def _handle_event_by_type(self, event, interact_key: str) -> None:
        """Dispatch an event to its dedicated handler.

        Centralized dispatch keeps event-flow extensible for upcoming handlers
        (e.g. Sandlord v2 and additional event types) without duplicating logic
        across mid-navigation and post-navigation paths.
        """
        etype = (event.event_type or "").strip().lower()
        handlers = {
            "carjack": self._handle_carjack_event,
            "sandlord": self._handle_sandlord_event,
        }
        fn = handlers.get(etype)
        if not fn:
            log.warning(f"[Events] Unrecognised event type '{event.event_type}' — skipping")
            return
        fn(event, interact_key)

    def _detect_active_sandlord_event(self, min_monsters: int = 3,
                                      radius: float = 2200.0):
        """Return a Sandlord event when wave monsters indicate active combat.

        This is used as a safety net when Sandlord is accidentally activated
        while another event flow is executing.
        """
        scanner = self._scanner
        if not scanner:
            return None
        events = scanner.get_typed_events() or []
        for ev in events:
            if (ev.event_type or "").lower() != "sandlord":
                continue
            if ev.bvalid == 0:
                continue
            sx, sy, _ = ev.position
            if abs(sx) < 1.0 and abs(sy) < 1.0:
                continue
            n = scanner.count_nearby_monsters(sx, sy, radius=radius)
            if n >= min_monsters:
                return ev
        return None

    def _handle_carjack_event(self, event, interact_key: str) -> None:
        """Handle Carjack with truck-stand default, conditional chase, and fallbacks."""
        scanner = self._scanner
        if not scanner:
            return

        log.info("[Activity] Carjack event started")

        # Activation: press F quickly to trigger the event flow.
        log.info(f"[Events] Carjack: pressing {interact_key.upper()} ×3 to activate")
        for _ in range(3):
            if not self._running:
                return
            self.input.press_key(interact_key)
            time.sleep(0.35)

        bounty_seen = self._handle_carjack_bounty_if_present()

        start_t = time.time()
        hard_deadline = start_t + 26.0  # 24s event + ~2s activation animation buffer
        no_monster_streak = 0
        no_monster_required = 5   # 5 * 0.5s = 2.5s stable empty window
        carjack_mode = ""
        end_reason = "cancelled"

        _TRUCK_STAND_RADIUS = 900.0
        _ESCAPE_DIST_TRUCK = 1800.0

        truck_pos = scanner.get_carjack_truck_position()
        if not truck_pos:
            ex, ey, _ = event.position
            truck_pos = (ex, ey)

        def _truck_lock() -> bool:
            """Keep character on truck and hard-stop movement unless chasing guards."""
            p0 = scanner.read_player_xy() if scanner else None
            if p0 is None:
                return False
            tx0, ty0 = truck_pos
            d0 = math.hypot(p0[0] - tx0, p0[1] - ty0)

            if d0 > _TRUCK_STAND_RADIUS:
                log.info(f"[Events] Carjack: off-truck ({d0:.0f}u) — returning")
                self._get_helper_rt_nav().navigate_to_target(tx0, ty0, tolerance=220.0, timeout=3.0)

            self._get_helper_rt_nav().stop_character()
            self.input.move_mouse(*CHARACTER_CENTER)

            p1 = scanner.read_player_xy() if scanner else None
            if p1 is None:
                return False
            time.sleep(0.12)
            p2 = scanner.read_player_xy() if scanner else None
            if p2 is None:
                return False

            d_truck = math.hypot(p2[0] - tx0, p2[1] - ty0)
            settled = math.hypot(p2[0] - p1[0], p2[1] - p1[1]) <= 40.0
            if d_truck <= _TRUCK_STAND_RADIUS and settled:
                return True

            self.input.move_mouse(*CHARACTER_CENTER)
            time.sleep(0.12)
            p3 = scanner.read_player_xy() if scanner else None
            if p3 is None:
                return False
            d_truck2 = math.hypot(p3[0] - tx0, p3[1] - ty0)
            settled2 = math.hypot(p3[0] - p2[0], p3[1] - p2[1]) <= 40.0
            if d_truck2 <= _TRUCK_STAND_RADIUS and settled2:
                return True

            log.warning(f"[Events] Carjack: truck lock weak (dist={d_truck2:.0f}u, settled={settled2})")
            return d_truck2 <= _TRUCK_STAND_RADIUS

        log.info("[Events] Carjack: truck-stand loop started")
        while self._running and not self._paused:
            now = time.time()
            if now >= hard_deadline:
                log.info("[Events] Carjack fallback timeout (26s) — finishing event flow")
                end_reason = "timeout"
                break

            current_events = scanner.get_typed_events() if scanner else []
            carjack_events = [e for e in current_events if e.event_type.lower() == "carjack"]

            # Keep truck position fresh while event is visible.
            latest_truck = scanner.get_carjack_truck_position()
            if latest_truck:
                truck_pos = latest_truck

            accidental_sandlord = self._detect_active_sandlord_event()
            if accidental_sandlord is not None:
                sx, sy, _ = accidental_sandlord.position
                log.warning(
                    f"[Events] Carjack safety: Sandlord active at ({sx:.0f},{sy:.0f}) "
                    "— moving to platform for recovery"
                )
                self._get_helper_rt_nav().navigate_to_target(sx, sy, tolerance=180.0, timeout=18.0)
                self._get_helper_rt_nav().stop_character()
                time.sleep(0.2)
                self._handle_sandlord_event(accidental_sandlord, interact_key)
                # Resume Carjack loop after Sandlord recovery attempt.
                start_t = time.time()
                hard_deadline = start_t + 26.0
                no_monster_streak = 0
                continue

            # Completion signal 1: no active Carjack event visible anymore.
            if not carjack_events and now - start_t > 2.0:
                log.info("[Events] Carjack event no longer visible — considered complete")
                end_reason = "event_hidden"
                break

            tx, ty = truck_pos
            nearby_monsters = scanner.count_nearby_monsters(tx, ty, radius=4000.0)
            if nearby_monsters <= 0 and now - start_t > 3.0:
                no_monster_streak += 1
            else:
                no_monster_streak = 0

            # Completion signal 2: stable no-monster window around truck.
            if no_monster_streak >= no_monster_required:
                log.info("[Events] Carjack near-truck monsters stable at 0 — considered complete")
                end_reason = "monsters_cleared"
                break

            guard_positions = scanner.get_carjack_guard_positions() if scanner else []
            player_xy = scanner.read_player_xy() if scanner else None
            if not player_xy:
                time.sleep(0.2)
                continue
            px, py = player_xy
            escaped = [g for g in guard_positions if g.get("dist_truck", 0.0) >= _ESCAPE_DIST_TRUCK]

            # Default Carjack posture: stay on truck and hard-stop drift.
            # Skip this only when actively chasing escaped guards.
            if not escaped:
                if carjack_mode != "truck_stand":
                    carjack_mode = "truck_stand"
                    log.info("[Activity] Carjack: guarding truck")
                _truck_lock()

            # Chase only escaped guard candidates, then return immediately.
            if escaped:
                if carjack_mode != "chase":
                    carjack_mode = "chase"
                    log.info("[Activity] Carjack: chasing guard")
                escaped.sort(key=lambda g: (g["x"] - px) ** 2 + (g["y"] - py) ** 2)
                tgt = escaped[0]
                tgt_addr = int(tgt.get("addr", 0) or 0)
                gx, gy = float(tgt["x"]), float(tgt["y"])
                log.info(f"[Events] Carjack chase: target=0x{tgt_addr:X} ({gx:.0f},{gy:.0f})")
                self._get_helper_rt_nav().navigate_to_target(gx, gy, tolerance=450.0, timeout=2.2)

                # Quick re-check for target survival; then snap back to truck.
                alive_after = scanner.get_carjack_guard_positions() if scanner else []
                if tgt_addr:
                    still_alive = any(int(a.get("addr", 0) or 0) == tgt_addr for a in alive_after)
                    if not still_alive:
                        log.info(f"[Events] Carjack chase target down/disappeared: 0x{tgt_addr:X}")
                log.info("[Activity] Carjack: going back to truck")
                self._get_helper_rt_nav().navigate_to_target(tx, ty, tolerance=450.0, timeout=2.2)
                _truck_lock()

            time.sleep(0.5)

        if bounty_seen:
            self._collect_carjack_strongboxes(interact_key)
        log.info(f"[Activity] Carjack event finished ({end_reason})")

    def _handle_sandlord_event(self, event, _interact_key: str) -> None:
        """Efficient Sandlord handler driven by monster-count transitions.

        Timing model (confirmed by user, 2026-03-02):
          platform step-on → ~1s spawn animation → wave N monsters appear
          → auto-bomb kills them → ~1s spawn animation → wave N+1 appears …
          → final wave killed → bValid→0 (game signals event complete)

        Design:
          Phase 1 — Activation: spam E at 150 ms intervals; exit immediately
            when nearby monsters > 0 (first wave appeared).  No fixed sleep.
          Phase 2 — Wave loop: poll at 150 ms.  Tracks when monsters transition
            0→N (new wave start) and N→0 (wave cleared).  Declares event done
            when monster count stays 0 for CLEAR_TIMEOUT seconds after at least
            one wave was seen — the 2.5s window comfortably spans the ~1s
            inter-wave animation plus scan jitter.  Also exits immediately on
            bValid=0 or actor-gone signals.

        wave_counter (EGameplay+0x618) is CONFIRMED UNRELIABLE (2026-03-02)
        and is not used anywhere in this handler.
        """
        scanner = self._scanner
        if not scanner:
            return
        log.info("[Activity] Sandlord event started")
        ex, ey, _ = event.position
        loot_key = self.config.get("loot_key", "e")
        sandlord_done_reason = "cancelled"

        def _platform_lock() -> bool:
            """Ensure character is on platform and fully stopped before handling waves.

            Sequence:
            1) Check distance to platform.
            2) If far, re-approach platform.
            3) Immediately place cursor on CHARACTER_CENTER to hard-stop drift.
            4) Verify both proximity and low movement over short interval.
            """
            pxy0 = scanner.read_player_xy() if scanner else None
            if pxy0 is None:
                return False
            px0, py0 = pxy0
            d0 = math.hypot(px0 - ex, py0 - ey)
            if d0 > 500.0:
                log.info(f"[Events] Sandlord: off-platform ({d0:.0f}u) — returning")
                self._get_helper_rt_nav().navigate_to_target(ex, ey, tolerance=120.0, timeout=18.0)

            # Always enforce hard-stop cursor placement after any platform move.
            self._get_helper_rt_nav().stop_character()
            self.input.move_mouse(*CHARACTER_CENTER)

            # Verify: close enough + movement has settled.
            p1 = scanner.read_player_xy() if scanner else None
            if p1 is None:
                return False
            time.sleep(0.12)
            p2 = scanner.read_player_xy() if scanner else None
            if p2 is None:
                return False

            d_platform = math.hypot(p2[0] - ex, p2[1] - ey)
            settled = math.hypot(p2[0] - p1[0], p2[1] - p1[1]) <= 35.0
            if d_platform <= 500.0 and settled:
                return True

            # One quick retry to absorb late follow-drift.
            self.input.move_mouse(*CHARACTER_CENTER)
            time.sleep(0.12)
            p3 = scanner.read_player_xy() if scanner else None
            if p3 is None:
                return False
            d_platform2 = math.hypot(p3[0] - ex, p3[1] - ey)
            settled2 = math.hypot(p3[0] - p2[0], p3[1] - p2[1]) <= 35.0
            if d_platform2 <= 500.0 and settled2:
                return True

            log.warning(f"[Events] Sandlord: platform lock weak (dist={d_platform2:.0f}u, settled={settled2})")
            return d_platform2 <= 500.0

        # Always acquire a verified platform lock before event handling.
        _platform_lock()

        def _alive() -> int:
            return scanner.count_nearby_monsters(ex, ey, radius=3000.0)

        def _gone_or_invalid() -> bool:
            nonlocal sandlord_done_reason
            ev = next((e for e in scanner.get_typed_events()
                       if e.address == event.address), None)
            if ev is None:
                log.info("[Events] Sandlord: actor gone — event complete")
                sandlord_done_reason = "actor_gone"
                return True
            if ev.bvalid == 0:
                log.info("[Events] Sandlord: bValid=0 — event complete")
                sandlord_done_reason = "bvalid_zero"
                return True
            return False

        # ── Phase 1: activation ──────────────────────────────────────────
        # Spam E and poll until first wave monsters appear.  No fixed wait —
        # exit the moment the scanner sees them so we waste zero time.
        log.info("[Events] Sandlord: waiting for first wave (max 8s)")
        act_deadline = time.time() + 8.0
        first_wave_seen = False
        while self._running and not self._paused and time.time() < act_deadline:
            self.input.press_key(loot_key)
            time.sleep(0.15)
            if _alive() > 0:
                first_wave_seen = True
                log.info(f"[Events] Sandlord: wave 1 appeared "
                         f"({_alive()} monsters)")
                break
        if not self._running or self._paused:
            return
        if not first_wave_seen:
            log.warning("[Events] Sandlord: no monsters after 8s — "
                        "proceeding to wave-loop anyway")

        # ── Phase 2: wave loop ───────────────────────────────────────────
        # Poll at 150 ms.  last_saw_t is the last timestamp at which
        # monster_count > 0 was observed.  Once it has been 0 for
        # CLEAR_TIMEOUT seconds (well past the ~1s spawn animation) we
        # declare the event done.  bValid=0 / actor-gone also terminate.
        POLL_S        = 0.15   # scan interval
        CLEAR_TIMEOUT = 2.5    # seconds of 0 monsters → event done
        TOTAL_TIMEOUT = 90.0   # hard cap for whole event

        wave_start = time.time()
        # last_saw_t=None means "never saw monsters in wave loop yet"
        last_saw_t: Optional[float] = time.time() if first_wave_seen else None
        in_wave    = first_wave_seen
        wave_num   = 1 if first_wave_seen else 0

        log.info("[Events] Sandlord: wave loop started")
        while self._running and not self._paused:
            if time.time() - wave_start > TOTAL_TIMEOUT:
                log.info("[Events] Sandlord: 90s hard cap — resuming navigation")
                sandlord_done_reason = "timeout"
                break
            pxy = scanner.read_player_xy() if scanner else None
            if pxy is not None:
                d_platform = math.hypot(pxy[0] - ex, pxy[1] - ey)
                if d_platform > 500.0:
                    _platform_lock()
                    continue
            if _gone_or_invalid():
                break

            count = _alive()
            self.input.press_key(loot_key)

            if count > 0:
                if not in_wave:
                    wave_num += 1
                    in_wave = True
                    log.info(f"[Events] Sandlord: wave {wave_num} appeared "
                             f"({count} monsters)")
                last_saw_t = time.time()
            else:
                if in_wave:
                    in_wave = False
                    log.info(f"[Events] Sandlord: wave {wave_num} cleared — "
                             f"waiting up to {CLEAR_TIMEOUT}s for next wave")
                if last_saw_t is not None:
                    since_clear = time.time() - last_saw_t
                    if since_clear >= CLEAR_TIMEOUT:
                        # Final confirmation scan before exiting
                        if _alive() == 0:
                            log.info(f"[Events] Sandlord: {wave_num} wave(s) "
                                     f"complete, {since_clear:.1f}s clear — "
                                     f"event done")
                            sandlord_done_reason = "waves_cleared"
                            break

            time.sleep(POLL_S)

        log.info(f"[Activity] Sandlord event finished ({sandlord_done_reason})")

    def _detect_carjack_bounty_ui(self) -> bool:
        """Template-matching detector for optional Carjack bounty UI popup."""
        try:
            import cv2
            import numpy as np
        except Exception:
            return False

        if not self._carjack_bounty_template_loaded:
            self._carjack_bounty_template_loaded = True
            if os.path.exists(CARJACK_BOUNTY_UI_TEMPLATE_PATH):
                self._carjack_bounty_template = cv2.imread(CARJACK_BOUNTY_UI_TEMPLATE_PATH)
                if self._carjack_bounty_template is None:
                    log.warning("[Events] Carjack bounty template could not be read")
            else:
                self._carjack_bounty_template = None

        if self._carjack_bounty_template is None:
            return False

        frame = self._screen_capture.capture_window()
        if frame is None:
            return False

        x1, y1, x2, y2 = CARJACK_BOUNTY_UI_SEARCH_REGION
        h, w = frame.shape[:2]
        x1 = max(0, min(x1, w - 1))
        x2 = max(1, min(x2, w))
        y1 = max(0, min(y1, h - 1))
        y2 = max(1, min(y2, h))
        patch = frame[y1:y2, x1:x2]
        if patch.size == 0:
            return False

        tpl = self._carjack_bounty_template
        th, tw = tpl.shape[:2]
        if patch.shape[0] < th or patch.shape[1] < tw:
            return False

        res = cv2.matchTemplate(patch, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        detected = max_val >= CARJACK_BOUNTY_UI_MATCH_THRESHOLD
        if detected:
            log.info(f"[Events] Carjack bounty UI detected (score={max_val:.3f})")
        return detected

    def _handle_carjack_bounty_if_present(self) -> bool:
        """Process optional bounty UI branch; returns True if detected/handled."""
        deadline = time.time() + 2.5
        while self._running and not self._paused and time.time() < deadline:
            if self._detect_carjack_bounty_ui():
                for x, y in CARJACK_BOUNTY_UI_CLICK_POSITIONS:
                    if not self._running:
                        break
                    self.input.click(x, y, button="left")
                    time.sleep(0.35)
                return True
            time.sleep(0.2)
        return False

    def _collect_carjack_strongboxes(self, interact_key: str) -> None:
        """After bounty Carjack, wait for spawn and open nearby strongboxes."""
        scanner = self._scanner
        if not scanner:
            return
        truck_pos = scanner.get_carjack_truck_position()
        if not truck_pos:
            return

        tx, ty = truck_pos
        log.info("[Events] Carjack bounty: waiting 4s for strongbox spawn")
        end_wait = time.time() + 4.0
        while self._running and not self._paused and time.time() < end_wait:
            time.sleep(0.2)

        opened_addrs = set()
        no_new_streak = 0
        started = time.time()
        while self._running and not self._paused and time.time() - started < 18.0:
            radius = (CARJACK_STRONGBOX_SEARCH_RADIUS_SQ ** 0.5)
            items = scanner.get_nearby_interactive_items(tx, ty, radius=radius, require_valid=True)
            candidates = []
            for it in items:
                if it.address in opened_addrs:
                    continue
                cls = (it.sub_object_class or "").lower()
                name = (it.sub_object_name or "").lower()
                is_box = (
                    "interactive" in cls or "interactive" in name
                    or "bao" in cls or "bao" in name
                    or "xiang" in cls or "xiang" in name
                )
                if is_box:
                    candidates.append(it)

            if not candidates:
                no_new_streak += 1
                if no_new_streak >= 5:
                    break
                time.sleep(0.8)
                continue

            no_new_streak = 0
            px, py = scanner.read_player_xy() or (tx, ty)
            candidates.sort(key=lambda it: (it.position[0] - px) ** 2 + (it.position[1] - py) ** 2)
            target = candidates[0]
            ix, iy, _ = target.position
            log.info(f"[Events] Carjack strongbox: interacting at ({ix:.0f},{iy:.0f})")
            self._get_helper_rt_nav().navigate_to_target(ix, iy, tolerance=250.0, timeout=5.0)
            for _ in range(3):
                if not self._running:
                    return
                self.input.press_key(interact_key)
                time.sleep(0.25)
            opened_addrs.add(target.address)

        if opened_addrs:
            log.info(f"[Events] Carjack strongbox flow complete — interacted with {len(opened_addrs)} item(s)")

    def _make_event_callbacks(self):
        """Build (checker, handler) callables for mid-navigation event interrupts.

        checker(px, py): returns the nearest unhandled typed event within
            EVENT_PROXIMITY_TRIGGER_UNITS, or None.
        handler(event): stops the character, navigates to the event, interacts
            (Carjack: press F×3; Sandlord: linger + wait for wave clearance),
            then returns so waypoint navigation resumes automatically.
        """
        scanner = self._scanner
        interact_key = self.config.get("interact_key", "f")

        def checker(px: float, py: float):
            if not scanner:
                return None
            events = scanner.get_typed_events()
            nearest = None
            nearest_dist = EVENT_PROXIMITY_TRIGGER_UNITS
            for ev in events:
                if ev.address in self._handled_event_addrs:
                    continue
                ex, ey, _ = ev.position
                if not ev.is_target_event:
                    continue
                if abs(ex) < 1.0 and abs(ey) < 1.0:
                    continue  # skip (0,0,0) phantoms
                dist = ((ex - px) ** 2 + (ey - py) ** 2) ** 0.5
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest = ev
            return nearest

        def handler(event):
            if event.address in self._handled_event_addrs:
                return
            self._handled_event_addrs.add(event.address)

            ex, ey, _ = event.position
            etype = event.event_type
            log.info(f"[Events] Interrupting navigation — handling {etype} "
                     f"at ({ex:.0f}, {ey:.0f})")

            if self._rt_navigator is None:
                # Manual nav mode: RTNavigator helper stops character and walks to event.
                rt = self._get_helper_rt_nav()
                rt.stop_character()
                time.sleep(0.2)
                reached = rt.navigate_to_target(ex, ey, tolerance=250.0, timeout=20.0)
                if not reached:
                    log.warning(f"[Events] Could not reach {etype} at ({ex:.0f}, {ey:.0f}) — skipping")
                    return
                rt.stop_character()
                time.sleep(0.3)
            # RTNav mode: character is already at the event (RTNav navigated there
            # before calling this handler).  No navigation needed here.

            self._handle_event_by_type(event, interact_key)

            log.info(f"[Events] {etype} handled — resuming waypoint navigation")
            # RTNav manages its own movement restart after this handler returns.
            # Only re-issue a right-click in manual nav mode.
            if self._rt_navigator is None:
                from src.utils.constants import CHARACTER_CENTER as _CC
                self.input.move_mouse(_CC[0], _CC[1] - 200)

        return checker, handler

    def _handle_map_events(self):
        """Post-navigation event cleanup: handle any remaining unhandled events.

        Called after waypoint navigation completes. Mid-navigation events are
        already handled by the navigator's event interrupt callbacks set in
        _handle_in_map(). This method catches any that were not within trigger
        range during navigation (e.g. spawned after passing nearby).

        Uses get_typed_events() for accurate Carjack vs Sandlord classification.
        Falls back silently if no scanner.
        """
        if not self._scanner:
            return

        events = self._scanner.get_typed_events()
        # Only handle events not already handled during navigation
        remaining = [e for e in events
                     if e.address not in self._handled_event_addrs
                     and e.is_target_event
                     and (abs(e.position[0]) > 1.0 or abs(e.position[1]) > 1.0)]

        if not remaining:
            log.info("[Events] No remaining events after navigation — skipping post-nav sweep")
            return

        log.info(f"[Events] Post-nav sweep: {len(remaining)} unhandled event(s)")
        interact_key = self.config.get("interact_key", "f")
        event_timeout = 60.0
        start = time.time()

        for event in remaining:
            if not self._running or self._paused:
                return
            if time.time() - start > event_timeout:
                log.warning("[Events] Post-nav event timeout — proceeding to exit")
                return
            if event.address in self._handled_event_addrs:
                continue

            self._handled_event_addrs.add(event.address)
            ex, ey, _ = event.position
            etype = event.event_type
            log.info(f"[Events] Navigating to {etype} at ({ex:.0f}, {ey:.0f})")

            reached = self._get_helper_rt_nav().navigate_to_target(ex, ey, tolerance=250.0, timeout=20.0)
            if not reached:
                log.warning(f"[Events] Could not reach {etype} — skipping")
                continue

            self._get_helper_rt_nav().stop_character()
            time.sleep(0.3)

            self._handle_event_by_type(event, interact_key)

            log.info(f"[Events] {etype} handled")

        log.info("[Events] Post-nav event sweep complete")

    def _handle_returning(self):
        if self._demo_mode:
            log.info("[DEMO] Returning to hideout...")
            time.sleep(1.5)
            self.game_state._map.is_in_map = False
            self.game_state._map.is_in_hideout = True
            self._set_state(BotState.MAP_COMPLETE)
            return

        if not self._navigation_completed:
            log.warning("Navigation did not complete - waiting in map")
            time.sleep(2.0)
            return

        log.info("[Activity] Now leaving map")

        # Scan walkable area before exiting — captures every position walked
        # during this run so the MinimapSaveObject data is as complete as possible.
        # Runs on a background thread; _navigate_to_boss() gives it time to finish.
        if self._scanner:
            self._scan_walkable_on_exit()

        # Navigate to boss area (if recorded for this map) before seeking exit portal
        self._navigate_to_boss()

        if self._portal_detector:
            result = self._handle_returning_with_portal_detection()
        else:
            result = self._handle_exit_portal_direct()

        if result:
            log.info("Exited map successfully")
            if self._portal_detector:
                self._portal_detector.stop_polling()
            self._set_state(BotState.MAP_COMPLETE)
        else:
            if not self._running:
                return
            log.error("Failed to find exit portal")
            self._set_state(BotState.ERROR)

    # ------------------------------------------------------------------
    # Exit portal (F-spam fallback when no portal detector)
    # ------------------------------------------------------------------

    def _handle_exit_portal_direct(self) -> bool:
        """Stop the character and spam F to enter exit portal (no detector)."""
        log.info("[Portal] At last waypoint — searching for exit portal (F-spam)")
        self._get_helper_rt_nav().stop_character()
        time.sleep(0.5)

        interact_key = self.config.get("interact_key", "f")
        max_attempts = 30

        for attempt in range(max_attempts):
            if not self._running:
                return False
            self.input.press_key(interact_key)
            self.game_state.update()
            if self.game_state.map.is_in_hideout:
                log.info(f"[Portal] Returned to hideout (attempt {attempt + 1})")
                return True
            if not self.game_state.map.is_in_map:
                log.info(f"[Portal] Zone changed — exited map (attempt {attempt + 1})")
                return True
            time.sleep(0.5)

        log.warning("[Portal] Exit portal not found after max attempts")
        return False

    # ------------------------------------------------------------------
    # Final-goal helpers (hardcoded per map)
    # ------------------------------------------------------------------

    def get_map_final_goal(self, map_name: str) -> Optional[tuple]:
        """Return hardcoded final-goal coordinates for map_name, or None.

        This is intended for per-map exit-portal anchor positions that are
        maintained in constants once measured from live runs.
        """
        entry = HARDCODED_MAP_FINAL_DESTINATIONS.get(map_name)
        if not isinstance(entry, dict):
            return None
        x = entry.get("x")
        y = entry.get("y")
        if x is None or y is None:
            return None
        try:
            return (float(x), float(y))
        except Exception:
            return None

    # ── Wall-scanner GUI helpers ────────────────────────────────────────────────

    def get_wall_data_status(self, map_name: str) -> str:
        """Return a human-readable status string for the walkable-area cache of map_name."""
        from src.core.wall_scanner import WallScanner as _WS
        data = _WS._load_json()
        raw = data.get(map_name)
        if raw:
            return f"Cached ({len(raw)} visited-position points)"
        return "No map coverage data yet — enter/explore the map to build coverage automatically"

    def get_all_coverage(self) -> dict:
        """Return a dict mapping each map name (from MAP_NAMES) to its cached point count.

        Maps with no data have a count of 0.
        """
        from src.core.wall_scanner import WallScanner as _WS
        from src.utils.constants import MAP_NAMES
        data = _WS._load_json()
        return {name: len(data[name]) if name in data else 0 for name in MAP_NAMES}

    def get_all_coverage_metrics(self) -> dict:
        """Return per-map coverage metrics using explorer-style frontier estimation.

        Output format:
          {
            map_name: {
              "covered": int,
              "estimated_total": int,
              "frontier": int,
              "pct": float,
            }
          }

        This mirrors MapExplorer's `_compute_coverage_estimate` idea:
          estimated_total = covered + frontier * MAP_EXPLORER_FRONTIER_ESTIMATE_MULTIPLIER
          pct = covered / estimated_total
        """
        from src.core.wall_scanner import WallScanner as _WS, WallPoint as _WP
        from src.utils.constants import MAP_NAMES

        out = {}
        data = _WS._load_json()

        for name in MAP_NAMES:
            raw = data.get(name, [])
            covered = len(raw) if isinstance(raw, list) else 0
            frontier_n = 0
            est_total = max(covered, 1)
            pct = 0.0

            if covered > 0:
                try:
                    points = [_WP.from_dict(p) for p in raw if isinstance(p, dict)]
                    if points:
                        # For per-map summary (not active run), center grid on point centroid.
                        cx = sum(p.x for p in points) / len(points)
                        cy = sum(p.y for p in points) / len(points)
                        ws = _WS.__new__(_WS)
                        grid = ws.build_walkable_grid(
                            points,
                            cx,
                            cy,
                            half_size=WALL_GRID_HALF_SIZE,
                            cell_size=WALL_GRID_CELL_SIZE,
                            log_summary=False,
                        )
                        frontier_n = len(grid.get_frontier_world_positions(max_samples=500))
                except Exception:
                    frontier_n = 0

                est_total = max(
                    covered,
                    covered + int(frontier_n * MAP_EXPLORER_FRONTIER_ESTIMATE_MULTIPLIER),
                    1,
                )
                pct = min(100.0, max(0.0, (covered / est_total) * 100.0))

            out[name] = {
                "covered": int(covered),
                "estimated_total": int(est_total),
                "frontier": int(frontier_n),
                "pct": float(pct),
            }

        return out

    def delete_wall_data(self, map_name: str):
        """Delete cached wall data for map_name (forces re-scan on next entry)."""
        from src.core.wall_scanner import WallScanner as _WS
        _WS._load_json()  # ensure data dir exists
        if self._wall_scanner is None:
            self._wall_scanner = WallScanner(self._scanner) if self._scanner else None
        if self._wall_scanner:
            self._wall_scanner.delete_wall_data(map_name)

    def delete_all_map_coverage_data(self) -> int:
        """Delete all persisted map coverage (walkable cache) and return removed map count."""
        from src.core.wall_scanner import WallScanner as _WS

        existing = _WS._load_json()
        removed = len(existing) if isinstance(existing, dict) else 0
        if not _WS._save_json({}):
            return 0

        # Clear runtime cache so UI/status reflects clean state immediately.
        self._wall_cache.clear()
        if self._wall_scanner is None:
            self._wall_scanner = WallScanner(self._scanner) if self._scanner else None
        log.info(f"[WallScan] Deleted map coverage data for {removed} maps")
        log.flush()
        return removed

    # ── Map Explorer (automatic walkable-area data collection) ─────────────────

    def start_map_explorer(self,
                           duration_s: float = None,
                           progress_cb=None) -> bool:
        """Launch a random-walk exploration session in the current map.

        Runs MapExplorer on a background daemon thread.  The explorer uses a
        lightweight RTNavigator for 120 Hz A*-pathed steering — no scanner,
        portal detector, or event handler needed.

        Parameters
        ----------
        duration_s  : exploration duration in seconds; None = run until coverage
                  completion criteria are met.
        progress_cb : callable(elapsed_s, duration_s, targets, positions) or None.

        Returns True if the explorer was started, False if already running or
        not attached.
        """
        from src.core.map_explorer import MapExplorer

        if self._explorer_thread and self._explorer_thread.is_alive():
            log.warning("[Explorer] Already running — call stop_map_explorer() first")
            return False
        if not self.memory.is_attached:
            log.warning("[Explorer] Not attached to game — cannot start explorer")
            return False
        if self._running:
            log.warning(
                "[Explorer] Bot loop is active — stop the bot before starting the "
                "explorer (right-click follow mode must be initiated exactly once "
                "per map; the running bot already owns that right-click)"
            )
            return False

        # Resolve current map name for position saving
        map_name = self._resolve_current_map()

        # Build RTNavigator for exploration with portal/scanner wiring so
        # explorer targets can use portal-hop routing on disconnected maps.
        explorer_rt_nav = RTNavigator(
            game_state       = self.game_state,
            input_ctrl       = self.input,
            pathfinder       = self._pathfinder,
            scanner          = self._scanner,
            portal_detector  = self._portal_detector,
            config           = self.config,
            pos_poller       = self._pos_poller,
            scale_calibrator = self._scale_calibrator,
        )
        if self._debug_overlay:
            explorer_rt_nav.set_overlay(self._debug_overlay)
        if map_name:
            explorer_rt_nav.set_map_name(map_name)

        self._explorer_rt_nav = explorer_rt_nav

        self._map_explorer = MapExplorer(
            rt_navigator = explorer_rt_nav,
            duration_s   = duration_s,
            map_name     = map_name,
            progress_cb  = progress_cb,
            pathfinder   = self._pathfinder,
            pos_poller   = self._pos_poller,
        )

        def _run():
            try:
                self._map_explorer.run()
            except Exception as exc:
                log.error(f"[Explorer] Crashed: {exc}", exc_info=True)

        self._explorer_thread = threading.Thread(
            target=_run, daemon=True, name="MapExplorer"
        )
        self._explorer_thread.start()
        if duration_s is None:
            log.info(f"[Explorer] Started (completion-driven, map='{map_name}')")
        else:
            log.info(f"[Explorer] Started ({duration_s:.0f}s session, map='{map_name}')")
        return True

    def stop_map_explorer(self):
        """Cancel the running MapExplorer session and clean up its RTNavigator."""
        with self._state_lock:
            if self._explorer_stop_in_progress:
                log.info("[Explorer] Stop already in progress — ignoring duplicate request")
                return
            self._explorer_stop_in_progress = True

        try:
            if self._map_explorer:
                self._map_explorer.cancel()
                log.info("[Explorer] Stop requested")
            if self._explorer_thread:
                self._explorer_thread.join(timeout=3.0)
                self._explorer_thread = None
            # Stop the explorer's RTNavigator loop (if still alive)
            rt = getattr(self, "_explorer_rt_nav", None)
            if rt is not None:
                try:
                    rt.stop()
                except Exception:
                    pass
                self._explorer_rt_nav = None
            self._map_explorer = None
        finally:
            with self._state_lock:
                self._explorer_stop_in_progress = False

    @property
    def explorer_running(self) -> bool:
        """True while a MapExplorer session is in progress."""
        return bool(self._explorer_thread and self._explorer_thread.is_alive())

    def _resolve_current_map(self) -> str:
        """Return the English map name for the current zone, or empty string."""
        fallback_map = getattr(self, "_current_map_name", "") or ""
        try:
            zone = self._scanner.read_real_zone_name() if self._scanner else ""
            if not zone:
                return fallback_map
            mapping = self._load_zone_name_mapping()
            return mapping.get(zone, fallback_map)
        except Exception:
            return fallback_map

    def _run_zone_position_sampler(self, map_name: str,
                                   stop_event: "threading.Event"):
        """Background thread: sample player position to wall_data.json.

        Runs while the player is in a map — covers regular bot runs AND manual
        play with the bot attached, not just explicit Explorer sessions.
        Same logic as MapExplorer._run_position_sampler() but driven from the
        ZoneWatcher rather than the MapExplorer.
        """
        from src.core.map_explorer import MapExplorer
        import math as _math

        sample_dist_sq = MAP_EXPLORER_POSITION_SAMPLE_DIST ** 2
        sampler = MapExplorer.__new__(MapExplorer)
        sampler._map_name = map_name
        sampler._cancelled = False
        sampler._sampler_last_pos = None

        existing_keys = sampler._load_existing_keys()
        pending:    list  = []
        last_flush: float = time.time()

        while not stop_event.is_set():
            now = time.time()

            if self.explorer_running:
                time.sleep(MAP_EXPLORER_POSITION_POLL_S)
                continue

            try:
                x, y = self._read_player_xy_runtime()
            except Exception:
                time.sleep(MAP_EXPLORER_POSITION_POLL_S)
                continue

            if abs(x) > 1.0 or abs(y) > 1.0:
                key = sampler._pos_key(x, y)
                if key not in existing_keys:
                    if sampler._sampler_last_pos is None:
                        sampler._sampler_last_pos = (x, y)
                        existing_keys.add(key)
                        pending.append((x, y))
                        self._live_grid_update(x, y)
                    else:
                        dx = x - sampler._sampler_last_pos[0]
                        dy = y - sampler._sampler_last_pos[1]
                        if dx * dx + dy * dy >= sample_dist_sq:
                            sampler._sampler_last_pos = (x, y)
                            existing_keys.add(key)
                            pending.append((x, y))
                            self._live_grid_update(x, y)

            if pending and (
                len(pending) >= MAP_EXPLORER_POSITION_FLUSH_EVERY
                or now - last_flush >= MAP_EXPLORER_POSITION_FLUSH_S
            ):
                sampler._flush_positions(pending)
                pending.clear()
                last_flush = now

            time.sleep(MAP_EXPLORER_POSITION_POLL_S)

        if pending:
            sampler._flush_positions(pending)

    def _live_grid_update(self, x: float, y: float):
        """Mark a freshly-sampled position walkable in the A* grid (live).

        Called from the PosSampler thread.  If the pathfinder has a grid loaded,
        the new position is immediately carved open so A* replans benefit from
        corridors the player is exploring *right now*, not just from wall_data
        loaded at map start.
        """
        if self._pathfinder and self._pathfinder.has_grid:
            try:
                self._pathfinder._grid.mark_circle_walkable(
                    x, y, VISITED_CELL_WALKABLE_RADIUS)
            except Exception:
                pass  # grid may be swapped out mid-run — harmless

    def _get_boss_position(self) -> Optional[tuple]:
        map_name = self.config.get("current_map", "")

        boss_pos = None
        if map_name:
            boss_pos = self.get_map_final_goal(map_name)
            if boss_pos:
                log.info(
                    f"[Boss] Using hardcoded final goal for '{map_name}': "
                    f"({boss_pos[0]:.0f}, {boss_pos[1]:.0f})"
                )

        if not boss_pos and self._scanner:
            boss_pos = self._scanner.scan_boss_room()
            if boss_pos:
                log.info(f"[Boss] MapBossRoom fallback detected at ({boss_pos[0]:.0f}, {boss_pos[1]:.0f})")

        return boss_pos

    def _navigate_to_boss(self) -> bool:
        """Navigate to the boss arena for the current map and linger for kill."""
        boss_pos = self._get_boss_position()
        if not boss_pos:
            log.info("[Boss] No final goal found (hardcoded or MapBossRoom fallback) - skipping")
            return False

        bx, by = boss_pos
        log.info(f"[Boss] Navigating to boss arena at ({bx:.0f}, {by:.0f})")

        reached = self._get_helper_rt_nav().navigate_to_target(bx, by, tolerance=300.0, timeout=30.0)
        if reached:
            log.info("[Boss] Reached boss arena — lingering 3s for auto-bomb kill")
            linger_start = time.time()
            while self._running and not self._paused and time.time() - linger_start < 3.0:
                time.sleep(0.2)
        else:
            log.warning("[Boss] Could not reach boss arena within timeout — proceeding to exit")
        return reached

    def _handle_returning_with_portal_detection(self) -> bool:
        """Use portal detector to find and navigate to exit portal."""
        log.info("Waiting for exit portal to spawn...")
        self._get_helper_rt_nav().stop_character()

        max_wait = 60.0
        poll_interval = 1.0
        start_time = time.time()

        while self._running and not self._paused:
            portal_pos = self._portal_detector.get_exit_portal_position()
            if portal_pos:
                x, y, z = portal_pos
                log.info(f"Exit portal detected at ({x:.0f}, {y:.0f}) - navigating...")

                if self._get_helper_rt_nav().navigate_to_target(x, y, tolerance=150.0, timeout=30.0):
                    log.info("Reached exit portal - pressing F to enter")
                    self._get_helper_rt_nav().stop_character()
                    time.sleep(0.3)

                    portal_icon_pos = self._find_exit_portal_icon_pos()
                    if portal_icon_pos:
                        # Template match found the portal icon — click its actual position.
                        # Works for both normal layout and Pirates shifted layout.
                        # If the portal icon isn't visible yet (e.g. player is near
                        # the Pirates NPC but hasn't reached the portal), this returns
                        # None and we fall back to pressing F instead.
                        log.info(f"[Portal] Clicking exit portal icon at {portal_icon_pos}")
                        self.input.click(*CHARACTER_CENTER, button="right")
                        time.sleep(0.2)
                        for attempt in range(20):
                            if not self._running:
                                return False
                            self.input.click(*portal_icon_pos, button="left")
                            time.sleep(0.5)
                            self.game_state.update()
                            if self.game_state.map.is_in_hideout:
                                log.info(f"[Portal] Exited via icon click (attempt {attempt + 1})")
                                return True
                            if not self.game_state.map.is_in_map:
                                log.info(f"[Portal] Zone changed - exited map (attempt {attempt + 1})")
                                return True
                    else:
                        interact_key = self.config.get("interact_key", "f")
                        for attempt in range(20):
                            if not self._running:
                                return False
                            self.input.press_key(interact_key)
                            time.sleep(0.5)
                            self.game_state.update()
                            if self.game_state.map.is_in_hideout:
                                log.info(f"Exited via portal (attempt {attempt + 1})")
                                return True
                            if not self.game_state.map.is_in_map:
                                log.info(f"Zone changed - exited map (attempt {attempt + 1})")
                                return True
                else:
                    log.warning("Failed to reach exit portal position")

            if time.time() - start_time > max_wait:
                log.warning("Exit portal wait timeout - falling back to F-spam")
                return self._handle_exit_portal_direct()

            time.sleep(poll_interval)

        return False

    def _find_exit_portal_icon_pos(self) -> Optional[tuple]:
        """Template-match the exit portal icon in the interaction button strip.

        Returns the client-area (x, y) centre of the matched icon, or None if
        not found.  Works for both the normal single-button layout and the
        Pirates event layout where the portal icon shifts left.  When the
        portal icon is not yet on screen (e.g. player is near the Pirates NPC
        but has not reached the portal) this correctly returns None and the
        caller falls back to pressing F.
        """
        try:
            import cv2
        except ImportError:
            log.debug("[Portal] cv2 not available")
            return None

        # Lazy-load and cache the template
        if not hasattr(self, '_exit_portal_template'):
            self._exit_portal_template = None
            if os.path.exists(EXIT_PORTAL_TEMPLATE_PATH):
                tmpl = cv2.imread(EXIT_PORTAL_TEMPLATE_PATH)
                if tmpl is not None:
                    self._exit_portal_template = tmpl
                    log.info(f"[Portal] Exit portal template loaded: {EXIT_PORTAL_TEMPLATE_PATH} {tmpl.shape}")
                else:
                    log.warning(f"[Portal] Failed to read exit portal template: {EXIT_PORTAL_TEMPLATE_PATH}")
            else:
                log.info(f"[Portal] No exit portal template at {EXIT_PORTAL_TEMPLATE_PATH}")

        tmpl = self._exit_portal_template
        if tmpl is None:
            return None

        try:
            sx, sy, sw, sh = EXIT_PORTAL_SEARCH_REGION
            frame = self._screen_capture.capture_region(sx, sy, sw, sh)
            if frame is None:
                return None

            th, tw = tmpl.shape[:2]
            if frame.shape[0] < th or frame.shape[1] < tw:
                return None

            result = cv2.matchTemplate(frame, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            log.debug(f"[Portal] Exit portal match score={max_val:.3f} threshold={EXIT_PORTAL_MATCH_THRESHOLD} loc={max_loc}")

            if max_val >= EXIT_PORTAL_MATCH_THRESHOLD:
                # Convert search-region-relative top-left → client-area centre of icon
                click_x = sx + max_loc[0] + tw // 2
                click_y = sy + max_loc[1] + th // 2
                log.info(f"[Portal] Exit portal icon located at client ({click_x}, {click_y}) score={max_val:.3f}")
                return (click_x, click_y)
            return None
        except Exception as exc:
            log.debug(f"[Portal] Exit portal template error: {exc}")
            return None

    START_POS_FILE = os.path.join("data", "map_starting_positions.json")
    ZONE_MAP_FILE = os.path.join("data", "zone_name_mapping.json")

    def _load_starting_positions(self) -> dict:
        if os.path.exists(self.START_POS_FILE):
            try:
                with open(self.START_POS_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def detect_map_from_position(self, x: float, y: float, threshold: float = 500.0) -> str:
        positions = self._load_starting_positions()
        if not positions:
            return ""
        best_name = ""
        best_dist = float("inf")
        for name, pos in positions.items():
            dx = x - pos.get("x", 0)
            dy = y - pos.get("y", 0)
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_name = name
        if best_dist <= threshold:
            return best_name
        return ""

    def detect_map_from_position_and_update(self, x: float, y: float):
        detected = self.detect_map_from_position(x, y)
        if detected:
            current = self.config.get("current_map", "")
            if detected != current:
                self.config.set("current_map", detected)
                log.info(f"[MapDetect] Auto-detected map: '{detected}' from position ({x:.0f}, {y:.0f})")
            self._learn_zone_name_mapping(detected)

    # English names that are always the player hideout, regardless of FName.
    _HIDEOUT_ENGLISH_NAMES = {"embers rest"}

    def _is_hideout_zone(self, fname: str) -> bool:
        """Return True when the raw zone FName represents the player hideout.

        The raw FName (e.g. 'XZ_YuJinZhiXiBiNanSuo200') never contains the
        word 'hideout' — the English mapping ('Embers Rest') must also be
        consulted.  Both checks are combined so this works even when the
        mapping file is missing or the FName is already an English-style name.
        """
        if not fname:
            return False
        lower = fname.lower()
        # Fast path: FName itself contains hideout/town keyword.
        if "hideout" in lower or "town" in lower:
            return True
        # Slow path: look up English name in zone_name_mapping.
        try:
            mapping = self._load_zone_name_mapping()
            english = mapping.get(fname, "").lower()
            if english in self._HIDEOUT_ENGLISH_NAMES:
                return True
        except Exception:
            pass
        return False

    def _load_zone_name_mapping(self) -> dict:
        if os.path.exists(self.ZONE_MAP_FILE):
            try:
                with open(self.ZONE_MAP_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_zone_name_mapping(self, mapping: dict):
        os.makedirs(os.path.dirname(self.ZONE_MAP_FILE), exist_ok=True)
        with open(self.ZONE_MAP_FILE, "w") as f:
            json.dump(mapping, f, indent=2)

    def _learn_zone_name_mapping(self, english_name: str):
        if not self._scanner:
            return
        internal_name = self._scanner.read_real_zone_name()
        if not internal_name:
            return
        mapping = self._load_zone_name_mapping()
        if internal_name in mapping and mapping[internal_name] == english_name:
            return
        mapping[internal_name] = english_name
        self._save_zone_name_mapping(mapping)
        log.info(f"[ZoneMap] Learned: '{internal_name}' -> '{english_name}' [{len(mapping)}/12 mapped]")

    def detect_map_from_zone_name(self) -> str:
        if not self._scanner:
            return ""
        internal_name = self._scanner.read_real_zone_name()
        if not internal_name:
            return ""
        mapping = self._load_zone_name_mapping()
        english_name = mapping.get(internal_name, "")
        if english_name:
            log.info(f"[ZoneMap] Identified map from FName: '{internal_name}' -> '{english_name}'")
        else:
            log.info(f"[ZoneMap] Unknown zone FName: '{internal_name}' (not in mapping yet)")
        return english_name

    def _record_starting_position(self, map_name: str, x: float, y: float):
        positions = self._load_starting_positions()
        if map_name in positions:
            log.info(f"Starting position for '{map_name}' already recorded — skipping")
            return
        positions[map_name] = {"x": round(x, 1), "y": round(y, 1)}
        os.makedirs(os.path.dirname(self.START_POS_FILE), exist_ok=True)
        with open(self.START_POS_FILE, "w") as f:
            json.dump(positions, f, indent=2)
        collected = len(positions)
        log.info(f"NEW starting position recorded: '{map_name}' = ({x:.1f}, {y:.1f})  [{collected}/12 maps collected]")

    def _handle_map_complete(self):
        self._maps_completed += 1
        log.info(f"Map #{self._maps_completed} complete")
        self._cycle_end("success")
        time.sleep(0.5)

        self.game_state.update()
        if self.game_state.map.is_in_hideout:
            self._set_state(BotState.IN_HIDEOUT)
        else:
            self._set_state(BotState.RETURNING)

    def _handle_error(self):
        log.error("Bot in error state - attempting recovery")
        if self._cycle_active:
            self._cycle_end("failed")
        if self._portal_detector:
            self._portal_detector.stop_polling()
        time.sleep(5.0)

        try:
            if not self.memory.is_attached:
                process_name = self.config.get("game_process", GAME_PROCESS_NAME)
                if self.memory.attach(process_name):
                    pid = self.memory.process_id
                    if pid:
                        self.window.set_target_pid(pid)
                    self.window.find_window()
                    if self.window.hwnd:
                        self.input.set_target_window(self.window.hwnd)
                    self._set_state(BotState.STARTING)
                    return
        except Exception as e:
            log.error(f"Error recovery failed: {e}")

        self.stop()
