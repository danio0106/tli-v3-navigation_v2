"""map_explorer.py — Automatic walkable-area data collection via RTNavigator.

The MapExplorer drives the player character through frontier and random
positions using the RTNavigator's 120 Hz A*-pathed steering loop.  This
replaces the old Navigator-based exploration which used slow 20 Hz direct-
line steering without path planning.

Architecture
------------
RTNavigator owns the real-time steering.  MapExplorer owns the *target
selection loop*: it picks the next exploration target (frontier-guided or
random Maximin), calls ``rt_nav.navigate_to_target(tx, ty, ...)``, evaluates
the result, picks the next target, and repeats until done.

A background *position sampler* thread runs concurrently and records player
position samples to ``wall_data.json`` via WallScanner, building the
walkable grid data for future A* use.

Right-click movement contract
------------------------------
RTNavigator.start() issues exactly ONE right-click to engage cursor-follow
mode.  MapExplorer must NOT issue any right-clicks — it delegates all
movement to RTNavigator.

The main bot loop (BotEngine._running) must NOT be active while the
explorer runs, because the bot already owns that right-click.
BotEngine.start_map_explorer() enforces this with an early-return guard.
"""

import math
import random
import time
import threading
from typing import Callable, List, Optional, Tuple, TYPE_CHECKING

from src.utils.logger import log
from src.core.navigation import NavigationTask, TaskNavigator
from src.utils.constants import (
    MAP_EXPLORER_RADIUS,
    MAP_EXPLORER_TARGET_TIMEOUT_S,
    MAP_EXPLORER_DURATION_S,
    MAP_EXPLORER_FRONTIER_REFRESH_S,
    MAP_EXPLORER_GRID_REBUILD_S,
    MAP_EXPLORER_COMPLETE_STABLE_S,
    MAP_EXPLORER_COMPLETE_MIN_GAIN,
    MAP_EXPLORER_FRONTIER_ESTIMATE_MULTIPLIER,
    MAP_EXPLORER_GLOBAL_STUCK_DIST,
    MAP_EXPLORER_GLOBAL_STUCK_TIME,
    MAP_EXPLORER_CANDIDATES,
    MAP_EXPLORER_POSITION_SAMPLE_DIST,
    MAP_EXPLORER_POSITION_POLL_S,
    MAP_EXPLORER_POSITION_FLUSH_EVERY,
    MAP_EXPLORER_POSITION_FLUSH_S,
    MAP_EXPLORER_NO_PROGRESS_TIMEOUT_S,
    MAP_EXPLORER_NO_PROGRESS_DIST,
    VISITED_CELL_WALKABLE_RADIUS,
    WALL_GRID_HALF_SIZE,
    WALL_GRID_CELL_SIZE,
)

if TYPE_CHECKING:
    from src.core.rt_navigator import RTNavigator
    from src.core.pathfinder import Pathfinder


# ── MapExplorer ────────────────────────────────────────────────────────────────

