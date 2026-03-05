import json
import os
import time
from typing import Optional, List, Callable
from dataclasses import dataclass, asdict

from src.core.game_state import GameState
from src.core.waypoint import Waypoint
from src.utils.constants import MAP_NAMES, PATHS_DIR
from src.utils.logger import log


class PathRecorder:
    def __init__(self, game_state: GameState):
        self._game_state = game_state
        self._waypoints: List[Waypoint] = []
        self._is_recording = False
        self._record_interval: float = 0.15
        self._min_distance: float = 50.0
        self._current_map: str = ""
        self._step_callback: Optional[Callable] = None
        self._logging_enabled: bool = True

        os.makedirs(PATHS_DIR, exist_ok=True)

    def set_step_callback(self, callback: Callable):
        self._step_callback = callback

    def _log(self, msg: str):
        if self._logging_enabled:
            log.info(f"[PathRecorder] {msg}")
        if self._step_callback:
            self._step_callback(msg)

    def set_logging_enabled(self, enabled: bool):
        self._logging_enabled = bool(enabled)

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def waypoint_count(self) -> int:
        return len(self._waypoints)

    @property
    def waypoints(self) -> List[Waypoint]:
        return self._waypoints

    def start_recording(self, map_name: str):
        self._waypoints = []
        self._is_recording = True
        self._current_map = map_name
        self._log(f"Recording started for: {map_name}")

    def stop_recording(self) -> List[Waypoint]:
        self._is_recording = False
        self._log(f"Recording stopped: {len(self._waypoints)} waypoints")
        return self._waypoints

    def record_tick(self) -> bool:
        if not self._is_recording:
            return False

        self._game_state.update()
        pos = self._game_state.player.position

        if self._waypoints:
            last = self._waypoints[-1]
            dist = last.distance_to(pos.x, pos.y)
            if dist < self._min_distance:
                return False

        wp = Waypoint(x=pos.x, y=pos.y, wp_type="node", is_portal=False)
        self._waypoints.append(wp)
        return True

    def add_portal_waypoint(self):
        if not self._is_recording:
            self._log("Not recording - cannot add portal waypoint")
            return

        self._game_state.update()
        pos = self._game_state.player.position

        wp = Waypoint(x=pos.x, y=pos.y, wp_type="stand", is_portal=True, label="Portal", wait_time=0.0)
        self._waypoints.append(wp)
        self._log(f"Portal waypoint added at ({pos.x:.0f}, {pos.y:.0f}) - index {len(self._waypoints) - 1}")

    def remove_last_waypoint(self):
        if self._waypoints:
            removed = self._waypoints.pop()
            portal_str = " (portal)" if removed.is_portal else ""
            self._log(f"Removed last waypoint{portal_str}")

    def save_path(self, map_name: str) -> bool:
        if not self._waypoints:
            self._log("No waypoints to save")
            return False

        filename = self._map_to_filename(map_name)
        filepath = os.path.join(PATHS_DIR, filename)

        data = {
            "map_name": map_name,
            "waypoint_count": len(self._waypoints),
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "waypoints": [
                {
                    "x": wp.x,
                    "y": wp.y,
                    "wp_type": wp.wp_type,
                    "is_portal": wp.is_portal,
                    "label": wp.label,
                    "wait_time": wp.wait_time,
                }
                for wp in self._waypoints
            ],
        }

        try:
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
            self._log(f"Saved {len(self._waypoints)} waypoints to {filepath}")
            return True
        except Exception as e:
            self._log(f"ERROR saving path: {e}")
            return False

    def load_path(self, map_name: str) -> Optional[List[Waypoint]]:
        filename = self._map_to_filename(map_name)
        filepath = os.path.join(PATHS_DIR, filename)

        if not os.path.exists(filepath):
            self._log(f"No saved path for: {map_name}")
            return None

        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            waypoints = []
            for wp_data in data.get("waypoints", []):
                wp = Waypoint(
                    x=wp_data["x"],
                    y=wp_data["y"],
                    wp_type=wp_data.get("wp_type", "node"),
                    is_portal=wp_data.get("is_portal", False),
                    label=wp_data.get("label", ""),
                    wait_time=wp_data.get("wait_time", 0.0),
                )
                waypoints.append(wp)

            self._log(f"Loaded {len(waypoints)} waypoints for: {map_name}")
            return waypoints
        except Exception as e:
            self._log(f"ERROR loading path: {e}")
            return None

    def delete_path(self, map_name: str) -> bool:
        filename = self._map_to_filename(map_name)
        filepath = os.path.join(PATHS_DIR, filename)

        if os.path.exists(filepath):
            os.remove(filepath)
            self._log(f"Deleted path for: {map_name}")
            return True
        return False

    def get_saved_maps(self) -> List[str]:
        saved = []
        for name in MAP_NAMES:
            filename = self._map_to_filename(name)
            filepath = os.path.join(PATHS_DIR, filename)
            if os.path.exists(filepath):
                saved.append(name)
        return saved

    def get_path_info(self, map_name: str) -> Optional[dict]:
        filename = self._map_to_filename(map_name)
        filepath = os.path.join(PATHS_DIR, filename)

        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            portal_count = sum(1 for wp in data.get("waypoints", []) if wp.get("is_portal", False))
            return {
                "map_name": map_name,
                "waypoint_count": data.get("waypoint_count", 0),
                "portal_count": portal_count,
                "recorded_at": data.get("recorded_at", "Unknown"),
            }
        except Exception:
            return None

    def _map_to_filename(self, map_name: str) -> str:
        safe = map_name.lower().replace(" ", "_")
        safe = "".join(c for c in safe if c.isalnum() or c == "_")
        return f"{safe}.json"
