import json
import os
import sys
import threading
import time
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.bot_engine import BotEngine
from src.gui.overlay import DebugOverlay
from src.gui_qt.engine_bridge import EngineBridge
from src.gui_qt.pages import (
    AddressesPage,
    CardsPage,
    DashboardPage,
    EntityScannerPage,
    PathsPage,
    SettingsPage,
)
from src.gui_qt.theme import WINDOW_STYLESHEET, set_button_variant
from src.utils.constants import APP_NAME, APP_VERSION, HARDCODED_MAP_PORTALS, WALL_GRID_CELL_SIZE
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


class BotAppQt(QMainWindow):
    """Qt shell with phased parity controls over the existing BotEngine."""

    _hotkey_action_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION} [Qt]")
        self._apply_default_window_placement()
        self.setMinimumSize(620, 620)

        self._engine = BotEngine()
        self._bridge = EngineBridge(self._engine)
        self._debug_ui_enabled = bool(self._engine.config.get("debug_ui_enabled", False))

        self._buttons: Dict[str, QPushButton] = {}
        self._pages: Dict[str, QFrame] = {}
        self._overlay: Optional[DebugOverlay] = None
        self._last_zone_fname = ""
        self._last_zone_english = ""
        self._overlay_data_lock = threading.Lock()
        self._overlay_data: Dict[str, Any] = {
            "portal_positions": [],
            "event_markers": [],
            "guard_markers": [],
            "nav_collision_markers": [],
        }
        self._overlay_worker_stop = threading.Event()
        self._overlay_worker_thread: Optional[threading.Thread] = None
        self._overlay_last_map_check_t = 0.0
        self._pos_poll_stop: Optional[threading.Event] = None
        self._hotkey_thread = None
        self._hotkey_thread_running = False
        self._stop_hotkey_lock = threading.Lock()
        self._stop_hotkey_in_progress = False

        self._build_ui()
        self._hotkey_action_signal.connect(self._on_hotkey_action)
        self._setup_hotkeys()
        self._setup_overlay_connection()
        self._set_page("dashboard")

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        self._overlay_feed_timer = QTimer(self)
        self._overlay_feed_timer.setInterval(33)
        self._overlay_feed_timer.timeout.connect(self._overlay_feed_tick)

        QTimer.singleShot(800, self._auto_attach_if_game_running)

        log.info(f"{APP_NAME} v{APP_VERSION} Qt shell started")

    def _apply_default_window_placement(self):
        """Place the bot on the left by default and size it for side-by-side play.

        Target: 2k (2560x1440) desktop where game + bot run next to each other.
        """
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if not screen:
            self.resize(980, 760)
            return

        avail = screen.availableGeometry()
        # Side panel width tuned for 2560-wide desktops, with safe clamps for others.
        width = min(760, max(620, int(avail.width() * 0.25)))
        # Leave enough margins for window frame/titlebar across DPI scales.
        frame_margin_x = 8
        frame_margin_top = 24
        frame_margin_bottom = 48
        # Keep the panel noticeably shorter by default to avoid oversized vertical feel.
        height = max(700, avail.height() - (frame_margin_top + frame_margin_bottom + 200))
        x = avail.x() + frame_margin_x
        y = avail.y() + frame_margin_top
        self.setGeometry(x, y, width, height)

    def _build_ui(self):
        self.setStyleSheet(WINDOW_STYLESHEET)

        root = QWidget(self)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame(root)
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(196)
        side_layout = QVBoxLayout(sidebar)
        self._side_layout = side_layout
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(8)

        title = QLabel("TL Bot")
        title.setObjectName("Title")
        subtitle = QLabel(f"v{APP_VERSION} | Qt Phase 4")
        subtitle.setObjectName("Subtitle")
        side_layout.addWidget(title)
        side_layout.addWidget(subtitle)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #30363D;")
        side_layout.addWidget(sep)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        nav_items = [
            ("dashboard", "Dashboard"),
            ("addresses", "Address Setup"),
            ("paths", "Map Paths"),
                *((('entities', 'Entity Scanner'),) if self._debug_ui_enabled else ()),
            ("cards", "Card Priority"),
            ("settings", "Settings"),
        ]
        for key, label in nav_items:
            btn = QPushButton(label)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, k=key: self._set_page(k))
            self._nav_group.addButton(btn)
            self._buttons[key] = btn
            side_layout.addWidget(btn)

        side_layout.addStretch(1)

        self._overlay_btn = QPushButton("Overlay: OFF")
        self._overlay_btn.clicked.connect(self._toggle_overlay)
        set_button_variant(self._overlay_btn, "info")
        side_layout.addWidget(self._overlay_btn)

        note = QLabel(
            "Safe preview mode:\n"
            "- Core logic unchanged\n"
            "- Tk GUI remains fallback"
        )
        note.setObjectName("PageBody")
        note.setWordWrap(True)
        side_layout.addWidget(note)

        content = QFrame(root)
        content.setObjectName("Content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(14, 14, 14, 14)

        self._stack = QStackedWidget(content)
        content_layout.addWidget(self._stack)

        self._pages["dashboard"] = DashboardPage(self._bridge)
        self._pages["addresses"] = AddressesPage(self._bridge, on_debug_ui_changed=self._on_debug_ui_changed)
        self._pages["paths"] = PathsPage(self._bridge)
        if self._debug_ui_enabled:
            self._pages["entities"] = EntityScannerPage(self._bridge)
        self._pages["cards"] = CardsPage(self._bridge)
        self._pages["settings"] = SettingsPage(self._bridge, on_calibrate=self._on_manual_calibrate_requested)

        page_order = ["dashboard", "addresses", "paths"]
        if self._debug_ui_enabled:
            page_order.append("entities")
        page_order.extend(["cards", "settings"])
        for key in page_order:
            self._stack.addWidget(self._pages[key])

        root_layout.addWidget(sidebar)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)

    def _setup_overlay_connection(self):
        paths = self._pages.get("paths")
        if paths is not None and hasattr(paths, "set_overlay_callback"):
            paths.set_overlay_callback(self._on_overlay_waypoints_update)
        if paths is not None and hasattr(paths, "set_grid_overlay_callback"):
            paths.set_grid_overlay_callback(self._on_grid_data_update)

    def _set_page(self, key: str):
        page = self._pages.get(key)
        btn = self._buttons.get(key)
        if page is None or btn is None:
            return
        self._stack.setCurrentWidget(page)
        btn.setChecked(True)

    def _on_debug_ui_changed(self, enabled: bool):
        self._set_debug_ui_enabled(enabled)

    def _set_debug_ui_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if self._debug_ui_enabled == enabled:
            return
        self._debug_ui_enabled = enabled

        if enabled:
            if "entities" not in self._pages:
                page = EntityScannerPage(self._bridge)
                self._pages["entities"] = page
                self._stack.addWidget(page)
            if "entities" not in self._buttons:
                btn = QPushButton("Entity Scanner")
                btn.setObjectName("NavButton")
                btn.setCheckable(True)
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda checked=False, k="entities": self._set_page(k))
                self._nav_group.addButton(btn)
                self._buttons["entities"] = btn
                cards_btn = self._buttons.get("cards")
                insert_at = self._side_layout.indexOf(cards_btn) if cards_btn is not None else -1
                if insert_at >= 0:
                    self._side_layout.insertWidget(insert_at, btn)
                else:
                    self._side_layout.addWidget(btn)
        else:
            if self._stack.currentWidget() is self._pages.get("entities"):
                self._set_page("dashboard")
            page = self._pages.pop("entities", None)
            if page is not None:
                self._stack.removeWidget(page)
                page.deleteLater()
            btn = self._buttons.pop("entities", None)
            if btn is not None:
                self._nav_group.removeButton(btn)
                btn.deleteLater()

    def _refresh_status(self):
        status = self._bridge.get_status()
        text = (
            f"attached={status.attached} | running={status.running} "
            f"| paused={status.paused} | map='{status.current_map or '-'}'"
        )
        for page in self._pages.values():
            if hasattr(page, "set_status"):
                page.set_status(text)

    def _auto_attach_if_game_running(self):
        if self._engine.memory.is_attached:
            return
        try:
            import psutil

            game_process = self._engine.config.get("game_process", "torchlight_infinite.exe")
            running = any(
                p.info["name"] == game_process
                for p in psutil.process_iter(["name"])
                if p.info["name"]
            )
            if running:
                log.info(f"[AutoAttach] {game_process} detected - attaching automatically")
                page = self._pages.get("addresses")
                if page and hasattr(page, "_on_attach"):
                    page._on_attach()
        except Exception:
            pass

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_P:
            self._on_portal_hotkey()
        elif event.key() == Qt.Key_F9:
            self._start_bot_and_focus_dashboard()
        elif event.key() == Qt.Key_F10:
            self._on_stop_hotkey()
        elif event.key() == Qt.Key_F11:
            self._engine.pause()
        elif event.key() == Qt.Key_F5:
            self._on_record_hotkey()
        elif event.key() == Qt.Key_F6:
            self._on_pause_record_hotkey()
        super().keyPressEvent(event)

    def _setup_hotkeys(self):
        if not HAS_HOTKEY_API:
            log.warning("[Hotkeys] Windows API unavailable in Qt backend")
            return

        self._hotkey_thread_running = True

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

        def _hotkey_thread_fn():
            r1 = r2 = r3 = r4 = r5 = 0
            try:
                import time

                # Do not use RegisterHotKey here: it hijacks keys globally and
                # breaks normal app behavior (e.g. browser F5 refresh).
                log.info("[Hotkeys] Using foreground-gated polling mode (no global key hijack)")

                # Polling safety: always poll key state so hotkeys still work
                # even if WM_HOTKEY delivery is blocked by other software.
                prev_down = {
                    "F5": False,
                    "F6": False,
                    "F9": False,
                    "F10": False,
                    "F11": False,
                }

                last_trigger = {
                    "F5": 0.0,
                    "F6": 0.0,
                    "F9": 0.0,
                    "F10": 0.0,
                    "F11": 0.0,
                }
                trigger_cooldown_s = 0.35

                def _is_game_foreground_for_hotkeys() -> bool:
                    wm = getattr(self._engine, "window", None)
                    if wm and getattr(wm, "hwnd", None):
                        try:
                            if wm.is_foreground():
                                return True
                        except Exception:
                            pass

                    # Process-based check is more robust than strict HWND equality
                    # (some setups focus child/related windows while in game).
                    try:
                        import psutil

                        fg = ctypes.windll.user32.GetForegroundWindow()
                        if not fg:
                            return False
                        pid = wintypes.DWORD()
                        ctypes.windll.user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
                        if not pid.value:
                            return False
                        proc_name = (psutil.Process(pid.value).name() or "").lower()
                        game_proc = (self._engine.config.get("game_process", "torchlight_infinite.exe") or "").lower()
                        return proc_name == game_proc
                    except Exception:
                        return False

                def _dispatch_hotkey(key_name: str, callback, action_name: str, source: str):
                    emergency_stop = (
                        key_name == "F10"
                        and (self._engine.is_running or self._engine.explorer_running)
                    )
                    if not emergency_stop and not _is_game_foreground_for_hotkeys():
                        return
                    if key_name == "F10" and getattr(self, "_stop_hotkey_in_progress", False):
                        return
                    now = time.monotonic()
                    if now - last_trigger[key_name] < trigger_cooldown_s:
                        return
                    last_trigger[key_name] = now
                    log.info(f"[Hotkeys] {key_name} via {source} - {action_name}")
                    self._hotkey_action_signal.emit(key_name)

                def _poll_hotkeys():
                    checks = [
                        ("F5", VK_F5, self._on_record_hotkey, "Record"),
                        ("F6", VK_F6, self._on_pause_record_hotkey, "Pause Recording"),
                        ("F9", VK_F9, self._start_bot_and_focus_dashboard, "Start"),
                        ("F10", VK_F10, self._on_stop_hotkey, "Stop"),
                        ("F11", VK_F11, self._engine.pause, "Pause/Resume"),
                    ]
                    for key_name, vk, callback, action_name in checks:
                        try:
                            is_down = bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
                        except Exception:
                            is_down = False
                        if is_down and not prev_down[key_name]:
                            _dispatch_hotkey(key_name, callback, action_name, "poll")
                        prev_down[key_name] = is_down

                msg = wintypes.MSG()
                while self._hotkey_thread_running:
                    result = ctypes.windll.user32.PeekMessageW(
                        ctypes.byref(msg), None, WM_HOTKEY, WM_HOTKEY, 1
                    )
                    if result:
                        hotkey_id = msg.wParam
                        if hotkey_id == HOTKEY_START:
                            _dispatch_hotkey("F9", None, "Start", "WM_HOTKEY")
                        elif hotkey_id == HOTKEY_STOP:
                            _dispatch_hotkey("F10", None, "Stop", "WM_HOTKEY")
                        elif hotkey_id == HOTKEY_PAUSE:
                            _dispatch_hotkey("F11", None, "Pause/Resume", "WM_HOTKEY")
                        elif hotkey_id == HOTKEY_RECORD:
                            _dispatch_hotkey("F5", None, "Record", "WM_HOTKEY")
                        elif hotkey_id == HOTKEY_RECORD_PAUSE:
                            _dispatch_hotkey("F6", None, "Pause Recording", "WM_HOTKEY")
                    _poll_hotkeys()

                    if not result:
                        time.sleep(0.05)
            except Exception as exc:
                log.error(f"[Hotkeys] Qt thread error: {exc}")
            finally:
                if r1:
                    ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_START)
                if r2:
                    ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_STOP)
                if r3:
                    ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_PAUSE)
                if r4:
                    ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_RECORD)
                if r5:
                    ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_RECORD_PAUSE)

        self._hotkey_thread = threading.Thread(target=_hotkey_thread_fn, daemon=True, name="QtGlobalHotkeys")
        self._hotkey_thread.start()

    def _on_record_hotkey(self):
        page = self._pages.get("paths")
        if page and hasattr(page, "_on_record"):
            page._on_record()

    def _on_hotkey_action(self, key_name: str):
        key = (key_name or "").upper()
        if key == "F9":
            self._start_bot_and_focus_dashboard()
        elif key == "F10":
            self._on_stop_hotkey()
        elif key == "F11":
            self._engine.pause()
        elif key == "F5":
            self._on_record_hotkey()
        elif key == "F6":
            self._on_pause_record_hotkey()

    def _start_bot_and_focus_dashboard(self):
        started = self._engine.start()
        if started:
            self._set_page("dashboard")

    def _on_pause_record_hotkey(self):
        page = self._pages.get("paths")
        if page and hasattr(page, "_on_pause_record"):
            page._on_pause_record()

    def _on_stop_hotkey(self):
        with self._stop_hotkey_lock:
            if self._stop_hotkey_in_progress:
                return
            self._stop_hotkey_in_progress = True

        log.info("[Control] STOP requested")

        def _run_stop():
            try:
                # Run both steps independently so one failure cannot block the other.
                try:
                    self._engine.stop_map_explorer()
                except Exception as exc:
                    log.error(f"[Hotkeys] Explorer stop failed: {exc}")

                try:
                    self._engine.stop()
                except Exception as exc:
                    log.error(f"[Hotkeys] Bot stop failed: {exc}")
            finally:
                log.info("[Control] STOP completed")
                QTimer.singleShot(0, self._clear_stop_hotkey_flag)

        threading.Thread(target=_run_stop, daemon=True, name="QtHotkeyStop").start()

    def _clear_stop_hotkey_flag(self):
        with self._stop_hotkey_lock:
            self._stop_hotkey_in_progress = False

    def _on_portal_hotkey(self):
        recorder = self._engine.path_recorder
        if recorder and recorder.is_recording:
            recorder.add_portal_waypoint()
            page = self._pages.get("paths")
            if page and hasattr(page, "_rec_count"):
                page._rec_count.setText(f"Points: {recorder.waypoint_count}")

    def _on_overlay_waypoints_update(self, waypoints):
        overlay = self._overlay
        if overlay and overlay._running:
            overlay.set_waypoints(waypoints)

    def _on_grid_data_update(self, walkable_xy, frontier_xy, cell_size: float = WALL_GRID_CELL_SIZE):
        overlay = self._overlay
        if overlay and overlay._running:
            overlay.set_grid_data(walkable_xy, frontier_xy, cell_size)
            overlay.set_layer_visible(DebugOverlay.LAYER_GRID, True)

    def _toggle_overlay(self):
        if self._overlay and self._overlay._running:
            self._stop_position_poll()
            self._stop_overlay_worker()
            self._overlay_feed_timer.stop()
            self._overlay.stop()
            self._overlay = None
            self._overlay_btn.setText("Overlay: OFF")
            self._engine.set_debug_overlay(None)
            log.info("[Overlay] Debug overlay stopped")
            return

        window_mgr = getattr(self._engine, "window", None)
        if window_mgr and hasattr(window_mgr, "get_client_rect"):
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

        paths = self._pages.get("paths")
        if paths is not None and hasattr(paths, "_loaded_waypoints"):
            self._overlay.set_waypoints(getattr(paths, "_loaded_waypoints", []))

        calibrator = self._engine.scale_calibrator
        if calibrator:
            current_map = self._resolve_current_map()
            cal = calibrator.get_calibration(current_map)
            if cal:
                self._overlay.set_calibration(cal, current_map)

        self._overlay.start()
        self._overlay_btn.setText("Overlay: ON")
        self._engine.set_debug_overlay(self._overlay)
        self._start_position_poll()
        self._start_overlay_worker()
        self._overlay_feed_timer.start()
        log.info("[Overlay] Debug overlay started")

    def _start_overlay_worker(self):
        self._overlay_worker_stop.clear()
        if self._overlay_worker_thread and self._overlay_worker_thread.is_alive():
            return

        def _worker_loop():
            interval = 0.20
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

                    portal_det = getattr(self._engine, "portal_detector", None)
                    if portal_det:
                        try:
                            if hasattr(portal_det, "get_portal_markers"):
                                portal_positions = portal_det.get_portal_markers() or []
                            elif hasattr(portal_det, "get_portal_positions"):
                                portal_positions = portal_det.get_portal_positions() or []
                        except Exception:
                            portal_positions = []

                    try:
                        current_map = self._resolve_current_map()
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

                    scanner = getattr(self._engine, "scanner", None)
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
                                nav_collision_markers = scanner.get_nav_collision_markers() or []
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

        self._overlay_worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="QtOverlayDataWorker")
        self._overlay_worker_thread.start()

    def _stop_overlay_worker(self):
        self._overlay_worker_stop.set()
        t = self._overlay_worker_thread
        if t and t.is_alive():
            t.join(timeout=0.5)
        self._overlay_worker_thread = None

    def _resolve_current_map(self) -> str:
        scanner = getattr(self._engine, "_scanner", None)
        if scanner:
            try:
                internal = scanner.read_real_zone_name()
                if internal:
                    if internal != self._last_zone_fname:
                        self._last_zone_fname = internal
                        self._last_zone_english = internal
                        mapping_file = os.path.join("data", "zone_name_mapping.json")
                        if os.path.exists(mapping_file):
                            try:
                                with open(mapping_file, "r", encoding="utf-8") as f:
                                    mapping = json.load(f)
                                english = mapping.get(internal, "")
                                if english:
                                    self._last_zone_english = english
                            except Exception:
                                pass
                    return self._last_zone_english
            except Exception:
                pass
        return self._engine.config.get("current_map", "hideout")

    def _on_manual_calibrate_requested(self):
        return self._start_calibration(reason="manual")

    def _start_calibration(self, map_name: Optional[str] = None, reason: str = "manual"):
        if not self._engine.memory.is_attached:
            return False, "Attach to game first"

        calibrator = self._engine.scale_calibrator
        if not calibrator or calibrator.is_calibrating:
            return False, "Calibration already running"

        if self._engine.is_running or self._engine.explorer_running:
            return False, "Stop bot/explorer before calibration"

        wm = getattr(self._engine, "window", None)
        if wm and not wm.is_foreground():
            return False, "Focus Torchlight window first"

        current_map = map_name or self._resolve_current_map()
        if not current_map or str(current_map).lower() == "hideout":
            return False, "Enter a map first"
        calibrator.set_current_map(current_map)

        def _run_calibration():
            try:
                cal = calibrator.calibrate(self._engine.game_state, map_name=current_map)
                if cal and self._overlay and self._overlay._running:
                    self._overlay.set_calibration(cal, current_map)
                if cal:
                    log.info(f"[ScaleCalibrator] Calibration complete ({reason}) for map: {current_map}")
                else:
                    log.warning(f"[ScaleCalibrator] Calibration failed ({reason}) for map: {current_map}")
            except Exception as e:
                log.warning(f"[ScaleCalibrator] Calibration error ({reason}): {e}")

        threading.Thread(target=_run_calibration, daemon=True).start()
        return True, f"Calibration started for {current_map}"

    def _start_position_poll(self):
        self._pos_poll_stop = threading.Event()
        t = threading.Thread(target=self._position_poll_loop, daemon=True, name="QtOverlayPosFeed")
        t.start()

    def _stop_position_poll(self):
        if self._pos_poll_stop is not None:
            self._pos_poll_stop.set()

    def _position_poll_loop(self):
        interval = 0.016
        while self._pos_poll_stop is not None and not self._pos_poll_stop.is_set():
            t0 = time.monotonic()
            try:
                overlay = self._overlay
                if not overlay or not overlay._running:
                    break
                x, y = self._engine._pos_poller.get_pos()
                if x != 0.0 or y != 0.0:
                    overlay.set_player_position(x, y)
            except Exception:
                pass
            sleep_for = interval - (time.monotonic() - t0)
            if sleep_for > 0.0001:
                time.sleep(sleep_for)

    def _overlay_feed_tick(self):
        overlay = self._overlay
        if not overlay or not overlay._running:
            self._overlay_feed_timer.stop()
            return

        try:
            wm = getattr(self._engine, "window", None)
            if wm:
                overlay.set_game_focused(wm.is_foreground())
                rect = wm.get_client_rect()
                if rect:
                    x, y, x2, y2 = rect
                    overlay.set_game_rect((x, y, x2 - x, y2 - y))

            calibrator = self._engine.scale_calibrator
            if calibrator and (time.monotonic() - self._overlay_last_map_check_t >= 0.5):
                self._overlay_last_map_check_t = time.monotonic()
                current_map = self._resolve_current_map()
                if current_map != overlay._current_map_name:
                    cal = calibrator.get_calibration(current_map)
                    overlay.set_calibration(cal, current_map)

            with self._overlay_data_lock:
                portal_positions = self._overlay_data["portal_positions"]
                event_markers = self._overlay_data["event_markers"]
                guard_markers = self._overlay_data["guard_markers"]
                nav_collision_markers = self._overlay_data["nav_collision_markers"]

            overlay.set_portal_positions(portal_positions)
            overlay.set_event_markers(event_markers)
            overlay.set_guard_markers(guard_markers)
            overlay.set_nav_collision_markers(nav_collision_markers)
        except Exception:
            pass

    def run(self):
        self.show()

    def closeEvent(self, event):  # noqa: N802
        try:
            if hasattr(self, "_status_timer"):
                self._status_timer.stop()
        except Exception:
            pass
        try:
            if hasattr(self, "_overlay_feed_timer"):
                self._overlay_feed_timer.stop()
        except Exception:
            pass
        self._stop_position_poll()
        self._stop_overlay_worker()
        if self._overlay:
            try:
                self._overlay.stop()
            except Exception:
                pass
            self._overlay = None
        try:
            self._engine.set_debug_overlay(None)
        except Exception:
            pass
        try:
            self._hotkey_thread_running = False
            if self._hotkey_thread and self._hotkey_thread.is_alive():
                self._hotkey_thread.join(timeout=0.5)
        except Exception:
            pass
        try:
            self._engine.cleanup()
        except Exception:
            pass
        super().closeEvent(event)


def run_qt_app() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    win = BotAppQt()
    win.run()
    return app.exec()
