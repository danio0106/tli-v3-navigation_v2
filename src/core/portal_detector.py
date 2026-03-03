import time
import struct
import threading
from typing import Optional, List, Tuple, Dict, Any
from dataclasses import dataclass

from src.core.memory_reader import MemoryReader
from src.core.scanner import UE4Scanner
from src.utils.logger import log
from src.utils.constants import (
    FIGHTMGR_MAP_PORTAL_OFFSET,
    UE4_OFFSETS,
)

# Confirmed TMap element layout (stride=24) — same as scanner._read_tmap_events:
#   +0x00 = int32  Key (spawn_index / logic_id)
#   +0x04 = int32  padding
#   +0x08 = ptr64  Value (EEntity*)
#   +0x10 = int32  HashNext
#   +0x14 = int32  HashIndex
_TMAP_STRIDE = 24
_TMAP_VALUE_OFFSET = 0x08


@dataclass
class PortalInfo:
    entity_ptr: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    portal_id: int = 0
    is_valid: bool = False


class PortalDetector:

    def __init__(self, memory: MemoryReader, scanner: UE4Scanner):
        self._memory = memory
        self._scanner = scanner
        self._lock = threading.Lock()
        self._fightmgr_ptr: int = 0
        self._portals: List[PortalInfo] = []
        self._poll_stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_interval: float = 0.5
        self._last_portal_count: int = 0
        self._exit_portal: Optional[PortalInfo] = None
        self._exit_portal_detected_at: float = 0.0
        # Used by overlay/manual checks when bot loop is not running (polling off).
        self._last_on_demand_read_at: float = 0.0
        self._on_demand_refresh_interval: float = 0.35

    # ------------------------------------------------------------------ properties

    @property
    def portals(self) -> List[PortalInfo]:
        with self._lock:
            return list(self._portals)

    @property
    def portal_count(self) -> int:
        with self._lock:
            return len(self._portals)

    @property
    def exit_portal(self) -> Optional[PortalInfo]:
        with self._lock:
            return self._exit_portal

    @property
    def is_polling(self) -> bool:
        return (
            not self._poll_stop_event.is_set()
            and self._poll_thread is not None
            and self._poll_thread.is_alive()
        )

    @property
    def fightmgr_found(self) -> bool:
        return self._fightmgr_ptr != 0

    @property
    def exit_portal_age(self) -> float:
        with self._lock:
            if self._exit_portal_detected_at > 0:
                return time.time() - self._exit_portal_detected_at
            return 0.0

    # ------------------------------------------------------------------ FightMgr

    def find_fightmgr(self) -> bool:
        """Find the live FightMgr singleton.

        Delegates to the scanner's already-correct transient-outer matching logic
        so both objects share a single, consistent ptr without duplicating the
        GObjects traversal.
        """
        ptr = self._scanner.get_fightmgr_ptr()
        if ptr:
            self._fightmgr_ptr = ptr
            log.info(f"[PortalDetector] FightMgr at 0x{ptr:X}")
            return True
        log.warning("[PortalDetector] FightMgr not found")
        return False

    # ------------------------------------------------------------------ portal reading

    def read_portals(self) -> List[PortalInfo]:
        """Read all portals from FightMgr.MapPortal TMap.

        Uses the confirmed TMap element layout (stride=24, value at +0x08).
        """
        if not self._fightmgr_ptr:
            return []

        tmap_addr = self._fightmgr_ptr + FIGHTMGR_MAP_PORTAL_OFFSET

        data_ptr = self._memory.read_value(tmap_addr, "ulong")
        array_num = self._memory.read_value(tmap_addr + 0x08, "int")

        if not data_ptr or not (0x10000 < data_ptr < 0x7FFFFFFFFFFF):
            return []
        if array_num is None or array_num <= 0 or array_num > 100:
            return []

        raw = self._memory.read_bytes(data_ptr, array_num * _TMAP_STRIDE)
        if not raw:
            return []

        portals = []
        for i in range(array_num):
            off = i * _TMAP_STRIDE + _TMAP_VALUE_OFFSET
            if off + 8 > len(raw):
                break
            entity_ptr = struct.unpack_from("<Q", raw, off)[0]
            if not (0x10000 < entity_ptr < 0x7FFFFFFFFFFF):
                continue
            portal = self._read_portal_position(entity_ptr, i)
            if portal and portal.is_valid:
                portals.append(portal)

        with self._lock:
            self._portals = portals
        return portals

    def _read_portal_position(self, entity_ptr: int, portal_idx: int) -> Optional[PortalInfo]:
        root_ptr = self._memory.read_value(
            entity_ptr + UE4_OFFSETS["RootComponent"], "ulong"
        )
        if not root_ptr or not (0x10000 < root_ptr < 0x7FFFFFFFFFFF):
            return None

        pos_data = self._memory.read_bytes(root_ptr + UE4_OFFSETS["RelativeLocation"], 12)
        if not pos_data or len(pos_data) < 12:
            return None

        x, y, z = struct.unpack_from("<fff", pos_data)

        # Reject NaN, infinite, or obviously wrong positions
        if x != x or y != y or z != z:
            return None
        if abs(x) > 1_000_000 or abs(y) > 1_000_000 or abs(z) > 1_000_000:
            return None

        return PortalInfo(
            entity_ptr=entity_ptr,
            x=x, y=y, z=z,
            portal_id=portal_idx,
            is_valid=True,
        )

    # ------------------------------------------------------------------ overlay interface

    def get_portal_positions(self) -> List[Tuple[float, float]]:
        """Return (x, y) positions of all portals for the debug overlay.

        When the main bot loop is not running, polling is typically off.
        In that state we do a throttled on-demand refresh so overlay markers are
        still visible during manual attach-only testing.
        """
        if not self.is_polling:
            now = time.time()
            if (now - self._last_on_demand_read_at) >= self._on_demand_refresh_interval:
                self._last_on_demand_read_at = now
                if not self._fightmgr_ptr:
                    self.find_fightmgr()
                if self._fightmgr_ptr:
                    try:
                        self.read_portals()
                    except Exception:
                        pass

        with self._lock:
            return [(p.x, p.y) for p in self._portals if p.is_valid]

    def get_portal_markers(self) -> List[Dict[str, Any]]:
        """Return portal markers with explicit exit/non-exit classification.

        Marker format:
          {"x": float, "y": float, "portal_id": int, "is_exit": bool}

        Exit flag is memory-backed: it marks the portal entity currently tracked
        as `_exit_portal` (entity pointer equality), not by overlay heuristics.
        """
        if not self.is_polling:
            now = time.time()
            if (now - self._last_on_demand_read_at) >= self._on_demand_refresh_interval:
                self._last_on_demand_read_at = now
                if not self._fightmgr_ptr:
                    self.find_fightmgr()
                if self._fightmgr_ptr:
                    try:
                        self.read_portals()
                    except Exception:
                        pass

        with self._lock:
            exit_ptr = self._exit_portal.entity_ptr if self._exit_portal and self._exit_portal.is_valid else 0
            markers: List[Dict[str, Any]] = []
            for p in self._portals:
                if not p.is_valid:
                    continue
                markers.append({
                    "x": p.x,
                    "y": p.y,
                    "portal_id": p.portal_id,
                    "is_exit": bool(exit_ptr and p.entity_ptr == exit_ptr),
                })
            return markers

    def get_exit_portal_position(self) -> Optional[Tuple[float, float, float]]:
        """Return (x, y, z) of the exit portal if detected, else None."""
        with self._lock:
            if self._exit_portal and self._exit_portal.is_valid:
                return (self._exit_portal.x, self._exit_portal.y, self._exit_portal.z)
            return None

    def get_status_text(self) -> str:
        if not self._fightmgr_ptr:
            return "FightMgr: Not found"
        portal_count = len(self._portals)
        portal_text = "No portals detected" if portal_count == 0 else f"Portals: {portal_count}"
        status = f"FightMgr: 0x{self._fightmgr_ptr:X}"
        if self.is_polling:
            status += f" | {portal_text}"
            if self._exit_portal:
                status += f" | Exit: ({self._exit_portal.x:.0f}, {self._exit_portal.y:.0f})"
            else:
                status += " | Exit: Waiting..."
        else:
            status += f" | {portal_text} | Polling: Off (on-demand refresh active)"
        return status

    # ------------------------------------------------------------------ polling

    def start_polling(self):
        if self.is_polling:
            return
        if not self._fightmgr_ptr:
            if not self.find_fightmgr():
                log.warning("[PortalDetector] Cannot start polling — FightMgr not found")
                return
        self._poll_stop_event.clear()
        self._exit_portal = None
        self._last_portal_count = 0
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="PortalPoll"
        )
        self._poll_thread.start()
        log.info("[PortalDetector] Portal polling started")

    def stop_polling(self):
        self._poll_stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=3.0)
        self._poll_thread = None
        with self._lock:
            self._portals = []
            self._exit_portal = None
        log.info("[PortalDetector] Portal polling stopped")

    def reset(self):
        self.stop_polling()
        with self._lock:
            self._portals = []
            self._exit_portal = None
        self._exit_portal_detected_at = 0.0
        self._last_portal_count = 0
        self._fightmgr_ptr = 0

    def _poll_loop(self):
        while not self._poll_stop_event.is_set():
            try:
                portals = self.read_portals()
                current_count = len(portals)

                if current_count > self._last_portal_count and self._last_portal_count >= 0:
                    new_count = current_count - self._last_portal_count
                    log.info(
                        f"[PortalDetector] {new_count} new portal(s) detected"
                        f" (total: {current_count})"
                    )
                    # Exit portal is always the most-recently added portal.
                    # We only set it when there is already at least one portal
                    # (the entry portal) so the *new* one is the exit portal.
                    if self._last_portal_count > 0 and portals:
                        newest = portals[-1]
                        with self._lock:
                            self._exit_portal = newest
                            self._exit_portal_detected_at = time.time()
                        log.info(
                            f"[PortalDetector] Exit portal at"
                            f" ({newest.x:.0f}, {newest.y:.0f}, {newest.z:.0f})"
                        )

                self._last_portal_count = current_count

            except Exception as e:
                log.debug(f"[PortalDetector] Poll error: {e}")

            self._poll_stop_event.wait(timeout=self._poll_interval)

