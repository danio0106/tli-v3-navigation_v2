from typing import Callable

from src.core.navigation.contracts import NavigationTask


class TaskNavigator:
    """Adapter that executes planner tasks on RTNavigator.

    Keeps execution concerns centralized and reusable across planners
    (explorer, hideout routing, map-cycle phases).
    """

    def __init__(self, rt_navigator):
        self._rt_nav = rt_navigator

    def execute(self, task: NavigationTask, cancel_fn: Callable[[], bool]) -> bool:
        if hasattr(self._rt_nav, "execute_navigation_task"):
            return self._rt_nav.execute_navigation_task(task, cancel_fn)

        return self._rt_nav.navigate_to_target(
            task.target_x,
            task.target_y,
            tolerance=task.tolerance,
            timeout=task.timeout_s,
            cancel_fn=cancel_fn,
            no_progress_timeout=task.no_progress_timeout_s,
            no_progress_dist=task.no_progress_dist,
        )
