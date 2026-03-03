"""pathfinder.py — A* pathfinding on a WallScanner GridData.

Implements:
  • A* search with an octile-distance heuristic (optimal for 8-directional movement).
  • DDA (Digital Differential Analyser) ray-cast path smoothing — same approach
    the game engine's own EWayfindingResult::E_success_dda uses — to remove
    redundant intermediate waypoints.
  • Nearest-walkable-cell fallback: if start or goal is inside a blocked cell,
    find the nearest unblocked cell before running A* so navigating near walls
    never produces an instant failure.

Performance notes:
  At WALL_GRID_CELL_SIZE = 75 a typical ±15 000-unit map gives a 400×400 grid
  (160 000 cells).  A* on 160 000 cells in pure Python with heapq typically
  completes in 100–500 ms — acceptable when run on the async replan worker
  thread (never blocks the 120 Hz tick loop).  The AUTO_NAV_ASTAR_MAX_NODES
  cap (default 200 000) prevents runaway computation on pathological inputs.
"""

import heapq
import math
from typing import List, Optional, Tuple

from src.utils.logger import log
from src.utils.constants import (
    WALL_GRID_CELL_SIZE,
    AUTO_NAV_ASTAR_MAX_NODES,
)
from src.core.wall_scanner import GridData


# ── Pathfinder ─────────────────────────────────────────────────────────────────

