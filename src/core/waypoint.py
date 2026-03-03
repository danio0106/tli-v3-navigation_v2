"""Waypoint data class used by path recording, navigation and GUI overlays."""

from dataclasses import dataclass


@dataclass
class Waypoint:
    x: float
    y: float
    wp_type: str = "node"
    is_portal: bool = False
    label: str = ""
    wait_time: float = 0.0

    def distance_to(self, other_x: float, other_y: float) -> float:
        dx = self.x - other_x
        dy = self.y - other_y
        return (dx * dx + dy * dy) ** 0.5
