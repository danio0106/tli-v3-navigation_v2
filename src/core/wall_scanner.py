"""wall_scanner.py — Walkable-area data collection + A* grid for auto-navigation.

PRIMARY DATA SOURCE — Direct position sampling (reliable):
  The bot samples the player's world position every 50 world units during any
  map session (Explorer runs, regular bot runs, and manual play with bot
  attached).  Positions are saved directly to wall_data.json via the
  PosSampler thread in ZoneWatcher and MapExplorer._run_position_sampler().
  No MinimapSaveObject dependency.

CONFIRMED NON-FUNCTIONAL — MinimapSaveObject.Records.Pos:
  Exhaustive testing (Feb 25 2026, High Court Maze):
  - 300s bot exploration + full manual fog clearing + boss kill → pos_count
    always 1 (the map spawn point, set at first teleport into the map).
  - Map re-entry: still pos_count=1.  No flush on exit.
  - MinimapSaveObject.Records.Pos stores TELEPORT/SPAWN EVENTS only, not
    continuous movement positions.
  scan_from_minimap_records() is kept for reference but returns useless data.
  The ZoneWatcher still calls it a few times on entry as a legacy check.

CONFIRMED NON-FUNCTIONAL — EMapTaleCollisionPoint GObjects scan:
  Investigation confirmed (Feb 25 2026, in-map SDK dump of YJ_XieDuYuZuo200):
  EMapTaleCollisionPoint instances are NOT registered in GObjects even when inside
  a live map — only the CDO at world origin exists.  The NineGrid C++ subsystem
  manages wall collision entirely outside of UObject/GObjects.  scan_wall_actors()
  is kept for future reference but will always return 0 useful results.

POST-UPDATE NOTE — wall_data.json:
  The JSON cache is keyed by English map name and stores position arrays written
  by direct sampling.  The schema is identical whether the source was (old)
  MinimapSaveObject or (new) direct sampling: list of {x, y, z, r} dicts.
  No migration needed.  Delete old cached entries if they have very few points
  (e.g. 1–5) since those were from MinimapSaveObject spawn-only data.
"""

import json
import math
import os
import random
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from src.utils.logger import log
from src.utils.constants import (
    WALL_ACTOR_CLASS,
    WALL_DATA_FILE,
    WALL_POINT_DEFAULT_RADIUS,
    VISITED_CELL_WALKABLE_RADIUS,
    WALL_GRID_CELL_SIZE,
    WALL_GRID_HALF_SIZE,
    WALL_GRID_MARGIN,
    UE4_OFFSETS,
    WALL_CONF_DECAY_HALFLIFE_S,
    WALL_CONF_DECAY_MIN_STEP_S,
    WALL_CONF_WALKABLE_STRENGTH,
    WALL_CONF_BLOCKED_STRENGTH,
    WALL_CONF_PENALTY_MAX,
)

if TYPE_CHECKING:
    from src.core.scanner import UE4Scanner


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class WallPoint:
    """A single point used to mark cells in the A* grid.

    pt_type="walkable": marks circle WALKABLE in the inverted grid.
    pt_type="blocked": heavily marks cells BLOCKED overriding walkability (SLAM Triangulated Walls).
    The radius field controls how large the circle marking is in world units.
    """
    x: float
    y: float
    z: float = 0.0
    radius: float = WALL_POINT_DEFAULT_RADIUS
    pt_type: str = "walkable"  # "walkable" or "blocked"

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "z": self.z, "r": self.radius, "t": self.pt_type}

    @staticmethod
    def from_dict(d: dict) -> "WallPoint":
        # Handle legacy formatting automatically.
        pt = WallPoint(
            x=float(d["x"]),
            y=float(d["y"]),
            z=float(d.get("z", 0.0))
        )
        pt.pt_type = str(d.get("t", "walkable"))
        # If type is walkable, use the dynamic constant. Else preserve radius exactly.
        if pt.pt_type == "walkable":
            pt.radius = VISITED_CELL_WALKABLE_RADIUS
        else:
            pt.radius = float(d.get("r", WALL_POINT_DEFAULT_RADIUS))
        return pt


