"""Thin Qt-to-engine bridge for safe incremental migration.

Phase 1 scope: read-only status data for shell pages.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class EngineStatus:
    attached: bool
    running: bool
    paused: bool
    current_map: str


class EngineBridge:
    """Read-only bridge that wraps BotEngine without changing runtime logic."""

    def __init__(self, engine):
        self._engine = engine

    @property
    def engine(self):
        return self._engine

    def get_status(self) -> EngineStatus:
        cfg = getattr(self._engine, "config", None)
        current_map = ""
        if cfg is not None:
            try:
                current_map = str(cfg.get("current_map", "") or "")
            except Exception:
                current_map = ""

        return EngineStatus(
            attached=bool(getattr(getattr(self._engine, "memory", None), "is_attached", False)),
            running=bool(getattr(self._engine, "is_running", False)),
            paused=bool(getattr(self._engine, "is_paused", False)),
            current_map=current_map,
        )
