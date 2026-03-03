import sys
import os
import json
import time
import math
import threading
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass

from src.utils.constants import CHARACTER_CENTER
from src.utils.logger import log

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        ctypes = None
else:
    ctypes = None

CALIBRATION_FILE = os.path.join("data", "map_calibrations.json")


@dataclass
class MapCalibration:
    screen_right_world: Tuple[float, float]
    screen_down_world: Tuple[float, float]
    inv_a: float
    inv_b: float
    inv_c: float
    inv_d: float
    inv_tx: float
    inv_ty: float

    @staticmethod
    def from_vectors(right_wx: float, right_wy: float,
                     down_wx: float, down_wy: float) -> Optional['MapCalibration']:
        det = right_wx * down_wy - right_wy * down_wx
        if abs(det) < 1e-12:
            return None

        inv_det = 1.0 / det
        inv_a = down_wy * inv_det
        inv_b = -down_wx * inv_det
        inv_c = -right_wy * inv_det
        inv_d = right_wx * inv_det

        return MapCalibration(
            screen_right_world=(right_wx, right_wy),
            screen_down_world=(down_wx, down_wy),
            inv_a=inv_a, inv_b=inv_b,
            inv_c=inv_c, inv_d=inv_d,
            inv_tx=0.0, inv_ty=0.0,
        )

    def world_to_screen(self, wx: float, wy: float,
                        player_wx: float, player_wy: float) -> Tuple[int, int]:
        dw_x = wx - player_wx
        dw_y = wy - player_wy

        sx = self.inv_a * dw_x + self.inv_b * dw_y
        sy = self.inv_c * dw_x + self.inv_d * dw_y

        cx, cy = CHARACTER_CENTER
        return (int(cx + sx), int(cy + sy))

    def world_distance_to_pixels(self, world_dist: float) -> float:
        avg_scale_x = math.sqrt(self.inv_a ** 2 + self.inv_c ** 2)
        avg_scale_y = math.sqrt(self.inv_b ** 2 + self.inv_d ** 2)
        return world_dist * (avg_scale_x + avg_scale_y) / 2.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "screen_right_world": list(self.screen_right_world),
            "screen_down_world": list(self.screen_down_world),
            "inv": [self.inv_a, self.inv_b, self.inv_c, self.inv_d],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> Optional['MapCalibration']:
        try:
            rw = d["screen_right_world"]
            dw = d["screen_down_world"]
            return MapCalibration.from_vectors(rw[0], rw[1], dw[0], dw[1])
        except (KeyError, IndexError, TypeError):
            return None


# ---------------------------------------------------------------------------
# TLI isometric camera angle presets
# ---------------------------------------------------------------------------
# Derived from real in-game calibration measurements across all 12 maps.
# TLI uses a fixed isometric camera that can appear in one of four 90° rotations
# depending on how each map is oriented in world space.
#
# All four variants share the same pixel-to-world SCALE (~1.67 units/px right,
# ~2.34 units/px down).  Only the direction (rotation) differs.
#
# Orient-0   (most common — 6/12 maps: Swirling Mines, Shadow Outpost, etc.)
#   screen-right ≈ world(-0.24, +1.67)   screen-down ≈ world(-2.34, -0.04)
# Orient-90  (2/12 maps: High Court Maze, Grimwind Woods)
#   screen-right ≈ world(+1.02, +1.34)   screen-down ≈ world(-1.68, +1.63)
# Orient-180 (3/12 maps: Defiled Side Chamber, Singing Sand, Wall of Last Breath)
#   screen-right ≈ world(-1.34, +1.01)   screen-down ≈ world(-1.62, -1.68)
# Orient-270 (1/12 maps: Deserted District — mirrored from Orient-90)
#   screen-right ≈ world(+1.34, -1.02)   screen-down ≈ world(+1.63, +1.69)
#
# DEFAULT_CALIBRATION uses Orient-0 as a fallback when no per-map calibration
# exists (e.g. hideout, newly entered maps).  Per-map calibration always wins.

