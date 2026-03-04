import sys
import json
import os
import time
import threading
import queue
from typing import Optional
import customtkinter as ctk

from src.gui.theme import COLORS, FONTS, apply_theme, create_label, create_accent_button
from src.gui.tabs.dashboard_tab import DashboardTab
from src.gui.tabs.address_manager_tab import AddressManagerTab
from src.gui.tabs.paths_tab import PathsTab
from src.gui.tabs.settings_tab import SettingsTab
from src.gui.tabs.entity_scanner_tab import EntityScannerTab
from src.gui.tabs.card_priority_tab import CardPriorityTab
from src.gui.overlay import DebugOverlay
from src.core.bot_engine import BotEngine
from src.utils.constants import APP_NAME, APP_VERSION, WALL_GRID_CELL_SIZE, HARDCODED_MAP_PORTALS
from src.utils.logger import log

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    try:
        import ctypes
        import ctypes.wintypes as wintypes
        HAS_HOTKEY_API = True
    except Exception:
        HAS_HOTKEY_API = False
else:
    HAS_HOTKEY_API = False


class BotApp(ctk.CTk):
    def __init__(self):
        apply_theme()
        super().__init__()

        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("580x900+10+10")
        self.minsize(520, 700)
        self.configure(fg_color=COLORS["bg_dark"])

        self._engine = BotEngine()
        self._tabs = {}
        self._active_tab = None
        self._overlay: Optional[DebugOverlay] = None
        self._last_zone_fname: str = ""  # cached FName for live map detection
        self._last_zone_english: str = ""  # cached English name matching _last_zone_fname
        self._stop_hotkey_lock = threading.Lock()
        self._stop_hotkey_in_progress = False
        self._gui_log_queue: "queue.Queue[tuple]" = queue.Queue(maxsize=3000)
        self._gui_log_dropped = 0
        self._gui_log_drop_lock = threading.Lock()
        self._log_callback = None
        self._log_pump_after_id = None
        self._overlay_data_lock = threading.Lock()
        self._overlay_data = {
            "portal_positions": [],
            "event_markers": [],
            "guard_markers": [],
            "nav_collision_markers": [],
        }
        self._overlay_worker_stop = threading.Event()
        self._overlay_worker_thread: Optional[threading.Thread] = None
        self._overlay_last_map_check_t = 0.0
        self._debug_ui_enabled = bool(self._engine.config.get("debug_ui_enabled", False))

        self._build_ui()
        self._setup_log_callback()
        self._setup_hotkeys()
        self._setup_overlay_connection()

        log.info(f"{APP_NAME} v{APP_VERSION} started")
        # Auto-attach if the game process is already running when the bot opens.
        self.after(800, self._auto_attach_if_game_running)

    def _auto_attach_if_game_running(self):
        """Automatically trigger attach if torchlight_infinite.exe is already running.

        Called once 800 ms after startup via self.after().  Silently skips if:
        - Already attached (e.g. saved-addresses fast-path ran)
        - Game process not found
        Delegates entirely to AddressManagerTab._on_attach so all UI feedback
        (button state, status labels, scan log) behaves identically to a manual click.
        """
        if self._engine.memory.is_attached:
            return
        import psutil
        game_process = self._engine.config.get("game_process", "torchlight_infinite.exe")
        running = any(
            p.info["name"] == game_process
            for p in psutil.process_iter(["name"])
            if p.info["name"]
        )
        if running:
            log.info(f"[AutoAttach] {game_process} detected — attaching automatically")
            addr_tab = self._tabs.get("addresses")
            if addr_tab and hasattr(addr_tab, "_on_attach"):
                addr_tab._on_attach()

    def _build_ui(self):
        sidebar = ctk.CTkFrame(self, fg_color=COLORS["bg_medium"], width=130, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._sidebar = sidebar

        logo_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        logo_frame.pack(fill="x", padx=16, pady=(20, 8))

        create_label(logo_frame, "TL Bot", "heading", "accent_cyan").pack(anchor="w")
        create_label(logo_frame, f"v{APP_VERSION} | Memory Edition", "small", "text_muted").pack(anchor="w")

        separator = ctk.CTkFrame(sidebar, fg_color=COLORS["border"], height=1)
        separator.pack(fill="x", padx=16, pady=12)

        self._nav_buttons = {}
        self._create_nav_button("dashboard", "Dashboard")
        self._create_nav_button("addresses", "Address Setup")
        self._create_nav_button("paths", "Map Paths")
        if self._debug_ui_enabled:
            self._create_nav_button("entities", "Entity Scanner")
        self._create_nav_button("cards", "Card Priority")
        self._create_nav_button("settings", "Settings")

        sidebar_bottom = ctk.CTkFrame(sidebar, fg_color="transparent")
        sidebar_bottom.pack(side="bottom", fill="x", padx=16, pady=16)

        separator2 = ctk.CTkFrame(sidebar_bottom, fg_color=COLORS["border"], height=1)
        separator2.pack(fill="x", pady=(0, 12))

        self._overlay_btn = create_accent_button(sidebar_bottom, "Overlay: OFF", self._toggle_overlay, color="accent_purple")
        self._overlay_btn.pack(fill="x", pady=(0, 4))

        self._calibrate_btn = create_accent_button(sidebar_bottom, "Calibrate Scale", self._calibrate_scale, color="accent_purple")
        self._calibrate_btn.pack(fill="x", pady=(0, 8))

        hotkey_info = (
            "Global Hotkeys:\n"
            "F5  - Record\n"
            "F6  - Pause Rec\n"
            "F9  - Start\n"
            "F10 - Stop\n"
            "F11 - Pause/Resume\n"
            "P   - Mark Portal"
        )
        create_label(sidebar_bottom, hotkey_info, "small", "text_muted").pack(anchor="w")

        self._content = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=0)
        self._content.pack(side="right", fill="both", expand=True)

        self._tabs["dashboard"] = DashboardTab(self._content, self._engine)
        self._tabs["addresses"] = AddressManagerTab(
            self._content,
            self._engine,
            on_debug_ui_changed=self._on_debug_ui_changed,
        )
        self._tabs["paths"] = PathsTab(self._content, self._engine)
        if self._debug_ui_enabled:
            self._tabs["entities"] = EntityScannerTab(self._content, self._engine)
        self._tabs["cards"] = CardPriorityTab(self._content, self._engine)
        self._tabs["settings"] = SettingsTab(self._content, self._engine)

        self._switch_tab("dashboard")

    def _switch_tab(self, tab_id):
        if tab_id not in self._tabs:
            return
        if self._active_tab:
            self._tabs[self._active_tab].pack_forget()

        for btn_id, btn in self._nav_buttons.items():
            if btn_id == tab_id:
                btn.configure(
                    fg_color=COLORS["bg_light"],
                    text_color=COLORS["accent_cyan"],
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=COLORS["text_secondary"],
                )

        self._tabs[tab_id].pack(fill="both", expand=True)
        self._active_tab = tab_id

    def _create_nav_button(self, tab_id: str, label: str, before: Optional[ctk.CTkButton] = None):
        btn = ctk.CTkButton(
            self._sidebar,
            text=f"  {label}",
            anchor="w",
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_secondary"],
            font=FONTS["small"],
            height=34,
            corner_radius=6,
            command=lambda t=tab_id: self._switch_tab(t),
        )
        pack_kwargs = {"fill": "x", "padx": 8, "pady": 2}
        if before is not None:
            pack_kwargs["before"] = before
        btn.pack(**pack_kwargs)
        self._nav_buttons[tab_id] = btn
        return btn

    def _on_debug_ui_changed(self, enabled: bool):
        self._set_debug_ui_enabled(enabled)

    def _set_debug_ui_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if self._debug_ui_enabled == enabled:
            return
        self._debug_ui_enabled = enabled

        if enabled:
            if "entities" not in self._tabs:
                self._tabs["entities"] = EntityScannerTab(self._content, self._engine)
            if "entities" not in self._nav_buttons:
                before_btn = self._nav_buttons.get("cards")
                self._create_nav_button("entities", "Entity Scanner", before=before_btn)
        else:
            if self._active_tab == "entities":
                self._switch_tab("dashboard")
            tab = self._tabs.pop("entities", None)
            if tab is not None:
                try:
                    tab.pack_forget()
                except Exception:
                    pass
                tab.destroy()
            btn = self._nav_buttons.pop("entities", None)
            if btn is not None:
                btn.destroy()

    def _setup_log_callback(self):
        dashboard = self._tabs.get("dashboard")
        if dashboard:
            def _queue_log(level, message):
                try:
                    self._gui_log_queue.put_nowait((level, message))
                except queue.Full:
                    with self._gui_log_drop_lock:
                        self._gui_log_dropped += 1

            self._log_callback = _queue_log
            log.add_callback(self._log_callback)
            self._pump_log_queue()

    def _pump_log_queue(self):
        dashboard = self._tabs.get("dashboard")
        if dashboard:
            processed = 0
            while processed < 250:
                try:
                    level, message = self._gui_log_queue.get_nowait()
                except queue.Empty:
                    break
                dashboard.add_log(level, message)
                processed += 1

            dropped = 0
            with self._gui_log_drop_lock:
                if self._gui_log_dropped > 0:
                    dropped = self._gui_log_dropped
                    self._gui_log_dropped = 0
            if dropped > 0:
                dashboard.add_log("WARNING", f"[GUI] Dropped {dropped} log lines (UI queue overflow)")

        self._log_pump_after_id = self.after(33, self._pump_log_queue)

    def _setup_hotkeys(self):
        self.bind("<p>", lambda e: self._on_portal_hotkey())
        self.bind("<P>", lambda e: self._on_portal_hotkey())

        if not HAS_HOTKEY_API:
            log.warning("[Hotkeys] Windows API not available, falling back to tkinter binds (won't work when game is focused)")
            self.bind("<F9>", lambda e: self._engine.start())
            self.bind("<F10>", lambda e: self._on_stop_hotkey())
            self.bind("<F11>", lambda e: self._engine.pause())
            self.bind("<F5>", lambda e: self._on_record_hotkey())
            self.bind("<F6>", lambda e: self._on_pause_record_hotkey())
            return

        self._hotkey_thread_running = True
        self._hotkey_ids = {}

        HOTKEY_START = 1
        HOTKEY_STOP = 2
        HOTKEY_PAUSE = 3
        HOTKEY_RECORD = 4
        HOTKEY_RECORD_PAUSE = 5

        VK_F5 = 0x74
        VK_F6 = 0x75
        VK_F9 = 0x78
        VK_F10 = 0x79
        VK_F11 = 0x7A
        MOD_NOREPEAT = 0x4000
        WM_HOTKEY = 0x0312

        def _hotkey_thread():
            try:
                r1 = ctypes.windll.user32.RegisterHotKey(None, HOTKEY_START, MOD_NOREPEAT, VK_F9)
                r2 = ctypes.windll.user32.RegisterHotKey(None, HOTKEY_STOP, MOD_NOREPEAT, VK_F10)
                r3 = ctypes.windll.user32.RegisterHotKey(None, HOTKEY_PAUSE, MOD_NOREPEAT, VK_F11)
                r4 = ctypes.windll.user32.RegisterHotKey(None, HOTKEY_RECORD, MOD_NOREPEAT, VK_F5)
                r5 = ctypes.windll.user32.RegisterHotKey(None, HOTKEY_RECORD_PAUSE, MOD_NOREPEAT, VK_F6)

                registered = []
                if r1: registered.append("F9=Start")
                if r2: registered.append("F10=Stop")
                if r3: registered.append("F11=Pause")
                if r4: registered.append("F5=Record")
                if r5: registered.append("F6=PauseRec")
                log.info(f"[Hotkeys] Global hotkeys registered: {', '.join(registered)}")

                all_ok = r1 and r2 and r3 and r4 and r5
                if not all_ok:
                    failed = []
                    if not r1: failed.append("F9")
                    if not r2: failed.append("F10")
                    if not r3: failed.append("F11")
                    if not r4: failed.append("F5")
                    if not r5: failed.append("F6")
                    log.warning(f"[Hotkeys] Failed to register: {', '.join(failed)} (may be in use by another app)")

                msg = wintypes.MSG()
                while self._hotkey_thread_running:
                    result = ctypes.windll.user32.PeekMessageW(
                        ctypes.byref(msg), None, WM_HOTKEY, WM_HOTKEY, 1
                    )
                    if result:
                        hotkey_id = msg.wParam
                        if hotkey_id == HOTKEY_START:
                            log.info("[Hotkeys] F9 pressed - Start")
                            self.after(0, self._engine.start)
                        elif hotkey_id == HOTKEY_STOP:
                            log.info("[Hotkeys] F10 pressed - Stop")
                            self.after(0, self._on_stop_hotkey)
                        elif hotkey_id == HOTKEY_PAUSE:
                            log.info("[Hotkeys] F11 pressed - Pause/Resume")
                            self.after(0, self._engine.pause)
                        elif hotkey_id == HOTKEY_RECORD:
                            log.info("[Hotkeys] F5 pressed - Record")
                            self.after(0, self._on_record_hotkey)
                        elif hotkey_id == HOTKEY_RECORD_PAUSE:
                            log.info("[Hotkeys] F6 pressed - Pause Recording")
                            self.after(0, self._on_pause_record_hotkey)
                    else:
                        import time
                        time.sleep(0.05)
            except Exception as e:
                log.error(f"[Hotkeys] Thread error: {e}")
            finally:
                ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_START)
                ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_STOP)
                ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_PAUSE)
                ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_RECORD)
                ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_RECORD_PAUSE)
                log.info("[Hotkeys] Global hotkeys unregistered")

        self._hotkey_thread = threading.Thread(target=_hotkey_thread, daemon=True, name="GlobalHotkeys")
        self._hotkey_thread.start()

    def _on_record_hotkey(self):
        paths_tab = self._tabs.get("paths")
        if paths_tab:
            paths_tab._on_record()

    def _on_pause_record_hotkey(self):
        paths_tab = self._tabs.get("paths")
        if paths_tab:
            paths_tab._on_pause_record()

    def _on_stop_hotkey(self):
        """Global stop action: stop bot and force-stop map explorer."""
        with self._stop_hotkey_lock:
            if self._stop_hotkey_in_progress:
                log.info("[Hotkeys] Stop already running — ignoring duplicate F10")
                return
            self._stop_hotkey_in_progress = True

        def _run_stop_sequence():
            try:
                self._engine.stop()
                self._engine.stop_map_explorer()
            finally:
                def _clear_flag():
                    with self._stop_hotkey_lock:
                        self._stop_hotkey_in_progress = False
                self.after(0, _clear_flag)

        threading.Thread(target=_run_stop_sequence, daemon=True, name="HotkeyStop").start()

    def _on_portal_hotkey(self):
        recorder = self._engine.path_recorder
        if recorder.is_recording:
            recorder.add_portal_waypoint()
            paths_tab = self._tabs.get("paths")
            if paths_tab:
                paths_tab._rec_count.configure(text=f"Points: {recorder.waypoint_count}")

    def _setup_overlay_connection(self):
        paths_tab = self._tabs.get("paths")
        if paths_tab:
            paths_tab.set_overlay_callback(self._on_overlay_waypoints_update)
            paths_tab.set_grid_overlay_callback(self._on_grid_data_update)

    def _on_grid_data_update(self, walkable_xy, frontier_xy, cell_size: float = WALL_GRID_CELL_SIZE):
        """Called by PathsTab when manual-explore grid data is refreshed."""
        from src.gui.overlay import DebugOverlay
        overlay = self._overlay
        if overlay and overlay._running:
            overlay.set_grid_data(walkable_xy, frontier_xy, cell_size)
            overlay.set_layer_visible(DebugOverlay.LAYER_GRID, True)

    def _toggle_overlay(self):
        if self._overlay and self._overlay._running:
            self._stop_position_poll()
            self._stop_overlay_worker()
            self._overlay.stop()
            self._overlay = None
            self._overlay_btn.configure(text="Overlay: OFF")
            self._engine.set_debug_overlay(None)
            log.info("[Overlay] Debug overlay stopped")
        else:
            window_mgr = getattr(self._engine, 'window', None)
            if window_mgr and hasattr(window_mgr, 'get_client_rect'):
                try:
                    rect = window_mgr.get_client_rect()
                    if rect:
                        x, y, x2, y2 = rect
                        self._overlay = DebugOverlay(game_window_rect=(x, y, x2 - x, y2 - y))
                    else:
                        self._overlay = DebugOverlay()
                except Exception:
                    self._overlay = DebugOverlay()
            else:
                self._overlay = DebugOverlay()

            paths_tab = self._tabs.get("paths")
            if paths_tab and paths_tab._loaded_waypoints:
                self._overlay.set_waypoints(paths_tab._loaded_waypoints)

            calibrator = self._engine.scale_calibrator
            if calibrator:
                current_map = self._resolve_current_map()
                cal = calibrator.get_calibration(current_map)
                if cal:
                    self._overlay.set_calibration(cal, current_map)
                    log.info(f"[Overlay] Loaded calibration for map: {current_map}")
                else:
                    log.info(f"[Overlay] No calibration for map: {current_map} — use 'Calibrate Scale' button")
            self._overlay.start()
            self._overlay_btn.configure(text="Overlay: ON")
            self._engine.set_debug_overlay(self._overlay)
            log.info("[Overlay] Debug overlay started")
            self._start_position_poll()
            self._start_overlay_worker()
            self._start_overlay_feed()

    def _start_overlay_worker(self):
        """Start background worker that collects heavy overlay data from memory.

        Keeps memory reads off Tk thread to avoid GUI stalls/freezes.
        """
        self._overlay_worker_stop.clear()
        if self._overlay_worker_thread and self._overlay_worker_thread.is_alive():
            return

        def _worker_loop():
            interval = 0.20  # 5 Hz heavy-data refresh
            while not self._overlay_worker_stop.is_set():
                t0 = time.monotonic()
                try:
                    overlay = self._overlay
                    if not overlay or not overlay._running:
                        break

                    portal_positions = []
                    event_markers = []
                    guard_markers = []
                    nav_collision_markers = []

                    portal_det = getattr(self._engine, 'portal_detector', None)
                    if portal_det:
                        try:
                            if hasattr(portal_det, 'get_portal_markers'):
                                portal_positions = portal_det.get_portal_markers() or []
                            elif hasattr(portal_det, 'get_portal_positions'):
                                portal_positions = portal_det.get_portal_positions() or []
                        except Exception:
                            portal_positions = []

                    # Merge hardcoded per-map portal presets for deterministic
                    # portal maps (e.g., Grimwind), so overlay stays informative
                    # even when live portal set is temporarily sparse.
                    try:
                        current_map = self._resolve_current_map()
                        hardcoded = HARDCODED_MAP_PORTALS.get(current_map, []) or []
                        if hardcoded:
                            existing = {
                                (int(round(float(p.get("x", 0.0)))), int(round(float(p.get("y", 0.0)))))
                                for p in portal_positions if isinstance(p, dict)
                            }
                            for hp in hardcoded:
                                key = (
                                    int(round(float(hp.get("x", 0.0)))),
                                    int(round(float(hp.get("y", 0.0)))),
                                )
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

                    # Final coalescing pass: live portal feed may contain
                    # duplicate markers at identical coordinates (including
                    # live + hardcoded overlap with slightly different fields).
                    # Keep one marker per rounded (x,y); prefer exit=True when
                    # both variants exist at the same position.
                    try:
                        dedup: dict = {}
                        for p in portal_positions:
                            if not isinstance(p, dict):
                                continue
                            x = float(p.get("x", 0.0))
                            y = float(p.get("y", 0.0))
                            key = (int(round(x)), int(round(y)))
                            prev = dedup.get(key)
                            if prev is None:
                                dedup[key] = dict(p)
                            else:
                                prev_exit = bool(prev.get("is_exit", False))
                                cur_exit = bool(p.get("is_exit", False))
                                if (not prev_exit) and cur_exit:
                                    dedup[key] = dict(p)
                        portal_positions = list(dedup.values())
                    except Exception:
                        pass

                    scanner = getattr(self._engine, 'scanner', None)
                    if scanner:
                        try:
                            events = scanner.get_typed_events() or []
                            for e in events:
                                if abs(e.position[0]) <= 1.0 and abs(e.position[1]) <= 1.0:
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
                            _raw_guards = scanner.get_carjack_guard_positions() or []
                            guard_markers = [
                                {
                                    "x": g["x"],
                                    "y": g["y"],
                                    "abp": g.get("abp", ""),
                                    "score": 0.0,
                                    "dist_truck": g.get("dist_truck", -1.0),
                                }
                                for g in _raw_guards
                            ]
                        except Exception:
                            guard_markers = []

                        try:
                            if self._engine.config.get("nav_collision_overlay_enabled", False):
                                raw_markers = scanner.get_nav_collision_markers() or []
                                show_raw = bool(self._engine.config.get("nav_collision_overlay_show_raw", True))
                                show_inflated = bool(self._engine.config.get("nav_collision_overlay_inflate_debug", False))
                                try:
                                    inflate_u = float(self._engine.config.get("nav_collision_grid_inflate_u", 0.0) or 0.0)
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

                    with self._overlay_data_lock:
                        self._overlay_data["portal_positions"] = portal_positions
                        self._overlay_data["event_markers"] = event_markers
                        self._overlay_data["guard_markers"] = guard_markers
                        self._overlay_data["nav_collision_markers"] = nav_collision_markers
                except Exception:
                    pass

                sleep_for = interval - (time.monotonic() - t0)
                if sleep_for > 0.001:
                    time.sleep(sleep_for)

        self._overlay_worker_thread = threading.Thread(
            target=_worker_loop,
            daemon=True,
            name="OverlayDataWorker",
        )
        self._overlay_worker_thread.start()

    def _stop_overlay_worker(self):
        self._overlay_worker_stop.set()
        t = self._overlay_worker_thread
        if t and t.is_alive():
            t.join(timeout=0.5)
        self._overlay_worker_thread = None

    def _resolve_current_map(self) -> str:
        """Return the English map name for the currently loaded zone.

        Reads the live zone FName from the scanner so it is correct even when
        the bot is not running and config["current_map"] is stale (e.g. when
        the user manually navigates to a new map between bot runs).
        Caches the last FName to avoid re-reading the mapping file on every
        200ms overlay tick when the zone has not changed.
        Falls back to config["current_map"] if the zone cannot be resolved.
        """
        scanner = getattr(self._engine, '_scanner', None)
        if scanner:
            try:
                internal = scanner.read_real_zone_name()
                if internal:
                    # Only re-read the mapping file when the zone FName changes
                    if internal != self._last_zone_fname:
                        self._last_zone_fname = internal
                        self._last_zone_english = internal  # default: raw FName
                        mapping_file = os.path.join("data", "zone_name_mapping.json")
                        if os.path.exists(mapping_file):
                            try:
                                with open(mapping_file, "r") as _f:
                                    _mapping = json.load(_f)
                                english = _mapping.get(internal, "")
                                if english:
                                    self._last_zone_english = english
                            except Exception:
                                pass
                    return self._last_zone_english
            except Exception:
                pass
        return self._engine.config.get("current_map", "hideout")

    def _calibrate_scale(self):
        if not self._engine.memory.is_attached:
            log.warning("[ScaleCalibrator] Not attached to game - attach first")
            return

        calibrator = self._engine.scale_calibrator
        if not calibrator:
            return

        if calibrator.is_calibrating:
            log.info("[ScaleCalibrator] Already calibrating...")
            return

        self._calibrate_btn.configure(text="Calibrating...", state="disabled")

        current_map = self._resolve_current_map()
        log.info(f"[ScaleCalibrator] Starting calibration for map: {current_map} - keep game focused!")
        calibrator.set_current_map(current_map)

        def run_calibration():
            try:
                cal = calibrator.calibrate(self._engine.game_state, map_name=current_map)
                if cal:
                    log.info(f"[ScaleCalibrator] Calibration saved for '{current_map}'")
                    if self._overlay and self._overlay._running:
                        self._overlay.set_calibration(cal, current_map)
                    self.after(0, lambda: self._calibrate_btn.configure(
                        text=f"Calibrated: {current_map}", state="normal"))
                else:
                    log.error("[ScaleCalibrator] Calibration failed")
                    self.after(0, lambda: self._calibrate_btn.configure(
                        text="Calibrate Scale", state="normal"))
            except Exception as e:
                log.error(f"[ScaleCalibrator] Error: {e}")
                self.after(0, lambda: self._calibrate_btn.configure(
                    text="Calibrate Scale", state="normal"))

        t = threading.Thread(target=run_calibration, daemon=True)
        t.start()

    def _on_overlay_waypoints_update(self, waypoints):
        if self._overlay and self._overlay._running:
            self._overlay.set_waypoints(waypoints)

    def _start_position_poll(self):
        """Spawn a ~60 Hz background thread dedicated to player position reads.

        Reads x,y directly via gs.read_chain() — only 2 pointer-chain walks per
        tick, completely independent of the tkinter event loop and its scheduler
        jitter.  Supplies samples to the overlay's velocity estimator so
        dead-reckoning is accurate at any movement speed.  60 Hz is sufficient
        because the dead-reckoning prediction fills the gap between reads.
        """
        self._pos_poll_stop = threading.Event()
        t = threading.Thread(target=self._position_poll_loop,
                             daemon=True, name="OverlayPosFeed")
        t.start()

    def _stop_position_poll(self):
        if hasattr(self, '_pos_poll_stop'):
            self._pos_poll_stop.set()

    def _position_poll_loop(self):
        """Feed the overlay with the position published by the shared PositionPoller.

        The PositionPoller is the single 120 Hz memory reader.  This loop
        no longer calls read_chain() directly — it just wakes up, grabs
        the cached (x, y) from the poller, and passes it to the overlay.
        """
        interval = 0.016  # 16 ms = ~60 Hz
        while not self._pos_poll_stop.is_set():
            t0 = time.monotonic()
            try:
                overlay = self._overlay
                if not overlay or not overlay._running:
                    break
                engine = self._engine
                if engine:
                    x, y = engine._pos_poller.get_pos()
                    if x != 0.0 or y != 0.0:
                        overlay.set_player_position(x, y)
            except Exception:
                pass
            sleep_for = interval - (time.monotonic() - t0)
            if sleep_for > 0.0001:
                time.sleep(sleep_for)

    def _start_overlay_feed(self):
        """Low-frequency feed (50 ms) for everything except position.

        Position is handled by the dedicated ~60 Hz _position_poll_loop thread.
        This feed covers: focus/rect sync, calibration map-change detection,
        navigator state, portals, and events — all of which change slowly.
        """
        if not self._overlay or not self._overlay._running:
            return

        try:
            wm = getattr(self._engine, 'window', None)
            if wm:
                game_focused = wm.is_foreground()
                bot_focused = self.focus_get() is not None
                self._overlay.set_game_focused(game_focused or bot_focused)

                client_rect = wm.get_client_rect()
                if client_rect:
                    x, y, x2, y2 = client_rect
                    self._overlay.set_game_rect((x, y, x2 - x, y2 - y))

            calibrator = self._engine.scale_calibrator
            if calibrator and (time.monotonic() - self._overlay_last_map_check_t >= 0.5):
                self._overlay_last_map_check_t = time.monotonic()
                current_map = self._resolve_current_map()
                if current_map != self._overlay._current_map_name:
                    cal = calibrator.get_calibration(current_map)
                    if cal:
                        self._overlay.set_calibration(cal, current_map)
                        log.info(f"[Overlay] Switched calibration to map: {current_map}")
                    else:
                        self._overlay.set_calibration(None, current_map)

            with self._overlay_data_lock:
                portal_positions = self._overlay_data["portal_positions"]
                event_markers = self._overlay_data["event_markers"]
                guard_markers = self._overlay_data["guard_markers"]
                nav_collision_markers = self._overlay_data["nav_collision_markers"]

            self._overlay.set_portal_positions(portal_positions)
            self._overlay.set_event_markers(event_markers)
            self._overlay.set_guard_markers(guard_markers)
            self._overlay.set_nav_collision_markers(nav_collision_markers)

        except Exception:
            pass

        if self._overlay and self._overlay._running:
            self.after(33, self._start_overlay_feed)

    def on_closing(self):
        if self._log_pump_after_id is not None:
            try:
                self.after_cancel(self._log_pump_after_id)
            except Exception:
                pass
            self._log_pump_after_id = None
        if self._log_callback is not None:
            try:
                log.remove_callback(self._log_callback)
            except Exception:
                pass
            self._log_callback = None
        if hasattr(self, '_hotkey_thread_running'):
            self._hotkey_thread_running = False
        self._stop_position_poll()
        self._stop_overlay_worker()
        if self._overlay:
            self._overlay.stop()
            self._overlay = None
        if self._engine.is_running:
            self._engine.stop()
        for tab in self._tabs.values():
            try:
                if hasattr(tab, 'destroy'):
                    tab.destroy()
            except Exception:
                pass
        self._engine.memory.detach()
        self.destroy()

    def run(self):
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.mainloop()
