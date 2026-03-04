"""rt_navigator.py — Real-time 60 Hz navigation brain for autonomous map runs.

Architecture
------------
A single background thread (_loop_thread) runs at ~60 Hz and owns all
low-level steering.  High-level phase logic (Events → Boss → Portal) runs on
the caller's thread via run_phases(), which communicates with the loop thread
through a small set of thread-safe goal/state variables.

Tick sequence (every ~16 ms):
  1. Read player world position via read_chain() — ~0.5 ms, no full update().
  2. Stuck detection: 40 consecutive frames with movement < RT_NAV_STUCK_DIST
     (default 15 world units) → trigger escape + immediate A* replan.
  3. Resolve active goal: the overriding goal (monster detour) if one is set
     and has not expired; otherwise the current phase goal.
  4. Follow path: advance waypoint index past waypoints within WAYPOINT_RADIUS;
     steer cursor toward the furthest ahead waypoint that passes a DDA
     line-of-sight check (lookahead).  Falls back to direct steering when no
     grid is loaded.
  5. A* replan: triggered when the goal changes, after a stuck escape, or once
     every RT_NAV_REPLAN_INTERVAL seconds as a safety net.  A* runs inline
     (~50–200 ms); this is acceptable because replanning only happens when the
     character is already stalled.
  6. Spam loot key.
  7. Every GOAL_ARBITER_TICKS (~4 Hz): scan for nearby monster clusters and
     set a temporary overriding goal so the auto-bomber walks through them.
     The override expires after 6 s or when the cluster is reached.

Stuck handling
--------------
40 consecutive 60 Hz frames with total displacement < RT_NAV_STUCK_DIST:
  • Move cursor to an escape angle screen position and hold for 0.45 s.
  • Force A* replan from the new position.
  • Rotate through 8 escape angles (NE, NW, SW, SE, N, S, E, W) so repeated
    stucks try progressively different directions.

Phase flow (run_phases)
-----------------------
Phase 1 — Events:   navigate to each Carjack/Sandlord in distance order;
                    rescan after each to catch lazily-loaded events.
Phase 2 — Boss:     navigate to the boss arena and linger 3 s.
Phase 3 — Portal:   poll PortalDetector; template-match the exit portal icon
                    for precise click position; fall back to F-key.

Manual waypoint navigation
--------------------------
navigate_waypoints() walks a recorded waypoint list using the same A* loop,
handling stand-type pauses, portal interactions, and event interrupts.
"""

import math
import json
import os
import sys
import time
import threading
from collections import deque
from typing import Callable, List, Optional, Tuple, TYPE_CHECKING, Dict, Any

from src.utils.logger import log
from src.utils.constants import (
    CHARACTER_CENTER,
    AUTO_NAV_ASTAR_MAX_NODES,
    RT_NAV_TICK_HZ,
    RT_NAV_STUCK_FRAMES,
    RT_NAV_STUCK_DIST,
    RT_NAV_LOOKAHEAD_DIST,
    RT_NAV_WAYPOINT_RADIUS,
    RT_NAV_GOAL_RADIUS,
    RT_NAV_REPLAN_INTERVAL,
    RT_NAV_MONSTER_RADIUS,
    RT_NAV_MONSTER_MIN_COUNT,
    RT_NAV_ESCAPE_DIST,
    RT_NAV_PROGRESS_TIMEOUT,
    RT_NAV_PROGRESS_MIN,
    RT_NAV_HEADING_BUF_SIZE,
    RT_NAV_ESCAPE_DURATION_S,
    RT_NAV_DRIFT_THRESHOLD,
    RT_NAV_HARD_STALL_FRAMES,
    RT_NAV_HARD_STALL_DIST,
    LEARNED_WALLS_FILE,
    PORTAL_PRIORS_FILE,
    HARDCODED_MAP_PORTALS,
    WALL_GRID_CELL_SIZE,
)

from src.core.scale_calibrator import MapCalibration, DEFAULT_CALIBRATION
from src.core.navigation import (
    NavigationTask,
    TaskNavigator,
    EventGoalProvider,
    BossGoalProvider,
    PortalGoalProvider,
)

if TYPE_CHECKING:
    from src.core.pathfinder import Pathfinder
    from src.core.portal_detector import PortalDetector
    from src.core.scanner import UE4Scanner, EventInfo
    from src.core.scale_calibrator import ScaleCalibrator