class GridData:
    """2D walkability grid for A* path planning.

    Grid layout:
        blocked[row][col]  where row = Y axis, col = X axis.
        True  = blocked (wall / unexplored).
        False = walkable (confirmed open floor).

    start_blocked controls the initial cell state:
        False (default) — all walkable; mark_circle_blocked() adds obstacles.
                          Used by legacy EMapTaleCollisionPoint approach.
        True            — all blocked; mark_circle_walkable() opens corridors.
                          Used by MinimapSaveObject approach (inverted grid).

    Out-of-bounds cells are always considered blocked (safe default for A*).
    """

    def __init__(self, min_x: float, min_y: float,
                 max_x: float, max_y: float,
                 cell_size: float = WALL_GRID_CELL_SIZE,
                 start_blocked: bool = False):
        self.min_x = min_x
        self.min_y = min_y
        self.cell_size = cell_size
        self.start_blocked = start_blocked

        self.cols: int = max(1, int(math.ceil((max_x - min_x) / cell_size)))
        self.rows: int = max(1, int(math.ceil((max_y - min_y) / cell_size)))

        self._data: List[bool] = [start_blocked] * (self.rows * self.cols)
        # Hybrid confidence overlay (v5.13.0): optional soft evidence for
        # walkability vs blockedness. Does NOT replace binary passability.
        self._walk_conf: List[float] = [0.0] * (self.rows * self.cols)
        self._block_conf: List[float] = [0.0] * (self.rows * self.cols)
        self._last_conf_decay_t: float = time.time()

    # ── Coordinate conversion ───────────────────────────────────────────────

    def world_to_grid(self, wx: float, wy: float) -> Tuple[int, int]:
        col = int((wx - self.min_x) / self.cell_size)
        row = int((wy - self.min_y) / self.cell_size)
        col = max(0, min(col, self.cols - 1))
        row = max(0, min(row, self.rows - 1))
        return row, col

    def grid_to_world(self, row: int, col: int) -> Tuple[float, float]:
        wx = self.min_x + col * self.cell_size + self.cell_size * 0.5
        wy = self.min_y + row * self.cell_size + self.cell_size * 0.5
        return wx, wy

    # ── Cell access ────────────────────────────────────────────────────────

    def is_blocked(self, row: int, col: int) -> bool:
        if row < 0 or row >= self.rows or col < 0 or col >= self.cols:
            return True
        return self._data[row * self.cols + col]

    def set_blocked(self, row: int, col: int, blocked: bool = True):
        if 0 <= row < self.rows and 0 <= col < self.cols:
            self._data[row * self.cols + col] = blocked

    def mark_circle_blocked(self, cx: float, cy: float, radius: float):
        """Mark all cells within radius as BLOCKED (wall-actor approach)."""
        self._mark_circle(cx, cy, radius, blocked=True)
        self.observe_blocked(cx, cy, radius, strength=WALL_CONF_BLOCKED_STRENGTH)

    def mark_circle_walkable(self, cx: float, cy: float, radius: float):
        """Mark all cells within radius as WALKABLE (minimap approach)."""
        self._mark_circle(cx, cy, radius, blocked=False)
        self.observe_walkable(cx, cy, radius, strength=WALL_CONF_WALKABLE_STRENGTH)

    def _mark_circle(self, cx: float, cy: float, radius: float, blocked: bool):
        cell_radius = int(math.ceil(radius / self.cell_size)) + 1
        center_row, center_col = self.world_to_grid(cx, cy)
        r2 = radius * radius
        for dr in range(-cell_radius, cell_radius + 1):
            for dc in range(-cell_radius, cell_radius + 1):
                row = center_row + dr
                col = center_col + dc
                if row < 0 or row >= self.rows or col < 0 or col >= self.cols:
                    continue
                wx, wy = self.grid_to_world(row, col)
                if (wx - cx) ** 2 + (wy - cy) ** 2 <= r2:
                    self.set_blocked(row, col, blocked)

    def clear_cell(self, row: int, col: int):
        self.set_blocked(row, col, False)

    def mark_rotated_box_blocked(self,
                                 cx: float,
                                 cy: float,
                                 extent_x: float,
                                 extent_y: float,
                                 yaw_deg: float,
                                 inflate_u: float = 0.0):
        """Rasterize a rotated rectangle into blocked cells.

        Used for NavModifierVolume priors decoded from runtime memory.
        The optional inflate_u expands extents to close small geometry gaps.
        """
        ex = max(1.0, float(extent_x) + max(0.0, float(inflate_u)))
        ey = max(1.0, float(extent_y) + max(0.0, float(inflate_u)))

        rad = math.radians(float(yaw_deg))
        cos_y = math.cos(rad)
        sin_y = math.sin(rad)

        # Axis-aligned bounding radius for candidate cell iteration.
        bound_r = math.hypot(ex, ey)
        cell_radius = int(math.ceil(bound_r / self.cell_size)) + 1
        center_row, center_col = self.world_to_grid(cx, cy)

        for dr in range(-cell_radius, cell_radius + 1):
            for dc in range(-cell_radius, cell_radius + 1):
                row = center_row + dr
                col = center_col + dc
                if row < 0 or row >= self.rows or col < 0 or col >= self.cols:
                    continue
                wx, wy = self.grid_to_world(row, col)
                dx = wx - cx
                dy = wy - cy
                # Inverse-rotate world delta into box-local frame.
                lx = dx * cos_y + dy * sin_y
                ly = -dx * sin_y + dy * cos_y
                if abs(lx) <= ex and abs(ly) <= ey:
                    self.set_blocked(row, col, True)

    # ── Hybrid confidence overlay ───────────────────────────────────────

    def _conf_decay(self) -> None:
        now = time.time()
        dt = now - self._last_conf_decay_t
        if dt < WALL_CONF_DECAY_MIN_STEP_S:
            return
        self._last_conf_decay_t = now
        # Exponential decay per elapsed seconds (half-life configured).
        decay = 0.5 ** (dt / max(1.0, WALL_CONF_DECAY_HALFLIFE_S))
        for i in range(len(self._walk_conf)):
            self._walk_conf[i] *= decay
            self._block_conf[i] *= decay

    def _iter_circle_cells(self, cx: float, cy: float, radius: float):
        cell_radius = int(math.ceil(radius / self.cell_size)) + 1
        center_row, center_col = self.world_to_grid(cx, cy)
        r2 = radius * radius
        for dr in range(-cell_radius, cell_radius + 1):
            for dc in range(-cell_radius, cell_radius + 1):
                row = center_row + dr
                col = center_col + dc
                if row < 0 or row >= self.rows or col < 0 or col >= self.cols:
                    continue
                wx, wy = self.grid_to_world(row, col)
                if (wx - cx) ** 2 + (wy - cy) ** 2 <= r2:
                    yield row, col

    def observe_walkable(self, cx: float, cy: float, radius: float,
                         strength: float = WALL_CONF_WALKABLE_STRENGTH) -> None:
        self._conf_decay()
        for row, col in self._iter_circle_cells(cx, cy, radius):
            idx = row * self.cols + col
            self._walk_conf[idx] += max(0.0, strength)

    def observe_blocked(self, cx: float, cy: float, radius: float,
                        strength: float = WALL_CONF_BLOCKED_STRENGTH) -> None:
        self._conf_decay()
        for row, col in self._iter_circle_cells(cx, cy, radius):
            idx = row * self.cols + col
            self._block_conf[idx] += max(0.0, strength)

    def get_hybrid_step_penalty(self, row: int, col: int) -> float:
        """Soft step penalty from confidence disagreement.

        Returned value is in [0, WALL_CONF_PENALTY_MAX].  Binary blocked/
        walkable checks still dominate passability; this only adjusts cost.
        """
        if row < 0 or row >= self.rows or col < 0 or col >= self.cols:
            return 0.0
        self._conf_decay()
        idx = row * self.cols + col
        walk = self._walk_conf[idx]
        block = self._block_conf[idx]
        # High blocked confidence relative to walk confidence => more penalty.
        imbalance = block - walk
        if imbalance <= 0.0:
            return 0.0
        return min(WALL_CONF_PENALTY_MAX, imbalance * 0.25)

    @property
    def walkable_count(self) -> int:
        return self._data.count(False)

    @property
    def blocked_count(self) -> int:
        return self._data.count(True)

    def get_frontier_world_positions(self,
                                     max_samples: int = 500,
                                     ) -> List[Tuple[float, float]]:
        """Return world (x, y) positions at the unexplored frontier.

        A frontier cell is a BLOCKED cell that has at least one WALKABLE
        neighbour.  These positions sit at the known boundary of the map — one
        cell beyond the last confirmed walkable tile.

        When the explorer navigates to a frontier target the character overshoots
        slightly across the boundary into unknown territory, and the PosSampler
        records those new positions.  This fills map gaps far more efficiently
        than random targeting.

        Returns an empty list when no walkable cells exist yet (all-blocked grid
        — the caller should fall back to pure random exploration in that case).

        If the total number of frontier cells exceeds max_samples the result is
        uniformly sub-sampled at random so downstream Maximin selection stays
        fast regardless of grid size.
        """
        if self.walkable_count == 0:
            return []

        dirs = [(-1, 0), (1, 0), (0, -1), (0, 1),
                (-1, -1), (-1, 1), (1, -1), (1, 1)]
        frontier: List[Tuple[float, float]] = []

        for row in range(self.rows):
            for col in range(self.cols):
                if not self.is_blocked(row, col):
                    continue  # only consider blocked cells
                # Keep only those adjacent to at least one walkable cell
                for dr, dc in dirs:
                    nr, nc = row + dr, col + dc
                    if 0 <= nr < self.rows and 0 <= nc < self.cols:
                        if not self.is_blocked(nr, nc):
                            wx, wy = self.grid_to_world(row, col)
                            frontier.append((wx, wy))
                            break  # one walkable neighbour is enough

        if len(frontier) <= max_samples:
            return frontier
        return random.sample(frontier, max_samples)

    def __repr__(self) -> str:
        mode = "inverted(visited→walkable)" if self.start_blocked else "normal(walls→blocked)"
        return (f"GridData({self.cols}×{self.rows} cells, "
                f"cell={self.cell_size:.0f}u, "
                f"walkable={self.walkable_count}/{self.rows * self.cols}, "
                f"mode={mode})")