class Pathfinder:
    """A* path planner that operates on a GridData walkability grid.

    Typical usage:
        pf = Pathfinder()
        pf.set_grid(grid_data)
        path = pf.find_path(player_x, player_y, goal_x, goal_y)
        if path:
            waypoints = [Waypoint(x=px, y=py) for px, py in path]
    """

    def __init__(self):
        self._grid: Optional[GridData] = None
        self._wall_model_mode: str = "legacy"
        # Soft-avoidance zones: list of (world_x, world_y, radius_u, penalty)
        # Applied during A* as extra per-step cost for cells inside the zone.
        # Used to steer around live event platforms the bot is NOT navigating to
        # (e.g. avoid walking through the Sandlord activation zone en route to
        # Carjack / kill-all clusters so the event never fires prematurely).
        self._avoid_zones: list = []

    def set_wall_model_mode(self, mode: str) -> None:
        mode_norm = (mode or "legacy").strip().lower()
        self._wall_model_mode = mode_norm if mode_norm in {"legacy", "hybrid"} else "legacy"
        log.info(f"[Pathfinder] Wall model mode: {self._wall_model_mode}")

    # ── Public API ──────────────────────────────────────────────────────────

    def set_grid(self, grid: GridData):
        """Install the walkability grid for the current map."""
        self._grid = grid
        log.info(f"[Pathfinder] Grid installed: {grid}")

    def clear_grid(self):
        """Release the current grid (called on zone transition)."""
        self._grid = None
        self._avoid_zones = []

    def set_avoid_zones(self, zones: list) -> None:
        """Set soft-avoidance zones for the next find_path call(s).

        Each zone is a 4-tuple (world_x, world_y, radius_u, penalty) where
        *penalty* is added to the A* step cost for every cell inside the zone.
        A penalty of 30 makes A* route well clear of even a 400-unit radius zone
        while keeping the zone traversable when there is no other path.
        Call clear_avoid_zones() when the zones are no longer needed.
        """
        self._avoid_zones = list(zones)

    def clear_avoid_zones(self) -> None:
        """Remove all soft-avoidance zones."""
        self._avoid_zones = []

    @property
    def has_grid(self) -> bool:
        return self._grid is not None

    def find_path(self,
                  start_wx: float, start_wy: float,
                  goal_wx: float,  goal_wy: float,
                  max_nodes: int = AUTO_NAV_ASTAR_MAX_NODES,
                  ) -> Optional[List[Tuple[float, float]]]:
        """Return a smoothed path from (start_wx, start_wy) to (goal_wx, goal_wy).

        Coordinates are in game world units.  Returns a list of (x, y) tuples
        representing waypoints including start and goal, or None if no path
        could be found within max_nodes expansions.

        If no grid has been loaded, returns a direct two-point path so the
        navigator can still steer (it just won't avoid walls).
        """
        if self._grid is None:
            # No wall data — return straight-line path
            return [(start_wx, start_wy), (goal_wx, goal_wy)]

        grid = self._grid
        start_row, start_col = grid.world_to_grid(start_wx, start_wy)
        goal_row,  goal_col  = grid.world_to_grid(goal_wx,  goal_wy)

        # Unblock start/goal so we can always enter/exit (player/goal might be
        # right next to a wall whose obstacle radius spills into their cell).
        orig_start_blocked = grid.is_blocked(start_row, start_col)
        orig_goal_blocked  = grid.is_blocked(goal_row,  goal_col)
        if orig_start_blocked:
            start_row, start_col = self._nearest_walkable(start_row, start_col)
            if start_row is None:
                log.warning("[Pathfinder] Cannot find walkable start cell")
                return None
        if orig_goal_blocked:
            goal_row, goal_col = self._nearest_walkable(goal_row, goal_col)
            if goal_row is None:
                log.warning("[Pathfinder] Cannot find walkable goal cell")
                return None

        # Trivial case: start == goal
        if (start_row, start_col) == (goal_row, goal_col):
            return [(goal_wx, goal_wy)]

        # Build per-cell extra-penalty map from soft-avoidance zones.
        # Cells inside a zone get an additional A* cost equal to the zone's
        # penalty.  The goal cell itself is always exempt so the bot can still
        # navigate directly to an event platform when intended.
        zone_penalties: dict = {}
        if self._avoid_zones:
            for zx, zy, zr, zpen in self._avoid_zones:
                cr, cc = grid.world_to_grid(zx, zy)
                r_cells = int(math.ceil(zr / WALL_GRID_CELL_SIZE)) + 1
                for dr in range(-r_cells, r_cells + 1):
                    for dc in range(-r_cells, r_cells + 1):
                        nr2, nc2 = cr + dr, cc + dc
                        if (nr2, nc2) == (goal_row, goal_col):
                            continue  # never penalise the goal itself
                        if math.hypot(dr, dc) * WALL_GRID_CELL_SIZE <= zr:
                            existing = zone_penalties.get((nr2, nc2), 0.0)
                            zone_penalties[(nr2, nc2)] = max(existing, zpen)

        grid_path = self._astar(start_row, start_col,
                                goal_row,  goal_col,
                                max_nodes, zone_penalties)
        if grid_path is None:
            log.warning(f"[Pathfinder] A* found no path "
                        f"({start_wx:.0f},{start_wy:.0f}) → "
                        f"({goal_wx:.0f},{goal_wy:.0f})")
            return None

        smoothed = self._smooth_path(grid_path)

        # Convert grid cells back to world coordinates; keep the exact goal pos.
        world_path: List[Tuple[float, float]] = []
        for r, c in smoothed:
            wx, wy = grid.grid_to_world(r, c)
            world_path.append((wx, wy))

        # Replace last waypoint with the precise goal world position
        if world_path:
            world_path[-1] = (goal_wx, goal_wy)

        log.debug(f"[Pathfinder] Path: {len(grid_path)} cells → "
                  f"{len(world_path)} waypoints after smoothing")
        return world_path

    # ── A* core ────────────────────────────────────────────────────────────

    # 8-directional move costs
    _DIRS = [(-1,-1), (-1,0), (-1,1),
             ( 0,-1),         ( 0,1),
             ( 1,-1), ( 1,0), ( 1,1)]
    _COSTS = [1.4142, 1.0, 1.4142,
              1.0,        1.0,
              1.4142, 1.0, 1.4142]

    def _heuristic(self, r: int, c: int, gr: int, gc: int) -> float:
        """Octile distance — admissible heuristic for 8-directional grid."""
        dr = abs(r - gr)
        dc = abs(c - gc)
        return max(dr, dc) + (1.4142 - 1.0) * min(dr, dc)

    def _astar(self,
               start_r: int, start_c: int,
               goal_r:  int, goal_c:  int,
               max_nodes: int,
               zone_penalties: dict = {},
               ) -> Optional[List[Tuple[int, int]]]:
        """Return list of (row, col) from start to goal, or None.

        Three-tier wall-clearance penalty:
          Tier 1 (distance-1): any neighbour is blocked → +8.0 per step.
          Tier 2 (distance-2): blocked cell within 2 cells → +2.0 per step.
          Tier 3 (distance-3): blocked cell within 3 cells → +0.5 per step.
        At 75 u/cell: tier 1 = within 75 u, tier 2 = 150 u, tier 3 = 225 u.
        In open areas A* naturally routes 3+ cells (225 u+) from walls.
        Narrow corridors remain traversable — the penalty makes them expensive,
        not impassable, so they are still used when no wider route exists.
        """
        grid = self._grid
        start = (start_r, start_c)
        goal  = (goal_r,  goal_c)

        # heap element: (f_score, g_score, row, col)
        open_heap: List = []
        heapq.heappush(open_heap, (self._heuristic(*start, *goal), 0.0, start_r, start_c))

        came_from: dict = {}
        g_score: dict = {start: 0.0}
        nodes_expanded = 0

        while open_heap:
            if nodes_expanded >= max_nodes:
                log.warning(f"[Pathfinder] A* node limit ({max_nodes}) reached")
                return None

            f, g, r, c = heapq.heappop(open_heap)
            nodes_expanded += 1

            if (r, c) == goal:
                # Reconstruct path
                path = []
                cur = goal
                while cur in came_from:
                    path.append(cur)
                    cur = came_from[cur]
                path.append(start)
                path.reverse()
                return path

            # Skip stale heap entries
            if g > g_score.get((r, c), math.inf):
                continue

            for (dr, dc), move_cost in zip(self._DIRS, self._COSTS):
                nr, nc = r + dr, c + dc
                if grid.is_blocked(nr, nc):
                    continue

                # Three-tier wall-proximity penalty.
                # Tier 1 — cell is directly adjacent (distance 1) to a blocked
                #   cell: strong penalty so A* avoids these unless forced.
                # Tier 2 — blocked cell is within 2 cells (diagonal distance):
                #   modest penalty keeps the path away from walls in open areas.
                # Tier 3 — blocked cell is within 3 cells: gentle nudge toward
                #   corridor centers. Only applied in genuinely open areas where
                #   tiers 1 & 2 didn't fire.
                # No tier makes the cell impassable — narrow corridors
                # (exactly 1-2 cells wide) remain reachable, just at higher cost.
                wall_pen = 0.0
                _hit1 = False
                for wdr in (-1, 0, 1):
                    for wdc in (-1, 0, 1):
                        if wdr == 0 and wdc == 0:
                            continue
                        if grid.is_blocked(nr + wdr, nc + wdc):
                            wall_pen = 4.0  # tier-1: distance-1 to wall
                            _hit1 = True
                            break
                    if _hit1:
                        break
                if not _hit1:
                    _hit2 = False
                    # Check distance-2 shell (5×5 minus inner 3×3)
                    for wdr in range(-2, 3):
                        for wdc in range(-2, 3):
                            if -1 <= wdr <= 1 and -1 <= wdc <= 1:
                                continue  # already checked in tier-1
                            if grid.is_blocked(nr + wdr, nc + wdc):
                                wall_pen = 1.0  # tier-2: distance-2 to wall
                                _hit2 = True
                                break
                        if _hit2:
                            break
                    if not _hit2:
                        # Check distance-3 shell (7×7 minus inner 5×5)
                        for wdr in range(-3, 4):
                            for wdc in range(-3, 4):
                                if -2 <= wdr <= 2 and -2 <= wdc <= 2:
                                    continue  # already checked in tiers 1-2
                                if grid.is_blocked(nr + wdr, nc + wdc):
                                    wall_pen = 0.5  # tier-3: distance-3 to wall
                                    break
                            if wall_pen:
                                break

                hybrid_pen = 0.0
                if self._wall_model_mode == "hybrid":
                    try:
                        hybrid_pen = grid.get_hybrid_step_penalty(nr, nc)
                    except Exception:
                        hybrid_pen = 0.0

                new_g = g + move_cost + wall_pen + hybrid_pen + zone_penalties.get((nr, nc), 0.0)
                if new_g < g_score.get((nr, nc), math.inf):
                    g_score[(nr, nc)] = new_g
                    came_from[(nr, nc)] = (r, c)
                    f_new = new_g + self._heuristic(nr, nc, goal_r, goal_c)
                    heapq.heappush(open_heap, (f_new, new_g, nr, nc))

        return None  # no path found

    # ── DDA path smoothing ──────────────────────────────────────────────────

    def _line_clear(self, r0: int, c0: int, r1: int, c1: int) -> bool:
        """Return True if the straight line from (r0,c0) to (r1,c1) passes
        only through walkable (non-blocked) cells.

        Uses a standard Bresenham line scan.  n = max(dr, dc)+1 cells are
        visited — the minimum set that covers the straight-line path.
        """
        grid = self._grid
        dr = abs(r1 - r0)
        dc = abs(c1 - c0)
        r, c = r0, c0
        r_inc = 1 if r1 > r0 else -1
        c_inc = 1 if c1 > c0 else -1
        # n = max(dr, dc) + 1 is the correct cell count for a Bresenham line.
        n = max(dr, dc) + 1
        err = dr - dc

        for i in range(n):
            if i > 0 and grid.is_blocked(r, c):
                return False
            if err > 0:
                r += r_inc
                err -= dc * 2
            elif err < 0:
                c += c_inc
                err += dr * 2
            else:
                # Exact diagonal step — advance both axes
                r += r_inc
                c += c_inc
                # err stays 0; no adjustment needed for equal diagonal steps

        return True

    def _smooth_path(self, path: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        """Remove redundant waypoints using DDA ray-cast visibility checks.

        Uses the correct greedy forward scan: from the current anchor point,
        scan FORWARD (not binary search) to the furthest visible cell.

        Binary search is incorrect here because LOS is NOT monotone on an
        arbitrary grid: it is possible that cell[mid] is visible from cell[i]
        while cell[mid-k] (closer to i on the path) is NOT visible, because
        the path curves around a wall.  Greedy forward scan is O(n^2) worst
        case but always produces a geometrically correct smoothed path.
        """
        if len(path) <= 2:
            return path

        smoothed = [path[0]]
        anchor = 0
        while anchor < len(path) - 1:
            # Scan forward from anchor to find the furthest visible cell
            best = anchor + 1
            for j in range(anchor + 2, len(path)):
                if self._line_clear(path[anchor][0], path[anchor][1],
                                    path[j][0],      path[j][1]):
                    best = j
                # Keep going — we want the FURTHEST visible, not first blocked
            smoothed.append(path[best])
            anchor = best

        return smoothed

    # ── Nearest-walkable fallback ───────────────────────────────────────────

    def _nearest_walkable(self, row: int, col: int,
                          search_radius: int = 20) -> Tuple[Optional[int], Optional[int]]:
        """Return the nearest walkable cell to (row, col) within search_radius.

        Searches in expanding rings.  Returns (None, None) if none found.
        """
        grid = self._grid
        if not grid.is_blocked(row, col):
            return row, col

        for r in range(1, search_radius + 1):
            for dr in range(-r, r + 1):
                for dc in range(-r, r + 1):
                    if abs(dr) != r and abs(dc) != r:
                        continue  # only perimeter of the ring
                    nr, nc = row + dr, col + dc
                    if not grid.is_blocked(nr, nc):
                        return nr, nc

        return None, None
