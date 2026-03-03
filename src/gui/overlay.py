import sys
import time
import tkinter as tk
import math
import threading
from typing import List, Optional, Tuple, Dict, Any

from src.core.waypoint import Waypoint
from src.core.scale_calibrator import MapCalibration, DEFAULT_CALIBRATION
from src.utils.constants import CHARACTER_CENTER, STAND_TOLERANCE, WALL_GRID_CELL_SIZE
from src.utils.logger import log


class DebugOverlay:
    LAYER_WAYPOINTS = "waypoints"
    LAYER_PLAYER = "player"
    LAYER_NAV_TARGET = "nav_target"
    LAYER_PORTALS = "portals"
    LAYER_EVENTS = "events"
    LAYER_ENTITIES = "entities"
    LAYER_STUCK = "stuck"
    LAYER_MINIMAP = "minimap"
    LAYER_AUTO_PATH = "auto_path"   # A* computed path (autonomous navigation)

    COLOR_NODE = "#79C0FF"
    COLOR_STAND = "#FFD866"
    COLOR_PORTAL = "#FF7B72"
    COLOR_PLAYER = "#7EE787"
    COLOR_NAV_LINE = "#D2A8FF"
    COLOR_PATH_LINE = "#8B949E"
    COLOR_TOLERANCE = "#9CA3AF"
    COLOR_PORTAL_DETECT = "#FF7B72"
    COLOR_EXIT_PORTAL = "#58A6FF"
    COLOR_EVENT = "#FFA657"
    COLOR_EVENT_UNKNOWN = "#6E7681"
    COLOR_CARJACK = "#FF7B72"
    COLOR_SANDLORD = "#FFD866"
    COLOR_ENTITY = "#FF9B9B"
    COLOR_STUCK = "#FF7B72"
    COLOR_AUTO_PATH = "#39D353"     # bright green dashed line for A* path
    COLOR_GUARD = "#FF8C00"         # vivid orange — Carjack security guard markers

    LAYER_GRID          = "grid"      # manual-explore coverage panel + near-frontier markers
    COLOR_GRID_EXPLORED = "#1B4332"   # visited cells — dark green
    COLOR_GRID_FRONTIER = "#39D353"   # unexplored edge — bright green (matches auto-path)
    COLOR_GRID_PANEL_BG = "#0D1117"   # panel background
    COLOR_GRID_PANEL_BDR= "#30363D"   # panel border
    _GRID_PANEL_SIZE    = 260         # grid panel width and height in screen pixels
    _GRID_NEAR_RANGE    = 3500.0      # world units: show wall-edge lines in game-space

    # Velocity dead-reckoning: predicts current position forward from the last memory read.
    # Eliminates projection lag during movement; settles in <3 frames after the player stops.
    _VEL_ALPHA = 0.55       # EMA blend for velocity estimate (tuned for ~60 Hz position feed)
    _VEL_DECAY = 0.74       # exponential decay per 5ms poll when position is unchanged
    _VEL_MAX = 5000.0       # clamp: max plausible world-units / second
    _TELEPORT_DIST = 500.0  # distance threshold to treat a jump as a teleport
    _PREDICT_CAP = 0.025    # max dead-reckoning horizon — covers 1.5× the 16ms poll interval

    # Minimap panel dimensions (pixels) and world-space radius shown
    _MINIMAP_SIZE = 190
    _MINIMAP_MARGIN = 15
    _MINIMAP_WORLD_RANGE = 2000.0  # world units from player to minimap edge

    def __init__(self, game_window_rect: Optional[Tuple[int, int, int, int]] = None):
        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._visible = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._game_rect = game_window_rect or (0, 0, 1920, 1080)
        self._player_pos: Optional[Tuple[float, float]] = None
        # Dead-reckoning state — written by position-poll thread, read by render thread (under _lock)
        self._vel_x: float = 0.0
        self._vel_y: float = 0.0
        self._last_read_time: float = 0.0
        # Predicted display position — render-thread private (no lock needed)
        self._display_pos: Optional[Tuple[float, float]] = None
        # Render-thread window state (no lock needed — only used inside overlay thread)
        self._last_applied_geo: str = ""
        self._waypoints: List[Waypoint] = []
        self._current_wp_index: int = -1
        self._portal_positions: List[Any] = []
        self._event_markers: List[Dict[str, Any]] = []
        self._entity_positions: List[Dict[str, Any]] = []
        self._is_stuck = False
        self._auto_path_waypoints: List[Tuple[float, float]] = []  # A* computed path
        self._selected_wp_indices: set = set()
        self._node_tolerance: float = 200.0
        self._stand_tolerance: float = float(STAND_TOLERANCE)
        self._game_focused = True

        self._layers = {
            self.LAYER_WAYPOINTS: True,
            self.LAYER_PLAYER: True,
            self.LAYER_NAV_TARGET: True,
            self.LAYER_PORTALS: True,
            self.LAYER_EVENTS: True,
            self.LAYER_ENTITIES: True,
            self.LAYER_STUCK: True,
            self.LAYER_MINIMAP: True,
            self.LAYER_AUTO_PATH: True,
            self.LAYER_GRID: False,   # enabled only during manual explore
        }

        self._calibration: Optional[MapCalibration] = None
        self._current_map_name: str = ""

        # --- Canvas item tracking (Option B: update-in-place, no full clear each frame) ---
        # Singleton item IDs; None = not yet created on the current canvas
        self._id_no_data: Optional[int] = None
        self._id_player_dot: Optional[int] = None
        self._id_player_label: Optional[int] = None
        self._id_nav_line: Optional[int] = None
        self._id_stuck_rect: Optional[int] = None
        self._id_stuck_text: Optional[int] = None

        # Item-ID pools — grow to match data length; extras are hidden, not deleted
        self._pool_path_lines: List[int] = []
        self._pool_wp_tol: List[int] = []
        self._pool_wp_select: List[int] = []
        self._pool_wp_dot: List[int] = []
        self._pool_wp_label: List[int] = []
        self._pool_portal_shape: List[int] = []
        self._pool_portal_label: List[int] = []
        self._pool_event_shape: List[int] = []
        self._pool_event_label: List[int] = []
        self._pool_entity_dot: List[int] = []
        self._pool_entity_label: List[int] = []
        # Guard markers — Carjack security guards (vivid orange circles + labels)
        self._guard_markers: List[Dict[str, Any]] = []
        self._pool_guard_dot: List[int] = []
        self._pool_guard_label: List[int] = []

        # --- Minimap canvas items (fully pooled — no delete/recreate each frame) ---
        self._mm_id_bg: Optional[int] = None
        self._mm_id_ring_inner: Optional[int] = None
        self._mm_id_ring_outer: Optional[int] = None
        self._mm_id_compass_line: Optional[int] = None
        self._mm_id_compass_text: Optional[int] = None
        self._mm_id_player: Optional[int] = None
        self._mm_id_name: Optional[int] = None
        self._mm_pool_path: List[int] = []
        self._mm_pool_wp: List[int] = []
        self._mm_pool_wp_lbl: List[int] = []    # at most 1 visible (current wp)
        self._mm_pool_portal: List[int] = []
        self._mm_pool_event: List[int] = []

        # --- Grid coverage layer (manual explore) ---
        self._grid_walkable: List[Tuple[float, float]] = []
        self._grid_frontier: List[Tuple[float, float]] = []
        self._grid_dirty:    bool = False
        self._grid_panel_bounds: Optional[Tuple[float, float, float]] = None  # wx0, wy0, wrange
        self._grid_cell_size:    float                                 = WALL_GRID_CELL_SIZE
        self._grid_walkable_keys: frozenset                             = frozenset()
        self._gp_player_dot: Optional[int] = None
        self._pool_gs_frontier: List[int] = []  # wall-edge line canvas items
        self._grid_cached_wall_segs: List[Tuple[float, float, float, float]] = []  # (wx0,wy0,wx1,wy1)
        self._grid_last_layout_pos: Optional[Tuple[float, float]] = None  # world pos at last seg rebuild

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_overlay, daemon=True, name="DebugOverlay")
        self._thread.start()

    def stop(self):
        self._running = False
        # Signal the overlay's own event loop to quit; the thread then calls
        # destroy() after mainloop() returns (owner-thread cleanup — safe on
        # all Windows/Tcl configurations).  Using destroy() directly from an
        # external thread can race with pending after() callbacks and cause a
        # hard crash in the Tcl interpreter.
        root = self._root
        if root:
            try:
                root.after(0, root.quit)
            except Exception:
                pass

    def toggle_visibility(self):
        self._visible = not self._visible
        if self._root:
            try:
                if self._visible:
                    self._root.after(0, lambda: self._root.deiconify())
                else:
                    self._root.after(0, lambda: self._root.withdraw())
            except Exception:
                pass

    def set_layer_visible(self, layer: str, visible: bool):
        if layer in self._layers:
            self._layers[layer] = visible

    def set_game_rect(self, rect: Tuple[int, int, int, int]):
        with self._lock:
            self._game_rect = rect

    def set_player_position(self, x: float, y: float):
        now = time.monotonic()
        with self._lock:
            if self._player_pos is not None and self._last_read_time > 0.0:
                dx = x - self._player_pos[0]
                dy = y - self._player_pos[1]
                dist_sq = dx * dx + dy * dy

                if dist_sq > self._TELEPORT_DIST ** 2:
                    # Teleport: snap velocity to zero immediately
                    self._vel_x = 0.0
                    self._vel_y = 0.0
                    self._player_pos = (x, y)
                    self._last_read_time = now

                elif dist_sq > 0.0:
                    # Genuine movement: update velocity via EMA
                    dt = now - self._last_read_time
                    if 0.001 < dt < 0.5:
                        inst_vx = max(-self._VEL_MAX, min(self._VEL_MAX, dx / dt))
                        inst_vy = max(-self._VEL_MAX, min(self._VEL_MAX, dy / dt))
                        a = self._VEL_ALPHA
                        self._vel_x += a * (inst_vx - self._vel_x)
                        self._vel_y += a * (inst_vy - self._vel_y)
                    self._player_pos = (x, y)
                    self._last_read_time = now

                else:
                    # Position unchanged — decay velocity so overlay settles quickly
                    # after the character stops, without feeding false zero-velocity
                    # samples into the EMA.
                    dt = now - self._last_read_time
                    if dt > 0.002:
                        decay = self._VEL_DECAY ** (dt / 0.005)
                        self._vel_x *= decay
                        self._vel_y *= decay
                        if abs(self._vel_x) < 1.0:
                            self._vel_x = 0.0
                        if abs(self._vel_y) < 1.0:
                            self._vel_y = 0.0
                        self._last_read_time = now  # reset so next decay uses fresh dt
            else:
                self._player_pos = (x, y)
                self._last_read_time = now

    def set_waypoints(self, waypoints: List[Waypoint]):
        with self._lock:
            self._waypoints = list(waypoints)

    def set_current_waypoint_index(self, idx: int):
        with self._lock:
            self._current_wp_index = idx

    def set_portal_positions(self, portals: List[Any]):
        with self._lock:
            self._portal_positions = list(portals)

    def set_event_markers(self, events: List[Dict[str, Any]]):
        with self._lock:
            self._event_markers = list(events)

    def set_entity_positions(self, entities: List[Dict[str, Any]]):
        with self._lock:
            self._entity_positions = list(entities)

    def set_guard_markers(self, guards: List[Dict[str, Any]]):
        """Push live Carjack guard positions to the overlay.
        Each entry includes position plus optional diagnostics:
        {"x": float, "y": float, "abp": str, "score": float,
         "dist_truck": float, "elite": int, "role_rarity": int}
        """
        with self._lock:
            self._guard_markers = list(guards)

    def set_auto_path(self, path: List[Tuple[float, float]]):
        """Set the A* computed path waypoints for display on the overlay."""
        with self._lock:
            self._auto_path_waypoints = list(path)

    def set_grid_data(self, walkable: List[Tuple[float, float]], frontier: List[Tuple[float, float]],
                       cell_size: float = WALL_GRID_CELL_SIZE):
        """Push updated coverage grid data. Marks the panel dirty for next render."""
        with self._lock:
            self._grid_walkable  = list(walkable)
            self._grid_frontier  = list(frontier)
            self._grid_cell_size = cell_size
            self._grid_dirty     = True

    def set_stuck(self, is_stuck: bool):
        with self._lock:
            self._is_stuck = is_stuck

    def set_game_focused(self, focused: bool):
        with self._lock:
            self._game_focused = focused

    def set_selected_waypoints(self, indices: set):
        with self._lock:
            self._selected_wp_indices = set(indices)

    def set_calibration(self, calibration: Optional[MapCalibration], map_name: str = ""):
        with self._lock:
            self._calibration = calibration
            self._current_map_name = map_name

    def _world_to_screen(self, wx: float, wy: float) -> Tuple[int, int]:
        cal = self._calibration or DEFAULT_CALIBRATION
        # Prefer predicted display position during render cycles; fall back to raw
        pos = self._display_pos if self._display_pos is not None else self._player_pos
        if cal and pos:
            return cal.world_to_screen(wx, wy, pos[0], pos[1])
        cx, cy = CHARACTER_CENTER
        return (cx, cy)

    def _make_click_through(self) -> None:
        """Make the overlay window transparent to mouse input (Windows only).

        Applies WS_EX_TRANSPARENT so all mouse events fall through to whatever
        window is underneath — the game — instead of being captured by the overlay.

        IMPORTANT: SetWindowLongW can reset the DWM layered-window attributes that
        Tkinter set via SetLayeredWindowAttributes (for -transparentcolor).  We
        must re-apply SetLayeredWindowAttributes(LWA_COLORKEY) afterwards so the
        window is not left fully invisible on some Windows/DWM configurations.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            LWA_COLORKEY = 0x00000001
            hwnd = self._root.winfo_id()
            cur = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, cur | WS_EX_LAYERED | WS_EX_TRANSPARENT
            )
            # Re-apply color key so black pixels remain transparent after the
            # SetWindowLongW call (which may clear previously-set layered attrs).
            ctypes.windll.user32.SetLayeredWindowAttributes(
                hwnd, 0x00000000, 0, LWA_COLORKEY
            )
        except Exception as e:
            log.warning(f"[Overlay] Could not set click-through: {e}")

    def _run_overlay(self):
        _timer_period_set = False
        try:
            self._root = tk.Tk()
            self._root.title("Bot Debug Overlay")
            self._root.attributes("-topmost", True)
            self._root.attributes("-alpha", 0.85)
            self._root.overrideredirect(True)

            gx, gy, gw, gh = self._game_rect
            self._root.geometry(f"{gw}x{gh}+{gx}+{gy}")
            self._last_applied_geo = f"{gw}x{gh}+{gx}+{gy}"

            try:
                self._root.attributes("-transparentcolor", "black")
                bg_color = "black"
            except Exception:
                bg_color = "#010101"

            self._canvas = tk.Canvas(self._root, width=gw, height=gh,
                                     bg=bg_color, highlightthickness=0)
            self._canvas.pack(fill="both", expand=True)
            self._reset_canvas_items()
            self._make_click_through()

            # Request 1ms Windows timer resolution so after(33) fires at ~33ms (~30 FPS)
            # with minimal jitter instead of the default 15ms coalesced resolution.
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.winmm.timeBeginPeriod(1)
                    _timer_period_set = True
                except Exception:
                    pass

            self._root.bind("<Escape>", lambda e: self.toggle_visibility())

            self._visible = True
            self._update_loop()
            self._root.mainloop()
            # mainloop() returned (quit() was posted by stop()).
            # Destroy the window in the owner thread — the only safe place.
            try:
                self._root.destroy()
            except Exception:
                pass
        except Exception as e:
            log.error(f"[Overlay] Error: {e}")
        finally:
            if _timer_period_set:
                try:
                    import ctypes
                    ctypes.windll.winmm.timeEndPeriod(1)
                except Exception:
                    pass
            self._running = False

    def _update_loop(self):
        if not self._running:
            return

        try:
            with self._lock:
                gx, gy, gw, gh = self._game_rect
                focused = self._game_focused

            new_geo = f"{gw}x{gh}+{gx}+{gy}"
            geo_changed = new_geo != self._last_applied_geo
            if geo_changed:
                self._root.geometry(new_geo)
                self._canvas.configure(width=gw, height=gh)
                self._last_applied_geo = new_geo

            # Show/hide via alpha so the overlay disappears when neither the game
            # nor the bot window is focused.  Alpha 0.0 = fully invisible but the
            # window stays "shown", avoiding the withdraw()/deiconify() pattern that
            # caused permanent invisibility in the v3.5.1 regression (overrideredirect
            # + -transparentcolor interactions).  -topmost is also cleared when
            # unfocused so other windows can freely cover the invisible overlay.
            focused_changed = focused != getattr(self, "_last_focused", None)
            if focused_changed:
                self._root.attributes("-topmost", focused)
                self._root.attributes("-alpha", 0.85 if focused else 0.0)
                self._last_focused = focused

            # Guarantee the overlay is visible (deiconify is a no-op when the
            # window is already shown, so this is zero-cost on every normal frame).
            if self._visible:
                self._root.deiconify()

            # Re-apply WS_EX_TRANSPARENT after any window-state change.
            # deiconify(), attributes("-topmost", …) and geometry changes can
            # all silently strip the extended style on Windows, making the
            # overlay capture mouse clicks instead of passing them to the game.
            if geo_changed or focused_changed:
                self._make_click_through()

            self._redraw()
        except Exception as e:
            log.error(f"[Overlay] Redraw error: {e}")

        if self._running and self._root:
            self._root.after(33, self._update_loop)

    def _redraw(self):
        if not self._canvas or not self._visible:
            return

        # Only clear edge-arrow items each frame (rare — off-screen indicators only).
        # All other canvas items (including minimap) are updated in-place via
        # coords/itemconfig — no full-canvas delete, no per-frame item creation.
        self._canvas.delete("edge_arrows")

        now = time.monotonic()
        with self._lock:
            waypoints = list(self._waypoints)
            player_pos = self._player_pos
            current_idx = self._current_wp_index
            portals = list(self._portal_positions)
            events = list(self._event_markers)
            entities = list(self._entity_positions)
            guards = list(self._guard_markers)
            is_stuck = self._is_stuck
            selected = set(self._selected_wp_indices)
            gw = self._game_rect[2]
            gh = self._game_rect[3]
            last_read_time = self._last_read_time
            vel_x = self._vel_x
            vel_y = self._vel_y
            auto_path = list(self._auto_path_waypoints)
            grid_walkable = list(self._grid_walkable)
            grid_frontier = list(self._grid_frontier)
            grid_dirty    = self._grid_dirty
            if grid_dirty:
                self._grid_dirty = False  # clear under lock so we don't miss next push

        # Dead-reckoning: extrapolate player position forward to "now" using
        # the smoothed velocity estimate.  Zero additional lag even at 60 FPS.
        if player_pos is not None:
            elapsed = min(now - last_read_time, self._PREDICT_CAP)
            self._display_pos = (
                player_pos[0] + vel_x * elapsed,
                player_pos[1] + vel_y * elapsed,
            )

        if not player_pos:
            self._show_no_data(gw, gh)
            return
        self._hide_no_data()

        if self._layers[self.LAYER_WAYPOINTS] and waypoints:
            self._update_waypoints(waypoints, current_idx, selected, gw, gh)
        else:
            self._hide_pool(self._pool_path_lines)
            self._hide_pool(self._pool_wp_tol)
            self._hide_pool(self._pool_wp_select)
            self._hide_pool(self._pool_wp_dot)
            self._hide_pool(self._pool_wp_label)

        if self._layers[self.LAYER_PORTALS] and portals:
            self._update_portals(portals, gw, gh)
        else:
            self._hide_pool(self._pool_portal_shape)
            self._hide_pool(self._pool_portal_label)

        if self._layers[self.LAYER_EVENTS] and events:
            self._update_events(events, gw, gh)
        else:
            self._hide_pool(self._pool_event_shape)
            self._hide_pool(self._pool_event_label)

        if self._layers[self.LAYER_ENTITIES] and entities:
            self._update_entities(entities)
        else:
            self._hide_pool(self._pool_entity_dot)
            self._hide_pool(self._pool_entity_label)

        if guards:
            self._update_guards(guards, gw, gh)
        else:
            self._hide_pool(self._pool_guard_dot)
            self._hide_pool(self._pool_guard_label)

        if self._layers[self.LAYER_PLAYER] and player_pos:
            self._update_player(player_pos)
        else:
            if self._id_player_dot is not None:
                self._canvas.itemconfig(self._id_player_dot, state='hidden')
            if self._id_player_label is not None:
                self._canvas.itemconfig(self._id_player_label, state='hidden')

        if (self._layers[self.LAYER_NAV_TARGET] and player_pos
                and waypoints and 0 <= current_idx < len(waypoints)):
            self._update_nav_target(player_pos, waypoints[current_idx])
        else:
            if self._id_nav_line is not None:
                self._canvas.itemconfig(self._id_nav_line, state='hidden')

        self._update_stuck_indicator(is_stuck, gw, gh)

        if self._layers[self.LAYER_AUTO_PATH] and auto_path and len(auto_path) >= 2:
            self._update_auto_path(auto_path, gw, gh)
        else:
            self._canvas.delete("auto_path")

        if self._layers[self.LAYER_GRID]:
            self._update_grid_layer(grid_walkable, grid_frontier, grid_dirty, gw, gh,
                                     self._grid_cell_size)
        else:
            self._hide_grid_layer()

        if self._layers[self.LAYER_MINIMAP] and self._display_pos:
            self._update_minimap(self._display_pos, waypoints, portals, events, current_idx, gw, gh)
        else:
            self._hide_minimap_all()

    # ------------------------------------------------------------------
    # Canvas item lifecycle helpers
    # ------------------------------------------------------------------
    def _reset_canvas_items(self) -> None:
        """Clear all tracked item IDs — called whenever a new canvas is created."""
        self._display_pos = None
        self._vel_x = 0.0
        self._vel_y = 0.0
        self._last_read_time = 0.0
        self._last_applied_geo = ""
        self._id_no_data = None
        self._id_player_dot = None
        self._id_player_label = None
        self._id_nav_line = None
        self._id_stuck_rect = None
        self._id_stuck_text = None
        self._pool_path_lines.clear()
        self._pool_wp_tol.clear()
        self._pool_wp_select.clear()
        self._pool_wp_dot.clear()
        self._pool_wp_label.clear()
        self._pool_portal_shape.clear()
        self._pool_portal_label.clear()
        self._pool_event_shape.clear()
        self._pool_event_label.clear()
        self._pool_entity_dot.clear()
        self._pool_entity_label.clear()
        self._pool_guard_dot.clear()
        self._pool_guard_label.clear()
        # Grid coverage layer
        self._grid_dirty       = False
        self._grid_walkable_keys = frozenset()
        self._grid_panel_bounds = None
        self._gp_player_dot    = None
        self._pool_gs_frontier.clear()
        self._grid_cached_wall_segs = []
        self._grid_last_layout_pos = None
        # Minimap singletons and pools
        self._mm_id_bg = None
        self._mm_id_ring_inner = None
        self._mm_id_ring_outer = None
        self._mm_id_compass_line = None
        self._mm_id_compass_text = None
        self._mm_id_player = None
        self._mm_id_name = None
        self._mm_pool_path.clear()
        self._mm_pool_wp.clear()
        self._mm_pool_wp_lbl.clear()
        self._mm_pool_portal.clear()
        self._mm_pool_event.clear()

    def _pool_ensure(self, pool: List[int], n: int, create_fn) -> None:
        """Grow pool to at least n items (new items created hidden); hide extras beyond n."""
        while len(pool) < n:
            pool.append(create_fn())
        for i in range(n, len(pool)):
            self._canvas.itemconfig(pool[i], state='hidden')

    def _hide_pool(self, pool: List[int]) -> None:
        for item_id in pool:
            self._canvas.itemconfig(item_id, state='hidden')

    def _show_no_data(self, gw: int, gh: int) -> None:
        if self._id_no_data is None:
            self._id_no_data = self._canvas.create_text(
                gw // 2, 30, text="Overlay: No player data",
                fill="#6E7681", font=("Consolas", 10))
        self._canvas.itemconfig(self._id_no_data, state='normal')
        # Hide all managed items so stale data is not visible
        for pool in (self._pool_path_lines, self._pool_wp_tol, self._pool_wp_select,
                     self._pool_wp_dot, self._pool_wp_label,
                     self._pool_portal_shape, self._pool_portal_label,
                     self._pool_event_shape, self._pool_event_label,
                     self._pool_entity_dot, self._pool_entity_label,
                     self._pool_guard_dot, self._pool_guard_label):
            self._hide_pool(pool)
        for item_id in (self._id_player_dot, self._id_player_label,
                        self._id_nav_line, self._id_stuck_rect, self._id_stuck_text):
            if item_id is not None:
                self._canvas.itemconfig(item_id, state='hidden')

    def _hide_no_data(self) -> None:
        if self._id_no_data is not None:
            self._canvas.itemconfig(self._id_no_data, state='hidden')

    # ------------------------------------------------------------------
    # Grid coverage layer
    # ------------------------------------------------------------------

    def _hide_grid_layer(self) -> None:
        if not self._canvas:
            return
        self._canvas.delete("grid_panel")
        self._canvas.delete("grid_world")
        self._gp_player_dot    = None
        self._pool_gs_frontier.clear()
        self._grid_cached_wall_segs = []
        self._grid_last_layout_pos = None
        self._grid_panel_bounds = None

    def _update_grid_layer(self,
                           grid_walkable: List[Tuple[float, float]],
                           grid_frontier: List[Tuple[float, float]],
                           grid_dirty: bool,
                           gw: int,
                           gh: int,
                           cell_size: float = WALL_GRID_CELL_SIZE) -> None:
        c = self._canvas
        if not c or not self._calibration:
            return

        all_pts = grid_walkable + grid_frontier
        if not all_pts:
            self._hide_grid_layer()
            return

        PS = self._GRID_PANEL_SIZE
        M  = 15
        panel_x0 = gw - PS - M
        panel_y0 = M
        panel_x1 = panel_x0 + PS
        panel_y1 = panel_y0 + PS

        # Mini-panel projection: use the calibration matrix directly, projecting
        # relative to the data centre (not the player position).  This keeps the
        # panel map fixed while only the player dot moves, and always matches the
        # game's isometric orientation — no manual Y-flip needed.
        ccx, ccy = CHARACTER_CENTER

        # Rebuild screen-space scale when data changes.
        # _grid_panel_bounds stores (data_cx, data_cy, scale) — world-space center
        # of all data points, and the pixel-per-world-unit scale factor.
        if grid_dirty or self._grid_panel_bounds is None:
            cal = self._calibration
            wxs = [p[0] for p in all_pts]
            wys = [p[1] for p in all_pts]
            data_cx = (min(wxs) + max(wxs)) * 0.5
            data_cy = (min(wys) + max(wys)) * 0.5
            # Find maximum screen offset from data center (determines scale)
            max_abs = 1.0
            for wx, wy in all_pts:
                sx, sy = cal.world_to_screen(wx, wy, data_cx, data_cy)
                max_abs = max(max_abs, abs(sx - ccx), abs(sy - ccy))
            scale = (PS / 2) / (max_abs * 1.1)
            self._grid_panel_bounds = (data_cx, data_cy, scale)

        data_cx, data_cy, pan_scale = self._grid_panel_bounds
        pc_x = panel_x0 + PS * 0.5
        pc_y = panel_y0 + PS * 0.5

        def w2p(wx: float, wy: float) -> Tuple[int, int]:
            sx, sy = self._calibration.world_to_screen(wx, wy, data_cx, data_cy)
            return (
                int(pc_x + (sx - ccx) * pan_scale),
                int(pc_y + (sy - ccy) * pan_scale),
            )

        # ── Static panel: full redraw only when data changes ──────────
        if grid_dirty:
            c.delete("grid_panel")
            self._gp_player_dot = None
            # Do NOT destroy the wall-line pool on dirty — pool items are valid
            # canvas objects that get repositioned every frame anyway.  Destroying
            # + recreating them every 2 s causes a one-frame flash where all wall
            # lines vanish then reappear.  Rebuild happens naturally via the per-
            # frame pool loop below.

            # Panel background + border
            c.create_rectangle(panel_x0, panel_y0, panel_x1, panel_y1,
                               fill=self.COLOR_GRID_PANEL_BG,
                               outline=self.COLOR_GRID_PANEL_BDR,
                               width=1, tags="grid_panel")
            # Title
            c.create_text(panel_x0 + PS // 2, panel_y0 + 8,
                          text="Coverage Map",
                          fill=self.COLOR_GRID_FRONTIER,
                          font=("Consolas", 8),
                          tags="grid_panel")

            # Walkable cells — sample at most 1500 for performance
            step = max(1, len(grid_walkable) // 1500)
            for i in range(0, len(grid_walkable), step):
                gpx, gpy = w2p(grid_walkable[i][0], grid_walkable[i][1])
                if panel_x0 + 2 <= gpx <= panel_x1 - 2 and panel_y0 + 14 <= gpy <= panel_y1 - 2:
                    c.create_rectangle(gpx, gpy, gpx + 1, gpy + 1,
                                       fill=self.COLOR_GRID_EXPLORED,
                                       outline="",
                                       tags="grid_panel")

            # Frontier cells — all of them, slightly larger dots
            for fx, fy in grid_frontier:
                gpx, gpy = w2p(fx, fy)
                if panel_x0 + 2 <= gpx <= panel_x1 - 2 and panel_y0 + 14 <= gpy <= panel_y1 - 2:
                    c.create_rectangle(gpx - 1, gpy - 1, gpx + 1, gpy + 1,
                                       fill=self.COLOR_GRID_FRONTIER,
                                       outline="",
                                       tags="grid_panel")

            # Rebuild walkable key set for wall-edge line computation
            self._grid_walkable_keys = frozenset(
                (round(wx / cell_size), round(wy / cell_size))
                for wx, wy in grid_walkable
            )

        # ── Player dot in panel — updated every frame ─────────────────
        if self._gp_player_dot is not None:
            try:
                c.delete(self._gp_player_dot)
            except Exception:
                pass
            self._gp_player_dot = None

        if self._display_pos:
            dpx, dpy = w2p(self._display_pos[0], self._display_pos[1])
            if panel_x0 <= dpx <= panel_x1 and panel_y0 <= dpy <= panel_y1:
                self._gp_player_dot = c.create_oval(
                    dpx - 3, dpy - 3, dpx + 3, dpy + 3,
                    fill="#FFFFFF", outline="#CCCCCC",
                    tags="grid_panel",
                )

        # ── World-space wall edges ─────────────────────────────────────────────
        # The segment *layout* (which cells are in range) is cached and only
        # recomputed when: (a) grid data changed, (b) first draw, or (c) player
        # moved > _GRID_LAYOUT_DIST world-units since last layout.
        # Screen coordinates are reprojected every frame so lines track smoothly.
        _GRID_LAYOUT_DIST = 500.0
        if self._display_pos and self._grid_walkable_keys:
            dpx_, dpy_ = self._display_pos
            needs_layout = (
                grid_dirty
                or self._grid_last_layout_pos is None
                or (
                    (dpx_ - self._grid_last_layout_pos[0]) ** 2
                    + (dpy_ - self._grid_last_layout_pos[1]) ** 2
                ) > _GRID_LAYOUT_DIST ** 2
            )
            if needs_layout:
                cs    = cell_size
                half  = cs * 0.5
                thresh_sq = (self._GRID_NEAR_RANGE + cs) ** 2
                wkeys = self._grid_walkable_keys
                segs: List[Tuple[float, float, float, float]] = []
                for wx, wy in grid_walkable:
                    if (wx - dpx_) ** 2 + (wy - dpy_) ** 2 > thresh_sq:
                        continue
                    gx = round(wx / cs)
                    gy = round(wy / cs)
                    if (gx, gy + 1) not in wkeys:  # N absent → top edge
                        segs.append((wx - half, wy + half, wx + half, wy + half))
                    if (gx, gy - 1) not in wkeys:  # S absent → bottom edge
                        segs.append((wx - half, wy - half, wx + half, wy - half))
                    if (gx + 1, gy) not in wkeys:  # E absent → right edge
                        segs.append((wx + half, wy - half, wx + half, wy + half))
                    if (gx - 1, gy) not in wkeys:  # W absent → left edge
                        segs.append((wx - half, wy - half, wx - half, wy + half))
                self._grid_cached_wall_segs = segs
                self._grid_last_layout_pos  = (dpx_, dpy_)

            # Project the stable cached list to screen every frame
            n = len(self._grid_cached_wall_segs)
            while len(self._pool_gs_frontier) < n:
                self._pool_gs_frontier.append(
                    c.create_line(0, 0, 1, 1,
                                  fill=self.COLOR_GRID_FRONTIER,
                                  width=2,
                                  state='hidden',
                                  tags="grid_world")
                )
            for i in range(n, len(self._pool_gs_frontier)):
                c.itemconfig(self._pool_gs_frontier[i], state='hidden')
            for i, (wx0, wy0, wx1, wy1) in enumerate(self._grid_cached_wall_segs):
                sx0, sy0 = self._world_to_screen(wx0, wy0)
                sx1, sy1 = self._world_to_screen(wx1, wy1)
                c.coords(self._pool_gs_frontier[i], sx0, sy0, sx1, sy1)
                c.itemconfig(self._pool_gs_frontier[i], state='normal')
        else:
            for item in self._pool_gs_frontier:
                c.itemconfig(item, state='hidden')

    # ------------------------------------------------------------------
    # Off-screen edge indicator
    # ------------------------------------------------------------------
    def _draw_edge_arrow(self, sx: int, sy: int, gw: int, gh: int,
                         color: str, label: str = "", margin: int = 22):
        """Draw a small directional triangle at the screen edge pointing toward (sx, sy)."""
        cx = gw // 2
        cy = gh // 2
        dx = sx - cx
        dy = sy - cy
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < 1e-6:
            return
        ndx = dx / dist
        ndy = dy / dist

        # Find edge intersection
        ix, iy = cx, cy
        candidates = []
        if ndx > 0:
            t = (gw - margin - cx) / ndx
            ey = cy + t * ndy
            if margin <= ey <= gh - margin:
                candidates.append((cx + t * ndx, ey))
        elif ndx < 0:
            t = (margin - cx) / ndx
            ey = cy + t * ndy
            if margin <= ey <= gh - margin:
                candidates.append((cx + t * ndx, ey))
        if ndy > 0:
            t = (gh - margin - cy) / ndy
            ex = cx + t * ndx
            if margin <= ex <= gw - margin:
                candidates.append((ex, cy + t * ndy))
        elif ndy < 0:
            t = (margin - cy) / ndy
            ex = cx + t * ndx
            if margin <= ex <= gw - margin:
                candidates.append((ex, cy + t * ndy))
        if not candidates:
            return
        ix, iy = candidates[0]

        # Arrow triangle pointing in direction of target
        sz = 9
        px = -ndy * sz * 0.5
        py = ndx * sz * 0.5
        self._canvas.create_polygon(
            ix + ndx * sz, iy + ndy * sz,
            ix - ndx * sz * 0.5 + px, iy - ndy * sz * 0.5 + py,
            ix - ndx * sz * 0.5 - px, iy - ndy * sz * 0.5 - py,
            fill=color, outline=color, tags=("edge_arrows",),
        )
        if label:
            self._canvas.create_text(
                int(ix + ndx * 14), int(iy + ndy * 14),
                text=label, fill=color, font=("Consolas", 7), anchor="center",
                tags=("edge_arrows",),
            )

    def _is_on_screen(self, sx: int, sy: int, gw: int, gh: int, pad: int = 60) -> bool:
        return -pad <= sx <= gw + pad and -pad <= sy <= gh + pad

    # ------------------------------------------------------------------
    # Layer update methods — in-place canvas item management
    # ------------------------------------------------------------------
    def _update_waypoints(self, waypoints: List[Waypoint], current_idx: int, selected: set,
                          gw: int, gh: int):
        n_lines = max(0, len(waypoints) - 1)
        n_wps = len(waypoints)
        c = self._canvas

        self._pool_ensure(self._pool_path_lines, n_lines,
                          lambda: c.create_line(0, 0, 1, 1,
                                                fill=self.COLOR_PATH_LINE, width=2,
                                                dash=(4, 4), state='hidden'))
        self._pool_ensure(self._pool_wp_tol, n_wps,
                          lambda: c.create_oval(0, 0, 1, 1,
                                                outline=self.COLOR_TOLERANCE, width=2,
                                                dash=(3, 3), state='hidden'))
        self._pool_ensure(self._pool_wp_select, n_wps,
                          lambda: c.create_oval(0, 0, 1, 1,
                                                outline="#FFFFFF", width=2, state='hidden'))
        self._pool_ensure(self._pool_wp_dot, n_wps,
                          lambda: c.create_oval(0, 0, 1, 1,
                                                fill=self.COLOR_NODE, outline=self.COLOR_NODE,
                                                state='hidden'))
        self._pool_ensure(self._pool_wp_label, n_wps,
                          lambda: c.create_text(0, 0, text="", fill=self.COLOR_NODE,
                                                font=("Consolas", 10, "bold"), anchor="w",
                                                state='hidden'))

        # Path lines
        for i in range(n_lines):
            sx1, sy1 = self._world_to_screen(waypoints[i].x, waypoints[i].y)
            sx2, sy2 = self._world_to_screen(waypoints[i + 1].x, waypoints[i + 1].y)
            lid = self._pool_path_lines[i]
            if self._is_on_screen(sx1, sy1, gw, gh) or self._is_on_screen(sx2, sy2, gw, gh):
                c.coords(lid, sx1, sy1, sx2, sy2)
                c.itemconfig(lid, state='normal')
            else:
                c.itemconfig(lid, state='hidden')

        # Waypoint dots, labels, tolerance circles
        cal = self._calibration or DEFAULT_CALIBRATION
        for i, wp in enumerate(waypoints):
            cx, cy = self._world_to_screen(wp.x, wp.y)
            is_current = (i == current_idx)
            is_selected = (i in selected)

            if wp.is_portal:
                color = self.COLOR_PORTAL
            elif wp.wp_type == "stand":
                color = self.COLOR_STAND
            else:
                color = self.COLOR_NODE

            label = str(i + 1)
            if wp.is_portal:
                label = f"{i + 1}P"
            if wp.wp_type == "stand" and wp.wait_time > 0:
                label = f"{i + 1}({wp.wait_time:.0f}s)"

            if not self._is_on_screen(cx, cy, gw, gh):
                c.itemconfig(self._pool_wp_tol[i], state='hidden')
                c.itemconfig(self._pool_wp_select[i], state='hidden')
                c.itemconfig(self._pool_wp_dot[i], state='hidden')
                c.itemconfig(self._pool_wp_label[i], state='hidden')
                self._draw_edge_arrow(cx, cy, gw, gh, color, label if is_current else "")
                continue

            # Tolerance circle
            tol = self._stand_tolerance if wp.wp_type == "stand" else self._node_tolerance
            tol_px = cal.world_distance_to_pixels(tol)
            tol_id = self._pool_wp_tol[i]
            if tol_px > 5:
                tol_color = self.COLOR_NAV_LINE if is_current else self.COLOR_TOLERANCE
                c.coords(tol_id, cx - tol_px, cy - tol_px, cx + tol_px, cy + tol_px)
                c.itemconfig(tol_id, outline=tol_color, state='normal')
            else:
                c.itemconfig(tol_id, state='hidden')

            # Selection highlight
            r = 7 if is_current else 5
            sel_id = self._pool_wp_select[i]
            if is_selected:
                c.coords(sel_id, cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3)
                c.itemconfig(sel_id, state='normal')
            else:
                c.itemconfig(sel_id, state='hidden')

            # Dot
            dot_id = self._pool_wp_dot[i]
            c.coords(dot_id, cx - r, cy - r, cx + r, cy + r)
            c.itemconfig(dot_id, fill=color, outline=color, state='normal')

            # Label
            lbl_id = self._pool_wp_label[i]
            c.coords(lbl_id, cx + 10, cy - 10)
            c.itemconfig(lbl_id, text=label, fill=color, state='normal')

    def _update_player(self, pos: Tuple[float, float]):
        # The character is always at CHARACTER_CENTER on screen (fixed isometric camera).
        # Dead-reckoning shifts the world anchor, not the character's screen position.
        cx, cy = CHARACTER_CENTER
        label_text = f"({pos[0]:.0f}, {pos[1]:.0f})"
        c = self._canvas

        if self._id_player_dot is None:
            self._id_player_dot = c.create_oval(
                cx - 6, cy - 6, cx + 6, cy + 6,
                fill=self.COLOR_PLAYER, outline=self.COLOR_PLAYER, width=2)
            self._id_player_label = c.create_text(
                cx + 12, cy, text=label_text,
                fill=self.COLOR_PLAYER, font=("Consolas", 10, "bold"), anchor="w")
        else:
            c.coords(self._id_player_dot, cx - 6, cy - 6, cx + 6, cy + 6)
            c.coords(self._id_player_label, cx + 12, cy)
            c.itemconfig(self._id_player_label, text=label_text)
        c.itemconfig(self._id_player_dot, state='normal')
        c.itemconfig(self._id_player_label, state='normal')

    def _update_nav_target(self, player_pos: Tuple[float, float], target: Waypoint):
        sx1, sy1 = CHARACTER_CENTER  # player is always at screen center
        sx2, sy2 = self._world_to_screen(target.x, target.y)
        c = self._canvas

        if self._id_nav_line is None:
            self._id_nav_line = c.create_line(
                sx1, sy1, sx2, sy2,
                fill=self.COLOR_NAV_LINE, width=2, dash=(6, 3))
        else:
            c.coords(self._id_nav_line, sx1, sy1, sx2, sy2)
        c.itemconfig(self._id_nav_line, state='normal')

    def _update_portals(self, portals: List[Any], gw: int, gh: int):
        n = len(portals)
        c = self._canvas

        self._pool_ensure(self._pool_portal_shape, n,
                          lambda: c.create_polygon(
                              0, 0, 0, 0, 0, 0,
                              outline=self.COLOR_PORTAL_DETECT, fill="", width=3,
                              state='hidden'))
        self._pool_ensure(self._pool_portal_label, n,
                          lambda: c.create_text(
                              0, 0, text="",
                              fill=self.COLOR_PORTAL_DETECT,
                              font=("Consolas", 10, "bold"), anchor="w",
                              state='hidden'))

        for i, portal in enumerate(portals):
            if isinstance(portal, dict):
                px = float(portal.get("x", 0.0))
                py = float(portal.get("y", 0.0))
                is_exit = bool(portal.get("is_exit", False))
            else:
                try:
                    px, py = portal
                except Exception:
                    continue
                is_exit = False
            cx, cy = self._world_to_screen(px, py)
            label = f"Exit {i + 1}" if is_exit else f"Portal {i + 1}"
            color = self.COLOR_EXIT_PORTAL if is_exit else self.COLOR_PORTAL_DETECT
            shape_id = self._pool_portal_shape[i]
            lbl_id = self._pool_portal_label[i]

            if not self._is_on_screen(cx, cy, gw, gh):
                c.itemconfig(shape_id, state='hidden')
                c.itemconfig(lbl_id, state='hidden')
                self._draw_edge_arrow(cx, cy, gw, gh, color, label)
                continue

            size = 10
            if is_exit:
                c.coords(shape_id, cx, cy - size, cx + size, cy, cx, cy + size, cx - size, cy)
                c.itemconfig(shape_id, outline=color, width=3, state='normal')
            else:
                c.coords(shape_id, cx, cy - size, cx + size, cy + size, cx - size, cy + size)
                c.itemconfig(shape_id, outline=color, width=3, state='normal')
            c.coords(lbl_id, cx + 14, cy)
            c.itemconfig(lbl_id, text=label, fill=color, state='normal')


    def _update_events(self, events: List[Dict[str, Any]], gw: int, gh: int):
        n = len(events)
        c = self._canvas

        # All event shapes use 4-vertex polygons so the pool stays homogeneous
        # (diamond for Carjack/Sandlord, rotated square for unknowns).
        self._pool_ensure(self._pool_event_shape, n,
                          lambda: c.create_polygon(
                              0, 0, 0, 0, 0, 0, 0, 0,
                              outline=self.COLOR_EVENT, fill="", width=2,
                              state='hidden'))
        self._pool_ensure(self._pool_event_label, n,
                          lambda: c.create_text(
                              0, 0, text="", fill=self.COLOR_EVENT,
                              font=("Consolas", 9, "bold"), anchor="w",
                              state='hidden'))

        for i, ev in enumerate(events):
            ex, ey = ev.get("x", 0), ev.get("y", 0)
            etype = ev.get("type", "unknown")
            wave = ev.get("wave", -1)
            is_target = ev.get("is_target", False)
            cx, cy = self._world_to_screen(ex, ey)

            if etype == "Carjack":
                color = self.COLOR_CARJACK
                label = "CARJACK"
                guards = ev.get("guards", -1)
                if guards is not None and guards >= 0:
                    label += f" G:{guards}"
                guard_classes = ev.get("guard_classes", "")
                if guard_classes:
                    label += f" [{guard_classes}]"
            elif etype == "Sandlord":
                color = self.COLOR_SANDLORD
                label = "SANDLORD"
            else:
                color = self.COLOR_EVENT_UNKNOWN
                label = etype.upper() if is_target else etype.lower()

            shape_id = self._pool_event_shape[i]
            lbl_id = self._pool_event_label[i]

            if not self._is_on_screen(cx, cy, gw, gh):
                c.itemconfig(shape_id, state='hidden')
                c.itemconfig(lbl_id, state='hidden')
                self._draw_edge_arrow(cx, cy, gw, gh, color, label[:8])
                continue

            size = 11 if etype in ("Carjack", "Sandlord") else 6
            # Diamond: top, right, bottom, left vertices
            c.coords(shape_id, cx, cy - size, cx + size, cy, cx, cy + size, cx - size, cy)
            c.itemconfig(shape_id, outline=color,
                         width=2 if etype in ("Carjack", "Sandlord") else 1,
                         state='normal')
            font_size = 9 if is_target else 8
            font_style = "bold" if is_target else "normal"
            c.coords(lbl_id, cx + size + 4, cy)
            c.itemconfig(lbl_id, text=label, fill=color,
                         font=("Consolas", font_size, font_style), state='normal')

    def _update_entities(self, entities: List[Dict[str, Any]]):
        n = len(entities)
        c = self._canvas

        self._pool_ensure(self._pool_entity_dot, n,
                          lambda: c.create_oval(0, 0, 1, 1,
                                                outline=self.COLOR_ENTITY, width=1,
                                                state='hidden'))
        self._pool_ensure(self._pool_entity_label, n,
                          lambda: c.create_text(0, 0, text="",
                                                fill=self.COLOR_ENTITY,
                                                font=("Consolas", 7), anchor="w",
                                                state='hidden'))

        for i, ent in enumerate(entities):
            ex, ey = ent.get("x", 0), ent.get("y", 0)
            ename = ent.get("name", "?")
            cx, cy = self._world_to_screen(ex, ey)
            c.coords(self._pool_entity_dot[i], cx - 3, cy - 3, cx + 3, cy + 3)
            c.itemconfig(self._pool_entity_dot[i], state='normal')
            c.coords(self._pool_entity_label[i], cx + 6, cy)
            c.itemconfig(self._pool_entity_label[i], text=ename, state='normal')

    def _update_guards(self, guards: List[Dict[str, Any]], gw: int, gh: int):
        """Draw live Carjack security guard positions as vivid orange circles.

        Each guard shows a numbered label (G1, G2 ...) and a shortened ABP
        name so the user can visually confirm whether the overlay's guard
        classification matches what they see in-game.  Off-screen guards get
        edge arrows so guards that have fled far outside the camera view are
        still visible and the bot can navigate toward them.
        """
        n = len(guards)
        c = self._canvas
        color = self.COLOR_GUARD

        self._pool_ensure(self._pool_guard_dot, n,
                          lambda: c.create_oval(0, 0, 1, 1,
                                                outline=color, fill=color, width=2,
                                                state='hidden'))
        self._pool_ensure(self._pool_guard_label, n,
                          lambda: c.create_text(0, 0, text="",
                                                fill=color,
                                                font=("Consolas", 10, "bold"), anchor="w",
                                                state='hidden'))

        for i, g in enumerate(guards):
            gx_w, gy_w = g.get("x", 0), g.get("y", 0)
            # Show world coords so user can pause game and verify position in-game
            label = f"G{i + 1} ({int(gx_w)}, {int(gy_w)})"
            sx, sy = self._world_to_screen(gx_w, gy_w)
            dot_id = self._pool_guard_dot[i]
            lbl_id = self._pool_guard_label[i]

            if not self._is_on_screen(sx, sy, gw, gh):
                c.itemconfig(dot_id, state='hidden')
                c.itemconfig(lbl_id, state='hidden')
                self._draw_edge_arrow(sx, sy, gw, gh, color, f"G{i + 1}")
                continue

            r = 10
            c.coords(dot_id, sx - r, sy - r, sx + r, sy + r)
            c.itemconfig(dot_id, outline=color, fill=color, state='normal')
            c.coords(lbl_id, sx + r + 3, sy)
            c.itemconfig(lbl_id, text=label, state='normal')

    def _update_stuck_indicator(self, is_stuck: bool, gw: int, gh: int):
        c = self._canvas
        if self._id_stuck_rect is None:
            self._id_stuck_rect = c.create_rectangle(
                3, 3, gw - 3, gh - 3,
                outline=self.COLOR_STUCK, width=3, dash=(8, 4))
            self._id_stuck_text = c.create_text(
                gw // 2, gh - 30, text="STUCK DETECTED",
                fill=self.COLOR_STUCK, font=("Consolas", 14, "bold"))
        vis = 'normal' if (is_stuck and self._layers[self.LAYER_STUCK]) else 'hidden'
        c.itemconfig(self._id_stuck_rect, state=vis)
        c.itemconfig(self._id_stuck_text, state=vis)

    # ------------------------------------------------------------------
    # Minimap panel — world-coordinate top-down view (fully pooled;
    # no item creation or deletion after first frame — only coords/itemconfig).
    # ------------------------------------------------------------------
    def _hide_minimap_all(self) -> None:
        for id_ in (self._mm_id_bg, self._mm_id_ring_inner, self._mm_id_ring_outer,
                    self._mm_id_compass_line, self._mm_id_compass_text,
                    self._mm_id_player, self._mm_id_name):
            if id_ is not None:
                self._canvas.itemconfig(id_, state='hidden')
        for pool in (self._mm_pool_path, self._mm_pool_wp, self._mm_pool_wp_lbl,
                     self._mm_pool_portal, self._mm_pool_event):
            self._hide_pool(pool)

    def _update_auto_path(self, path: List[Tuple[float, float]], gw: int, gh: int):
        """Draw the A* computed path as a bright green dashed polyline."""
        # Re-tag approach: delete old items and recreate (path changes infrequently)
        self._canvas.delete("auto_path")

        if not self._display_pos:
            return

        cal = self._calibration or DEFAULT_CALIBRATION
        if not cal:
            return

        pts = []
        for wx, wy in path:
            sx, sy = cal.world_to_screen(wx, wy, self._display_pos[0], self._display_pos[1])
            pts.append((sx, sy))

        if len(pts) < 2:
            return

        # Draw segments; skip pairs where both endpoints are far off-screen
        color = self.COLOR_AUTO_PATH
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            # Only draw if at least one endpoint is within a generous margin
            if ((-gw < x0 < 2 * gw or -gh < y0 < 2 * gh)
                    or (-gw < x1 < 2 * gw or -gh < y1 < 2 * gh)):
                self._canvas.create_line(
                    x0, y0, x1, y1,
                    fill=color, width=2,
                    dash=(6, 4),
                    tags=("auto_path",),
                )

        # Label at the goal (last waypoint)
        gx, gy = pts[-1]
        if -50 < gx < gw + 50 and -50 < gy < gh + 50:
            self._canvas.create_oval(gx - 5, gy - 5, gx + 5, gy + 5,
                                     fill=color, outline="", tags=("auto_path",))
            self._canvas.create_text(gx + 8, gy, text="GOAL",
                                     fill=color, font=("Segoe UI", 8, "bold"),
                                     anchor="w", tags=("auto_path",))

    def _update_minimap(self, render_pos: Tuple[float, float],
                        waypoints: List[Waypoint],
                        portals: List[Any],
                        events: List[Dict[str, Any]],
                        current_idx: int,
                        gw: int, gh: int) -> None:
        """Update the minimap panel in-place — no canvas items are created or deleted
        after the first frame.  All geometry uses true world coordinates so it is
        immune to isometric perspective distortion at any distance from the player.

        World axes (Torchlight Infinite, derived from calibration):
          +Y → minimap right  (screen right)
          +X → minimap up     (screen up)
        """
        ms = self._MINIMAP_SIZE
        mm = self._MINIMAP_MARGIN
        half = max(self._MINIMAP_WORLD_RANGE, 1.0)

        x0 = gw - ms - mm
        y0 = gh - ms - mm
        x1 = gw - mm
        y1 = gh - mm
        cx_mm = (x0 + x1) // 2
        cy_mm = (y0 + y1) // 2
        px, py = render_pos
        c = self._canvas

        def w2m(wx: float, wy: float) -> Tuple[int, int]:
            return (int(cx_mm + (wy - py) / half * ms * 0.5),
                    int(cy_mm - (wx - px) / half * ms * 0.5))

        # Background
        if self._mm_id_bg is None:
            self._mm_id_bg = c.create_rectangle(
                x0, y0, x1, y1, fill="#0D1117", outline="#30363D", width=1)
        else:
            c.coords(self._mm_id_bg, x0, y0, x1, y1)
        c.itemconfig(self._mm_id_bg, state='normal')

        # Range rings (25 % and 75 % of world range)
        for frac, attr in ((0.25, '_mm_id_ring_inner'), (0.75, '_mm_id_ring_outer')):
            r_px = int(ms * 0.5 * frac)
            id_ = getattr(self, attr)
            if id_ is None:
                id_ = c.create_oval(cx_mm - r_px, cy_mm - r_px,
                                    cx_mm + r_px, cy_mm + r_px,
                                    outline="#21262D", width=1)
                setattr(self, attr, id_)
            else:
                c.coords(id_, cx_mm - r_px, cy_mm - r_px, cx_mm + r_px, cy_mm + r_px)
            c.itemconfig(id_, state='normal')

        # Compass (N = world +X = minimap up)
        if self._mm_id_compass_line is None:
            self._mm_id_compass_line = c.create_line(
                cx_mm, y0 + 2, cx_mm, y0 + 7, fill="#30363D", width=1)
            self._mm_id_compass_text = c.create_text(
                cx_mm, y0 + 10, text="N", fill="#30363D",
                font=("Consolas", 6), anchor="center")
        else:
            c.coords(self._mm_id_compass_line, cx_mm, y0 + 2, cx_mm, y0 + 7)
            c.coords(self._mm_id_compass_text, cx_mm, y0 + 10)
        c.itemconfig(self._mm_id_compass_line, state='normal')
        c.itemconfig(self._mm_id_compass_text, state='normal')

        # Path lines
        n_lines = max(0, len(waypoints) - 1)
        while len(self._mm_pool_path) < n_lines:
            self._mm_pool_path.append(
                c.create_line(0, 0, 1, 1, fill=self.COLOR_PATH_LINE, width=1, state='hidden'))
        for i in range(n_lines, len(self._mm_pool_path)):
            c.itemconfig(self._mm_pool_path[i], state='hidden')
        for i in range(n_lines):
            mx1, my1 = w2m(waypoints[i].x, waypoints[i].y)
            mx2, my2 = w2m(waypoints[i + 1].x, waypoints[i + 1].y)
            lid = self._mm_pool_path[i]
            if x0 <= mx1 <= x1 or x0 <= mx2 <= x1:
                c.coords(lid, mx1, my1, mx2, my2)
                c.itemconfig(lid, state='normal')
            else:
                c.itemconfig(lid, state='hidden')

        # Waypoint dots
        n_wps = len(waypoints)
        while len(self._mm_pool_wp) < n_wps:
            self._mm_pool_wp.append(
                c.create_oval(0, 0, 1, 1,
                              fill=self.COLOR_NODE, outline=self.COLOR_NODE,
                              state='hidden'))
        for i in range(n_wps, len(self._mm_pool_wp)):
            c.itemconfig(self._mm_pool_wp[i], state='hidden')

        # Waypoint current-target label (at most 1 visible)
        if not self._mm_pool_wp_lbl:
            self._mm_pool_wp_lbl.append(
                c.create_text(0, 0, text="", fill=self.COLOR_NODE,
                              font=("Consolas", 7), anchor="w", state='hidden'))
        lbl_used = False
        for i, wp in enumerate(waypoints):
            mx, my = w2m(wp.x, wp.y)
            dot_id = self._mm_pool_wp[i]
            in_panel = x0 <= mx <= x1 and y0 <= my <= y1
            if not in_panel:
                c.itemconfig(dot_id, state='hidden')
                continue
            color = (self.COLOR_PORTAL if wp.is_portal
                     else self.COLOR_STAND if wp.wp_type == "stand"
                     else self.COLOR_NODE)
            r = 4 if i == current_idx else 2
            c.coords(dot_id, mx - r, my - r, mx + r, my + r)
            c.itemconfig(dot_id, fill=color, outline=color, state='normal')
            if i == current_idx and not lbl_used:
                lbl_id = self._mm_pool_wp_lbl[0]
                c.coords(lbl_id, mx + 6, my)
                c.itemconfig(lbl_id, text=str(i + 1), fill=color, state='normal')
                lbl_used = True
        if not lbl_used:
            c.itemconfig(self._mm_pool_wp_lbl[0], state='hidden')

        # Portal markers (small triangles)
        n_portals = len(portals)
        while len(self._mm_pool_portal) < n_portals:
            self._mm_pool_portal.append(
                c.create_polygon(0, 0, 0, 0, 0, 0,
                                 outline=self.COLOR_PORTAL_DETECT, fill="", width=1,
                                 state='hidden'))
        for i in range(n_portals, len(self._mm_pool_portal)):
            c.itemconfig(self._mm_pool_portal[i], state='hidden')
        for i, portal in enumerate(portals):
            if isinstance(portal, dict):
                pox = float(portal.get("x", 0.0))
                poy = float(portal.get("y", 0.0))
                is_exit = bool(portal.get("is_exit", False))
            else:
                try:
                    pox, poy = portal
                except Exception:
                    continue
                is_exit = False
            mx, my = w2m(pox, poy)
            pid = self._mm_pool_portal[i]
            if x0 <= mx <= x1 and y0 <= my <= y1:
                if is_exit:
                    c.coords(pid, mx, my - 4, mx + 4, my, mx, my + 4, mx - 4, my)
                    c.itemconfig(pid, outline=self.COLOR_EXIT_PORTAL, state='normal')
                else:
                    c.coords(pid, mx, my - 4, mx + 4, my + 4, mx - 4, my + 4)
                    c.itemconfig(pid, outline=self.COLOR_PORTAL_DETECT, state='normal')
            else:
                c.itemconfig(pid, state='hidden')

        # Event markers (small diamonds)
        n_events = len(events)
        while len(self._mm_pool_event) < n_events:
            self._mm_pool_event.append(
                c.create_polygon(0, 0, 0, 0, 0, 0, 0, 0,
                                 outline=self.COLOR_EVENT, fill="", width=1,
                                 state='hidden'))
        for i in range(n_events, len(self._mm_pool_event)):
            c.itemconfig(self._mm_pool_event[i], state='hidden')
        for i, ev in enumerate(events):
            ex, ey = ev.get("x", 0), ev.get("y", 0)
            etype = ev.get("type", "unknown")
            mx, my = w2m(ex, ey)
            eid = self._mm_pool_event[i]
            if not (x0 <= mx <= x1 and y0 <= my <= y1):
                c.itemconfig(eid, state='hidden')
                continue
            color = (self.COLOR_CARJACK if etype == "Carjack"
                     else self.COLOR_SANDLORD if etype == "Sandlord"
                     else self.COLOR_EVENT_UNKNOWN)
            c.coords(eid, mx, my - 4, mx + 4, my, mx, my + 4, mx - 4, my)
            c.itemconfig(eid, outline=color, state='normal')

        # Player dot at minimap center
        if self._mm_id_player is None:
            self._mm_id_player = c.create_oval(
                cx_mm - 4, cy_mm - 4, cx_mm + 4, cy_mm + 4,
                fill=self.COLOR_PLAYER, outline=self.COLOR_PLAYER)
        else:
            c.coords(self._mm_id_player,
                     cx_mm - 4, cy_mm - 4, cx_mm + 4, cy_mm + 4)
        c.itemconfig(self._mm_id_player, state='normal')

        # Map name label
        if self._mm_id_name is None:
            self._mm_id_name = c.create_text(
                x0 + 3, y1 - 3, text="",
                fill="#6E7681", font=("Consolas", 6), anchor="sw")
        else:
            c.coords(self._mm_id_name, x0 + 3, y1 - 3)
        if self._current_map_name:
            c.itemconfig(self._mm_id_name,
                         text=self._current_map_name[:18], state='normal')
        else:
            c.itemconfig(self._mm_id_name, state='hidden')