class MapExplorer:
    """RTNavigator-powered map explorer for automatic walkable-area data collection.

    Uses the 120 Hz A*-pathed RTNavigator loop for all movement — dramatically
    faster than the old Navigator's 20 Hz direct-line steering.

    When an existing walkable grid is available (via the optional *pathfinder*
    argument) the explorer uses **frontier-guided** mode: it navigates to cells
    at the boundary of the known walkable area, filling gaps efficiently.
    With no grid (first run, no prior data) it falls back to random Maximin
    targeting automatically.

    Parameters
    ----------
    rt_navigator : RTNavigator instance — owns the 120 Hz steering loop.
    pos_poller   : PositionPoller — shared 120 Hz position reader.
    duration_s   : Total exploration time in seconds (None = completion-driven).
    map_name     : English map name used as wall_data.json cache key.
    progress_cb  : Optional callable for GUI progress updates.
    pathfinder   : Optional Pathfinder holding the current map's GridData.
    """

    _FAILED_TARGET_COOLDOWN_S = 15.0
    _FAILED_TARGET_RADIUS = 600.0

    def __init__(self,
                 rt_navigator: "RTNavigator",
                 pos_poller,
                 duration_s: Optional[float] = None,
                 map_name: str = "",
                 progress_cb: Optional[Callable] = None,
                 pathfinder: Optional["Pathfinder"] = None):
        self._rt_nav     = rt_navigator
        self._task_nav   = TaskNavigator(rt_navigator)
        self._pos_poller = pos_poller
        self._duration   = duration_s
        self._map_name   = map_name
        self._progress   = progress_cb
        self._pathfinder = pathfinder
        self._cancelled  = False
        self._lock       = threading.Lock()

        # Stats exposed for the GUI
        self.targets_attempted: int = 0
        self.elapsed_s: float       = 0.0

        # History of targets attempted this session — used by _pick_target() to
        # bias new picks away from already-visited areas (Maximin strategy).
        self._previous_targets: list = []

        # Frontier positions computed once at run() start from the existing grid.
        # Empty list → no grid or grid is all-blocked → pure random Maximin used.
        self._frontier: List[Tuple[float, float]] = []
        self._frontier_last_refresh_s: float = 0.0  # last frontier-only rescan
        self._grid_rebuild_last_s: float = 0.0       # last full disk-read + grid rebuild

        # Position sampler state
        self._sampler_last_pos: Optional[Tuple[float, float]] = None
        self._last_good_pos: Optional[Tuple[float, float]] = None
        self._failed_targets: List[Tuple[float, float, float]] = []  # (x, y, expiry_ts)

        # Real-time character speed estimate (world units/second), updated by the
        # background position sampler.  Used to compute per-target nav timeouts.
        self._speed_ups: float = 0.0

        # Live coverage/estimate telemetry
        self.coverage_percent: float = 0.0
        self.covered_points: int = 0
        self.estimated_total_points: int = 0
        self.frontier_count: int = 0

    # ── Public API ──────────────────────────────────────────────────────────

    def run(self, cancel_fn: Optional[Callable[[], bool]] = None) -> int:
        """Run the exploration loop.

        Blocks the calling thread until duration expires or cancel() is called.
        Returns the number of targets attempted.

        RTNavigator.start() is called automatically and issues the single
        right-click needed for cursor-follow mode.  On exit, RTNavigator.stop()
        freezes the character.
        """
        with self._lock:
            self._cancelled = False

        start_time = time.time()
        if self._duration is None:
            duration_mode = "completion"
            deadline = None
        else:
            duration_mode = "timed"
            duration_s = self._duration if self._duration > 0 else MAP_EXPLORER_DURATION_S
            deadline = start_time + duration_s
        targets = 0

        # Snapshot a valid player position as the exploration centre.
        cx, cy = self._wait_for_valid_player_pos(timeout_s=2.0)

        self._refresh_frontier_live(force_rebuild=True)
        if self._frontier:
            log.info(
                f"[Explorer] Frontier-guided mode: {len(self._frontier)} unexplored-edge targets"
            )
        else:
            log.info("[Explorer] No frontier yet — using random targets until data grows")

        if duration_mode == "completion":
            log.info(
                f"[Explorer] Starting completion-driven exploration from ({cx:.0f}, {cy:.0f})"
                + (f" — saving positions to '{self._map_name}'" if self._map_name else "")
            )
        else:
            log.info(
                f"[Explorer] Starting {duration_s:.0f}s exploration from ({cx:.0f}, {cy:.0f})"
                + (f" — saving positions to '{self._map_name}'" if self._map_name else "")
            )

        # Start RTNavigator's 120 Hz loop (issues the single right-click).
        if not self._rt_nav.is_running:
            self._rt_nav.start()

        # Start background position sampler thread
        sampler_thread = threading.Thread(
            target=self._run_position_sampler,
            args=(cancel_fn,),
            daemon=True,
            name="ExplorerSampler",
        )
        sampler_thread.start()

        # Completion-driven termination state
        last_growth_time = time.time()
        last_growth_count = self._read_cached_count()
        frontier_empty_since: Optional[float] = None
        self._frontier_last_refresh_s = 0.0

        # ── Global stuck guard state ─────────────────────────────────────
        last_check_pos = (cx, cy)
        last_check_time = time.time()

        def _is_cancelled() -> bool:
            with self._lock:
                if self._cancelled:
                    return True
            return bool(cancel_fn and cancel_fn())

        while True:
            now = time.time()
            self.elapsed_s = now - start_time

            # Live frontier/grid updates (two-rate)
            if now - self._grid_rebuild_last_s >= MAP_EXPLORER_GRID_REBUILD_S:
                self._refresh_frontier_live(force_rebuild=True)
                self._grid_rebuild_last_s = now
                self._frontier_last_refresh_s = now
            elif now - self._frontier_last_refresh_s >= MAP_EXPLORER_FRONTIER_REFRESH_S:
                self._refresh_frontier_live(force_rebuild=False)

            # Live coverage estimate update
            covered, est_total, pct = self._compute_coverage_estimate()
            self.covered_points = covered
            self.estimated_total_points = est_total
            self.coverage_percent = pct
            self.frontier_count = len(self._frontier)

            # Time limit
            if deadline is not None and now >= deadline:
                break

            # External cancel
            if _is_cancelled():
                break

            # Completion-driven exit (only when no fixed duration requested)
            if deadline is None:
                if covered - last_growth_count >= MAP_EXPLORER_COMPLETE_MIN_GAIN:
                    last_growth_count = covered
                    last_growth_time = now

                if self._frontier:
                    frontier_empty_since = None
                else:
                    if frontier_empty_since is None:
                        frontier_empty_since = now

                if (
                    frontier_empty_since is not None
                    and now - frontier_empty_since >= MAP_EXPLORER_COMPLETE_STABLE_S
                    and now - last_growth_time >= MAP_EXPLORER_COMPLETE_STABLE_S
                ):
                    log.info(
                        "[Explorer] Completion reached: frontier stable-empty and "
                        "coverage growth plateaued"
                    )
                    break

            # ── Global stuck detection ──────────────────────────────────
            px, py = self._player_pos()
            if now - last_check_time >= MAP_EXPLORER_GLOBAL_STUCK_TIME:
                dist_since_check = math.sqrt(
                    (px - last_check_pos[0]) ** 2 + (py - last_check_pos[1]) ** 2
                )
                if dist_since_check < MAP_EXPLORER_GLOBAL_STUCK_DIST:
                    log.info(
                        f"[Explorer] Global stuck (moved {dist_since_check:.0f}u "
                        f"in {MAP_EXPLORER_GLOBAL_STUCK_TIME:.0f}s) — "
                        f"forcing far A* escape"
                    )
                    self._force_escape(cx, cy, _is_cancelled)
                last_check_pos = (px, py)
                last_check_time = now

            # ── Pick a new target and navigate to it with RTNavigator ───
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 1.0:
                    break
                timeout = min(MAP_EXPLORER_TARGET_TIMEOUT_S, remaining)
            else:
                timeout = MAP_EXPLORER_TARGET_TIMEOUT_S

            px, py = self._player_pos()
            tx, ty = self._pick_target(cx, cy, px, py)

            targets += 1
            self.targets_attempted = targets
            self._previous_targets.append((tx, ty))

            distance_to_target = math.sqrt((tx - px) ** 2 + (ty - py) ** 2)
            # Compute timeout from real measured speed; fall back to 800 u/s.
            _spd = self._speed_ups if self._speed_ups > 50.0 else 800.0
            dynamic_timeout = max(3.0, min(timeout, distance_to_target / _spd * 2.5))

            if deadline is None:
                log.debug(
                    f"[Explorer] Target #{targets}: ({tx:.0f}, {ty:.0f}) "
                    f"d={distance_to_target:.0f} t={dynamic_timeout:.1f}s "
                    f"| cov={pct:.1f}% ({covered}/{est_total})"
                )
            else:
                log.debug(
                    f"[Explorer] Target #{targets}: ({tx:.0f}, {ty:.0f}) "
                    f"d={distance_to_target:.0f} t={dynamic_timeout:.1f}s "
                    f"| {max(0.0, deadline - time.time()):.0f}s remaining"
                )

            before_pos = (px, py)
            before_count = covered

            task = self._build_navigation_task(
                tx=tx,
                ty=ty,
                timeout_s=dynamic_timeout,
                target_index=targets,
                distance_to_target=distance_to_target,
                covered=covered,
                estimated_total=est_total,
            )
            reached = self._task_nav.execute(task, cancel_fn=_is_cancelled)

            after_x, after_y = self._player_pos()
            moved_dist = math.sqrt(
                (after_x - before_pos[0]) ** 2 + (after_y - before_pos[1]) ** 2
            )
            after_count = self._read_cached_count()
            gained = max(0, after_count - before_count)
            end_dist = math.sqrt((tx - after_x) ** 2 + (ty - after_y) ** 2)

            if not reached and (
                gained == 0
                or moved_dist < 400.0
                or end_dist > distance_to_target * 0.9
            ):
                self._remember_failed_target(tx, ty)
                log.debug(
                    f"[Explorer] Target unreachable — cooling down sector: "
                    f"({tx:.0f}, {ty:.0f}) moved={moved_dist:.0f} gain={gained}"
                )

            # Fire GUI progress callback
            self._emit_progress()

        # ── Shutdown ──────────────────────────────────────────────────────
        with self._lock:
            self._cancelled = True
        sampler_thread.join(timeout=2.0)

        # Stop the RTNavigator loop (freezes character + saves learned walls)
        self._rt_nav.stop()
        self.elapsed_s = time.time() - start_time
        pos_count = self._read_cached_count()
        log.info(
            f"[Explorer] Finished: {targets} targets in {self.elapsed_s:.0f}s "
            f"| {pos_count} positions saved for '{self._map_name}'"
        )
        self._emit_progress(force=True)
        return targets

    def cancel(self):
        """Signal the running run() to stop after the current target."""
        with self._lock:
            self._cancelled = True
        self._rt_nav.cancel()

    # ── Position sampler ────────────────────────────────────────────────────


    def _live_grid_update(self, x: float, y: float):
        if self._pathfinder and self._pathfinder.has_grid:
            self._pathfinder._grid.mark_circle_walkable(x, y, VISITED_CELL_WALKABLE_RADIUS)

    def _run_position_sampler(self,
                              cancel_fn: Optional[Callable[[], bool]] = None):
        """Background thread: sample player position and batch-save to wall_data.json.

        Samples every MAP_EXPLORER_POSITION_SAMPLE_DIST world units.
        Uses a grid-key set for O(1) deduplication against existing points.
        Flushes to disk every MAP_EXPLORER_POSITION_FLUSH_EVERY new points
        or every MAP_EXPLORER_POSITION_FLUSH_S seconds, whichever comes first.
        """
        if not self._map_name:
            return

        # Build initial set of already-saved grid keys (one per SAMPLE_DIST cell)
        existing_keys = self._load_existing_keys()

        pending:    list  = []   # new (x, y) tuples not yet written to disk
        last_flush: float = time.time()

        # Speed tracking state: measure u/s every tick regardless of saved-sample distance.
        _spd_px: float = 0.0
        _spd_py: float = 0.0
        _spd_pt: float = time.time()
        _spd_window: List[float] = []  # rolling window of per-tick speeds (u/s)

        while not self._cancelled and not (cancel_fn and cancel_fn()):
            x, y = self._player_pos()

            # ── Real-time speed from consecutive position reads ──────────────
            _spd_now = time.time()
            _spd_dt  = _spd_now - _spd_pt
            if abs(x) > 1.0 and abs(y) > 1.0 and _spd_dt > 0.005 and (_spd_px != 0.0 or _spd_py != 0.0):
                _spd_dx = x - _spd_px
                _spd_dy = y - _spd_py
                _spd_v  = math.sqrt(_spd_dx * _spd_dx + _spd_dy * _spd_dy) / _spd_dt
                if _spd_v > 1.0:  # ignore noise / stationary reads
                    _spd_window.append(_spd_v)
                    if len(_spd_window) > 30:
                        _spd_window.pop(0)
                    if len(_spd_window) >= 5:
                        self._speed_ups = sum(_spd_window) / len(_spd_window)
            _spd_px, _spd_py, _spd_pt = x, y, _spd_now

            if abs(x) > 1.0 or abs(y) > 1.0:
                key = self._pos_key(x, y)
                if key not in existing_keys:
                    if self._sampler_last_pos is None:
                        self._sampler_last_pos = (x, y)
                        existing_keys.add(key)
                        pending.append((x, y))
                    else:
                        dx = x - self._sampler_last_pos[0]
                        dy = y - self._sampler_last_pos[1]
                        if dx * dx + dy * dy >= MAP_EXPLORER_POSITION_SAMPLE_DIST ** 2:
                            self._sampler_last_pos = (x, y)
                            existing_keys.add(key)
                            pending.append((x, y))
                            self._live_grid_update(x, y)

            now = time.time()
            # --- Active Entity SLAM ---
            # During exploration, also slurp up all monster positions as walkable
            if hasattr(self, '_scanner') and self._scanner and getattr(self, '_last_ent_slam', 0) < now - 0.5:
                self._last_ent_slam = now
                try:
                    ents = self._scanner.get_monster_entities()
                    for ent in ents:
                        ex, ey, ez = ent.position
                        if abs(ex) > 1.0 or abs(ey) > 1.0:
                            e_key = self._pos_key(ex, ey)
                            if e_key not in existing_keys:
                                existing_keys.add(e_key)
                                pending.append((ex, ey))
                                self._live_grid_update(ex, ey)
                except Exception:
                    pass

            if pending and (
                len(pending) >= MAP_EXPLORER_POSITION_FLUSH_EVERY
                or now - last_flush >= MAP_EXPLORER_POSITION_FLUSH_S
            ):
                self._flush_positions(pending)
                pending.clear()
                last_flush = now

            time.sleep(MAP_EXPLORER_POSITION_POLL_S)

        # Final flush of anything remaining
        if pending:
            self._flush_positions(pending)

    @staticmethod
    def _pos_key(x: float, y: float) -> tuple:
        """Round (x, y) to nearest sample-grid cell for O(1) dedup."""
        d = MAP_EXPLORER_POSITION_SAMPLE_DIST
        return (round(x / d), round(y / d))

    def _load_existing_keys(self) -> set:
        """Load grid keys for all already-saved positions for this map."""
        try:
            from src.core.wall_scanner import WallScanner
            data = WallScanner._load_json()
            return {
                self._pos_key(e["x"], e["y"])
                for e in data.get(self._map_name, [])
            }
        except Exception:
            return set()

    def _flush_positions(self, new_xys: list):
        """Bulk-append new (x, y) positions to wall_data.json in one write."""
        if not self._map_name or not new_xys:
            return
        try:
            from src.core.wall_scanner import WallScanner
            with WallScanner._WALL_DATA_LOCK:
                data = WallScanner._load_json()
                existing = data.get(self._map_name, [])

                # Rebuild keys from disk (another thread/process may have written meanwhile).
                disk_keys = {self._pos_key(e["x"], e["y"]) for e in existing}
                added = 0
                for x, y in new_xys:
                    key = self._pos_key(x, y)
                    if key not in disk_keys:
                        existing.append({
                            "x": x,
                            "y": y,
                            "z": 0.0,
                            "r": VISITED_CELL_WALKABLE_RADIUS,
                            "t": "walkable"
                        })
                        disk_keys.add(key)
                        added += 1

                if added > 0:
                    data[self._map_name] = existing
                    WallScanner._save_json(data)
                    log.info(
                        f"[Explorer] +{added} positions for '{self._map_name}' "
                        f"(total: {len(existing)})"
                    )
        except Exception as exc:
            log.warning(f"[Explorer] Flush error: {exc}")

    # ── Internals ───────────────────────────────────────────────────────────

    def _player_pos(self) -> Tuple[float, float]:
        # Primary: shared PositionPoller (required in new constructor)
        if self._pos_poller is not None:
            x, y = self._pos_poller.get_pos()
            if abs(x) > 1.0 or abs(y) > 1.0:
                self._last_good_pos = (x, y)
                return x, y
        # Fallback: RTNavigator's internal game-state read
        try:
            gs = self._rt_nav._game_state
            gs.update()
            pos = gs.player.position
            x, y = pos.x, pos.y
            if abs(x) > 1.0 or abs(y) > 1.0:
                self._last_good_pos = (x, y)
                return x, y
        except Exception:
            pass
        if self._last_good_pos is not None:
            return self._last_good_pos
        return 0.0, 0.0

    def _wait_for_valid_player_pos(self, timeout_s: float) -> Tuple[float, float]:
        end_time = time.time() + max(0.0, timeout_s)
        while time.time() < end_time:
            x, y = self._player_pos()
            if abs(x) > 1.0 or abs(y) > 1.0:
                return x, y
            time.sleep(0.05)
        return self._player_pos()

    def _build_navigation_task(self,
                               tx: float,
                               ty: float,
                               timeout_s: float,
                               target_index: int,
                               distance_to_target: float,
                               covered: int,
                               estimated_total: int) -> NavigationTask:
        return NavigationTask(
            kind="explore_frontier_or_random",
            target_x=tx,
            target_y=ty,
            tolerance=500.0,
            timeout_s=timeout_s,
            no_progress_timeout_s=MAP_EXPLORER_NO_PROGRESS_TIMEOUT_S,
            no_progress_dist=MAP_EXPLORER_NO_PROGRESS_DIST,
            metadata={
                "target_index": target_index,
                "distance_to_target": distance_to_target,
                "covered": covered,
                "estimated_total": estimated_total,
            },
        )

    def _pick_target(self,
                     cx: float,
                     cy: float,
                     current_x: float,
                     current_y: float) -> Tuple[float, float]:
        """Pick the next exploration target.

        Priority order:
        1. **Frontier-guided** — if an existing grid was loaded, pick from the
           unexplored-edge cells using Maximin (maximally far from all previous
           targets).  This ensures gaps are filled before revisiting known areas.
        2. **Random Maximin** — once the frontier is exhausted (or no grid exists),
           generate MAP_EXPLORER_CANDIDATES random positions inside the map radius
           and pick the one farthest from all previous targets.
        """
        has_current = abs(current_x) > 1.0 or abs(current_y) > 1.0
        self._prune_failed_targets()

        # ── 1. Frontier-guided pick (nearest-first / roomba style) ───────────
        if self._frontier:
            # Always head to the NEAREST unexplored edge first so the character
            # sweeps the map line-by-line like a vacuum cleaner before going far.
            available = [f for f in self._frontier if not self._is_failed_target(f[0], f[1])]
            if not available:
                # All frontier cells are on cooldown — ignore cooldown and try the
                # full frontier anyway; wall-freeze abort will bail fast if walled.
                available = list(self._frontier)

            if has_current:
                available.sort(
                    key=lambda p: (p[0] - current_x) ** 2 + (p[1] - current_y) ** 2
                )

            # Pre-validate A* reachability on the nearest 8 candidates.
            # Unreachable cells get a short failure cooldown; we move to next nearest.
            # If all 8 fail, hand the first one to the navigator anyway — the
            # wall-freeze detector in navigate_to_position() will abort in ~150 ms.
            chosen = None
            for candidate in available[:8]:
                if (
                    not has_current
                    or self._is_target_reachable(current_x, current_y, candidate[0], candidate[1])
                ):
                    chosen = candidate
                    break
                self._remember_failed_target(candidate[0], candidate[1])
                log.debug(
                    f"[Explorer] Nearest frontier ({candidate[0]:.0f},{candidate[1]:.0f}) "
                    f"A* unreachable — skipping"
                )

            if chosen is None:
                chosen = available[0]

            try:
                self._frontier.remove(chosen)
            except ValueError:
                pass
            return chosen

        # ── 2. Random Maximin fallback (no grid / frontier exhausted) ────────
        base_x, base_y = (current_x, current_y) if has_current else (cx, cy)

        if not self._previous_targets:
            return (
                base_x + random.uniform(-MAP_EXPLORER_RADIUS * 0.6, MAP_EXPLORER_RADIUS * 0.6),
                base_y + random.uniform(-MAP_EXPLORER_RADIUS * 0.6, MAP_EXPLORER_RADIUS * 0.6),
            )

        best_pos      = None
        best_min_dist = -1.0

        for _ in range(MAP_EXPLORER_CANDIDATES):
            tx = base_x + random.uniform(-MAP_EXPLORER_RADIUS * 0.6, MAP_EXPLORER_RADIUS * 0.6)
            ty = base_y + random.uniform(-MAP_EXPLORER_RADIUS * 0.6, MAP_EXPLORER_RADIUS * 0.6)

            if self._is_failed_target(tx, ty):
                continue

            if has_current and not self._is_target_reachable(current_x, current_y, tx, ty):
                continue

            min_dist = min(
                math.sqrt((tx - px) ** 2 + (ty - py) ** 2)
                for px, py in self._previous_targets
            )

            if min_dist > best_min_dist:
                best_min_dist = min_dist
                best_pos = (tx, ty)

        if best_pos is None:
            return base_x, base_y
        return best_pos

    def _remember_failed_target(self, tx: float, ty: float):
        expiry = time.time() + self._FAILED_TARGET_COOLDOWN_S
        self._failed_targets.append((tx, ty, expiry))

    def _prune_failed_targets(self):
        now = time.time()
        if not self._failed_targets:
            return
        self._failed_targets = [e for e in self._failed_targets if e[2] > now]

    def _is_failed_target(self, tx: float, ty: float) -> bool:
        r2 = self._FAILED_TARGET_RADIUS ** 2
        for fx, fy, expiry in self._failed_targets:
            if expiry <= time.time():
                continue
            dx = tx - fx
            dy = ty - fy
            if dx * dx + dy * dy <= r2:
                return True
        return False

    def _is_target_reachable(self,
                             current_x: float,
                             current_y: float,
                             target_x: float,
                             target_y: float) -> bool:
        if not self._pathfinder or not self._pathfinder.has_grid:
            return True
        try:
            path = self._pathfinder.find_path(
                current_x,
                current_y,
                target_x,
                target_y,
                max_nodes=25000,
            )
            if path and len(path) >= 2:
                return True
            # Explorer can still be reachable via RTNavigator portal-hop.
            portal_det = getattr(self._rt_nav, "_portal_det", None)
            if portal_det and hasattr(portal_det, "get_portal_markers"):
                try:
                    return bool(portal_det.get_portal_markers())
                except Exception:
                    pass
            return False
        except Exception:
            return True

    def _force_escape(self,
                      cx: float, cy: float,
                      cancel_fn: Optional[Callable[[], bool]] = None):
        """Navigate to a far cardinal point to break out of a stuck area.

        Uses RTNavigator's full A* pathing so the escape can route around
        walls instead of blindly running into them.
        """
        angles = [0, 90, 180, 270, 45, 135, 225, 315]
        dist   = MAP_EXPLORER_RADIUS * 0.8
        for angle_deg in angles:
            if self._cancelled or (cancel_fn and cancel_fn()):
                return
            rad = math.radians(angle_deg)
            px, py = self._player_pos()
            tx = cx + math.cos(rad) * dist
            ty = cy + math.sin(rad) * dist
            log.info(
                f"[Explorer] Escape {angle_deg}° → ({tx:.0f}, {ty:.0f})"
            )
            self._rt_nav.navigate_to_target(
                tx, ty, tolerance=800.0, timeout=8.0, cancel_fn=cancel_fn,
            )
            new_x, new_y = self._player_pos()
            moved = math.sqrt((new_x - px) ** 2 + (new_y - py) ** 2)
            if moved > MAP_EXPLORER_GLOBAL_STUCK_DIST:
                log.info(f"[Explorer] Escape succeeded: moved {moved:.0f}u")
                return
        log.warning("[Explorer] All escape angles exhausted — continuing")

    def _read_cached_count(self) -> int:
        """Return cached position count for the current map (or total if no map name)."""
        try:
            from src.core.wall_scanner import WallScanner
            data = WallScanner._load_json()
            if self._map_name:
                return len(data.get(self._map_name, []))
            return sum(len(v) for v in data.values())
        except Exception:
            return 0

    def _refresh_frontier_live(self, force_rebuild: bool = False):
        """Refresh frontier list from latest cached walkable positions.

        force_rebuild=True  — disk read + full grid reconstruction + frontier scan.
                              Do this only when new position data may have been
                              flushed (every MAP_EXPLORER_GRID_REBUILD_S seconds).
        force_rebuild=False — frontier scan only, from the already-loaded in-memory
                              grid.  Cheap (~58 ms); safe at 0.5 s intervals.
        """
        self._frontier_last_refresh_s = time.time()
        if not self._map_name or self._pathfinder is None:
            return
        try:
            # ── Fast path: frontier-only scan from existing grid ──────────────
            if not force_rebuild and self._pathfinder.has_grid:
                self._frontier = self._pathfinder._grid.get_frontier_world_positions(max_samples=500)
                return

            # ── Full rebuild: disk read + grid construction ───────────────────
            from src.core.wall_scanner import WallScanner, WallPoint
            raw_points = WallScanner._load_json().get(self._map_name, [])
            points = [WallPoint.from_dict(p) for p in raw_points if isinstance(p, dict)]
            if not points:
                self._frontier = []
                return
            cx, cy = self._player_pos()
            ws = WallScanner.__new__(WallScanner)
            grid = ws.build_walkable_grid(points, cx, cy,
                                          half_size=WALL_GRID_HALF_SIZE,
                                          cell_size=WALL_GRID_CELL_SIZE)
            self._pathfinder.set_grid(grid)
            if self._pathfinder.has_grid:
                self._frontier = self._pathfinder._grid.get_frontier_world_positions(max_samples=500)
            else:
                self._frontier = []
        except Exception as exc:
            log.debug(f"[Explorer] Frontier refresh failed: {exc}")

    def _compute_coverage_estimate(self) -> Tuple[int, int, float]:
        covered = max(0, self._read_cached_count())
        frontier_n = max(0, len(self._frontier))
        estimated_total = max(
            covered,
            covered + int(frontier_n * MAP_EXPLORER_FRONTIER_ESTIMATE_MULTIPLIER),
            1,
        )
        pct = min(100.0, max(0.0, (covered / estimated_total) * 100.0))
        return covered, estimated_total, pct

    def _emit_progress(self, force: bool = False):
        if not self._progress:
            return
        covered, est_total, pct = self._compute_coverage_estimate()
        total_for_ui = self._duration if self._duration is not None else 0.0
        try:
            self._progress(
                self.elapsed_s,
                total_for_ui,
                self.targets_attempted,
                covered,
                pct,
                covered,
                est_total,
                len(self._frontier),
                force,
            )
        except TypeError:
            try:
                self._progress(self.elapsed_s, total_for_ui, self.targets_attempted, covered)
            except Exception:
                pass
        except Exception:
            pass
