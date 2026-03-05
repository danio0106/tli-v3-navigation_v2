import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QMetaObject, QObject, Qt, QUrl
from PySide6.QtQuick import QQuickView

from src.core.waypoint import Waypoint
from src.utils.constants import CHARACTER_CENTER
from src.utils.logger import log


class QtQuickOverlay(QObject):
    """Qt Quick overlay window backed by native engine snapshots."""

    LAYER_WAYPOINTS = "waypoints"
    LAYER_PLAYER = "player"
    LAYER_NAV_TARGET = "nav_target"
    LAYER_PORTALS = "portals"
    LAYER_EVENTS = "events"
    LAYER_ENTITIES = "entities"
    LAYER_STUCK = "stuck"
    LAYER_AUTO_PATH = "auto_path"
    LAYER_MINIMAP = "minimap"
    LAYER_GRID = "grid"
    LAYER_NAV_COLLISION = "nav_collision"

    def __init__(
        self,
        game_window_rect: Optional[Tuple[int, int, int, int]] = None,
        lod_settings: Optional[Dict[str, Any]] = None,
    ):
        super().__init__()
        self._running = False
        self._visible = False

        self._view: Optional[QQuickView] = None
        self._root_obj = None

        self._game_rect = game_window_rect or (0, 0, 1920, 1080)
        self._game_focused = True

        self._player_pos: Optional[Tuple[float, float]] = None
        self._waypoints: List[Waypoint] = []
        self._current_wp_index: int = -1
        self._portal_positions: List[Dict[str, Any]] = []
        self._event_markers: List[Dict[str, Any]] = []
        self._entity_positions: List[Dict[str, Any]] = []
        self._guard_markers: List[Dict[str, Any]] = []
        self._nav_collision_markers: List[Dict[str, Any]] = []
        self._auto_path_waypoints: List[Tuple[float, float]] = []
        self._grid_walkable: List[Tuple[float, float]] = []
        self._grid_frontier: List[Tuple[float, float]] = []
        self._grid_cell_size: float = 0.0
        self._is_stuck = False

        defaults = {
            "enabled": True,
            "max_portals": 120,
            "max_events": 100,
            "max_guards": 100,
            "max_entities": 150,
            "max_nav_collision": 200,
            "max_grid_walkable": 2200,
            "max_grid_frontier": 900,
        }
        self._lod = dict(defaults)
        if isinstance(lod_settings, dict):
            self._lod.update(lod_settings)

        self._layers = {
            self.LAYER_WAYPOINTS: True,
            self.LAYER_PLAYER: True,
            self.LAYER_NAV_TARGET: True,
            self.LAYER_PORTALS: True,
            self.LAYER_EVENTS: True,
            self.LAYER_ENTITIES: True,
            self.LAYER_STUCK: True,
            self.LAYER_AUTO_PATH: True,
            self.LAYER_MINIMAP: True,
            self.LAYER_GRID: False,
            self.LAYER_NAV_COLLISION: True,
        }

        self._calibration = None
        self._current_map_name = ""

    def start(self):
        if self._running:
            return

        view = QQuickView()
        view.setColor(Qt.transparent)
        view.setFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.BypassWindowManagerHint
            | Qt.WindowTransparentForInput
        )
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        qml_path = os.path.join(os.path.dirname(__file__), "qml", "overlay.qml")
        view.setSource(QUrl.fromLocalFile(qml_path))

        if view.status() == QQuickView.Error:
            errs = ", ".join([e.toString() for e in view.errors()])
            raise RuntimeError(f"Qt Quick overlay QML failed: {errs}")

        self._view = view
        self._root_obj = view.rootObject()
        if self._root_obj is None:
            raise RuntimeError("Qt Quick overlay root object is null")

        self._apply_rect()
        self._apply_click_through()
        self._view.show()
        self._visible = True
        self._running = True
        self._push_frame()

    def stop(self):
        self._running = False
        self._visible = False
        if self._view is not None:
            try:
                self._view.hide()
                self._view.close()
                self._view.deleteLater()
            except Exception:
                pass
        self._view = None
        self._root_obj = None

    def set_game_rect(self, rect: Tuple[int, int, int, int]):
        self._game_rect = rect
        self._apply_rect()

    def set_game_focused(self, focused: bool):
        self._game_focused = bool(focused)
        if self._view is not None:
            self._view.setOpacity(0.86 if self._game_focused else 0.0)
            self._view.setFlag(Qt.WindowStaysOnTopHint, self._game_focused)
            self._view.show()

    def toggle_visibility(self):
        if not self._view:
            return
        self._visible = not self._visible
        if self._visible:
            self._view.show()
        else:
            self._view.hide()

    def set_layer_visible(self, _layer: str, _visible: bool):
        layer = str(_layer or "")
        if layer in self._layers:
            self._layers[layer] = bool(_visible)

    def set_player_position(self, x: float, y: float):
        self._player_pos = (float(x), float(y))

    def set_waypoints(self, waypoints: List[Waypoint]):
        self._waypoints = list(waypoints)

    def set_current_waypoint_index(self, idx: int):
        self._current_wp_index = int(idx)

    def set_portal_positions(self, portals: List[Any]):
        normalized: List[Dict[str, Any]] = []
        for p in portals or []:
            if isinstance(p, dict):
                normalized.append(p)
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                normalized.append({"x": float(p[0]), "y": float(p[1]), "is_exit": False})
        self._portal_positions = normalized

    def set_event_markers(self, events: List[Dict[str, Any]]):
        self._event_markers = list(events or [])

    def set_guard_markers(self, guards: List[Dict[str, Any]]):
        self._guard_markers = list(guards or [])

    def set_auto_path(self, path: List[Tuple[float, float]]):
        self._auto_path_waypoints = list(path or [])

    def set_nav_collision_markers(self, _markers: List[Dict[str, Any]]):
        self._nav_collision_markers = list(_markers or [])

    def set_grid_data(self, _walkable, _frontier, _cell_size=0.0):
        self._grid_walkable = list(_walkable or [])
        self._grid_frontier = list(_frontier or [])
        try:
            self._grid_cell_size = float(_cell_size or 0.0)
        except Exception:
            self._grid_cell_size = 0.0

    def set_stuck(self, _is_stuck: bool):
        self._is_stuck = bool(_is_stuck)

    def set_entity_positions(self, _entities: List[Dict[str, Any]]):
        self._entity_positions = list(_entities or [])

    def set_selected_waypoints(self, _indices: set):
        return

    def set_calibration(self, calibration, map_name: str = ""):
        self._calibration = calibration
        self._current_map_name = map_name or ""

    def flush(self):
        self._push_frame()

    def _apply_rect(self):
        if self._view is None:
            return
        gx, gy, gw, gh = self._game_rect
        self._view.setGeometry(int(gx), int(gy), int(gw), int(gh))

    def _world_to_screen(self, wx: float, wy: float) -> Tuple[float, float]:
        if self._calibration is not None and self._player_pos is not None:
            try:
                sx, sy = self._calibration.world_to_screen(
                    float(wx), float(wy), float(self._player_pos[0]), float(self._player_pos[1])
                )
                return float(sx), float(sy)
            except Exception:
                pass

        cx, cy = CHARACTER_CENTER
        if self._player_pos is not None:
            return cx + (float(wx) - self._player_pos[0]), cy + (float(wy) - self._player_pos[1])
        return float(cx), float(cy)

    def _to_screen_points(self, points: List[Tuple[float, float]]) -> List[Dict[str, float]]:
        out: List[Dict[str, float]] = []
        for wx, wy in points:
            sx, sy = self._world_to_screen(wx, wy)
            out.append({"x": sx, "y": sy})
        return out

    def _decimate(self, items: List[Any], max_items: int) -> List[Any]:
        if not self._lod.get("enabled", True):
            return list(items)
        cap = int(max_items or 0)
        if cap <= 0 or len(items) <= cap:
            return list(items)
        step = max(1, int(math.ceil(len(items) / float(cap))))
        return [items[i] for i in range(0, len(items), step)]

    def _build_nav_collision_polygons(self, markers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for m in markers:
            try:
                cx = float(m.get("x", 0.0))
                cy = float(m.get("y", 0.0))
                ex = max(1.0, float(m.get("extent_x", 0.0)))
                ey = max(1.0, float(m.get("extent_y", 0.0)))
                yaw_deg = float(m.get("yaw", 0.0))
            except Exception:
                continue
            if not all(math.isfinite(v) for v in (cx, cy, ex, ey, yaw_deg)):
                continue
            yaw = math.radians(yaw_deg)
            cos_y = math.cos(yaw)
            sin_y = math.sin(yaw)
            corners = []
            for lx, ly in [(-ex, -ey), (ex, -ey), (ex, ey), (-ex, ey)]:
                rx = (lx * cos_y) - (ly * sin_y)
                ry = (lx * sin_y) + (ly * cos_y)
                sx, sy = self._world_to_screen(cx + rx, cy + ry)
                corners.append({"x": sx, "y": sy})
            style = str(m.get("overlay_style", "raw") or "raw")
            label = str(m.get("overlay_label", "") or "")
            out.append({
                "points": corners,
                "style": style,
                "label": label,
                "cx": sum(p["x"] for p in corners) / 4.0,
                "cy": sum(p["y"] for p in corners) / 4.0,
            })
        return out

    def _push_frame(self):
        if not self._running or self._root_obj is None:
            return

        payload: Dict[str, Any] = {
            "player": None,
            "pathLines": [],
            "autoPath": [],
            "navLine": None,
            "waypoints": [],
            "portals": [],
            "events": [],
            "guards": [],
            "entities": [],
            "stuck": self._is_stuck,
            "navCollision": [],
            "gridWalkable": [],
            "gridFrontier": [],
            "gridCellSize": self._grid_cell_size,
            "layers": {
                "waypoints": bool(self._layers.get(self.LAYER_WAYPOINTS, True)),
                "player": bool(self._layers.get(self.LAYER_PLAYER, True)),
                "nav_target": bool(self._layers.get(self.LAYER_NAV_TARGET, True)),
                "portals": bool(self._layers.get(self.LAYER_PORTALS, True)),
                "events": bool(self._layers.get(self.LAYER_EVENTS, True)),
                "entities": bool(self._layers.get(self.LAYER_ENTITIES, True)),
                "stuck": bool(self._layers.get(self.LAYER_STUCK, True)),
                "auto_path": bool(self._layers.get(self.LAYER_AUTO_PATH, True)),
                "minimap": bool(self._layers.get(self.LAYER_MINIMAP, True)),
                "grid": bool(self._layers.get(self.LAYER_GRID, False)),
                "nav_collision": bool(self._layers.get(self.LAYER_NAV_COLLISION, True)),
            },
        }

        if self._player_pos is not None:
            px, py = CHARACTER_CENTER
            payload["player"] = {
                "x": px,
                "y": py,
                "label": f"{self._player_pos[0]:.0f}, {self._player_pos[1]:.0f}",
            }

        if self._auto_path_waypoints:
            payload["autoPath"] = self._to_screen_points(self._auto_path_waypoints)

        if self._waypoints:
            line_segments: List[Dict[str, Dict[str, float]]] = []
            waypoint_draw: List[Dict[str, Any]] = []
            screen_waypoints: List[Tuple[float, float]] = []
            for idx, wp in enumerate(self._waypoints):
                sx, sy = self._world_to_screen(wp.x, wp.y)
                screen_waypoints.append((sx, sy))
                waypoint_draw.append(
                    {
                        "x": sx,
                        "y": sy,
                        "kind": str(getattr(wp, "wp_type", "node") or "node"),
                        "current": idx == self._current_wp_index,
                        "label": str(idx + 1),
                        "isPortal": bool(getattr(wp, "is_portal", False)),
                    }
                )

            for i in range(1, len(screen_waypoints)):
                a = screen_waypoints[i - 1]
                b = screen_waypoints[i]
                line_segments.append({"a": {"x": a[0], "y": a[1]}, "b": {"x": b[0], "y": b[1]}})

            payload["waypoints"] = waypoint_draw
            payload["pathLines"] = line_segments

            if self._player_pos is not None and 0 <= self._current_wp_index < len(self._waypoints):
                wp = self._waypoints[self._current_wp_index]
                sx, sy = self._world_to_screen(wp.x, wp.y)
                pxy = payload["player"]
                if pxy is not None:
                    payload["navLine"] = {
                        "from": {"x": pxy["x"], "y": pxy["y"]},
                        "to": {"x": sx, "y": sy},
                    }

        portal_points: List[Dict[str, Any]] = []
        for p in self._decimate(self._portal_positions, int(self._lod.get("max_portals", 120))):
            try:
                wx = float(p.get("x", 0.0))
                wy = float(p.get("y", 0.0))
            except Exception:
                continue
            sx, sy = self._world_to_screen(wx, wy)
            is_exit = bool(p.get("is_exit", False))
            idx = len(portal_points) + 1
            portal_points.append(
                {
                    "x": sx,
                    "y": sy,
                    "isExit": is_exit,
                    "label": f"Exit {idx}" if is_exit else f"Portal {idx}",
                }
            )
        payload["portals"] = portal_points

        event_points: List[Dict[str, Any]] = []
        for e in self._decimate(self._event_markers, int(self._lod.get("max_events", 100))):
            try:
                wx = float(e.get("x", 0.0))
                wy = float(e.get("y", 0.0))
            except Exception:
                continue
            if not (math.isfinite(wx) and math.isfinite(wy)):
                continue
            sx, sy = self._world_to_screen(wx, wy)
            etype = str(e.get("type", "") or "")
            label = etype.upper() if etype else "EVENT"
            if etype == "Carjack":
                guards = int(e.get("guards", -1) or -1)
                label = "CARJACK" if guards < 0 else f"CARJACK G:{guards}"
            elif etype == "Sandlord":
                label = "SANDLORD"
            event_points.append({"x": sx, "y": sy, "type": etype, "label": label})
        payload["events"] = event_points

        guard_points: List[Dict[str, Any]] = []
        for g in self._decimate(self._guard_markers, int(self._lod.get("max_guards", 100))):
            try:
                wx = float(g.get("x", 0.0))
                wy = float(g.get("y", 0.0))
            except Exception:
                continue
            if not (math.isfinite(wx) and math.isfinite(wy)):
                continue
            sx, sy = self._world_to_screen(wx, wy)
            idx = len(guard_points) + 1
            guard_points.append({"x": sx, "y": sy, "label": f"G{idx}"})
        payload["guards"] = guard_points

        entity_points: List[Dict[str, Any]] = []
        for e in self._decimate(self._entity_positions, int(self._lod.get("max_entities", 150))):
            try:
                wx = float(e.get("x", 0.0))
                wy = float(e.get("y", 0.0))
            except Exception:
                continue
            if not (math.isfinite(wx) and math.isfinite(wy)):
                continue
            sx, sy = self._world_to_screen(wx, wy)
            name = str(e.get("name", "") or "")
            entity_points.append({"x": sx, "y": sy, "name": name})
        payload["entities"] = entity_points

        raw_boxes = self._decimate(
            self._nav_collision_markers,
            int(self._lod.get("max_nav_collision", 200)),
        )
        payload["navCollision"] = self._build_nav_collision_polygons(raw_boxes)

        walkable = self._decimate(
            self._grid_walkable,
            int(self._lod.get("max_grid_walkable", 2200)),
        )
        frontier = self._decimate(
            self._grid_frontier,
            int(self._lod.get("max_grid_frontier", 900)),
        )
        payload["gridWalkable"] = self._to_screen_points(walkable)
        payload["gridFrontier"] = self._to_screen_points(frontier)

        self._root_obj.setProperty("overlayData", payload)
        try:
            QMetaObject.invokeMethod(self._root_obj, "refresh")
        except Exception:
            pass

    def _apply_click_through(self):
        if self._view is None or sys.platform != "win32":
            return
        try:
            import ctypes

            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            LWA_COLORKEY = 0x00000001
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020

            hwnd = int(self._view.winId())
            user32 = ctypes.windll.user32
            cur = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, cur | WS_EX_LAYERED | WS_EX_TRANSPARENT)
            user32.SetLayeredWindowAttributes(hwnd, 0x00000000, 0, LWA_COLORKEY)
            user32.SetWindowPos(
                hwnd,
                0,
                0,
                0,
                0,
                0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
        except Exception as exc:
            log.warning(f"[QtQuickOverlay] click-through setup failed: {exc}")
