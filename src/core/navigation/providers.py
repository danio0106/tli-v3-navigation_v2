import math
from typing import Callable, Optional, Set

from src.core.navigation.contracts import GoalProvider, NavigationTask
from src.utils.constants import RT_NAV_GOAL_RADIUS


class EventGoalProvider(GoalProvider):
    """Plans next event-navigation task from current scanner state."""

    def __init__(self, scanner, current_pos_fn: Callable[[], tuple], handled_addrs: Set[int]):
        self._scanner = scanner
        self._current_pos_fn = current_pos_fn
        self._handled_addrs = handled_addrs

    def get_pending_events(self):
        if not self._scanner:
            return []
        events = self._scanner.get_typed_events() or []
        return [
            e for e in events
            if e.is_target_event and e.address not in self._handled_addrs
        ]

    def next_task(self) -> Optional[NavigationTask]:
        pending = self.get_pending_events()
        if not pending:
            return None

        px, py = self._current_pos_fn()
        pending.sort(key=lambda e: math.hypot(e.position[0] - px, e.position[1] - py))
        evt = pending[0]
        tolerance = 150.0 if evt.event_type.lower() == "sandlord" else RT_NAV_GOAL_RADIUS

        return NavigationTask(
            kind="event",
            target_x=evt.position[0],
            target_y=evt.position[1],
            tolerance=tolerance,
            timeout_s=75.0,
            suppress_arbiter=True,
            metadata={"event": evt, "event_type": evt.event_type},
        )


class BossGoalProvider(GoalProvider):
    """Plans boss-arena navigation task from boss locator callback."""

    def __init__(self, boss_locate_fn: Callable[[], Optional[tuple]]):
        self._boss_locate_fn = boss_locate_fn

    def next_task(self) -> Optional[NavigationTask]:
        boss_pos = self._boss_locate_fn()
        if not boss_pos:
            return None
        bx, by = boss_pos
        return NavigationTask(
            kind="boss",
            target_x=bx,
            target_y=by,
            tolerance=300.0,
            timeout_s=45.0,
            suppress_arbiter=True,
        )


class PortalGoalProvider(GoalProvider):
    """Plans exit-portal navigation task from PortalDetector state."""

    def __init__(self, portal_detector):
        self._portal_detector = portal_detector

    def next_task(self) -> Optional[NavigationTask]:
        if not self._portal_detector:
            return None
        ppos = self._portal_detector.get_exit_portal_position()
        if not ppos:
            return None
        px_w, py_w, _ = ppos
        return NavigationTask(
            kind="portal",
            target_x=px_w,
            target_y=py_w,
            tolerance=150.0,
            timeout_s=30.0,
            suppress_arbiter=True,
            metadata={"portal": ppos},
        )
