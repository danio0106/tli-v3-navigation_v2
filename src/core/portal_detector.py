import time
import struct
import json
import os
import threading
from collections import Counter
from typing import Optional, List, Tuple, Dict, Any
from dataclasses import dataclass

from src.core.memory_reader import MemoryReader
from src.core.scanner import UE4Scanner
from src.utils.logger import log
from src.utils.constants import (
    FIGHTMGR_MAP_PORTAL_OFFSET,
    UE4_OFFSETS,
    UE4_UOBJECT_CLASS_OFFSET,
    UE4_UOBJECT_OUTER_OFFSET,
    PORTAL_DEBUG_DIR,
    PORTAL_DEBUG_TICKS_FILE,
    PORTAL_DEBUG_SUMMARY_FILE,
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

    def __init__(
        self,
        memory: MemoryReader,
        scanner: UE4Scanner,
        debug_enabled: bool = False,
        debug_summary_interval_s: float = 5.0,
        debug_strict_class_check: bool = False,
        debug_max_entries_per_tick: int = 60,
    ):
        self._memory = memory
        self._scanner = scanner
        self._lock = threading.Lock()
        self._fightmgr_ptr: int = 0
        self._portals: List[PortalInfo] = []
        self._poll_stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_interval: float = 0.5
        self._last_portal_count: int = 0
        self._last_portal_entity_ptrs: set = set()
        self._exit_portal: Optional[PortalInfo] = None
        self._exit_portal_detected_at: float = 0.0
        # Used by overlay/manual checks when bot loop is not running (polling off).
        self._last_on_demand_read_at: float = 0.0
        self._on_demand_refresh_interval: float = 0.35
        self._debug_enabled: bool = bool(debug_enabled)
        self._debug_summary_interval_s: float = max(1.0, float(debug_summary_interval_s or 5.0))
        self._debug_strict_class_check: bool = bool(debug_strict_class_check)
        self._debug_max_entries_per_tick: int = max(10, int(debug_max_entries_per_tick or 60))
        self._debug_tick_seq: int = 0
        self._debug_last_summary_at: float = 0.0
        self._debug_counters: Counter = Counter()
        self._debug_last_tick_fingerprint: str = ""
        self._debug_last_tick_written_at: float = 0.0
        self._debug_repeat_suppressed: int = 0
        self._debug_repeat_heartbeat_s: float = 10.0
        self._debug_ticks_file: str = PORTAL_DEBUG_TICKS_FILE
        self._debug_summary_file: str = PORTAL_DEBUG_SUMMARY_FILE
        self._invalid_tmap_streak: int = 0
        self._last_fightmgr_refresh_at: float = 0.0
        self._fightmgr_refresh_cooldown_s: float = 1.0
        if self._debug_enabled:
            self._ensure_debug_paths()

    def _ensure_debug_paths(self) -> None:
        try:
            os.makedirs(PORTAL_DEBUG_DIR, exist_ok=True)
        except Exception:
            pass

    @staticmethod
    def _is_probable_ptr(value: int) -> bool:
        return bool(value and 0x10000 < value < 0x7FFFFFFFFFFF)

    def _is_portal_tmap_sane(self, fightmgr_ptr: int) -> Tuple[bool, int, int, str]:
        if not self._is_probable_ptr(fightmgr_ptr):
            return False, 0, 0, "invalid_fightmgr_ptr"
        tmap_addr = fightmgr_ptr + FIGHTMGR_MAP_PORTAL_OFFSET
        data_ptr = self._memory.read_value(tmap_addr, "ulong") or 0
        array_num = self._memory.read_value(tmap_addr + 0x08, "int")
        arr = int(array_num or 0)

        # Empty TMap is valid (no portals currently).
        if array_num is None:
            return False, data_ptr, arr, "invalid_tmap_count"
        if arr == 0:
            return True, data_ptr, arr, "empty_tmap"
        if arr < 0 or arr > 512:
            return False, data_ptr, arr, "invalid_tmap_count"
        if not self._is_probable_ptr(data_ptr):
            return False, data_ptr, arr, "invalid_tmap_data_ptr"
        return True, data_ptr, arr, "ok"

    def _try_rebind_fightmgr(self, reason: str) -> bool:
        now = time.time()
        if (now - self._last_fightmgr_refresh_at) < self._fightmgr_refresh_cooldown_s:
            return False
        self._last_fightmgr_refresh_at = now

        # Reset shared scanner cache first so the next lookup is not short-circuited.
        try:
            self._scanner._fightmgr_ptr = 0
        except Exception:
            pass

        best_ptr = 0
        best_state = ""
        fnamepool = self._scanner.fnamepool_addr
        try:
            candidates = self._scanner.find_object_by_name("FightMgr") or []
        except Exception:
            candidates = []

        for obj_ptr, _name in candidates:
            if not self._is_probable_ptr(obj_ptr):
                continue
            outer_ptr = self._memory.read_value(obj_ptr + UE4_UOBJECT_OUTER_OFFSET, "ulong") or 0
            if not self._is_probable_ptr(outer_ptr):
                continue
            outer_name = self._memory.read_uobject_name(fnamepool, outer_ptr) if fnamepool else ""
            if "transient" not in outer_name.lower():
                continue
            sane, _dp, _an, state = self._is_portal_tmap_sane(obj_ptr)
            if sane and state != "empty_tmap":
                best_ptr = obj_ptr
                best_state = state
                break
            if sane and not best_ptr:
                best_ptr = obj_ptr
                best_state = state

        if not best_ptr:
            ptr = self._scanner.get_fightmgr_ptr()
            if ptr:
                sane, _dp, _an, state = self._is_portal_tmap_sane(ptr)
                if sane:
                    best_ptr = ptr
                    best_state = state

        if best_ptr:
            previous = self._fightmgr_ptr
            self._fightmgr_ptr = best_ptr
            self._invalid_tmap_streak = 0
            if previous != best_ptr:
                log.info(
                    f"[PortalDetector] FightMgr rebound 0x{previous:X} -> 0x{best_ptr:X}"
                    f" (reason={reason}, state={best_state})"
                )
            return True
        return False

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
        debug_tick: Dict[str, Any] = {
            "ts": time.time(),
            "seq": self._debug_tick_seq,
            "fightmgr_ptr": self._fightmgr_ptr,
            "accepted": 0,
            "rejected": 0,
            "reasons": {},
            "entries": [],
        }
        self._debug_tick_seq += 1

        if not self._fightmgr_ptr:
            self._invalid_tmap_streak += 1
            if self._invalid_tmap_streak >= 2:
                self._try_rebind_fightmgr("fightmgr_missing")
            self._debug_record_tick(debug_tick, early_reason="fightmgr_missing")
            return []

        tmap_addr = self._fightmgr_ptr + FIGHTMGR_MAP_PORTAL_OFFSET
        debug_tick["tmap_addr"] = tmap_addr

        sane, data_ptr, array_num, state = self._is_portal_tmap_sane(self._fightmgr_ptr)
        debug_tick["data_ptr"] = data_ptr
        debug_tick["array_num"] = int(array_num or 0)
        debug_tick["tmap_state"] = state

        if not sane:
            self._invalid_tmap_streak += 1
            if self._invalid_tmap_streak >= 2:
                rebound = self._try_rebind_fightmgr(state)
                debug_tick["fightmgr_rebind_attempted"] = True
                debug_tick["fightmgr_rebind_ok"] = bool(rebound)
                debug_tick["fightmgr_ptr_after"] = self._fightmgr_ptr
            self._debug_record_tick(debug_tick, early_reason=state)
            return []

        self._invalid_tmap_streak = 0
        if int(array_num or 0) == 0:
            with self._lock:
                self._portals = []
            self._debug_record_tick(debug_tick, early_reason="empty_tmap")
            return []

        raw = self._memory.read_bytes(data_ptr, int(array_num) * _TMAP_STRIDE)
        if not raw:
            self._debug_record_tick(debug_tick, early_reason="tmap_read_failed")
            return []

        portals = []
        accepted_portals: List[Dict[str, Any]] = []
        reason_counter: Counter = Counter()
        for i in range(int(array_num)):
            entry_off = i * _TMAP_STRIDE
            if entry_off + _TMAP_STRIDE > len(raw):
                reason_counter["entry_out_of_range"] += 1
                break
            logic_id = struct.unpack_from("<i", raw, entry_off + 0x00)[0]
            entity_ptr = struct.unpack_from("<Q", raw, entry_off + _TMAP_VALUE_OFFSET)[0]
            hash_next = struct.unpack_from("<i", raw, entry_off + 0x10)[0]
            hash_idx = struct.unpack_from("<i", raw, entry_off + 0x14)[0]

            portal, reason, extra = self._read_portal_position(entity_ptr, i)
            entry_debug = {
                "i": i,
                "logic_id": logic_id,
                "entity_ptr": entity_ptr,
                "hash_next": hash_next,
                "hash_idx": hash_idx,
                "reason": reason,
            }
            if extra:
                entry_debug.update(extra)
            if len(debug_tick["entries"]) < self._debug_max_entries_per_tick:
                debug_tick["entries"].append(entry_debug)

            if portal and portal.is_valid:
                portals.append(portal)
                accepted_portals.append({
                    "entity_ptr": int(portal.entity_ptr),
                    "x": round(float(portal.x), 1),
                    "y": round(float(portal.y), 1),
                    "z": round(float(portal.z), 1),
                })
                reason_counter["accepted"] += 1
            else:
                reason_counter[reason] += 1

        debug_tick["accepted"] = len(portals)
        rejected = sum(v for k, v in reason_counter.items() if k != "accepted")
        debug_tick["rejected"] = rejected
        debug_tick["reasons"] = dict(reason_counter)
        debug_tick["accepted_portals"] = accepted_portals

        with self._lock:
            self._portals = portals
        self._debug_record_tick(debug_tick)
        return portals

    def _read_portal_position(self, entity_ptr: int, portal_idx: int) -> Tuple[Optional[PortalInfo], str, Dict[str, Any]]:
        extra: Dict[str, Any] = {}
        if not self._is_probable_ptr(entity_ptr):
            return None, "invalid_entity_ptr", extra

        if self._debug_strict_class_check:
            fnamepool = self._scanner.fnamepool_addr
            class_ptr = self._memory.read_value(entity_ptr + UE4_UOBJECT_CLASS_OFFSET, "ulong") or 0
            if not self._is_probable_ptr(class_ptr):
                return None, "strict_missing_class_ptr", {"class_ptr": class_ptr}
            class_name = self._memory.read_uobject_name(fnamepool, class_ptr) if fnamepool else ""
            extra["class_name"] = class_name
            if not class_name or "portal" not in class_name.lower():
                return None, "strict_class_mismatch", extra

        root_ptr = self._memory.read_value(
            entity_ptr + UE4_OFFSETS["RootComponent"], "ulong"
        )
        if not self._is_probable_ptr(root_ptr or 0):
            return None, "invalid_root_component", extra
        extra["root_ptr"] = root_ptr

        pos_data = self._memory.read_bytes(root_ptr + UE4_OFFSETS["RelativeLocation"], 12)
        if not pos_data or len(pos_data) < 12:
            return None, "missing_relative_location", extra

        x, y, z = struct.unpack_from("<fff", pos_data)
        extra["x"] = x
        extra["y"] = y
        extra["z"] = z

        # Reject NaN, infinite, or obviously wrong positions
        if x != x or y != y or z != z:
            return None, "nan_position", extra
        if abs(x) > 120_000 or abs(y) > 120_000 or abs(z) > 80_000:
            return None, "out_of_range_position", extra

        portal = PortalInfo(
            entity_ptr=entity_ptr,
            x=x, y=y, z=z,
            portal_id=portal_idx,
            is_valid=True,
        )
        return portal, "accepted", extra

    def _debug_record_tick(self, tick: Dict[str, Any], early_reason: str = "") -> None:
        if early_reason:
            tick["accepted"] = 0
            tick["rejected"] = 1
            tick["reasons"] = {early_reason: 1}

        if not self._debug_enabled:
            return

        now = time.time()
        fingerprint = self._build_debug_tick_fingerprint(tick)
        should_write_tick = True
        if (
            fingerprint
            and fingerprint == self._debug_last_tick_fingerprint
            and (now - self._debug_last_tick_written_at) < self._debug_repeat_heartbeat_s
        ):
            should_write_tick = False
            self._debug_repeat_suppressed += 1

        if should_write_tick:
            tick_to_write = dict(tick)
            if self._debug_repeat_suppressed > 0:
                tick_to_write["suppressed_repeats"] = int(self._debug_repeat_suppressed)
                self._debug_repeat_suppressed = 0
            try:
                self._ensure_debug_paths()
                with open(self._debug_ticks_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(tick_to_write, ensure_ascii=False) + "\n")
                self._debug_last_tick_fingerprint = fingerprint
                self._debug_last_tick_written_at = now
                self._debug_counters["ticks_written"] += 1
            except Exception as e:
                log.debug(f"[PortalDebug] tick write failed: {e}")
        else:
            self._debug_counters["ticks_suppressed_repeats"] += 1

        reasons = tick.get("reasons") or {}
        for key, value in reasons.items():
            try:
                self._debug_counters[key] += int(value)
            except Exception:
                continue
        self._debug_counters["ticks"] += 1
        self._debug_counters["accepted_total"] += int(tick.get("accepted", 0) or 0)
        self._debug_counters["rejected_total"] += int(tick.get("rejected", 0) or 0)

        now = time.time()
        if (now - self._debug_last_summary_at) >= self._debug_summary_interval_s:
            self._debug_last_summary_at = now
            self._write_debug_summary(now)

    @staticmethod
    def _build_debug_tick_fingerprint(tick: Dict[str, Any]) -> str:
        try:
            accepted = tick.get("accepted_portals") or []
            accepted_sorted = sorted(
                (
                    round(float(p.get("x", 0.0) or 0.0), 1),
                    round(float(p.get("y", 0.0) or 0.0), 1),
                    round(float(p.get("z", 0.0) or 0.0), 1),
                )
                for p in accepted
            )
            reduced = {
                "accepted_portals": accepted_sorted,
            }
            return json.dumps(reduced, ensure_ascii=False, sort_keys=True)
        except Exception:
            return ""

    def _write_debug_summary(self, ts: float) -> None:
        top_reasons = self._debug_counters.most_common(6)
        summary = {
            "ts": ts,
            "fightmgr_ptr": self._fightmgr_ptr,
            "strict_class_check": self._debug_strict_class_check,
            "dedup": {
                "heartbeat_s": self._debug_repeat_heartbeat_s,
                "suppressed_repeats_pending": int(self._debug_repeat_suppressed),
            },
            "counters": dict(self._debug_counters),
            "top_reasons": [{"reason": k, "count": v} for k, v in top_reasons],
        }
        try:
            self._ensure_debug_paths()
            with open(self._debug_summary_file, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.debug(f"[PortalDebug] summary write failed: {e}")

        top_text = ", ".join(f"{k}:{v}" for k, v in top_reasons if k not in {"ticks", "accepted_total", "rejected_total"})
        log.info(
            "[PortalDebug] "
            f"ticks={int(self._debug_counters.get('ticks', 0))} "
            f"accepted={int(self._debug_counters.get('accepted_total', 0))} "
            f"rejected={int(self._debug_counters.get('rejected_total', 0))} "
            f"top=[{top_text}]"
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
        if self._debug_enabled:
            self._ensure_debug_paths()
        self._poll_stop_event.clear()
        self._exit_portal = None
        self._last_portal_count = 0
        self._last_portal_entity_ptrs = set()
        self._debug_last_summary_at = 0.0
        self._debug_counters = Counter()
        self._debug_last_tick_fingerprint = ""
        self._debug_last_tick_written_at = 0.0
        self._debug_repeat_suppressed = 0
        self._invalid_tmap_streak = 0
        self._last_fightmgr_refresh_at = 0.0
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="PortalPoll"
        )
        self._poll_thread.start()
        log.info(
            "[PortalDetector] Portal polling started"
            + (
                f" | debug={self._debug_enabled} strict_class={self._debug_strict_class_check}"
                if self._debug_enabled else ""
            )
        )

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
        self._last_portal_entity_ptrs = set()
        self._fightmgr_ptr = 0

    def _poll_loop(self):
        while not self._poll_stop_event.is_set():
            try:
                portals = self.read_portals()
                current_count = len(portals)
                current_ptrs = {p.entity_ptr for p in portals if p.is_valid}

                # If tracked exit portal disappeared, clear stale exit pointer.
                if self._exit_portal and self._exit_portal.entity_ptr not in current_ptrs:
                    with self._lock:
                        self._exit_portal = None
                        self._exit_portal_detected_at = 0.0

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

                # Robust fallback: portal set changed but total count did not
                # (e.g. one portal despawned and another spawned in same tick).
                # IMPORTANT: never promote the very first portal on map start
                # to exit. Exit should only be set after at least one baseline
                # portal already existed.
                new_ptrs = current_ptrs - self._last_portal_entity_ptrs
                if new_ptrs and self._last_portal_count > 0 and current_count > 1:
                    candidates = [p for p in portals if p.entity_ptr in new_ptrs and p.is_valid]
                    if candidates:
                        newest = max(candidates, key=lambda p: p.portal_id)
                        with self._lock:
                            self._exit_portal = newest
                            self._exit_portal_detected_at = time.time()
                        log.info(
                            f"[PortalDetector] Exit portal updated (set change) at"
                            f" ({newest.x:.0f}, {newest.y:.0f}, {newest.z:.0f})"
                        )

                self._last_portal_count = current_count
                self._last_portal_entity_ptrs = current_ptrs

            except Exception as e:
                log.debug(f"[PortalDetector] Poll error: {e}")

            self._poll_stop_event.wait(timeout=self._poll_interval)