TLI_ORIENT_0   = MapCalibration.from_vectors(-0.24,  1.67, -2.34, -0.04)  # most common
TLI_ORIENT_90  = MapCalibration.from_vectors( 1.02,  1.34, -1.68,  1.63)
TLI_ORIENT_180 = MapCalibration.from_vectors(-1.34,  1.01, -1.62, -1.68)
TLI_ORIENT_270 = MapCalibration.from_vectors( 1.34, -1.02,  1.63,  1.69)

DEFAULT_CALIBRATION: Optional[MapCalibration] = TLI_ORIENT_0


class ScaleCalibrator:
    CALIBRATE_PIXEL_OFFSET = 300
    STABILIZE_THRESHOLD = 15.0
    STABILIZE_CHECKS = 5
    STABILIZE_INTERVAL = 0.3
    CALIBRATE_TIMEOUT = 8.0
    MIN_WORLD_DIST = 30.0

    def __init__(self, window_manager=None, input_controller=None):
        self._window_manager = window_manager
        self._input = input_controller
        self._lock = threading.Lock()
        self._calibrations: Dict[str, MapCalibration] = {}
        self._current_map: Optional[str] = None
        self._calibrating = False
        self._load_calibrations()

    def _load_calibrations(self):
        try:
            if os.path.exists(CALIBRATION_FILE):
                with open(CALIBRATION_FILE, "r") as f:
                    data = json.load(f)
                for map_name, cal_dict in data.items():
                    cal = MapCalibration.from_dict(cal_dict)
                    if cal:
                        self._calibrations[map_name] = cal
                log.info(f"[ScaleCalibrator] Loaded calibrations for {len(self._calibrations)} maps: {list(self._calibrations.keys())}")
        except Exception as e:
            log.warning(f"[ScaleCalibrator] Failed to load calibrations: {e}")

    def _save_calibrations(self):
        try:
            os.makedirs(os.path.dirname(CALIBRATION_FILE), exist_ok=True)
            data = {}
            for map_name, cal in self._calibrations.items():
                data[map_name] = cal.to_dict()
            with open(CALIBRATION_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error(f"[ScaleCalibrator] Failed to save calibrations: {e}")

    def set_current_map(self, map_name: str):
        with self._lock:
            self._current_map = map_name

    def get_calibration(self, map_name: Optional[str] = None) -> Optional[MapCalibration]:
        with self._lock:
            name = map_name or self._current_map
            if name:
                return self._calibrations.get(name)
            return None

    def has_calibration(self, map_name: Optional[str] = None) -> bool:
        return self.get_calibration(map_name) is not None

    @property
    def is_calibrating(self) -> bool:
        return self._calibrating

    def get_calibrated_maps(self) -> list:
        with self._lock:
            return list(self._calibrations.keys())

    def _client_to_screen(self, cx: int, cy: int) -> Optional[Tuple[int, int]]:
        if not IS_WINDOWS or ctypes is None or not self._window_manager or not self._window_manager.hwnd:
            return None
        try:
            point = wintypes.POINT(cx, cy)
            ctypes.windll.user32.ClientToScreen(self._window_manager.hwnd, ctypes.byref(point))
            return (point.x, point.y)
        except Exception:
            return None

    def _read_player_pos(self, game_state) -> Optional[Tuple[float, float]]:
        try:
            game_state.update()
            pos = game_state.player.position
            if pos and pos.x != 0 and pos.y != 0:
                return (pos.x, pos.y)
        except Exception:
            pass
        return None

    def _wait_for_stabilize(self, game_state) -> Optional[Tuple[float, float]]:
        positions = []
        for _ in range(self.STABILIZE_CHECKS):
            p = self._read_player_pos(game_state)
            if p:
                positions.append(p)
            time.sleep(self.STABILIZE_INTERVAL)

        if len(positions) < self.STABILIZE_CHECKS:
            return None

        last = positions[-1]
        for p in positions[:-1]:
            dx = p[0] - last[0]
            dy = p[1] - last[1]
            if math.sqrt(dx * dx + dy * dy) > self.STABILIZE_THRESHOLD:
                return None
        return last

    def _move_and_measure(self, game_state, px_dx: int, px_dy: int,
                          center_screen: Tuple[int, int]) -> Optional[Tuple[float, float]]:
        cx, cy = CHARACTER_CENTER
        if self._input:
            self._input.move_mouse(cx, cy)
        time.sleep(1.0)

        start_pos = self._wait_for_stabilize(game_state)
        if not start_pos:
            log.warning("[ScaleCalibrator] Player not stable at start, skipping direction")
            return None

        target_cx = cx + px_dx
        target_cy = cy + px_dy
        if self._input:
            self._input.click(target_cx, target_cy, button="left")
        axis = "X" if px_dx != 0 else "Y"
        log.info(f"[ScaleCalibrator] Left-clicked at client ({target_cx}, {target_cy}), pixel offset=({px_dx}, {px_dy}) [{axis}-axis]")

        deadline = time.time() + self.CALIBRATE_TIMEOUT
        end_pos = None
        while time.time() < deadline:
            time.sleep(0.3)
            end_pos = self._wait_for_stabilize(game_state)
            if end_pos:
                world_dx = end_pos[0] - start_pos[0]
                world_dy = end_pos[1] - start_pos[1]
                world_dist = math.sqrt(world_dx * world_dx + world_dy * world_dy)
                if world_dist > self.MIN_WORLD_DIST:
                    break
                end_pos = None

        if end_pos is None:
            log.warning(f"[ScaleCalibrator] Player did not move enough for {axis}-axis")
            return None

        world_dx = end_pos[0] - start_pos[0]
        world_dy = end_pos[1] - start_pos[1]
        log.info(f"[ScaleCalibrator] {axis}-axis: pixel_offset=({px_dx},{px_dy}) -> world_delta=({world_dx:.1f}, {world_dy:.1f})")
        return (world_dx, world_dy)

    def calibrate(self, game_state, map_name: Optional[str] = None) -> Optional[MapCalibration]:
        self._calibrating = True
        name = map_name or self._current_map or "unknown"
        log.info(f"[ScaleCalibrator] Starting calibration for map: {name}")

        try:
            center_screen = self._client_to_screen(CHARACTER_CENTER[0], CHARACTER_CENTER[1])
            if not center_screen:
                log.error("[ScaleCalibrator] Cannot convert CHARACTER_CENTER to screen coords")
                return None

            px = self.CALIBRATE_PIXEL_OFFSET

            right_delta = self._move_and_measure(game_state, px, 0, center_screen)
            if right_delta is None:
                log.error("[ScaleCalibrator] Failed to measure screen-right direction")
                return None

            down_delta = self._move_and_measure(game_state, 0, px, center_screen)
            if down_delta is None:
                log.error("[ScaleCalibrator] Failed to measure screen-down direction")
                return None

            right_per_px = (right_delta[0] / px, right_delta[1] / px)
            down_per_px = (down_delta[0] / px, down_delta[1] / px)

            log.info(f"[ScaleCalibrator] Per-pixel vectors: right=({right_per_px[0]:.4f}, {right_per_px[1]:.4f}) down=({down_per_px[0]:.4f}, {down_per_px[1]:.4f})")

            cal = MapCalibration.from_vectors(right_per_px[0], right_per_px[1],
                                               down_per_px[0], down_per_px[1])
            if cal is None:
                log.error("[ScaleCalibrator] Degenerate transformation matrix (det=0)")
                return None

            with self._lock:
                self._calibrations[name] = cal
                self._current_map = name

            self._save_calibrations()

            if self._input:
                self._input.move_mouse(CHARACTER_CENTER[0], CHARACTER_CENTER[1])

            log.info(f"[ScaleCalibrator] Calibration complete for '{name}' — saved to {CALIBRATION_FILE}")
            return cal

        except Exception as e:
            log.error(f"[ScaleCalibrator] Calibration error: {e}")
            return None

        finally:
            self._calibrating = False
