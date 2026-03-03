from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


@dataclass(frozen=True)
class NavigationTask:
    """High-level navigation intent produced by planners.

    Planners decide *what* target should be attempted next.
    Low-level navigator decides *how* to execute movement.
    """

    kind: str
    target_x: float
    target_y: float
    tolerance: float
    timeout_s: float
    suppress_arbiter: bool = True
    no_progress_timeout_s: Optional[float] = None
    no_progress_dist: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class GoalProvider(Protocol):
    """Planner interface that yields next movement task."""

    def next_task(self) -> Optional[NavigationTask]:
        ...