# ── WallScanner ────────────────────────────────────────────────────────────────

class WallScanner:
    """Builds A* walkability grids for the 12 predefined TLI maps.

    PRIMARY PATH — MinimapSaveObject (pure memory reading, reliable):
        scan_from_minimap_records(map_name, raw_zone_name)
          → reads MinimapSaveObject.Records.Pos for current map via scanner
          → returns list of WallPoint (each = visited world position)
          → caller uses build_walkable_grid() to build an INVERTED GridData
             (all blocked → visited circles marked walkable)

    LEGACY PATH — EMapTaleCollisionPoint GObjects scan (non-functional):
        scan_wall_actors() is kept for reference but always returns empty —
        the NineGrid C++ subsystem manages these actors outside GObjects.

    Cache:
        wall_data.json is keyed by English map name and stores visited-position
        arrays.  Since map layouts are predefined/static, this JSON is permanent
        once collected — delete it only when testing a fresh scan.
    """

    # Shared lock for all wall_data.json read/modify/write operations.
    _WALL_DATA_LOCK = threading.RLock()

    def __init__(self, scanner: "UE4Scanner"):
        self._scanner = scanner
        self._memory = scanner._memory

    # ── MinimapSaveObject approach (primary) ────────────────────────────────

    def scan_from_minimap_records(self, map_name: str, raw_zone_name: str) -> List[WallPoint]:
        """Read visited world positions from MinimapSaveObject.Records for this map.

        This is the primary wall-detection path.  It reads MinimapSaveObject, a
        live GObjects singleton that persists visited-position data across all game
        sessions.  Since the 12 TLI maps never change layout, a single collection
        run permanently populates the cache.

        Parameters
        ----------
        map_name : str     English map name used as cache key ('Defiled Side Chamber').
        raw_zone_name : str  Internal FName string used as TMap lookup key ('YJ_XieDuYuZuo200').

        Returns list of WallPoint (one per visited position, radius=VISITED_CELL_WALKABLE_RADIUS).
        Returns empty list if MinimapSaveObject is unavailable or map has no history yet.
        """
        t0 = time.monotonic()
        log.info(f"[WallScan] Reading MinimapSaveObject.Records for '{map_name}' (zone='{raw_zone_name}')")

        positions = self._scanner.read_minimap_visited_positions(raw_zone_name)

        elapsed = time.monotonic() - t0

        if not positions:
            log.info(
                f"[WallScan] MinimapSaveObject returned 0 positions for '{raw_zone_name}' "
                f"in {elapsed:.2f}s — map not yet visited or GObjects/FNamePool not resolved"
            )
            return []

        log.info(
            f"[WallScan] MinimapSaveObject: {len(positions)} visited positions for "
            f"'{map_name}' in {elapsed:.2f}s — building walkable-area cache"
        )

        points = [
            WallPoint(x=px, y=py, radius=VISITED_CELL_WALKABLE_RADIUS)
            for px, py in positions
        ]
        return points

    @staticmethod
    def _projected_half_extent(ex: float,
                               ey: float,
                               yaw_deg: float,
                               dir_x: float,
                               dir_y: float) -> float:
        """Project an oriented rectangle half-extent onto world direction."""
        rad = math.radians(float(yaw_deg))
        ax_x = math.cos(rad)
        ax_y = math.sin(rad)
        ay_x = -ax_y
        ay_y = ax_x
        return (
            abs(dir_x * ax_x + dir_y * ax_y) * float(ex)
            + abs(dir_x * ay_x + dir_y * ay_y) * float(ey)
        )

    @staticmethod
    def compose_nav_collision_blockers(nav_collision_markers: Optional[List[dict]],
                                       inflate_u: float = 0.0,
                                       bridge_gap_u: float = 0.0,
                                       bridge_half_width_u: float = 0.0) -> Tuple[List[Dict[str, Any]], int]:
        """Build final nav blockers from raw markers.

        Output includes:
        - source='raw' markers (optionally inflated extents)
        - source='bridge' markers joining small pairwise gaps between boxes
        """
        raw_inflate = max(0.0, float(inflate_u))
        max_gap = max(0.0, float(bridge_gap_u))
        bridge_half_width = max(10.0, float(bridge_half_width_u))

        cleaned: List[Dict[str, Any]] = []
        for marker in (nav_collision_markers or []):
            try:
                area_class = str(marker.get("area_class", "") or "").lower()
                if "portal" in area_class:
                    continue
                mx = float(marker.get("x", 0.0))
                my = float(marker.get("y", 0.0))
                ex = float(marker.get("extent_x", 0.0))
                ey = float(marker.get("extent_y", 0.0))
                yaw = float(marker.get("yaw", 0.0))
                if ex < 1.0 or ey < 1.0:
                    continue
                cleaned.append({
                    "x": mx,
                    "y": my,
                    "extent_x": max(1.0, ex + raw_inflate),
                    "extent_y": max(1.0, ey + raw_inflate),
                    "yaw": yaw,
                    "area_class": area_class,
                    "source": "raw",
                })
            except Exception:
                continue

        if not cleaned:
            return [], 0

        blockers: List[Dict[str, Any]] = list(cleaned)
        if max_gap <= 0.0 or len(cleaned) < 2:
            return blockers, 0

        bucket_size = max(150.0, max_gap * 1.5)
        buckets: Dict[Tuple[int, int], List[int]] = {}
        for i, marker in enumerate(cleaned):
            bx = int(math.floor(marker["x"] / bucket_size))
            by = int(math.floor(marker["y"] / bucket_size))
            buckets.setdefault((bx, by), []).append(i)

        bridge_count = 0
        seen_pairs: Set[Tuple[int, int]] = set()
        for i, a in enumerate(cleaned):
            abx = int(math.floor(a["x"] / bucket_size))
            aby = int(math.floor(a["y"] / bucket_size))
            for nx in (abx - 1, abx, abx + 1):
                for ny in (aby - 1, aby, aby + 1):
                    for j in buckets.get((nx, ny), []):
                        if j <= i:
                            continue
                        pair = (i, j)
                        if pair in seen_pairs:
                            continue
                        seen_pairs.add(pair)

                        b = cleaned[j]
                        dx = b["x"] - a["x"]
                        dy = b["y"] - a["y"]
                        dist = math.hypot(dx, dy)
                        if dist < 1e-3:
                            continue

                        dir_x = dx / dist
                        dir_y = dy / dist

                        reach_a = WallScanner._projected_half_extent(
                            a["extent_x"], a["extent_y"], a["yaw"], dir_x, dir_y
                        )
                        reach_b = WallScanner._projected_half_extent(
                            b["extent_x"], b["extent_y"], b["yaw"], -dir_x, -dir_y
                        )

                        gap = dist - reach_a - reach_b
                        if gap <= 1.0 or gap > max_gap:
                            continue

                        sx = a["x"] + dir_x * reach_a
                        sy = a["y"] + dir_y * reach_a
                        ex2 = b["x"] - dir_x * reach_b
                        ey2 = b["y"] - dir_y * reach_b

                        cx = (sx + ex2) * 0.5
                        cy = (sy + ey2) * 0.5
                        yaw = math.degrees(math.atan2(dir_y, dir_x))

                        blockers.append({
                            "x": cx,
                            "y": cy,
                            "extent_x": max(1.0, gap * 0.5),
                            "extent_y": bridge_half_width,
                            "yaw": yaw,
                            "area_class": a.get("area_class", "") or b.get("area_class", ""),
                            "source": "bridge",
                        })
                        bridge_count += 1

        return blockers, bridge_count

    def build_walkable_grid(self, visited_points: List[WallPoint],
                             center_x: float, center_y: float,
                             half_size: float = WALL_GRID_HALF_SIZE,
                             cell_size: float = WALL_GRID_CELL_SIZE,
                             apply_blocked_points: bool = False,
                             nav_collision_markers: Optional[List[dict]] = None,
                             nav_collision_inflate_u: float = 0.0,
                             nav_collision_bridge_gap_u: float = 0.0,
                             nav_collision_bridge_half_width_u: float = 0.0,
                             nav_collision_min_raw_priors: int = 20,
                             nav_collision_min_coverage_ratio: float = 0.02,
                             log_summary: bool = True) -> GridData:
        """Build an INVERTED walkability grid from visited-position points.

        Starts with all cells BLOCKED.  Each visited position marks a circle of
        radius WallPoint.radius as WALKABLE.  The A* planner then routes only
        through cells the player has actually visited in previous runs.

        When visited_points is non-empty the grid bounds are derived from the
        data itself (bounding box of all points + WALL_GRID_MARGIN on every
        side).  This guarantees that every sampled position — even those far
        from the map spawn — is fully contained in the grid, regardless of map
        layout or spawn location.

        When visited_points is empty (first run, no history) the fallback
        center_x/center_y ± half_size bounds are used so the caller can still
        build an empty grid for the direct-navigation fallback.
        """
        t0 = time.monotonic()

        if visited_points:
            # Data-driven bounds: cover the full extent of all points
            # plus a margin so edge circles are not clipped.
            margin = WALL_GRID_MARGIN
            min_x = min(pt.x for pt in visited_points) - margin
            min_y = min(pt.y for pt in visited_points) - margin
            max_x = max(pt.x for pt in visited_points) + margin
            max_y = max(pt.y for pt in visited_points) + margin
        else:
            min_x = center_x - half_size
            min_y = center_y - half_size
            max_x = center_x + half_size
            max_y = center_y + half_size

        # start_blocked=True: all cells are blocked by default; visited circles open them
        grid = GridData(min_x, min_y, max_x, max_y, cell_size, start_blocked=True)

        walkable_pts = [pt for pt in visited_points if pt.pt_type == "walkable"]
        for pt in walkable_pts:
            grid.mark_circle_walkable(pt.x, pt.y, pt.radius)

        blocked_pts = [pt for pt in visited_points if pt.pt_type == "blocked"]
        # NOTE (v5.8.0): persisted SLAM blocked points proved too aggressive and
        # could sever narrow corridors, leading to immediate "A* found no path"
        # loops from map start. Keep blocked points in cache for forensics, but
        # do NOT apply them by default when constructing the production grid.
        #
        # apply_blocked_points=True is reserved for explicitly curated priors
        # (e.g. offline atlas) where blocked confidence has already been filtered.
        if apply_blocked_points:
            for pt in blocked_pts:
                grid.mark_circle_blocked(pt.x, pt.y, pt.radius)

        # Runtime NavModifierVolume priors: apply raw decode markers directly.
        # Nav collision is treated as authoritative blocked geometry.
        nav_raw_count = 0
        for marker in (nav_collision_markers or []):
            try:
                mx = float(marker.get("x", 0.0))
                my = float(marker.get("y", 0.0))
                ex = float(marker.get("extent_x", 0.0))
                ey = float(marker.get("extent_y", 0.0))
                yaw = float(marker.get("yaw", 0.0))
                if ex < 1.0 or ey < 1.0:
                    continue
                grid.mark_rotated_box_blocked(
                    mx,
                    my,
                    ex,
                    ey,
                    yaw,
                    inflate_u=0.0,
                )
                nav_raw_count += 1
            except Exception:
                continue

        if log_summary:
            elapsed = time.monotonic() - t0
            log.info(
                f"[WallScan] Walkable grid built from {len(walkable_pts)} Walkable, "
                f"{len(blocked_pts)} Blocked points ({'applied' if apply_blocked_points else 'ignored'}) "
                f"+ {nav_raw_count} nav-collision priors "
                f"in {elapsed:.3f}s: {grid}"
            )
        return grid

    # ── Legacy: EMapTaleCollisionPoint GObjects scan ─────────────────────────

    def scan_wall_actors(self) -> List[WallPoint]:
        """[LEGACY / NON-FUNCTIONAL] Enumerate GObjects for EMapTaleCollisionPoint.

        Confirmed non-functional (Feb 25 2026, in-map dump of YJ_XieDuYuZuo200):
        EMapTaleCollisionPoint instances are NOT registered in GObjects when in a
        live map — only the CDO at world-origin exists.  The NineGrid C++ subsystem
        manages these actors outside of UObject/GObjects entirely.

        This method is retained for diagnostic/reference purposes.  It will always
        return an empty list (after filtering the origin CDO).
        """
        gobjects  = self._scanner._gobjects_addr
        fnamepool = self._scanner._fnamepool_addr

        if not gobjects or not fnamepool:
            log.warning("[WallScan] GObjects/FNamePool not resolved — cannot run legacy wall scan")
            return []

        log.debug(
            f"[WallScan] [Legacy] Scanning GObjects for '{WALL_ACTOR_CLASS}' "
            f"(GObjects=0x{gobjects:X}, FNamePool=0x{fnamepool:X})"
        )

        actors = self._memory.find_gobjects_by_class_name(
            gobjects, fnamepool, WALL_ACTOR_CLASS
        )

        if not actors:
            log.info(f"[WallScan] [Legacy] No '{WALL_ACTOR_CLASS}' objects found in GObjects "
                     f"— confirmed: NineGrid manages walls outside GObjects")
            return []

        log.info(
            f"[WallScan] [Legacy] Found {len(actors)} '{WALL_ACTOR_CLASS}' object(s) in GObjects "
            f"— expected only CDO at origin; these will be filtered"
        )

        walls: List[WallPoint] = []
        root_offset = UE4_OFFSETS["RootComponent"]
        loc_offset  = UE4_OFFSETS["RelativeLocation"]

        for actor_ptr, inst_name in actors:
            root_comp = self._read_ptr(actor_ptr + root_offset)
            if not root_comp:
                log.debug(f"[WallScan] [Legacy]   0x{actor_ptr:X} ('{inst_name}'): RootComponent null — skip")
                continue

            pos_data = self._memory.read_bytes(root_comp + loc_offset, 12)
            if not pos_data or len(pos_data) < 12:
                log.debug(f"[WallScan] [Legacy]   0x{actor_ptr:X} ('{inst_name}'): failed to read position — skip")
                continue

            x, y, z = struct.unpack_from("<fff", pos_data)

            if abs(x) < 1.0 and abs(y) < 1.0:
                log.debug(
                    f"[WallScan] [Legacy]   0x{actor_ptr:X} ('{inst_name}'): "
                    f"position=(0,0,{z:.1f}) — CDO / unspawned, filtered out"
                )
                continue

            log.debug(f"[WallScan] [Legacy]   0x{actor_ptr:X} ('{inst_name}'): pos=({x:.0f},{y:.0f},{z:.0f})")
            walls.append(WallPoint(x=x, y=y, z=z))

        log.info(
            f"[WallScan] [Legacy] Collected {len(walls)} wall actors "
            f"({len(actors) - len(walls)} filtered at origin — CDO/unspawned)"
        )
        return walls

    def build_grid(self, walls: List[WallPoint],
                   center_x: float, center_y: float,
                   half_size: float = WALL_GRID_HALF_SIZE,
                   cell_size: float = WALL_GRID_CELL_SIZE) -> GridData:
        """Build a NORMAL walkability grid from wall-actor positions (legacy path).

        Starts with all cells WALKABLE; each wall actor marks a circle as BLOCKED.
        """
        min_x = center_x - half_size
        min_y = center_y - half_size
        max_x = center_x + half_size
        max_y = center_y + half_size

        grid = GridData(min_x, min_y, max_x, max_y, cell_size, start_blocked=False)

        for w in walls:
            grid.mark_circle_blocked(w.x, w.y, w.radius)

        log.info(f"[WallScan] [Legacy] Grid built: {grid} | walls={len(walls)} | "
                 f"center=({center_x:.0f},{center_y:.0f})")
        return grid

    # ── Persistence ─────────────────────────────────────────────────────────

    def has_wall_data(self, map_name: str) -> bool:
        data = self._load_json()
        return map_name in data and len(data[map_name]) > 0

    def save_wall_data(self, map_name: str, walls: List[WallPoint]) -> bool:
        if not walls:
            return False
        data = self._load_json()
        data[map_name] = [w.to_dict() for w in walls]
        return self._save_json(data)

    def load_wall_data(self, map_name: str) -> Optional[List[WallPoint]]:
        data = self._load_json()
        raw = data.get(map_name)
        if not raw:
            return None
        try:
            return [WallPoint.from_dict(d) for d in raw]
        except Exception as e:
            log.warning(f"[WallScan] Failed to parse cached data for '{map_name}': {e}")
            return None

    def delete_wall_data(self, map_name: str) -> bool:
        data = self._load_json()
        if map_name in data:
            del data[map_name]
            self._save_json(data)
            log.info(f"[WallScan] Deleted walkable-area cache for '{map_name}'")
            return True
        return False

    def get_cached_maps(self) -> List[str]:
        return list(self._load_json().keys())

    # ── Internals ───────────────────────────────────────────────────────────

    def _read_ptr(self, addr: int) -> Optional[int]:
        val = self._memory.read_value(addr, "ulong")
        if val and 0x10000 < val < 0x7FFFFFFFFFFF:
            return val
        return None

    @staticmethod
    def _load_json() -> dict:
        with WallScanner._WALL_DATA_LOCK:
            os.makedirs(os.path.dirname(WALL_DATA_FILE), exist_ok=True)
            if not os.path.exists(WALL_DATA_FILE):
                return {}
            try:
                with open(WALL_DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}

    @staticmethod
    def _save_json(data: dict) -> bool:
        with WallScanner._WALL_DATA_LOCK:
            os.makedirs(os.path.dirname(WALL_DATA_FILE), exist_ok=True)
            temp_path = f"{WALL_DATA_FILE}.tmp"
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(temp_path, WALL_DATA_FILE)
                return True
            except Exception as e:
                log.error(f"[WallScan] Failed to save walkable-area cache: {e}")
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass
                return False