_TICK_S              = 1.0 / RT_NAV_TICK_HZ          # ~8.33 ms @ 120 Hz
_GOAL_ARBITER_TICKS  = max(1, RT_NAV_TICK_HZ // 8)   # 8 Hz goal arbiter (was 4)
_MONSTER_SCAN_TICKS  = max(1, RT_NAV_TICK_HZ // 4)   # 4 Hz monster scan (was 2)


class RTNavigator:
    """Real-time 60 Hz goal-directed navigator for autonomous map runs."""

    # 8 escape angles tried in rotating order on repeated stucks
    ESCAPE_ANGLES = [135, 45, 225, 315, 90, 270, 0, 180]

    def __init__(
        self,
        game_state,
        input_ctrl,
        pathfinder:          "Pathfinder",
        scanner:             Optional["UE4Scanner"] = None,
        portal_detector:     Optional["PortalDetector"] = None,
        event_handler_fn:    Optional[Callable] = None,
        boss_locate_fn:      Optional[Callable] = None,
        config                                  = None,
        behavior:            str = "rush_events",
        portal_entered_fn:   Optional[Callable[[], bool]] = None,
        find_portal_icon_fn: Optional[Callable] = None,
        pos_poller                              = None,
        scale_calibrator                        = None,
    ):
        self._gs          = game_state
        self._pos_poller  = pos_poller
        self._scale_cal   = scale_calibrator
        self._input       = input_ctrl
        self._pf          = pathfinder
        self._scanner     = scanner
        self._portal_det  = portal_detector
        self._evt_hdlr    = event_handler_fn
        self._boss_fn     = boss_locate_fn
        self._config      = config
        self._behavior    = (behavior or "rush_events").strip().lower()
        if self._behavior not in {"rush_events", "kill_all", "boss_rush"}:
            self._behavior = "rush_events"
        self._portal_fn   = portal_entered_fn
        self._icon_fn     = find_portal_icon_fn
        self._task_nav    = TaskNavigator(self)

        # Provide a minimal config dict when none is supplied (exploration mode)
        if self._config is None:
            self._config = {}

        # ── Thread-safe shared state (all reads/writes under _lock) ─────
        self._lock            = threading.RLock()
        self._pos             : Tuple[float, float] = (0.0, 0.0)
        self._phase_goal      : Optional[Tuple[float, float]] = None
        self._phase_goal_tol  : float = RT_NAV_GOAL_RADIUS
        self._override_goal   : Optional[Tuple[float, float]] = None
        self._override_tol    : float = RT_NAV_GOAL_RADIUS
        self._override_expiry : float = 0.0
        self._path            : List[Tuple[float, float]] = []
        self._path_goal       : Optional[Tuple[float, float]] = None
        self._path_idx        : int = 0
        self._last_steer_idx  : int = -1
        self._last_steer_goal : Optional[Tuple[float, float]] = None
        self._last_steer_t    : float = 0.0
        self._stuck_frames    : int = 0
        self._drift_replan_hits: int = 0
        self._escape_idx      : int = 0
        self._last_replan_t   : float = 0.0
        # Goal-progress tracking: reset whenever distance to goal improves by
        # RT_NAV_PROGRESS_MIN.  If no improvement for RT_NAV_PROGRESS_TIMEOUT
        # seconds the character is treated as stuck (wall-sliding detection).
        self._progress_best_dist : float = float("inf")
        self._progress_t         : float = 0.0
        self._cancelled          : bool = False
        self._loop_running       : bool = False
        # When True, the goal arbiter is suppressed for the current _navigate_to
        # call.  Prevents kill_all monster detours from interrupting critical
        # event approaches (truck arrival, portal, etc.).
        self._arbiter_suppressed : bool = False

        # ── Heading buffer (loop-thread private) ────────────────────────
        # Rolling buffer of per-frame (dx, dy) displacement vectors.
        # Averaged to produce the character's actual heading at stuck time.
        self._heading_buf: deque = deque(maxlen=RT_NAV_HEADING_BUF_SIZE)
        self._stall_buf: deque = deque(maxlen=RT_NAV_HARD_STALL_FRAMES)
        self._hard_stalled: bool = False

        # ── Non-blocking escape state machine (under _lock) ─────────────
        # Instead of blocking the 60 Hz loop with time.sleep(), the escape
        # sets a target + deadline.  The next N ticks steer toward the escape
        # target, then force a replan from the new position.
        self._escape_target   : Optional[Tuple[float, float]] = None  # world coords
        self._escape_deadline : float = 0.0
        self._escape_gx       : float = 0.0   # original goal at escape time
        self._escape_gy       : float = 0.0

        # ── Learned walls (under _lock) ──────────────────────────────────
        # Wall cells inferred from stuck events during this run.
        # Persisted to LEARNED_WALLS_FILE on stop() so they survive restarts.
        self._learned_walls   : List[Tuple[int, int]] = []  # (grid_row, grid_col)
        self._map_name_for_walls : str = ""

        # ── Async A* replan worker ──────────────────────────────────────
        self._replan_request  : Optional[Tuple[float, float, float, float]] = None
        self._replan_worker   : Optional[threading.Thread] = None
        self._replan_pending  : bool = False
        self._last_replan_sig : Optional[Tuple[int, int, int, int]] = None

        # ── Reliability counters (run-scoped) ───────────────────────────
        self._metrics: Dict[str, int] = {
            "replans_requested": 0,
            "replans_suppressed": 0,
            "replans_success": 0,
            "no_path": 0,
            "stuck_escapes": 0,
            "navigate_timeout": 0,
            "navigate_no_progress_abort": 0,
        }
        self._consecutive_no_path: int = 0

        # ── Portal-hop fallback state (for disconnected map segments) ───
        # When direct A* to goal fails, planner can route to a reachable portal
        # first. Once near that portal, the loop presses interact and quickly
        # replans to the original goal from the potentially new segment.
        self._portal_hop_target      : Optional[Tuple[float, float]] = None
        self._portal_hop_key         : Optional[Tuple[int, int]] = None
        self._portal_hop_next_try_t  : float = 0.0
        self._portal_hop_cooldowns   : Dict[Tuple[int, int], float] = {}
        self._portal_hop_last_interact_pos: Optional[Tuple[float, float]] = None
        self._portal_hop_arrival_hold_until: float = 0.0
        self._portal_priors_cache    : Dict[str, List[Tuple[float, float]]] = {}
        self._portal_priors_last_save_t: float = 0.0
        self._portal_link_dest_by_key: Dict[Tuple[int, int], Tuple[float, float]] = {}

        # ── Overlay reference (set externally) ──────────────────────────
        self._overlay = None

        # Loot spam state — loop-thread private
        self._loot_t          : float = 0.0
        self._loot_interval   : float = self._config.get("loot_spam_interval_ms", 150) / 1000.0

        # Tick counter — loop-thread private (no lock needed)
        self._tick_cnt        : int = 0

        # Background loop thread
        self._loop_thread     : Optional[threading.Thread] = None

    # ────────────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────────────

    def start(self):
        """Start the 60 Hz background navigation loop."""
        with self._lock:
            if self._loop_running:
                return
            self._cancelled    = False
            self._loop_running = True
        # Load learned walls for the current map into the live grid
        self._loop_thread = threading.Thread(
            target=self._loop, daemon=True, name="RTNav60Hz"
        )
        self._loop_thread.start()
        # Issue the single right-click that engages cursor-follow mode.
        # (TLI movement: one right-click → character follows cursor; a second
        # right-click in the same map TOGGLES it OFF — so we issue exactly one.)
        self._input.click(*CHARACTER_CENTER, button="right")
        time.sleep(0.25)
        log.info("[RTNav] 60 Hz loop started")

    def stop(self):
        """Stop the background loop and freeze the character."""
        with self._lock:
            self._cancelled = True
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=1.5)
        self._input.move_mouse(*CHARACTER_CENTER)
        # Persist any walls learned during this run
        with self._lock:
            metrics = dict(self._metrics)
        log.info(
            "[RTNav] Reliability summary: "
            f"replan req={metrics['replans_requested']} "
            f"supp={metrics['replans_suppressed']} "
            f"ok={metrics['replans_success']} "
            f"no_path={metrics['no_path']} "
            f"stuck={metrics['stuck_escapes']} "
            f"timeout={metrics['navigate_timeout']} "
            f"no_prog={metrics['navigate_no_progress_abort']}"
        )
        log.info("[RTNav] Loop stopped")

    def set_overlay(self, overlay) -> None:
        """Set the overlay reference so A* paths can be drawn."""
        self._overlay = overlay

    def set_map_name(self, name: str) -> None:
        """Set the current map name for learned-walls persistence."""
        self._map_name_for_walls = name or ""

    def cancel(self):
        """Signal the loop to stop at the next tick."""
        with self._lock:
            self._cancelled = True

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._loop_running and not self._cancelled

    def navigate_to_target(self, gx: float, gy: float,
                           tolerance: float = 600.0,
                           timeout: float = 15.0,
                           cancel_fn: Optional[Callable[[], bool]] = None,
                           no_progress_timeout: Optional[float] = None,
                           no_progress_dist: float = 0.0) -> bool:
        """Public API: navigate to a world-space target with full A* steering.

        Starts the 120 Hz loop if it is not already running.  Blocks the
        calling thread until the target is reached, the timeout expires, or
        *cancel_fn* returns True.  The goal arbiter (monster detours) is
        suppressed so the character heads straight for the target.

        Returns True if the target was reached within *tolerance* world units.
        """
        if not self.is_running:
            self.start()

        def _cancel() -> bool:
            with self._lock:
                if self._cancelled:
                    return True
            return bool(cancel_fn and cancel_fn())

        return self._navigate_to(
            gx, gy, tolerance, timeout,
            cancel_fn=_cancel,
            suppress_arbiter=True,
            no_progress_timeout=no_progress_timeout,
            no_progress_dist=no_progress_dist,
        )

    def execute_navigation_task(self, task: NavigationTask,
                                cancel_fn: Optional[Callable[[], bool]] = None) -> bool:
        """Execute a planner-produced NavigationTask using RTNavigator internals."""
        if not self.is_running:
            self.start()

        def _cancel() -> bool:
            with self._lock:
                if self._cancelled:
                    return True
            return bool(cancel_fn and cancel_fn())

        return self._navigate_to(
            task.target_x,
            task.target_y,
            task.tolerance,
            task.timeout_s,
            cancel_fn=_cancel,
            suppress_arbiter=task.suppress_arbiter,
            no_progress_timeout=task.no_progress_timeout_s,
            no_progress_dist=task.no_progress_dist,
        )

    def _build_navigation_task(
        self,
        kind: str,
        x: float,
        y: float,
        tolerance: float,
        timeout_s: float,
        suppress_arbiter: bool = True,
        no_progress_timeout_s: Optional[float] = None,
        no_progress_dist: float = 0.0,
        metadata: Optional[dict] = None,
    ) -> NavigationTask:
        return NavigationTask(
            kind=kind,
            target_x=x,
            target_y=y,
            tolerance=tolerance,
            timeout_s=timeout_s,
            suppress_arbiter=suppress_arbiter,
            no_progress_timeout_s=no_progress_timeout_s,
            no_progress_dist=no_progress_dist,
            metadata=metadata or {},
        )

    def stop_character(self):
        """Move cursor to character centre to halt movement."""
        self._input.move_mouse(*CHARACTER_CENTER)

    def navigate_waypoints(self, waypoints, cancel_fn=None,
                           event_checker=None, event_handler=None) -> bool:
        """Walk a list of Waypoint objects using the 120 Hz A* loop.

        Parameters
        ----------
        waypoints      : list of Waypoint (from src.core.waypoint)
        cancel_fn      : callable returning True to abort
        event_checker  : (px, py) -> Optional[EventInfo]  (mid-nav event scan)
        event_handler  : (event) -> None                  (handles the event)

        Returns True if all waypoints were reached, False on cancel/error.
        """
        from src.utils.constants import STAND_TOLERANCE

        if not waypoints:
            return True

        if not self.is_running:
            self.start()

        def _cancel() -> bool:
            with self._lock:
                if self._cancelled:
                    return True
            return bool(cancel_fn and cancel_fn())

        total = len(waypoints)
        log.info(f"[RTNav] navigate_waypoints: {total} waypoints")

        event_tick = 0
        _EVENT_CHECK_INTERVAL = 25  # check events every N waypoints of polling

        for idx, wp in enumerate(waypoints):
            if _cancel():
                log.info("[RTNav] Waypoint navigation cancelled")
                return False

            tol = float(STAND_TOLERANCE) if wp.wp_type == "stand" else 200.0
            dist = 99999.0
            try:
                px, py = self._current_pos()
                dist = math.hypot(wp.x - px, wp.y - py)
            except Exception:
                pass

            timeout = max(5.0, min(30.0, dist / 600.0 * 2.5))

            reached = self._navigate_to(
                wp.x, wp.y, tol, timeout,
                cancel_fn=_cancel, suppress_arbiter=True,
            )

            if _cancel():
                return False

            if reached:
                label = f" ({wp.label})" if wp.label else ""
                portal = " [PORTAL]" if wp.is_portal else ""
                log.info(f"[RTNav] WP {idx}/{total} [{wp.wp_type.upper()}]{label}{portal}")

                if wp.wp_type == "stand":
                    self.stop_character()
                    if wp.wait_time > 0:
                        time.sleep(wp.wait_time)

                if wp.is_portal:
                    self._handle_portal_waypoint(wp)
            else:
                log.warning(f"[RTNav] WP {idx}/{total} unreachable — continuing")

            # Periodic event check
            event_tick += 1
            if (event_checker and event_handler
                    and event_tick % _EVENT_CHECK_INTERVAL == 0):
                try:
                    px, py = self._current_pos()
                    event = event_checker(px, py)
                    if event is not None:
                        log.info(f"[RTNav] Event interrupt: {event.event_type}")
                        self.stop_character()
                        time.sleep(0.2)
                        event_handler(event)
                except Exception as exc:
                    log.debug(f"[RTNav] Event check error: {exc}")

            # Spam loot between waypoints
            self._spam_loot()

        log.info("[RTNav] All waypoints reached")
        return True

    def _handle_portal_waypoint(self, wp):
        """Press interact key several times at a portal waypoint."""
        log.info("[RTNav] Portal waypoint — pressing F to enter")
        self._input.move_mouse(*CHARACTER_CENTER)
        time.sleep(0.2)
        interact_key = self._config.get("interact_key", "f")
        for _ in range(5):
            with self._lock:
                if self._cancelled:
                    return
            self._input.press_key(interact_key)
            time.sleep(0.3)
        time.sleep(1.0)

    # ────────────────────────────────────────────────────────────────────────
    # Blocking phase runner (called from bot_engine thread)
    # ────────────────────────────────────────────────────────────────────────

    def run_phases(self, cancel_fn: Optional[Callable[[], bool]] = None) -> bool:
        """Blocking: run Events → Boss → Portal.  Returns True on portal entry."""
        self.start()

        def is_cancelled() -> bool:
            if cancel_fn and cancel_fn():
                self.cancel()
                return True
            with self._lock:
                return self._cancelled

        log.info(f"[RTNav] run_phases | behavior={self._behavior}")

        if self._behavior == "kill_all":
            # Unified pass: events + monster sweep + boss in one geographically-
            # optimised route with live rebuild after every stop.
            # _phase_portal is the only separate step needed afterward.
            if not self._phase_kill_all_unified(is_cancelled):
                self.stop()
                return False
            ok = self._phase_portal(is_cancelled)
            self.stop()
            return ok

        if self._behavior != "boss_rush":
            if not self._phase_events(is_cancelled):
                self.stop()
                return False

        self._phase_boss(is_cancelled)
        ok = self._phase_portal(is_cancelled)
        self.stop()
        return ok

    # ────────────────────────────────────────────────────────────────────────
    # Phase implementations
    # ────────────────────────────────────────────────────────────────────────

    def _phase_events(self, is_cancelled: Callable[[], bool]) -> bool:
        log.info("[RTNav] Phase 1 — Events")
        handled: set = set()
        planner = EventGoalProvider(self._scanner, self._current_pos, handled)
        deadline = time.time() + 300.0  # 5-min hard cap

        while not is_cancelled() and time.time() < deadline:
            pending = planner.get_pending_events()
            if not pending:
                log.info("[RTNav] No remaining target events")
                break

            task = planner.next_task()
            if not task:
                break

            evt = task.metadata.get("event")
            ex, ey = task.target_x, task.target_y

            if abs(ex) < 1.0 and abs(ey) < 1.0:
                log.debug(f"[RTNav] Event {evt.event_type} pos=(0,0) — waiting for load")
                time.sleep(1.5)
                continue

            log.info(f"[RTNav] Navigating to {evt.event_type} at ({ex:.0f},{ey:.0f})")

            # Sandlord activation zone avoidance: if we are NOT navigating to
            # this Sandlord, register its platform position as a high-cost zone
            # in A* so the route stays at least ~450u away and never accidentally
            # steps onto the platform (which would fire the wave sequence early).
            sandlord_centers: List[Tuple[float, float]] = []
            if self._pf:
                if evt.event_type.lower() == "sandlord":
                    # Going TO the platform — remove any avoidance for it.
                    self._pf.clear_avoid_zones()
                else:
                    sandlord_zones = [
                        (e.position[0], e.position[1], 450.0, 30.0)
                        for e in pending
                        if e.event_type.lower() == "sandlord"
                    ]
                    sandlord_centers = [(z[0], z[1]) for z in sandlord_zones]
                    if sandlord_zones:
                        self._pf.set_avoid_zones(sandlord_zones)
                        log.debug(f"[RTNav] Routing around "
                                  f"{len(sandlord_zones)} Sandlord zone(s)")
                    else:
                        self._pf.clear_avoid_zones()

            if evt.event_type.lower() == "carjack" and self._behavior in {"rush_events", "kill_all"}:
                self._preclear_event_area(
                    ex,
                    ey,
                    radius=2500.0,
                    budget_s=16.0,
                    is_cancelled=is_cancelled,
                    avoid_centers=sandlord_centers,
                )

            reached = self._task_nav.execute(task, is_cancelled)
            if is_cancelled():
                return False
            if not reached:
                # Character is not at the event — calling the handler from a
                # distance would fire Sandlord/Carjack logic miles away from the
                # activation point.  Mark as handled to avoid infinite retries.
                log.warning(f"[RTNav] Could not reach {evt.event_type} — skipping handler")
                handled.add(evt.address)
                continue
            handled.add(evt.address)

            # Stop all RTNav steering before the event handler takes control.
            # Without this the 60 Hz loop races the event handler's cursor moves,
            # causing the character to oscillate between the event and open terrain.
            with self._lock:
                self._override_goal   = None
                self._override_expiry = 0.0
                self._path            = []
            time.sleep(0.08)  # let the loop thread see cleared state

            try:
                self._evt_hdlr(evt)
            except Exception as exc:
                log.error(f"[RTNav] event_handler raised: {exc}")

            if is_cancelled():
                return False
            time.sleep(0.3)

        # Clear avoidance zones so they don't affect subsequent phases.
        if self._pf:
            self._pf.clear_avoid_zones()
        return not is_cancelled()

    def _phase_boss(self, is_cancelled: Callable[[], bool]) -> bool:
        log.info("[RTNav] Phase 2 — Boss")
        planner = BossGoalProvider(self._boss_fn)
        try:
            task = planner.next_task()
        except Exception as exc:
            log.error(f"[RTNav] boss_locate_fn raised: {exc}")
            task = None

        if not task:
            log.info("[RTNav] No boss position — skipping boss phase")
            return False

        bx, by = task.target_x, task.target_y
        log.info(f"[RTNav] Boss arena at ({bx:.0f},{by:.0f})")
        reached = self._task_nav.execute(task, is_cancelled)
        if reached and not is_cancelled():
            log.info("[RTNav] Boss arena reached — lingering 3 s for auto-bomb")
            self._stop_movement()
            t_end = time.time() + 3.0
            while not is_cancelled() and time.time() < t_end:
                time.sleep(0.2)
        else:
            if not is_cancelled():
                log.warning("[RTNav] Could not reach boss arena — proceeding to portal")
        return reached

    def _phase_portal(self, is_cancelled: Callable[[], bool]) -> bool:
        log.info("[RTNav] Phase 3 — Exit portal")
        planner = PortalGoalProvider(self._portal_det)
        self._stop_movement()
        deadline = time.time() + 90.0

        while not is_cancelled() and time.time() < deadline:
            try:
                task = planner.next_task()
            except Exception:
                task = None

            if not task:
                time.sleep(1.0)
                continue

            px_w, py_w = task.target_x, task.target_y
            log.info(f"[RTNav] Exit portal at ({px_w:.0f},{py_w:.0f}) — navigating")
            reached = self._task_nav.execute(task, is_cancelled)
            if not reached or is_cancelled():
                time.sleep(1.0)
                continue

            self._stop_movement()
            time.sleep(0.3)

            interact_key = self._config.get("interact_key", "f")
            for attempt in range(25):
                if is_cancelled():
                    return False

                # Template-match portal icon for precise click; fall back to F-key
                icon_pos = None
                if self._icon_fn:
                    try:
                        icon_pos = self._icon_fn()
                    except Exception:
                        pass

                if icon_pos:
                    # Special case (e.g. Pirates shifted layout): stop cursor-follow
                    # drift immediately before precise icon click.
                    self._input.click(*CHARACTER_CENTER, button="right")
                    time.sleep(0.15)
                    self._input.click(*icon_pos, button="left")
                else:
                    self._input.press_key(interact_key)

                time.sleep(0.5)

                confirmed = False
                if self._portal_fn:
                    try:
                        confirmed = self._portal_fn()
                    except Exception:
                        pass
                if confirmed:
                    log.info(f"[RTNav] Portal entry confirmed (attempt {attempt + 1})")
                    return True

            log.warning("[RTNav] Portal interact attempts exhausted — re-polling")
            time.sleep(1.0)

        return False

    # ────────────────────────────────────────────────────────────────────────
    # kill_all sweep phase
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _cluster_entities(entities, cluster_radius: float = 1500.0) -> list:
        """Group entities into spatial clusters by proximity.

        Single-pass greedy: each entity joins the nearest existing cluster
        centroid within *cluster_radius* world units, or seeds a new cluster.
        Returns a list of (centroid_x, centroid_y) tuples.
        """
        clusters: list = []  # each entry: [cx, cy, count]
        for e in entities:
            ex, ey = e.position[0], e.position[1]
            best_i = -1
            best_d = cluster_radius
            for i, (cx, cy, _) in enumerate(clusters):
                d = math.hypot(ex - cx, ey - cy)
                if d < best_d:
                    best_d = d
                    best_i = i
            if best_i >= 0:
                cx, cy, cnt = clusters[best_i]
                nc = cnt + 1
                clusters[best_i] = ((cx * cnt + ex) / nc, (cy * cnt + ey) / nc, nc)
            else:
                clusters.append((ex, ey, 1))
        return [(cx, cy) for cx, cy, _ in clusters]

    def _phase_clear_map(self, is_cancelled: Callable[[], bool]) -> bool:
        """kill_all mode: pre-plan a greedy nearest-neighbour route through all
        alive monster clusters and navigate it without backtracking.

        The entity scanner detects monsters several screens ahead, giving an
        accurate whole-map snapshot upfront.  The route visits every cluster
        in NN order from the current position so the character never backtracks.
        Lazy-spawned groups are appended to the route tail on periodic rescans.
        """
        log.info("[RTNav] Phase 1.5 — Kill All sweep")
        if not self._scanner:
            return True

        deadline = time.time() + 180.0  # 3-min hard cap

        def get_alive():
            entities = self._scanner.get_monster_entities() or []
            return [
                e for e in entities
                if e.bvalid != 0
                and (abs(e.position[0]) > 1.0 or abs(e.position[1]) > 1.0)
            ]

        # ── initial snapshot + route build ─────────────────────────────────
        px, py = self._current_pos()
        alive  = get_alive()
        if not alive:
            log.info("[RTNav] Kill-all: no monsters at sweep start")
            return True

        ini_clusters = self._cluster_entities(alive)
        log.info(f"[RTNav] Kill-all: {len(alive)} monsters → "
                 f"{len(ini_clusters)} clusters")

        # Greedy nearest-neighbour route starting from player position.
        route: list = []       # [(cx, cy), ...] in visit order
        visited: list = []     # centroids already navigated to
        remaining = list(ini_clusters)
        cx, cy = px, py
        while remaining:
            bi = min(range(len(remaining)),
                     key=lambda i: (remaining[i][0] - cx) ** 2 + (remaining[i][1] - cy) ** 2)
            nxt = remaining.pop(bi)
            route.append(nxt)
            cx, cy = nxt

        log.info("[RTNav] Kill-all route: " +
                 " → ".join(f"({x:.0f},{y:.0f})" for x, y in route))

        # ── navigate planned route ──────────────────────────────────────────
        route_idx  = 0
        last_rescan = time.time()

        while not is_cancelled() and time.time() < deadline:

            # Route exhausted — final check for lazy-spawned groups.
            if route_idx >= len(route):
                final = get_alive()
                if not final:
                    log.info("[RTNav] Kill-all sweep complete")
                    return True
                new_clusters = self._cluster_entities(final)
                added = 0
                for ncx, ncy in new_clusters:
                    if all(math.hypot(ncx - vx, ncy - vy) > 2000.0
                           for vx, vy in visited):
                        route.append((ncx, ncy))
                        added += 1
                if not added:
                    log.info(f"[RTNav] Kill-all: {len(final)} lingering monsters "
                             "all near visited centroids — sweep done")
                    return True
                log.info(f"[RTNav] Kill-all: {added} new cluster(s) found "
                         f"after route exhaustion")
                continue

            tx, ty = route[route_idx]
            px, py = self._current_pos()
            dist   = math.hypot(tx - px, ty - py)

            # Prune dissolved clusters: if no live monster is within 2000u of
            # this centroid AND it is far away, skip without navigating.
            if dist > 6000.0:
                local_alive = [
                    e for e in get_alive()
                    if math.hypot(e.position[0] - tx, e.position[1] - ty) <= 2000.0
                ]
                if not local_alive:
                    log.info(f"[RTNav] Kill-all prune stop {route_idx + 1} "
                             f"({tx:.0f},{ty:.0f}) empty+far — skip")
                    visited.append((tx, ty))
                    route_idx += 1
                    continue

            log.info(f"[RTNav] Kill-all stop {route_idx + 1}/{len(route)}: "
                     f"({tx:.0f},{ty:.0f}) d={dist:.0f}")
            task = self._build_navigation_task(
                "cluster",
                tx,
                ty,
                tolerance=400.0,
                timeout_s=18.0,
                suppress_arbiter=False,
            )
            self._task_nav.execute(task, is_cancelled)

            # Brief dwell — let the autobomber clear the cluster.
            t_dwell = time.time() + 1.5
            while not is_cancelled() and time.time() < t_dwell:
                time.sleep(0.2)

            visited.append((tx, ty))
            route_idx += 1

            # Periodic rescan: append newly visible clusters (lazy spawns).
            if time.time() - last_rescan >= 8.0:
                last_rescan = time.time()
                new_entities = get_alive()
                new_clusters = self._cluster_entities(new_entities)
                added = 0
                for ncx, ncy in new_clusters:
                    near_vis  = any(math.hypot(ncx - vx, ncy - vy) <= 2000.0
                                    for vx, vy in visited)
                    in_plan   = any(math.hypot(ncx - rx, ncy - ry) <= 1500.0
                                    for rx, ry in route[route_idx:])
                    if not near_vis and not in_plan:
                        route.append((ncx, ncy))
                        added += 1
                if added:
                    log.info(f"[RTNav] Kill-all: {added} new cluster(s) "
                             f"appended (lazy spawn)")

        if time.time() >= deadline:
            log.warning("[RTNav] Kill-all sweep timeout")
        return not is_cancelled()

    def _phase_kill_all_unified(self, is_cancelled: Callable[[], bool]) -> bool:
        """kill_all: one geographically-optimal pass through events + monster
        clusters + boss, with a full live route rebuild after every stop.

        Stop types:
          'event'   — mandatory; calls _evt_hdlr(evt) when reached.
          'cluster' — navigate to centroid of a live monster group, 1.5s dwell.
          'boss'    — always the final stop; 3s dwell for auto-bomb.

        Solving the monster-population delay:
          Entity scanner doesn't populate MapRoleMonster until the character
          moves at least slightly.  If no clusters are visible at route-build
          time the method proceeds with events first; by the time the first
          event stop is completed, the character has moved and the scanner is
          warm.  The post-stop route rebuild then picks up all clusters.
          On no-event maps a short polling wait is used instead.

        Live adaptation:
          After EVERY stop the entire remaining route is discarded and rebuilt
          from scratch: pending events are preserved, monster clusters come from
          a fresh scan, boss always goes last, the whole list is re-sorted NN
          from the character's ACTUAL current position (not a stale plan pos).
          This makes the route self-correcting for:
            - Wall slides (character ended up somewhere off the planned path)
            - Lag / teleport (position jumped)
            - Lazy spawns (new clusters added automatically)
            - AoE kills (empty clusters are skipped on pre-check)
            - Early boss spawn (boss position added whenever _boss_fn succeeds)
        """
        log.info("[RTNav] Phase 1 — Kill All unified (events + clusters + boss)")
        if not self._scanner:
            return True

        deadline = time.time() + 360.0  # 6-min hard cap

        def get_alive():
            entities = self._scanner.get_monster_entities() or []
            return [
                e for e in entities
                if e.bvalid != 0
                and (abs(e.position[0]) > 1.0 or abs(e.position[1]) > 1.0)
            ]

        def build_route(cur_x: float, cur_y: float,
                        pending_evts: list,
                        boss: Optional[tuple]) -> list:
            """Build a fully fresh NN-sorted route.

            Combines pending event stops + fresh monster clusters.
            Cluster centroids that fall within 800u of a pending event are
            omitted — the event handler will clear them.
            Boss is appended last regardless of distance.
            """
            stops: list = []
            for ev in pending_evts:
                stops.append({"type": "event",
                               "x": ev.position[0], "y": ev.position[1],
                               "evt": ev})
            alive = get_alive()
            for cx, cy in self._cluster_entities(alive):
                if any(math.hypot(cx - s["x"], cy - s["y"]) <= 800.0
                       for s in stops if s["type"] == "event"):
                    continue  # event handler will sweep this area
                stops.append({"type": "cluster", "x": cx, "y": cy, "evt": None})

            # NN sort of all non-boss stops from current position
            ordered: list = []
            remaining = list(stops)
            nx, ny = cur_x, cur_y
            while remaining:
                bi = min(range(len(remaining)),
                         key=lambda i: (remaining[i]["x"] - nx) ** 2
                                     + (remaining[i]["y"] - ny) ** 2)
                nxt = remaining.pop(bi)
                ordered.append(nxt)
                nx, ny = nxt["x"], nxt["y"]

            # Boss always last
            if boss:
                ordered.append({"type": "boss", "x": boss[0], "y": boss[1],
                                 "evt": None})
            return ordered

        # ── Initial setup ──────────────────────────────────────────────────
        handled_evts: set = set()

        pending_events = [
            e for e in (self._scanner.get_typed_events() or [])
            if e.is_target_event
            and abs(e.position[0]) > 1.0 and abs(e.position[1]) > 1.0
        ]

        boss_pos: Optional[tuple] = None
        try:
            boss_pos = self._boss_fn()
        except Exception as exc:
            log.error(f"[RTNav] boss_locate_fn raised: {exc}")

        px, py = self._current_pos()
        route = build_route(px, py, pending_events, boss_pos)

        # If there are no events and no clusters yet, the entity scanner hasn't
        # populated (character hasn't moved).  Poll briefly so we don't bail
        # on an empty route immediately.
        has_clusters = any(s["type"] == "cluster" for s in route)
        if not pending_events and not has_clusters and not boss_pos:
            log.info("[RTNav] Unified: entity scanner not warmed — waiting up to 8s")
            pop_dl = time.time() + 8.0
            while not is_cancelled() and time.time() < pop_dl:
                if get_alive():
                    break
                time.sleep(0.4)
            px, py = self._current_pos()
            route = build_route(px, py, pending_events, boss_pos)

        if not route:
            log.info("[RTNav] Unified: nothing to do")
            return not is_cancelled()

        log.info("[RTNav] Unified route: " +
                 " \u2192 ".join(
                     f"({s['x']:.0f},{s['y']:.0f})[{s['type'][0].upper()}]"
                     for s in route))

        # ── Main navigation loop ───────────────────────────────────────────
        while not is_cancelled() and time.time() < deadline:
            if not route:
                log.info("[RTNav] Unified: route complete")
                break

            stop  = route[0]
            stype = stop["type"]
            tx, ty = stop["x"], stop["y"]
            px, py = self._current_pos()
            dist   = math.hypot(tx - px, ty - py)

            # ── Sandlord avoidance: A* soft-penalty around platforms we are
            #    NOT intentionally heading to (prevents accidental activation)
            if self._pf:
                if stype == "event" and stop["evt"] and \
                        stop["evt"].event_type.lower() == "sandlord":
                    self._pf.clear_avoid_zones()
                else:
                    sl_zones = [
                        (s["x"], s["y"], 450.0, 30.0)
                        for s in route
                        if s["type"] == "event" and s["evt"]
                        and s["evt"].event_type.lower() == "sandlord"
                    ]
                    if sl_zones:
                        self._pf.set_avoid_zones(sl_zones)
                    else:
                        self._pf.clear_avoid_zones()

            # ── Pre-check: skip empty cluster stops before navigating ──────
            if stype == "cluster":
                near = [
                    e for e in get_alive()
                    if math.hypot(e.position[0] - tx, e.position[1] - ty) <= 1800.0
                ]
                if not near:
                    log.info(f"[RTNav] Unified: cluster ({tx:.0f},{ty:.0f}) "
                             f"already empty — skip")
                    route.pop(0)
                    continue

                # Reachability guard: in maps with adjacent corridor lanes,
                # nearby monster clusters can be geometrically close but
                # disconnected from the current segment. Skip such clusters
                # so unified routing can continue to events/portal flow.
                if self._pf and self._pf.has_grid:
                    cpath = self._pf.find_path(
                        px, py, tx, ty,
                        max_nodes=max(15000, AUTO_NAV_ASTAR_MAX_NODES // 4),
                    )
                    if not cpath:
                        log.info(f"[RTNav] Unified: cluster ({tx:.0f},{ty:.0f}) "
                                 "unreachable from current segment — skip")
                        route.pop(0)
                        continue

            # ── Per-type navigation parameters ────────────────────────────
            if stype == "event":
                evt_obj = stop["evt"]
                tol     = (150.0 if evt_obj
                           and evt_obj.event_type.lower() == "sandlord"
                           else RT_NAV_GOAL_RADIUS)
                nav_t   = 75.0
                # suppress arbiter: don't let monster detours interrupt event
                # navigation — the character must reach the activation point
                suppress = True
            elif stype == "cluster":
                tol     = 400.0
                nav_t   = 18.0
                # DO NOT suppress: the 60 Hz arbiter will pick up any adjacent
                # groups encountered during travel at no extra route cost
                suppress = False
            else:  # boss
                tol     = 300.0
                nav_t   = 45.0
                suppress = True

            # ── Carjack: pre-clear nearby area before triggering the truck ─
            if stype == "event" and stop["evt"] and \
                    stop["evt"].event_type.lower() == "carjack":
                sl_centers = [
                    (s["x"], s["y"])
                    for s in route
                    if s["type"] == "event"
                    and s["evt"]
                    and s["evt"].event_type.lower() == "sandlord"
                ]
                self._preclear_event_area(
                    tx,
                    ty,
                    radius=2500.0,
                    budget_s=16.0,
                    is_cancelled=is_cancelled,
                    avoid_centers=sl_centers,
                )
                if is_cancelled():
                    break

            # ── Navigate ──────────────────────────────────────────────────
            if stype == "cluster":
                # Mid-level re-evaluation every 3 s during cluster travel.
                # Aborts early (without resetting the 60 Hz loop) when:
                #   1. Cluster dissolved — AoE already cleared the area.
                #   2. A significantly closer cluster appeared (>2 500 u nearer
                #      from actual position) — reroute; rebuild will re-sort.
                # Events and boss are mandatory (full timeout, handled below).
                _eval_t   = [time.time()]
                _exit_why = [None]   # "dissolved" | "reroute"

                def _cluster_cancel() -> bool:
                    if is_cancelled():
                        return True
                    with self._lock:
                        if not self._hard_stalled:
                            return False
                    now = time.time()
                    if now - _eval_t[0] < 3.0:
                        return False
                    _eval_t[0] = now
                    _cx, _cy = self._current_pos()
                    # Check 1 — cluster dissolved
                    near_now = [
                        e for e in get_alive()
                        if math.hypot(e.position[0] - tx,
                                      e.position[1] - ty) <= 1800.0
                    ]
                    if not near_now:
                        _exit_why[0] = "dissolved"
                        return True
                    # Check 2 — significantly closer alternative
                    cur_d      = math.hypot(tx - _cx, ty - _cy)
                    best_other = min(
                        (math.hypot(s["x"] - _cx, s["y"] - _cy)
                         for s in route[1:] if s["type"] == "cluster"),
                        default=float("inf"))
                    if best_other < cur_d - 2500.0:
                        _exit_why[0] = "reroute"
                        return True
                    return False

                log.info(f"[RTNav] Unified [C] ({tx:.0f},{ty:.0f}) d={dist:.0f}")
                task = self._build_navigation_task(
                    "cluster",
                    tx,
                    ty,
                    tolerance=tol,
                    timeout_s=nav_t,
                    suppress_arbiter=False,
                )
                reached = self._task_nav.execute(task, _cluster_cancel)
                if _exit_why[0] == "dissolved":
                    log.info(f"[RTNav] [C] ({tx:.0f},{ty:.0f}) "
                             f"cleared mid-travel — skip")
                elif _exit_why[0] == "reroute":
                    log.info(f"[RTNav] [C] ({tx:.0f},{ty:.0f}) "
                             f"outpaced by closer cluster — rerouting")
            else:
                log.info(f"[RTNav] Unified [{stype[0].upper()}] "
                         f"({tx:.0f},{ty:.0f}) d={dist:.0f}")
                task = self._build_navigation_task(
                    stype,
                    tx,
                    ty,
                    tolerance=tol,
                    timeout_s=nav_t,
                    suppress_arbiter=suppress,
                    metadata={"event_type": stop["evt"].event_type} if stype == "event" and stop["evt"] else {},
                )
                reached = self._task_nav.execute(task, is_cancelled)

            if is_cancelled():
                break

            route.pop(0)  # consume this stop regardless of reached/not-reached

            # ── Stop-type dispatch ─────────────────────────────────────────
            if stype == "event":
                evt = stop["evt"]
                if not reached:
                    log.warning(f"[RTNav] Unified: could not reach "
                                f"{evt.event_type} — skipping handler")
                else:
                    handled_evts.add(evt.address)
                    with self._lock:
                        self._override_goal   = None
                        self._override_expiry = 0.0
                        self._path            = []
                    time.sleep(0.08)
                    try:
                        self._evt_hdlr(evt)
                    except Exception as exc:
                        log.error(f"[RTNav] event handler raised: {exc}")
                    if is_cancelled():
                        break
                    time.sleep(0.3)

            elif stype == "cluster":
                if reached:  # no dwell when redirected mid-travel
                    t_dwell = time.time() + 1.5
                    while not is_cancelled() and time.time() < t_dwell:
                        time.sleep(0.2)

            elif stype == "boss":
                if reached:
                    log.info("[RTNav] Unified: boss arena reached — 3s dwell")
                    self._stop_movement()
                    t_end = time.time() + 3.0
                    while not is_cancelled() and time.time() < t_end:
                        time.sleep(0.2)
                else:
                    log.warning("[RTNav] Unified: could not reach boss arena")

            if is_cancelled():
                break

            # ── Live route rebuild ─────────────────────────────────────────
            # Rebuild after every non-boss stop.  Monster clusters come from
            # a fresh scan; all non-boss stops are re-NN sorted from the
            # character's ACTUAL current position, so lag / wall-slide /
            # position drift are automatically corrected on every iteration.
            if stype != "boss":
                rem_evts = [
                    s["evt"] for s in route
                    if s["type"] == "event"
                    and s["evt"].address not in handled_evts
                ]
                # Inherit boss from remaining route or retry _boss_fn
                rem_boss = next(
                    ((s["x"], s["y"]) for s in route if s["type"] == "boss"),
                    None)
                if rem_boss is None:
                    try:
                        bp = self._boss_fn()
                        if bp:
                            boss_pos = bp
                            rem_boss = bp
                    except Exception:
                        pass

                px2, py2 = self._current_pos()
                route = build_route(px2, py2, rem_evts, rem_boss)
                # Remove already-handled events that slipped through
                route = [
                    s for s in route
                    if s["type"] != "event"
                    or s["evt"].address not in handled_evts
                ]
                log.debug(f"[RTNav] Unified: rebuilt → "
                          f"{len(route)} stop(s) remaining")

        if self._pf:
            self._pf.clear_avoid_zones()
        if time.time() >= deadline:
            log.warning("[RTNav] Unified phase timeout")
        return not is_cancelled()

    def _preclear_event_area(self, ex: float, ey: float, radius: float,
                              budget_s: float, is_cancelled: Callable[[], bool],
                              avoid_centers: Optional[List[Tuple[float, float]]] = None) -> None:
        """rush_events / kill_all: quick local pre-clear around a Carjack truck.

        Navigates to nearby alive monsters within *radius* world units of the
        event position for up to *budget_s* seconds.  Returns early when the
        area looks clean for 2 consecutive scans.
        """
        if not self._scanner:
            return
        deadline  = time.time() + budget_s
        radius_sq = radius * radius
        avoid_centers = avoid_centers or []
        avoid_radius_sq = 550.0 ** 2
        stable    = 0
        log.info(f"[RTNav] Pre-clear around event ({ex:.0f},{ey:.0f}) r={radius:.0f}")

        while not is_cancelled() and time.time() < deadline:
            entities = self._scanner.get_monster_entities() or []
            local = [
                e for e in entities
                if e.bvalid != 0
                and (e.position[0] - ex) ** 2 + (e.position[1] - ey) ** 2 <= radius_sq
                and all(
                    (e.position[0] - ax) ** 2 + (e.position[1] - ay) ** 2 > avoid_radius_sq
                    for ax, ay in avoid_centers
                )
            ]

            if not local:
                stable += 1
                if stable >= 2:
                    log.info("[RTNav] Pre-clear complete")
                    return
                time.sleep(0.35)
                continue

            stable = 0
            px, py = self._current_pos()
            local.sort(key=lambda e: (e.position[0] - px) ** 2 + (e.position[1] - py) ** 2)
            # Navigate to cluster centroid of the nearest dense group rather than
            # a single entity, so isolated walled-off monsters don't cause the bot
            # to charge into a wall repeatedly.
            nearest     = local[0]
            nx, ny      = nearest.position[0], nearest.position[1]
            cluster     = [e for e in local[:min(8, len(local))]
                           if math.hypot(e.position[0] - nx, e.position[1] - ny) <= 700.0]
            tx = sum(e.position[0] for e in cluster) / len(cluster)
            ty = sum(e.position[1] for e in cluster) / len(cluster)
            task = self._build_navigation_task(
                "preclear",
                tx,
                ty,
                tolerance=450.0,
                timeout_s=8.0,
                suppress_arbiter=True,
            )
            self._task_nav.execute(task, is_cancelled)

    # ────────────────────────────────────────────────────────────────────────
    # Core movement helper: set phase goal and wait for arrival
    # ────────────────────────────────────────────────────────────────────────

    def _navigate_to(self, gx: float, gy: float, tolerance: float,
                     timeout: float, cancel_fn: Callable,
                     suppress_arbiter: bool = False,
                     no_progress_timeout: Optional[float] = None,
                     no_progress_dist: float = 0.0) -> bool:
        """Set the phase goal and block until arrived, timed out, or cancelled.

        The 60 Hz loop thread handles the actual steering.  This method simply
        monitors position and returns.

        suppress_arbiter: when True, the goal arbiter is disabled for the
            duration so kill_all monster detours do not interrupt critical
            event / portal approaches.
        """
        with self._lock:
            self._phase_goal         = (gx, gy)
            self._phase_goal_tol     = tolerance
            # Invalidate stale path so the loop replans immediately
            self._path               = []
            self._path_idx           = 0
            self._path_goal          = None
            self._stuck_frames       = 0
            self._drift_replan_hits  = 0
            # Reset progress tracker for the new goal
            self._progress_best_dist = float("inf")
            self._progress_t         = time.time()
            # Clear any active monster detour so the phase goal takes priority
            self._override_goal      = None
            self._arbiter_suppressed = suppress_arbiter

        try:
            deadline = time.time() + timeout
            stall_anchor = self._current_pos()
            stall_since = time.time()
            while time.time() < deadline:
                if cancel_fn():
                    return False
                px, py = self._current_pos()
                if math.hypot(px - gx, py - gy) <= tolerance:
                    return True

                if no_progress_timeout is not None and no_progress_timeout > 0.0:
                    moved = math.hypot(px - stall_anchor[0], py - stall_anchor[1])
                    if moved >= max(1.0, no_progress_dist):
                        stall_anchor = (px, py)
                        stall_since = time.time()
                    elif time.time() - stall_since >= no_progress_timeout:
                        log.debug(
                            f"[RTNav] _navigate_to ({gx:.0f},{gy:.0f}) no-progress abort "
                            f"after {no_progress_timeout:.2f}s (moved {moved:.0f}u)"
                        )
                        with self._lock:
                            self._metrics["navigate_no_progress_abort"] += 1
                        return False
                time.sleep(0.04)  # check at 25 Hz — no need to poll faster

            log.warning(f"[RTNav] _navigate_to ({gx:.0f},{gy:.0f}) timed out after {timeout:.0f}s")
            with self._lock:
                self._metrics["navigate_timeout"] += 1
            return False
        finally:
            with self._lock:
                self._phase_goal         = None
                self._arbiter_suppressed = False

    def _stop_movement(self):
        """Move cursor to CHARACTER_CENTER to halt the character."""
        self._input.move_mouse(*CHARACTER_CENTER)

    # ────────────────────────────────────────────────────────────────────────
    # 60 Hz loop
    # ────────────────────────────────────────────────────────────────────────

    def _loop(self):
        """Main 60 Hz navigation loop — runs on _loop_thread."""
        _timer_set = False
        try:
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.winmm.timeBeginPeriod(1)
                    _timer_set = True
                except Exception:
                    pass

            while True:
                with self._lock:
                    if self._cancelled:
                        break
                t0 = time.monotonic()
                try:
                    self._tick()
                except Exception as exc:
                    log.error(f"[RTNav] tick error: {exc}")
                elapsed = time.monotonic() - t0
                remaining = _TICK_S - elapsed
                if remaining > 0.0002:
                    time.sleep(remaining)

        finally:
            if _timer_set:
                try:
                    import ctypes
                    ctypes.windll.winmm.timeEndPeriod(1)
                except Exception:
                    pass
            with self._lock:
                self._loop_running = False

    # ────────────────────────────────────────────────────────────────────────
    # Per-tick logic
    # ────────────────────────────────────────────────────────────────────────

    def _tick(self):
        self._tick_cnt += 1

        # 1. Read current position
        px, py = self._read_pos_direct()
        with self._lock:
            old_px, old_py = self._pos
            self._pos = (px, py)

        # 1b. Update heading buffer (per-frame displacement vector)
        dx_frame = px - old_px
        dy_frame = py - old_py
        self._heading_buf.append((dx_frame, dy_frame))
        self._stall_buf.append((px, py))
        hard_stalled = False
        if len(self._stall_buf) >= RT_NAV_HARD_STALL_FRAMES:
            sx, sy = self._stall_buf[0]
            wx, wy = self._stall_buf[-1]
            hard_stalled = math.hypot(wx - sx, wy - sy) < RT_NAV_HARD_STALL_DIST
        with self._lock:
            self._hard_stalled = hard_stalled

        # 1c. Non-blocking escape state machine.
        # When active, steer toward the escape target for ESCAPE_DURATION_S
        # instead of following the normal goal path.  The 60 Hz loop never
        # stalls — steering continues throughout recovery.
        with self._lock:
            esc_target   = self._escape_target
            esc_deadline = self._escape_deadline
            esc_gx       = self._escape_gx
            esc_gy       = self._escape_gy

        if esc_target is not None:
            now_esc = time.time()
            dist_esc = math.hypot(px - esc_target[0], py - esc_target[1])
            if now_esc >= esc_deadline or dist_esc < 100.0:
                # Escape phase complete — force replan from new position
                moved = math.hypot(px - old_px, py - old_py)
                log.debug(f"[RTNav] Escape done at ({px:.0f},{py:.0f})")
                with self._lock:
                    self._escape_target = None
                    # Reset progress tracker from new position
                    self._progress_best_dist = math.hypot(px - esc_gx, py - esc_gy)
                    self._progress_t         = time.time()
                    self._stuck_frames       = 0
                self._request_replan(px, py, esc_gx, esc_gy)
            else:
                self._steer_direct(px, py, esc_target[0], esc_target[1])
            self._spam_loot()
            return

        # 2. Determine active goal
        with self._lock:
            now = time.time()
            if self._override_goal and now < self._override_expiry:
                goal    = self._override_goal
                tol     = self._override_tol
                is_over = True
            else:
                self._override_goal = None
                goal    = self._phase_goal
                tol     = self._phase_goal_tol
                is_over = False

        if goal is None:
            return  # nothing to navigate to — stay still

        gx, gy = goal

        # 3. Check if active goal already reached
        dist_goal = math.hypot(px - gx, py - gy)
        if dist_goal <= tol:
            if is_over:
                with self._lock:
                    self._override_goal = None
            return

        # 3a. Portal-hop assist: when current path is intentionally routed to a
        # reachable portal (direct goal path was unavailable), press interact
        # near the portal and force rapid replans toward the original goal.
        with self._lock:
            hop_target = self._portal_hop_target
            hop_key = self._portal_hop_key
            hop_next_try_t = self._portal_hop_next_try_t
            hop_last_interact_pos = self._portal_hop_last_interact_pos

        if hop_target is not None:
            now_hop = time.time()
            verify_hop = bool((self._config or {}).get("portal_transition_verify", True))

            # Transition verification: if we recently interacted with the hop
            # portal and the character position jumped significantly, treat it
            # as successful portal traversal and clear hop state.
            if verify_hop and hop_last_interact_pos is not None:
                hop_moved = math.hypot(px - hop_last_interact_pos[0], py - hop_last_interact_pos[1])
                if hop_moved >= 900.0:
                    self._learn_portal_link_from_transition(hop_key, px, py)
                    now_confirm = time.time()
                    with self._lock:
                        # Anti-bounce guard: after successful transition through
                        # a hop portal, suppress immediate re-selection of the
                        # same portal so planner can continue deeper instead of
                        # instantly hopping back.
                        if hop_key is not None:
                            self._portal_hop_cooldowns[hop_key] = time.time() + 12.0
                        self._portal_hop_target = None
                        self._portal_hop_key = None
                        self._portal_hop_next_try_t = 0.0
                        self._portal_hop_last_interact_pos = None
                        # Short hold right after teleport to avoid immediate
                        # re-hopping through nearby return-side portals.
                        self._portal_hop_arrival_hold_until = now_confirm + 3.5
                        self._path = []
                        self._path_idx = 0
                    # Important: the immediate bounce source after teleport is
                    # usually the arrival-side return portal near current pos,
                    # not the departure portal key above.
                    self._cooldown_arrival_return_portal(px, py, duration_s=12.0)
                    log.info(f"[RTNav] Portal transition confirmed (moved {hop_moved:.0f}u) — replanning to original goal")
                    self._request_replan(px, py, gx, gy)

            dh = math.hypot(px - hop_target[0], py - hop_target[1])
            if dh <= 260.0 and now_hop >= hop_next_try_t:
                try:
                    interact_key = (self._config or {}).get("interact_key", "f")
                    self._input.press_key(interact_key)
                except Exception:
                    pass
                with self._lock:
                    self._portal_hop_next_try_t = now_hop + 0.75
                    self._portal_hop_last_interact_pos = (px, py)
                    self._path = []
                    self._path_idx = 0
                self._request_replan(px, py, gx, gy)

        # 3b. Goal-progress stuck detection.
        # Catches wall-sliding (character moves at full speed but sideways) which
        # raw per-frame displacement misses — frame_dist can be 16+ units/tick
        # even when the character is stuck against a wall going nowhere useful.
        now_t = time.time()
        with self._lock:
            if dist_goal < self._progress_best_dist - RT_NAV_PROGRESS_MIN:
                self._progress_best_dist = dist_goal
                self._progress_t         = now_t
            progress_stalled = (now_t - self._progress_t) > RT_NAV_PROGRESS_TIMEOUT

        if progress_stalled and hard_stalled:
            log.debug(f"[RTNav] Progress stalled at ({px:.0f},{py:.0f}) dist={dist_goal:.0f} "
                      f"— no improvement in {RT_NAV_PROGRESS_TIMEOUT:.0f}s, escaping")
            self._handle_stuck(px, py, gx, gy)
            return

        # 4. Stuck detection — consecutive frames with near-zero displacement
        frame_dist = math.hypot(dx_frame, dy_frame)
        with self._lock:
            if frame_dist < RT_NAV_STUCK_DIST:
                self._stuck_frames += 1
                stuck = self._stuck_frames >= RT_NAV_STUCK_FRAMES
            else:
                self._stuck_frames = 0
                stuck = False

        if stuck:
            self._handle_stuck(px, py, gx, gy)
            return

        # 5. Goal arbiter (4 Hz): consider monster detour.
        # Suppressed during critical _navigate_to calls (e.g. event approach).
        with self._lock:
            _arb_ok = not self._arbiter_suppressed
        if _arb_ok and self._tick_cnt % _GOAL_ARBITER_TICKS == 0:
            self._run_goal_arbiter(px, py, gx, gy)

        # 6. Ensure path is current (replans when needed)
        with self._lock:
            path_goal   = self._path_goal
            last_replan = self._last_replan_t
            has_path    = bool(self._path)
            path_copy   = list(self._path) if self._path else []
            p_idx       = self._path_idx
            drift_hits  = self._drift_replan_hits

        goal_changed = path_goal != (gx, gy)

        # 6b. Path-deviation detection: if player drifted too far from the
        # current path segment, replan immediately instead of waiting for the
        # periodic safety-net interval.  Catches wall slides within 1-2 ticks.
        drift_replan = False
        if has_path and not goal_changed and p_idx < len(path_copy):
            # Evaluate deviation against a short polyline window around the
            # current waypoint index, not just a single segment. This avoids
            # false drift when steering naturally follows a farther lookahead
            # waypoint in open terrain.
            seg_lo = max(0, p_idx - 1)
            seg_hi = min(len(path_copy) - 1, p_idx + 3)
            deviation = float("inf")
            for i in range(seg_lo, seg_hi):
                a = path_copy[i]
                b = path_copy[i + 1]
                d = self._point_to_segment_dist(px, py, a[0], a[1], b[0], b[1])
                if d < deviation:
                    deviation = d

            # If movement speed is healthy, drift should not trigger immediate
            # replans; otherwise we get oscillation even while traversing open
            # areas. Slow-movement drift still accumulates and can replan.
            if deviation > RT_NAV_DRIFT_THRESHOLD and hard_stalled:
                drift_hits += 1
            elif deviation > RT_NAV_DRIFT_THRESHOLD and not hard_stalled:
                drift_hits = max(0, drift_hits - 2)
            else:
                drift_hits = max(0, drift_hits - 1)
            with self._lock:
                self._drift_replan_hits = drift_hits
            if drift_hits >= 10 and (time.time() - last_replan) > 1.5:
                log.debug(f"[RTNav] Drift {deviation:.0f}u sustained ({drift_hits} ticks) — replanning")
                drift_replan = True
        else:
            with self._lock:
                self._drift_replan_hits = 0

        # Periodic safety-net replan (8 s) — catches all other stale-path cases.
        interval_hit = hard_stalled and (time.time() - last_replan > RT_NAV_REPLAN_INTERVAL)
        if goal_changed or drift_replan or interval_hit:
            self._request_replan(px, py, gx, gy)

        # 7. Steer toward best lookahead waypoint
        self._steer(px, py, gx, gy)

        # 8. Spam loot key
        self._spam_loot()

    # ────────────────────────────────────────────────────────────────────────
    # A* path planning
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _replan_signature(px: float, py: float, gx: float, gy: float) -> Tuple[int, int, int, int]:
        """Coarse signature for duplicate replan suppression (100u bins)."""
        return (
            int(round(px / 100.0)),
            int(round(py / 100.0)),
            int(round(gx / 100.0)),
            int(round(gy / 100.0)),
        )

    def _request_replan(self, px: float, py: float, gx: float, gy: float,
                        force: bool = False):
        """Submit an A* replan request to the background worker thread.

        Non-blocking — the 60 Hz loop continues steering the existing path
        (or direct-to-goal) while the worker computes.  Only one request is
        active at a time; newer requests override stale ones.
        """
        now = time.time()
        sig = self._replan_signature(px, py, gx, gy)
        min_dt = float((self._config or {}).get("rt_nav_replan_duplicate_cooldown_s", 0.45) or 0.45)

        with self._lock:
            self._metrics["replans_requested"] += 1
            if (not force
                    and self._last_replan_sig == sig
                    and (now - self._last_replan_t) < min_dt):
                self._metrics["replans_suppressed"] += 1
                return

            self._replan_request = (px, py, gx, gy)
            self._last_replan_t  = now  # prevent re-queueing
            self._last_replan_sig = sig

            if not self._replan_pending:
                self._replan_pending = True
                t = threading.Thread(target=self._replan_worker_fn,
                                     daemon=True, name="RTNavReplan")
                t.start()

    def _replan_worker_fn(self):
        """Worker thread: run A* and atomically install the new path."""
        try:
            while True:
                with self._lock:
                    req = self._replan_request
                    self._replan_request = None
                if req is None:
                    break
                self._do_replan(*req)
                # Check if another request arrived while we were computing
                with self._lock:
                    if self._replan_request is None:
                        break
        finally:
            with self._lock:
                self._replan_pending = False

    def _do_replan(self, px: float, py: float, gx: float, gy: float):
        """Run A* from (px,py) to (gx,gy) and install the new path.

        Called on the replan worker thread.
        """
        hop_target: Optional[Tuple[float, float]] = None
        hop_key: Optional[Tuple[int, int]] = None
        if self._pf and self._pf.has_grid:
            path = self._pf.find_path(px, py, gx, gy,
                                       max_nodes=AUTO_NAV_ASTAR_MAX_NODES)
            if not path:
                hop_plan = self._find_portal_hop_path(px, py, gx, gy)
                if hop_plan is not None:
                    path, hop_target, hop_key = hop_plan
        else:
            # No grid: straight-line path; steering handles the rest
            path = [(px, py), (gx, gy)]

        with self._lock:
            # Discard result if the goal has changed since the request was made
            current_goal = self._phase_goal or self._override_goal
            if current_goal and current_goal != (gx, gy):
                return  # stale — a newer request will overwrite

            self._path               = path or []
            self._path_idx           = 0
            self._path_goal          = (gx, gy)
            self._last_replan_t      = time.time()
            self._stuck_frames       = 0  # reset after every replan
            self._drift_replan_hits  = 0
            # Fresh progress window after each replan — new path may take a
            # different route so give it a full timeout to show improvement.
            self._progress_best_dist = math.hypot(px - gx, py - gy)
            self._progress_t         = time.time()
            self._portal_hop_target  = hop_target
            self._portal_hop_key     = hop_key
            self._portal_hop_next_try_t = 0.0
            self._portal_hop_last_interact_pos = None

            if path:
                self._metrics["replans_success"] += 1
                self._consecutive_no_path = 0
            else:
                self._metrics["no_path"] += 1
                self._consecutive_no_path += 1

        if path:
            wp_str = " ".join(f"({int(x)},{int(y)})" for x, y in path[:10])
            extra = f" (+{len(path)-10} more)" if len(path) > 10 else ""
            if hop_target is not None:
                log.info(f"[RTNav] A* no direct path; routing via portal "
                         f"({hop_target[0]:.0f},{hop_target[1]:.0f})")
            log.debug(f"[RTNav] A* -> {len(path)} wp to ({gx:.0f},{gy:.0f}): {wp_str}{extra}")
            # Feed path to overlay for visual debugging
            if self._overlay:
                try:
                    self._overlay.set_auto_path(path)
                except Exception:
                    pass
        else:
            # If we failed even after trying portal-hop candidates, cooldown the
            # last attempted hop portal briefly so the next replan can try a
            # different one instead of looping the same candidate.
            if hop_key is not None:
                with self._lock:
                    self._portal_hop_cooldowns[hop_key] = time.time() + 8.0

            # Fallback ladder (local recovery): after no-path, arm a short
            # side/back nudge so the next replan samples a different start cell.
            # This avoids repeating identical failed replans at the same spot.
            nudge_s = float((self._config or {}).get("rt_nav_nopath_escape_duration_s", 0.35) or 0.35)
            with self._lock:
                step_idx = self._escape_idx % len(self.ESCAPE_ANGLES)
                self._escape_idx += 1
            angle_deg = self.ESCAPE_ANGLES[step_idx]
            angle_rad = math.radians(angle_deg)
            step_u = WALL_GRID_CELL_SIZE * 2.0
            nudge_wx = px + math.cos(angle_rad) * step_u
            nudge_wy = py + math.sin(angle_rad) * step_u
            with self._lock:
                self._escape_target = (nudge_wx, nudge_wy)
                self._escape_deadline = time.time() + nudge_s
                self._escape_gx = gx
                self._escape_gy = gy
            log.warning(f"[RTNav] A* found no path to ({gx:.0f},{gy:.0f})")

    def _find_portal_hop_path(self, px: float, py: float,
                              gx: float, gy: float
                              ) -> Optional[Tuple[List[Tuple[float, float]], Tuple[float, float], Tuple[int, int]]]:
        """Try a one-hop plan via a reachable portal when direct A* fails.

        Returns:
          (path_to_portal, portal_xy, portal_key) or None
        """
        if not self._pf or not self._pf.has_grid or not self._portal_det:
            return None

        markers: List[Dict[str, Any]] = []
        try:
            if hasattr(self._portal_det, "get_portal_markers"):
                markers = self._portal_det.get_portal_markers() or []
            elif hasattr(self._portal_det, "get_portal_positions"):
                raw = self._portal_det.get_portal_positions() or []
                markers = [{"x": p[0], "y": p[1], "is_exit": False} for p in raw]
        except Exception:
            return None

        map_name = (self._map_name_for_walls or "").strip()
        hardcoded_markers: List[Dict[str, Any]] = []
        hardcoded_name_to_xy: Dict[str, Tuple[float, float]] = {}
        hardcoded_dest_by_key: Dict[Tuple[int, int], Tuple[float, float]] = {}
        hardcoded_meta_by_key: Dict[Tuple[int, int], Dict[str, Any]] = {}
        if map_name:
            for hp in (HARDCODED_MAP_PORTALS.get(map_name, []) or []):
                try:
                    hx = float(hp.get("x", 0.0))
                    hy = float(hp.get("y", 0.0))
                    hname = str(hp.get("name", "") or "").strip()
                    hpair = str(hp.get("pair", "") or "").strip()
                    his_return = bool(hp.get("is_return", False))
                    if not his_return and hname:
                        lname = hname.lower()
                        his_return = lname.startswith("return") or ("_return" in lname)
                    hprio = int(hp.get("hop_priority", 500) or 500)
                    hardcoded_markers.append({
                        "x": hx,
                        "y": hy,
                        "name": hname,
                        "pair": hpair,
                        "is_return": his_return,
                        "hop_priority": hprio,
                        "is_exit": bool(hp.get("is_exit", False)),
                        "use_for_hop": bool(hp.get("use_for_hop", True)),
                    })
                    if hname:
                        hardcoded_name_to_xy[hname] = (hx, hy)
                    hkey = (int(round(hx)), int(round(hy)))
                    hardcoded_meta_by_key[hkey] = {
                        "name": hname,
                        "is_return": his_return,
                        "hop_priority": hprio,
                    }
                except Exception:
                    continue

        if hardcoded_markers and hardcoded_name_to_xy:
            for hm in hardcoded_markers:
                try:
                    hpair = str(hm.get("pair", "") or "").strip()
                    if not hpair:
                        continue
                    dest_xy = hardcoded_name_to_xy.get(hpair)
                    if not dest_xy:
                        continue
                    hkey = (int(round(float(hm.get("x", 0.0)))), int(round(float(hm.get("y", 0.0)))))
                    hardcoded_dest_by_key[hkey] = (float(dest_xy[0]), float(dest_xy[1]))
                except Exception:
                    continue

        with self._lock:
            learned_dest_by_key = dict(self._portal_link_dest_by_key)

        dest_by_key: Dict[Tuple[int, int], Tuple[float, float]] = {}
        dest_by_key.update(learned_dest_by_key)
        dest_by_key.update(hardcoded_dest_by_key)

        if markers and map_name:
            self._record_portal_priors(map_name, markers)

        goal_is_exit = False
        try:
            exit_pos = self._portal_det.get_exit_portal_position()
            if exit_pos is not None:
                goal_is_exit = math.hypot(gx - float(exit_pos[0]), gy - float(exit_pos[1])) <= 500.0
        except Exception:
            goal_is_exit = False

        if not markers and map_name:
            prior_pts = self._load_portal_priors(map_name)
            if prior_pts:
                markers = [{"x": x, "y": y, "is_exit": False} for (x, y) in prior_pts]

        if hardcoded_markers:
            existing = {
                (int(round(float(m.get("x", 0.0)))), int(round(float(m.get("y", 0.0)))) )
                for m in markers
            }
            for hm in hardcoded_markers:
                key = (int(round(hm["x"])), int(round(hm["y"])))
                if key in existing:
                    continue
                markers.append(hm)
                existing.add(key)

        if not markers:
            return None

        # Reliability guard: with only a single observed portal marker, hop
        # routing is usually low-value/noisy (common at map start before portal
        # set stabilizes). Keep pursuing normal no-path recovery until at least
        # two non-exit portal markers are available.
        if not goal_is_exit:
            non_exit_count = 0
            for m in markers:
                try:
                    if not bool(m.get("is_exit", False)):
                        non_exit_count += 1
                except Exception:
                    continue
            if non_exit_count < 2 and map_name:
                prior_pts = self._load_portal_priors(map_name)
                if prior_pts:
                    existing = {
                        (int(round(float(m.get("x", 0.0)))), int(round(float(m.get("y", 0.0)))) )
                        for m in markers
                    }
                    for x, y in prior_pts:
                        key = (int(round(x)), int(round(y)))
                        if key in existing:
                            continue
                        markers.append({"x": x, "y": y, "is_exit": False})
                        existing.add(key)
                    non_exit_count = sum(1 for m in markers if not bool(m.get("is_exit", False)))
            if non_exit_count < 2:
                return None

        now = time.time()
        with self._lock:
            arrival_hold_until = self._portal_hop_arrival_hold_until
            no_path_streak = int(self._consecutive_no_path)
        candidate_best = None
        best_rank: Optional[Tuple[int, float]] = None
        goal_dist_before = math.hypot(px - gx, py - gy)
        min_improve_u = 250.0
        require_known_dest = bool((self._config or {}).get("portal_hop_require_known_destination", True))

        for m in markers:
            try:
                mx = float(m.get("x", 0.0))
                my = float(m.get("y", 0.0))
                is_exit = bool(m.get("is_exit", False))
                use_for_hop = bool(m.get("use_for_hop", True))
                mname = str(m.get("name", "") or "").strip()
                mis_return = bool(m.get("is_return", False))
                mprio = int(m.get("hop_priority", 500) or 500)
            except Exception:
                continue

            if not use_for_hop:
                continue

            # Guard against exit/mid-portal semantic confusion:
            # never use exit portal as an intermediate hop unless we are
            # currently navigating to the exit itself.
            if is_exit and not goal_is_exit:
                continue

            # Skip candidate nearly identical to current position or goal.
            if math.hypot(mx - px, my - py) < 250.0:
                continue
            if math.hypot(mx - gx, my - gy) < 300.0:
                continue

            key = (int(round(mx)), int(round(my)))
            meta = hardcoded_meta_by_key.get(key) or {}
            if meta:
                if not mname:
                    mname = str(meta.get("name", "") or "")
                mis_return = bool(meta.get("is_return", mis_return))
                try:
                    mprio = int(meta.get("hop_priority", mprio) or mprio)
                except Exception:
                    pass

            # Return portals are heavily restricted by policy:
            # default: never use for non-exit goals.
            # rare recovery escape hatch: only when no-path streak is very high
            # and we are already near the return portal (possible wrong teleport).
            if mis_return and not goal_is_exit:
                rare_recovery = (no_path_streak >= 16 and math.hypot(mx - px, my - py) <= 1100.0)
                if not rare_recovery:
                    continue

            cooldown_until = self._portal_hop_cooldowns.get(key, 0.0)
            if cooldown_until > now:
                continue

            # Immediately after a hop transition, ignore portals very close to
            # current position for a short grace window to prevent A↔B bounce.
            if now < arrival_hold_until and math.hypot(mx - px, my - py) <= 1200.0:
                continue

            # Reachability test: only portals with a valid path from current
            # segment are considered; score prefers shorter approach + closer
            # post-portal distance to final goal.
            ppath = self._pf.find_path(
                px, py, mx, my,
                max_nodes=max(20000, AUTO_NAV_ASTAR_MAX_NODES // 3),
            )
            if not ppath:
                continue

            teleport_dest = dest_by_key.get(key)
            if teleport_dest is None:
                if require_known_dest and not goal_is_exit:
                    continue
                # Exit-goal safety: without destination evidence we still skip
                # unknown hops to avoid teleporting into disconnected sectors.
                continue

            dest_goal_dist = math.hypot(float(teleport_dest[0]) - gx, float(teleport_dest[1]) - gy)
            if not goal_is_exit and (goal_dist_before - dest_goal_dist) < min_improve_u:
                continue

            dpath = self._pf.find_path(
                float(teleport_dest[0]), float(teleport_dest[1]), gx, gy,
                max_nodes=max(20000, AUTO_NAV_ASTAR_MAX_NODES // 3),
            )
            if not dpath:
                continue

            score = float(len(ppath)) + 0.002 * dest_goal_dist
            rank = (int(mprio), float(score))
            if best_rank is None or rank < best_rank:
                best_rank = rank
                candidate_best = (ppath, (mx, my), key)

        return candidate_best

    def _load_portal_priors(self, map_name: str) -> List[Tuple[float, float]]:
        if not map_name:
            return []
        try:
            with self._lock:
                if map_name in self._portal_priors_cache:
                    return list(self._portal_priors_cache.get(map_name, []))

            if not os.path.exists(PORTAL_PRIORS_FILE):
                with self._lock:
                    self._portal_priors_cache[map_name] = []
                return []

            with open(PORTAL_PRIORS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}

            raw = data.get(map_name, [])
            pts: List[Tuple[float, float]] = []
            for item in raw:
                try:
                    pts.append((float(item[0]), float(item[1])))
                except Exception:
                    continue
            with self._lock:
                self._portal_priors_cache[map_name] = pts
            return list(pts)
        except Exception:
            return []

    def _record_portal_priors(self, map_name: str, markers: List[Dict[str, Any]]) -> None:
        if not map_name:
            return

        live_pts: List[Tuple[float, float]] = []
        for m in markers:
            try:
                if bool(m.get("is_exit", False)):
                    continue
                x = float(m.get("x", 0.0))
                y = float(m.get("y", 0.0))
                live_pts.append((x, y))
            except Exception:
                continue

        if not live_pts:
            return

        try:
            prior = self._load_portal_priors(map_name)
            merged = list(prior)
            merge_dist = 350.0
            changed = False

            for x, y in live_pts:
                if any(math.hypot(x - px, y - py) <= merge_dist for px, py in merged):
                    continue
                merged.append((x, y))
                changed = True

            if not changed:
                return

            now = time.time()
            if now - self._portal_priors_last_save_t < 1.0:
                with self._lock:
                    self._portal_priors_cache[map_name] = merged
                return

            directory = os.path.dirname(PORTAL_PRIORS_FILE)
            if directory:
                os.makedirs(directory, exist_ok=True)

            data: Dict[str, List[List[float]]] = {}
            if os.path.exists(PORTAL_PRIORS_FILE):
                try:
                    with open(PORTAL_PRIORS_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f) or {}
                except Exception:
                    data = {}

            data[map_name] = [[round(p[0], 1), round(p[1], 1)] for p in merged]
            with open(PORTAL_PRIORS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            with self._lock:
                self._portal_priors_cache[map_name] = merged
                self._portal_priors_last_save_t = now
        except Exception:
            return

    def _cooldown_arrival_return_portal(self, px: float, py: float, duration_s: float = 12.0) -> None:
        """Cooldown nearest non-exit portal around current position.

        After hop transition, player appears near the paired return portal on
        the arrival side. Cooling this nearby portal avoids immediate
        bounce-back re-selection on the next replan cycle.
        """
        if not self._portal_det:
            return

        markers: List[Dict[str, Any]] = []
        try:
            if hasattr(self._portal_det, "get_portal_markers"):
                markers = self._portal_det.get_portal_markers() or []
            elif hasattr(self._portal_det, "get_portal_positions"):
                raw = self._portal_det.get_portal_positions() or []
                markers = [{"x": p[0], "y": p[1], "is_exit": False} for p in raw]
        except Exception:
            return

        if not markers:
            return

        nearest_non_exit_key: Optional[Tuple[int, int]] = None
        nearest_any_key: Optional[Tuple[int, int]] = None
        nearest_dist = float("inf")
        nearest_any_dist = float("inf")

        for m in markers:
            try:
                is_exit = bool(m.get("is_exit", False))
                mx = float(m.get("x", 0.0))
                my = float(m.get("y", 0.0))
            except Exception:
                continue

            d = math.hypot(px - mx, py - my)
            key = (int(round(mx)), int(round(my)))
            if d < nearest_any_dist:
                nearest_any_dist = d
                nearest_any_key = key
            if (not is_exit) and d < nearest_dist:
                nearest_dist = d
                nearest_non_exit_key = key

        # Arrival paired portal should be close after transition.
        selected_key = nearest_non_exit_key if nearest_non_exit_key is not None else nearest_any_key
        selected_dist = nearest_dist if nearest_non_exit_key is not None else nearest_any_dist
        if selected_key is not None and selected_dist <= 900.0:
            with self._lock:
                self._portal_hop_cooldowns[selected_key] = max(
                    self._portal_hop_cooldowns.get(selected_key, 0.0),
                    time.time() + max(2.0, float(duration_s)),
                )

    def _learn_portal_link_from_transition(self, source_key: Optional[Tuple[int, int]],
                                           px: float, py: float) -> None:
        """Learn source-portal -> arrival-side destination mapping after a hop.

        Uses the nearest non-exit portal around the post-transition position as
        destination anchor. This keeps hop routing destination-aware even when
        live portal markers are sparse or delayed.
        """
        if source_key is None or not self._portal_det:
            return

        markers: List[Dict[str, Any]] = []
        try:
            if hasattr(self._portal_det, "get_portal_markers"):
                markers = self._portal_det.get_portal_markers() or []
            elif hasattr(self._portal_det, "get_portal_positions"):
                raw = self._portal_det.get_portal_positions() or []
                markers = [{"x": p[0], "y": p[1], "is_exit": False} for p in raw]
        except Exception:
            return

        if not markers:
            return

        nearest_key: Optional[Tuple[int, int]] = None
        nearest_xy: Optional[Tuple[float, float]] = None
        nearest_dist = float("inf")

        for m in markers:
            try:
                if bool(m.get("is_exit", False)):
                    continue
                mx = float(m.get("x", 0.0))
                my = float(m.get("y", 0.0))
            except Exception:
                continue
            key = (int(round(mx)), int(round(my)))
            if key == source_key:
                continue
            d = math.hypot(px - mx, py - my)
            if d < nearest_dist:
                nearest_dist = d
                nearest_key = key
                nearest_xy = (mx, my)

        if nearest_key is None or nearest_xy is None or nearest_dist > 1200.0:
            return

        with self._lock:
            self._portal_link_dest_by_key[source_key] = (nearest_xy[0], nearest_xy[1])
            src_xy = self._portal_link_dest_by_key.get(source_key)
            if src_xy is not None:
                self._portal_link_dest_by_key[nearest_key] = (float(source_key[0]), float(source_key[1]))

    # ────────────────────────────────────────────────────────────────────────
    # Steering
    # ────────────────────────────────────────────────────────────────────────

    def _steer(self, px: float, py: float, gx: float, gy: float):
        """Move cursor toward the best lookahead target on the current path."""
        with self._lock:
            path = list(self._path)
            idx = self._path_idx
            path_goal = self._path_goal
            last_idx = self._last_steer_idx
            last_goal = self._last_steer_goal
            last_t = self._last_steer_t

        if not path:
            # No path yet — steer directly toward goal
            self._steer_direct(px, py, gx, gy)
            return

        # Advance past waypoints already within WAYPOINT_RADIUS
        original_idx = idx
        while idx < len(path) - 1:
            wpx, wpy = path[idx]
            if math.hypot(px - wpx, py - wpy) <= RT_NAV_WAYPOINT_RADIUS:
                idx += 1
            else:
                break
        if idx != original_idx:
            with self._lock:
                self._path_idx = idx

        # Lookahead: aim at the furthest ahead waypoint with clear line-of-sight
        target_idx = self._lookahead_index(path, idx, px, py)
        now = time.time()
        if (
            path_goal == last_goal
            and 0 <= last_idx < len(path)
            and target_idx < last_idx
            and (now - last_t) <= 0.45
        ):
            lx, ly = path[last_idx]
            if math.hypot(px - lx, py - ly) <= RT_NAV_LOOKAHEAD_DIST * 1.35:
                target_idx = last_idx

        tx, ty = path[target_idx]
        with self._lock:
            self._last_steer_idx = target_idx
            self._last_steer_goal = path_goal
            self._last_steer_t = now

        # 1 Hz diagnostic: log player pos + which waypoint is targeted
        if self._tick_cnt % RT_NAV_TICK_HZ == 0:
            fallback = (target_idx == idx)
            log.debug(f"[RTNav] Nav pos=({px:.0f},{py:.0f}) wp={target_idx}/{len(path)-1} "
                      f"target=({tx:.0f},{ty:.0f}) d={math.hypot(px-tx,py-ty):.0f}"
                      f"{' FALLBACK' if fallback else ''}")
        self._steer_direct(px, py, tx, ty)

    def _lookahead_index(self, path: list, from_idx: int,
                         px: float, py: float) -> int:
        """Return the furthest path index ahead of from_idx with clear LOS.

        Scans backward from the end of the path to find the first point that:
          (a) is within RT_NAV_LOOKAHEAD_DIST world units from the player, and
          (b) passes a DDA line-of-sight check against the walkability grid.
        Falls back to from_idx if nothing qualifies.
        """
        grid = self._pf._grid if (self._pf and self._pf.has_grid) else None

        for i in range(len(path) - 1, from_idx, -1):
            tx, ty = path[i]
            dist = math.hypot(px - tx, py - ty)
            if dist > RT_NAV_LOOKAHEAD_DIST:
                continue
            if grid is None:
                return i  # no grid — take furthest in-range
            # DDA LOS check through the walkability grid
            sr, sc = grid.world_to_grid(px, py)
            er, ec = grid.world_to_grid(tx, ty)
            if self._pf._line_clear(sr, sc, er, ec):
                return i

        # All forward waypoints are beyond LOOKAHEAD_DIST.  Rather than steering
        # blindly toward path[from_idx] (which may require crossing a wall corner
        # when the character has drifted laterally off the planned route), search
        # backward from from_idx for the nearest already-visited waypoint that
        # still has a clear line-of-sight.  This brings the character back on-path
        # safely instead of scraping against walls in narrow corridors.
        if grid is not None:
            sr2, sc2 = grid.world_to_grid(px, py)
            for i in range(from_idx, -1, -1):
                tx2, ty2 = path[i]
                er2, ec2 = grid.world_to_grid(tx2, ty2)
                if self._pf._line_clear(sr2, sc2, er2, ec2):
                    return i
        return from_idx  # absolute fallback — no grid or no clear LOS found

    def _steer_direct(self, px: float, py: float, tx: float, ty: float):
        """Move cursor toward world-space target (tx, ty).

        Uses the per-map MapCalibration matrix to project the world-space delta
        into screen-space pixel offsets, then normalises to a fixed 200 px radius.
        This handles every map's camera orientation (4 variants in TLI) correctly
        — without it Orient-180 maps (Singing Sand, WotLB, Defiled) steer in the
        wrong X direction since their world +X maps to screen LEFT not RIGHT.
        """
        dx = tx - px
        dy = ty - py
        if math.sqrt(dx * dx + dy * dy) < 1.0:
            return

        cal: Optional[MapCalibration] = (
            self._scale_cal.get_calibration() if self._scale_cal else None
        ) or DEFAULT_CALIBRATION

        cx, cy = CHARACTER_CENTER
        if cal is not None:
            # Project world delta through calibration inverse matrix → pixel delta
            px_off = cal.inv_a * dx + cal.inv_b * dy
            py_off = cal.inv_c * dx + cal.inv_d * dy
            pix_dist = math.sqrt(px_off * px_off + py_off * py_off)
            if pix_dist < 1.0:
                return
            sx = cx + int(px_off / pix_dist * 200)
            sy = cy + int(py_off / pix_dist * 200)
        else:
            # Absolute fallback (should never happen — DEFAULT_CALIBRATION always set)
            dist = math.sqrt(dx * dx + dy * dy)
            sx = cx + int((dx / dist) * 200)
            sy = cy - int((dy / dist) * 200)

        sx = max(50, min(1870, sx))
        sy = max(50, min(1030, sy))
        self._input.move_mouse(sx, sy)

    # ────────────────────────────────────────────────────────────────────────
    # Stuck handling
    # ────────────────────────────────────────────────────────────────────────

    def _handle_stuck(self, px: float, py: float, gx: float, gy: float):
        """Wall-aware, non-blocking stuck escape.

        1. Infer forward heading from the heading buffer.
        2. Mark the grid cell(s) ahead as blocked (learned wall).
        3. Choose an escape direction perpendicular to heading; prefer the
           side with more walkable cells (DDA-style ray probe).
        4. Arm the escape state machine (_escape_target / _escape_deadline)
           instead of sleeping — the 60 Hz loop steers toward the target
           until the deadline expires, then triggers a replan.
        """
        with self._lock:
            self._stuck_frames = 0  # reset immediately
            self._metrics["stuck_escapes"] += 1

        # ── 1. Heading inference ─────────────────────────────────────────────
        avg_hx, avg_hy = self._get_avg_heading()
        heading_valid = (avg_hx != 0.0 or avg_hy != 0.0)

        if heading_valid:
            hmag = math.hypot(avg_hx, avg_hy)
            fwd_x, fwd_y = avg_hx / hmag, avg_hy / hmag
        else:
            # No heading history — fall back to direction toward goal
            dx, dy = gx - px, gy - py
            dmag = math.hypot(dx, dy)
            if dmag < 1.0:
                fwd_x, fwd_y = 1.0, 0.0
            else:
                fwd_x, fwd_y = dx / dmag, dy / dmag

        # ── 2. Learn wall (mark cell ahead as blocked) ───────────────────────
        if self._pf and self._pf.has_grid:
            grid = self._pf._grid
            # Mark 1-2 cells ahead of current position as blocked
            for dist_mult in (1.0, 2.0):
                wall_wx = px + fwd_x * WALL_GRID_CELL_SIZE * dist_mult
                wall_wy = py + fwd_y * WALL_GRID_CELL_SIZE * dist_mult
                
                # Apply immediately to grid
                grid.mark_circle_blocked(wall_wx, wall_wy, WALL_GRID_CELL_SIZE * 0.9)
                
                # v5.8.0: keep this as a runtime-only correction. Persisting
                # blocked points across sessions can poison map connectivity.
                log.debug(f"[RTNav] Runtime hard-wall mark: world ({wall_wx:.0f},{wall_wy:.0f})")

        # ── 3. Wall-aware escape direction ───────────────────────────────────
        # Two perpendicular candidates: +90° and -90° from heading
        perp_left  = (-fwd_y,  fwd_x)   # 90° CCW
        perp_right = ( fwd_y, -fwd_x)   # 90° CW

        escape_wx, escape_wy = px, py  # fallback
        if self._pf and self._pf.has_grid:
            grid = self._pf._grid
            left_score  = self._ray_walkable_score(px, py, perp_left[0],  perp_left[1],  grid)
            right_score = self._ray_walkable_score(px, py, perp_right[0], perp_right[1], grid)

            if left_score >= right_score and left_score > 0:
                escape_wx = px + perp_left[0]  * WALL_GRID_CELL_SIZE * 3
                escape_wy = py + perp_left[1]  * WALL_GRID_CELL_SIZE * 3
            elif right_score > 0:
                escape_wx = px + perp_right[0] * WALL_GRID_CELL_SIZE * 3
                escape_wy = py + perp_right[1] * WALL_GRID_CELL_SIZE * 3
            else:
                # Both sides blocked — try backward
                escape_wx = px - fwd_x * WALL_GRID_CELL_SIZE * 3
                escape_wy = py - fwd_y * WALL_GRID_CELL_SIZE * 3
        else:
            # No grid — use rotating angle table as fallback
            with self._lock:
                angle_deg = self.ESCAPE_ANGLES[self._escape_idx % len(self.ESCAPE_ANGLES)]
                self._escape_idx += 1
            angle_rad = math.radians(angle_deg)
            escape_wx = px + math.cos(angle_rad) * WALL_GRID_CELL_SIZE * 3
            escape_wy = py + math.sin(angle_rad) * WALL_GRID_CELL_SIZE * 3

        # ── 4. Arm escape state machine (non-blocking) ──────────────────────
        with self._lock:
            self._escape_target   = (escape_wx, escape_wy)
            self._escape_deadline = time.time() + RT_NAV_ESCAPE_DURATION_S
            # Store the *navigation* goal (world coords) — after the escape
            # phase ends, _tick() replans from the new position toward this goal.
            self._escape_gx = gx
            self._escape_gy = gy

        log.debug(f"[RTNav] Stuck at ({px:.0f},{py:.0f}) — escaping toward "
                  f"({escape_wx:.0f},{escape_wy:.0f}) for {RT_NAV_ESCAPE_DURATION_S}s")

    def _ray_walkable_score(self, px: float, py: float,
                            dx: float, dy: float,
                            grid) -> int:
        """Count walkable cells along a ray from (px,py) in direction (dx,dy).

        Probes up to 5 cells.  Returns number of walkable cells encountered
        before hitting a blocked cell or grid boundary.
        """
        score = 0
        for step in range(1, 6):
            wx = px + dx * WALL_GRID_CELL_SIZE * step
            wy = py + dy * WALL_GRID_CELL_SIZE * step
            r, c = grid.world_to_grid(wx, wy)
            if grid.is_blocked(r, c):
                break
            score += 1
        return score

    # ────────────────────────────────────────────────────────────────────────
    # Heading & geometry helpers
    # ────────────────────────────────────────────────────────────────────────

    def _get_avg_heading(self) -> Tuple[float, float]:
        """Return smoothed heading (dx, dy) from the heading buffer.

        Returns (0, 0) if the buffer is empty or net displacement is negligible.
        """
        if not self._heading_buf:
            return 0.0, 0.0
        sx = sum(v[0] for v in self._heading_buf)
        sy = sum(v[1] for v in self._heading_buf)
        if abs(sx) < 0.001 and abs(sy) < 0.001:
            return 0.0, 0.0
        return sx, sy

    @staticmethod
    def _point_to_segment_dist(px: float, py: float,
                               ax: float, ay: float,
                               bx: float, by: float) -> float:
        """Perpendicular distance from point (px,py) to line segment (a→b).

        Returns the shortest Euclidean distance from the point to any location
        on the segment [a, b].
        """
        abx, aby = bx - ax, by - ay
        ab2 = abx * abx + aby * aby
        if ab2 < 1e-9:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / ab2))
        proj_x = ax + t * abx
        proj_y = ay + t * aby
        return math.hypot(px - proj_x, py - proj_y)

    # ────────────────────────────────────────────────────────────────────────
    # Goal arbiter (4 Hz)
    # ────────────────────────────────────────────────────────────────────────

    def _run_goal_arbiter(self, px: float, py: float,
                          phase_gx: float, phase_gy: float):
        """Decide whether to set a temporary overriding goal for a monster detour.

        Only fires the monster scan every other arbiter tick (2 Hz) because
        get_monster_entities() is a heavier GObjects scan.

        Detour triggers when:
          • ≥ RT_NAV_MONSTER_MIN_COUNT alive entities within RT_NAV_MONSTER_RADIUS
          • The cluster centroid is > 45° off the current heading to the phase goal
          • No override is already active
        This ensures the auto-bomber character walks through nearby packs without
        chasing every scattered monster across the entire map.
        """
        # Only run monster scan at 2 Hz
        if self._tick_cnt % _MONSTER_SCAN_TICKS != 0:
            return

        with self._lock:
            if self._override_goal and time.time() < self._override_expiry:
                return  # override already running

        if not self._scanner:
            return

        try:
            monsters = self._scanner.get_monster_entities()
        except Exception:
            return

        if not monsters:
            return

        # ── Mode-aware thresholds ─────────────────────────────────────────
        # kill_all: engage any cluster within a close radius regardless of
        # heading — we want to pick up every pack we pass near.
        # rush_events / boss_rush: only deviate for packs that are clearly
        # off our current heading (would be missed by direct navigation).
        if self._behavior == "kill_all":
            scan_radius = 1500.0   # tighter: only packs we're actually near
            min_count   = 2        # even small groups worth visiting
            angle_thresh = 360     # no angle filter — always detour
            override_dur = 8.0
        else:
            scan_radius = RT_NAV_MONSTER_RADIUS   # 2500
            min_count   = RT_NAV_MONSTER_MIN_COUNT  # 5
            angle_thresh = 45      # only groups clearly off the heading
            override_dur = 6.0

        # Filter: alive, valid coords, within mode scan radius
        radius_sq = scan_radius ** 2
        alive = [
            m for m in monsters
            if m.bvalid != 0
            and (abs(m.position[0]) > 1.0 or abs(m.position[1]) > 1.0)
            and (m.position[0] - px) ** 2 + (m.position[1] - py) ** 2 <= radius_sq
        ]

        if len(alive) < min_count:
            return

        # Cluster: nearest dense group, up to 8 members
        alive.sort(key=lambda m: (m.position[0]-px)**2 + (m.position[1]-py)**2)
        cluster = alive[:min(8, len(alive))]
        cx = sum(m.position[0] for m in cluster) / len(cluster)
        cy = sum(m.position[1] for m in cluster) / len(cluster)

        # Apply angle filter for non-kill_all modes
        diff = 0.0
        if angle_thresh < 360 and (abs(phase_gx) > 1.0 or abs(phase_gy) > 1.0):
            bearing_goal    = math.atan2(phase_gy - py, phase_gx - px)
            bearing_cluster = math.atan2(cy - py, cx - px)
            diff = abs(math.degrees(bearing_goal - bearing_cluster)) % 360
            if diff > 180:
                diff = 360 - diff
            if diff < angle_thresh:
                return  # cluster is already on the heading — no override needed

        # Reachability guard for detours: avoid setting an override toward a
        # centroid that is in a disconnected adjacent lane/room.
        if self._pf and self._pf.has_grid:
            cpath = self._pf.find_path(
                px, py, cx, cy,
                max_nodes=max(12000, AUTO_NAV_ASTAR_MAX_NODES // 5),
            )
            if not cpath:
                return

        log.debug(f"[RTNav] Monster detour ({self._behavior}): {len(alive)} monsters → "
                  f"centroid ({cx:.0f},{cy:.0f}), {diff:.0f}° off heading")

        with self._lock:
            self._override_goal    = (cx, cy)
            self._override_tol     = 350.0
            self._override_expiry  = time.time() + override_dur
            # Invalidate current path so the loop replans toward the cluster
            self._path      = []
            self._path_idx  = 0
            self._path_goal = None

    # ────────────────────────────────────────────────────────────────────────
    # Loot
    # ────────────────────────────────────────────────────────────────────────

    def _spam_loot(self):
        now = time.time()
        if now - self._loot_t >= self._loot_interval:
            loot_key = self._config.get("loot_key", "e")
            self._input.press_key(loot_key)
            self._loot_t = now

    # ────────────────────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────────────────────

    def _read_pos_direct(self) -> Tuple[float, float]:
        """Return the latest player world position.

        Prefers the shared PositionPoller (single 120 Hz reader, zero extra
        memory ops) when available.  Falls back to a direct read_chain()
        call, then to the cached last-known position on any error.
        """
        try:
            if self._pos_poller is not None:
                x, y = self._pos_poller.get_pos()
                if x != 0.0 or y != 0.0:
                    return x, y
            x = self._gs.read_chain("player_x")
            y = self._gs.read_chain("player_y")
            if x is not None and y is not None:
                return float(x), float(y)
        except Exception:
            pass
        with self._lock:
            return self._pos  # return last known on read failure

    def _current_pos(self) -> Tuple[float, float]:
        """Return latest known player position (thread-safe)."""
        with self._lock:
            return self._pos
