import time
import struct
import os
import json
import csv
import threading
from collections import deque
from datetime import datetime
from typing import Optional, List, Callable, Tuple

from src.core.memory_reader import MemoryReader, PointerChain
from src.core.address_manager import AddressManager
from src.utils.logger import log
from src.utils.constants import (
    DUMP_VERIFIED_CHAIN, FNAMEPOOL_PATTERNS, FNAMEPOOL_MASKS,
    FNAMEPOOL_LEA_OFFSETS,
    GOBJECTS_PATTERNS, GOBJECTS_MASKS,
    UE4_UOBJECT_FNAME_OFFSET, UE4_UOBJECT_OUTER_OFFSET,
    ECFGCOMPONENT_CFGINFO_OFFSET, CFGINFO_ID_OFFSET, CFGINFO_TYPE_OFFSET,
    CFGINFO_EXTENDID_OFFSET, EGAMEPLAY_EVENT_TYPES, EGAMEPLAY_TARGET_IDS,
    UE4_OFFSETS,
    FIGHTMGR_MAP_GAMEPLAY_OFFSET, FIGHTMGR_MAP_CUSTOMTRAP_OFFSET,
    FIGHTMGR_MAP_MONSTER_OFFSET,
    FIGHTMGR_MAP_INTERACTIVE_OFFSET,
    MINIMAP_SAVE_OBJECT_CLASS, MINIMAP_RECORDS_OFFSET,
    TMAP_FSTRING_ELEM_STRIDE, MINIMAP_FSTRING_KEY_PTR, MINIMAP_FSTRING_KEY_LEN,
    MINIMAP_RECORD_POS_PTR, MINIMAP_RECORD_POS_NUM, FVECTOR_SIZE,
    MINIMAP_KEY_MAP_FILE,
    ENTITY_SCAN_INTERVAL_S,
    GUARD_SEED_WINDOW_SECS, GUARD_SEED_MAX,
    GUARD_FLEE_MIN_SPEED, GUARD_MIN_SURVIVE_SECS,
)

# Distance threshold (in world units) for detecting ABP cache address reuse.
# If a cached entity's position jumps by more than this between scans the cache
# entry is invalidated and the entity is re-scanned with a fresh memory read.
# Value chosen to exceed the minimum event separation (~4775u on the map with
# Sandlord@(2500,-650) / Carjack@(7050,800)) while being far larger than normal
# guard movement speed (~60u per 1 s scan cycle).
_ABP_REUSE_THRESHOLD = 4000.0
_ABP_REUSE_THRESHOLD_SQ = _ABP_REUSE_THRESHOLD ** 2

UE4_GWORLD_PATTERNS = [
    b"\x48\x8B\x05\x00\x00\x00\x00\x48\x3B\xC3",
    b"\x48\x8B\x1D\x00\x00\x00\x00\x48\x85\xDB\x74",
    b"\x48\x89\x05\x00\x00\x00\x00\x48\x85\xC0\x74",
    b"\x48\x8B\x05\x00\x00\x00\x00\x48\x85\xC0\x74\x08",
    b"\x48\x8B\x05\x00\x00\x00\x00\x48\x8B\x88",
    b"\x48\x8B\x05\x00\x00\x00\x00\x0F\x28",
    b"\x48\x8B\x15\x00\x00\x00\x00\x48\x85\xD2\x74",
    b"\x48\x8B\x0D\x00\x00\x00\x00\x48\x85\xC9\x74",
]

UE4_GWORLD_MASKS = [
    "xxx????xxx",
    "xxx????xxxx",
    "xxx????xxxx",
    "xxx????xxxxx",
    "xxx????xxx",
    "xxx????xx",
    "xxx????xxxx",
    "xxx????xxxx",
]


class EventInfo:
    def __init__(self):
        self.address: int = 0
        self.event_type: str = ""
        self.cfg_id: int = 0
        self.cfg_type: int = 0
        self.cfg_extend_id: int = 0
        self.position: tuple = (0.0, 0.0, 0.0)
        self.is_target_event: bool = False
        self.ecfg_address: int = 0
        self.sub_object_name: str = ""
        self.sub_object_class: str = ""
        # ⚠️ UNRELIABLE — EGameplay+0x618 fluctuates randomly at runtime,
        # confirmed broken 2026-03-02 (values 1–4 changing every second even
        # while idle; no consistent correlation to wave state or completion).
        # Kept in memory reads ONLY for raw overlay display.  NEVER drive
        # game logic (activation detection, completion detection) from this.
        self.wave_counter: int = -1
        self.spawn_index: int = -1
        self.bvalid: int = -1   # EEntity::bValid at +0x720; 0 = event ended/invalidated
        self.abp_class: str = ""  # AnimBlueprintGeneratedClass name (cached silently for data collection)
        self.source_type: int = -1  # repurposed: holds cfg_id from ECfgComponent for Entity Scanner display
        self.monster_point_id: int = -1  # MonsterPointId from cfg cache (display only)
        self.carjack_vehicle_addr: int = 0
        self.carjack_cur_status_index: int = -1
        self.carjack_cur_status: int = -1
        self.carjack_trap_execute_state: int = -1
        self.carjack_wait_time: float = -1.0
        self.carjack_hit_count: int = -1
        self.carjack_work_count: int = -1
        self.carjack_max_work_count: int = -1
        self.carjack_skill_index: int = -1
        self.carjack_trigger_index: int = -1
        self.carjack_player_enter: int = -1

    def __repr__(self):
        sub_info = f", sub='{self.sub_object_name}'" if self.sub_object_name else ""
        return (f"EventInfo(type={self.event_type}, cfg_id=0x{self.cfg_id:X}, "
                f"pos=({self.position[0]:.0f}, {self.position[1]:.0f}){sub_info}, "
                f"wave={self.wave_counter}, spawn={self.spawn_index}, "
                f"addr=0x{self.address:X})")


class ScanResult:
    def __init__(self):
        self.success: bool = False
        self.base_module: str = ""
        self.gworld_static_offset: int = 0
        self.chain_offsets: List[int] = []
        self.player_x: float = 0.0
        self.player_y: float = 0.0
        self.player_z: float = 0.0
        self.gworld_pattern_index: int = -1
        self.path_type: str = ""
        self.module_base: int = 0
        self.module_size: int = 0
        self.zone_name: str = ""
        self.fnamepool_addr: int = 0
        self.gobjects_addr: int = 0


class UE4Scanner:
    def __init__(self, memory: MemoryReader, addresses: AddressManager,
                 progress_callback: Optional[Callable] = None):
        self._memory = memory
        self._addresses = addresses
        self._progress = progress_callback or (lambda msg: None)
        self._cancelled = False
        self._last_gworld_pattern_index = -1
        self._cached_gworld_static = None
        self._last_pawn_ptr = None
        self._fnamepool_addr = 0
        self._gobjects_addr = 0
        self._fightmgr_ptr = 0
        self._typed_events_fp: str = ""
        # MinimapSaveObject pointer cache — avoids 0.5s GObjects re-scan on every poll.
        # Validated cheaply on each use by reading the UObject class slot.
        self._minimap_save_obj_ptr: int = 0
        # Track last reported pos count so we only emit INFO logs when count changes.
        self._last_minimap_pos_count: int = -1
        # Set of monster addresses whose verbose CompScan log has been emitted this
        # session.  On first encounter the full component chain is logged; subsequent
        # scans use a silent retry path so the log is not flooded.
        self._comp_logged_ptrs: set = set()
        # Cache of monster address → ABP class name string (populated by CompScan).
        # Only populated on success — empty result is never stored here so that
        # entities whose ueComponents TMap was not yet initialized at first scan
        # are retried silently on every subsequent scan cycle.
        self._abp_cache: dict = {}
        # Cache of monster address → EConfigMapIcon ordinal int (from EMapIconComponent+0x120).
        # -1 = not yet read.
        self._map_icon_cache: dict = {}
        # Cache of monster address → ECfgComponent.CfgInfo.ID int32 (or tuple when extended).
        # -1 = not yet read.  Repurposed in Entity Scanner display as 'source_type' field.
        self._cfg_scan_cache: dict = {}
        # Learned point-id profile for active Carjack guards in current map session.
        # Last known (x, y) position per monster address — used to detect memory
        # address reuse between events.  If a cached entity's position jumps by
        # more than _ABP_REUSE_THRESHOLD_SQ the cache entry is invalidated.
        self._abp_last_pos: dict = {}
        # Player HP reading — cached ERoleComponent address and RoleLogic offset within it.
        # Offset -1 = not yet found.  Reset to 0/-1 when cache is invalidated.
        self._erole_comp_ptr: int = 0
        self._role_logic_offset: int = -1
        # Flag: True while the background HP-scan thread is running.
        self._hp_pending: bool = False
        # Monotonic timestamp of last failed HP scan (0 = never tried).
        # Used to debounce retry attempts — only re-scan every 10 s after failure.
        self._hp_scan_failed_at: float = 0.0
        # Dedup: last-logged strings for high-frequency log lines.  Only emit
        # a new log line when the content changes (avoids 20 Hz spam when the
        # overlay calls get_monster_entities() via get_nearby_monsters()).
        self._last_monster_scan_log: str = ""
        self._last_near_events_log: str = ""
        self._last_guard_targets_log: str = ""
        self._last_guard_targets_log_time: float = 0.0
        # Differential probe cache (truck component byte comparison).
        self._last_carjack_probe_time: float = 0.0
        self._carjack_probe_prev_bytes: dict = {}
        self._carjack_probe_prev_links: dict = {}
        self._carjack_link_ptrs_by_vehicle: dict = {}
        # ── Background EntityScan thread ─────────────────────────────────────
        # Scans MapRoleMonster TMap at ~120 Hz (ENTITY_SCAN_INTERVAL_S = 8 ms)
        # and logs ONE compact [EScan] line per new entity + ONE [EScan] RESOLVED
        # line when ABP resolves.  Probe-free after v4.65.0 cleanup — each tick is
        # only TMap position reads + cached ABP lookups.
        #
        # _ever_seen_addrs   — all addresses observed this FightMgr session;
        #                      cleared on map transition (FightMgr ptr reset).
        # _pending_abp_retry — addresses whose ABP was not resolved on first
        #                      encounter; retried each tick.
        self._ever_seen_addrs: set = set()
        self._pending_abp_retry: set = set()
        self._entity_scan_thread_active: bool = False
        self._entity_scan_thread: Optional[threading.Thread] = None
        # Per-address last emitted [EScanTrack] sample cache: {addr: (x, y, ts)}.
        self._escan_track_last: dict = {}
        # Last observed XY position per monster address (updated in get_monster_entities()).
        self._monster_last_pos: dict = {}
        # Carjack truck world position — set by get_typed_events() when a Carjack
        # event is classified.  None = truck not yet located.
        self._carjack_truck_pos: Optional[Tuple[float, float]] = None
        self._carjack_vehicle_addr: int = 0
        # One-shot flag so probe logging only fires once per FightMgr session.
        self._truck_probe_done: bool = False
        # ── Movement-prediction guard detection (v4.65.0) ────────────────────
        # Position history: addr → deque of (monotonic_ts, x, y) tuples.
        # Depth 16 @ ~120 Hz ≈ 133 ms of movement data per entity.
        self._entity_pos_history: dict = {}   # addr → deque(maxlen=16)
        self._entity_first_seen_t: dict = {}     # addr → monotonic_ts of first appearance
        # Monotonic timestamp when the current Carjack event was first detected.
        # 0.0 = no active Carjack.  Reset to 0.0 when the event ends (bValid→0).
        self._carjack_active_since: float = 0.0
        # Monotonic deadline until which Carjack is considered active (2 s grace).
        self._carjack_active_until: float = 0.0
        # [GuardSeed] tracking — first GUARD_SEED_MAX entities within 2500u of
        # the truck in the first GUARD_SEED_WINDOW_SECS.  Data collection only.
        self._guard_seed_addrs: set = set()
        self._guard_seed_count: int = 0
        # Per-address last [FleeTrack] log timestamp (throttle: 1 log/entity/2 s).
        self._flee_track_last_log: dict = {}  # addr → monotonic_ts
        # ── Movement-data CSV (one file per session, 30 Hz writer thread) ─────
        # Rows: t_abs_ms, carjack_n, t_carjack_ms, addr, x, y, abp, dist_truck, is_seed
        self._movdata_queue: deque = deque(maxlen=50000)  # raw row tuples (capped: ~25s at 120Hz/100 entities)
        self._movdata_file = None                      # open file handle (or None)
        self._movdata_csv = None                       # csv.writer instance
        self._movdata_session_t0: float = 0.0          # monotonic when file opened
        self._movdata_carjack_n: int = 0               # increments each Carjack in session
        self._movdata_open_done: bool = False          # True after first open
        self._movdata_stop: threading.Event = threading.Event()
        self._movdata_thread: Optional[threading.Thread] = None

    def cancel(self):
        self._movdata_close_session()
        self._cancelled = True
        self._entity_scan_thread_active = False   # stop background EntityScan thread
        t = self._entity_scan_thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=0.5)
        self._entity_scan_thread = None

    def _log(self, msg: str):
        self._progress(msg)

    def _log_debug(self, msg: str):
        log.debug(f"[UE4 Scanner] {msg}")

    def _is_probable_game_ptr(self, ptr: int, require_alignment: bool = True) -> bool:
        if not isinstance(ptr, int):
            return False
        # Torchlight pointers in this process live in high canonical user-space
        # regions (typically 0x1BF... heap or 0x7FF... module). Reject low
        # pseudo-pointers aggressively to avoid noisy invalid reads.
        if not (0x10000000000 <= ptr <= 0x7FFFFFFFFFFF):
            return False
        if require_alignment and (ptr & 0x7) != 0:
            return False
        return True

    def _read_ptr(self, addr: int) -> Optional[int]:
        ptr = self._memory.read_value(addr, "ulong")
        if ptr and self._is_probable_game_ptr(ptr):
            return ptr
        return None

    def scan_dump_chain(self, use_cache=True) -> ScanResult:
        result = ScanResult()
        self._cancelled = False

        if not self._memory.is_attached:
            self._log("ERROR: Not attached to game process")
            return result

        self._log("=== UE4 Dump-Verified Chain ===")
        self._log("Chain: GWorld -> OwningGameInstance -> LocalPlayers[0] -> PlayerController -> Pawn -> RootComponent -> RelativeLocation")
        self._log(f"Offsets: {' -> '.join(f'0x{o:X}' for o in DUMP_VERIFIED_CHAIN)}")

        modules = self._memory.list_modules()
        if not modules:
            self._log("FAILED: No modules found")
            return result

        main_module_name = modules[0][0]
        main_module_base = modules[0][1]
        main_module_size = modules[0][2]

        self._log(f"Main module: {main_module_name} at 0x{main_module_base:X}")

        max_retries = 10
        for attempt in range(max_retries):
            if self._cancelled:
                self._log("Scan cancelled")
                return result

            chain_result = self._walk_dump_chain(
                main_module_name, main_module_base, main_module_size, use_cache
            )
            if chain_result is not None:
                return chain_result

            if attempt < max_retries - 1:
                self._log(f"Chain walk failed (attempt {attempt + 1}/{max_retries}) - retrying in 1s...")
                for _ in range(10):
                    if self._cancelled:
                        self._log("Scan cancelled")
                        return result
                    time.sleep(0.1)

        self._log("FAILED: Dump chain could not resolve after all retries")
        self._cached_gworld_static = None
        return result

    def _walk_dump_chain(self, module_name, module_base, module_size, use_cache) -> Optional[ScanResult]:
        gworld_static = None

        if use_cache and self._cached_gworld_static:
            ptr = self._read_ptr(self._cached_gworld_static)
            if ptr:
                gworld_static = self._cached_gworld_static
                self._log(f"Using cached GWorld at 0x{gworld_static:X}")
            else:
                self._log("Cached GWorld stale - falling back to sig scan")
                self._cached_gworld_static = None

        if not gworld_static:
            saved_addr = self._addresses.get_address("player_x")
            if saved_addr and saved_addr.get("base_offset", 0) != 0:
                saved_gworld = module_base + saved_addr["base_offset"]
                ptr = self._read_ptr(saved_gworld)
                if ptr:
                    self._log(f"Trying saved GWorld offset 0x{saved_addr['base_offset']:X}...")
                    gworld_static = saved_gworld
                else:
                    self._log(f"Saved GWorld offset stale - falling back to sig scan")

        if not gworld_static:
            gworld_static = self._find_gworld(module_base, module_size)
            if not gworld_static:
                self._log("FAILED: Could not find GWorld pointer")
                return None

        gworld_ptr = self._read_ptr(gworld_static)
        if not gworld_ptr:
            self._log("FAILED: GWorld pointer is null")
            return None

        self._log(f"GWorld static at 0x{gworld_static:X}, points to 0x{gworld_ptr:X}")

        chain_labels = [
            "OwningGameInstance",
            "LocalPlayers (TArray data)",
            "LocalPlayer[0]",
            "PlayerController",
            "Pawn (ERolePlayer)",
            "RootComponent",
        ]
        chain_offsets = DUMP_VERIFIED_CHAIN
        current_ptr = gworld_ptr

        for i, offset in enumerate(chain_offsets[:-1]):
            next_ptr = self._read_ptr(current_ptr + offset)
            label = chain_labels[i] if i < len(chain_labels) else f"step {i}"
            if not next_ptr:
                self._log(f"FAILED: {label} is null at 0x{current_ptr:X}+0x{offset:X}")
                return None
            self._log(f"  {label}: 0x{current_ptr:X}+0x{offset:X} -> 0x{next_ptr:X}")
            if label == "Pawn (ERolePlayer)":
                self._last_pawn_ptr = next_ptr
            current_ptr = next_ptr

        loc_offset = chain_offsets[-1]
        root_ptr = current_ptr

        x = self._memory.read_value(root_ptr + loc_offset, "float")
        y = self._memory.read_value(root_ptr + loc_offset + 4, "float")
        z = self._memory.read_value(root_ptr + loc_offset + 8, "float")

        if x is None or y is None or z is None:
            self._log(f"FAILED: Could not read floats at RootComponent+0x{loc_offset:X}")
            return None

        if not self._is_plausible_coordinate(x) or not self._is_plausible_coordinate(y):
            self._log(f"FAILED: Coordinates not plausible: ({x}, {y}, {z})")
            return None

        self._log(f"  RelativeLocation: ({x:.1f}, {y:.1f}, {z:.1f})")

        self._log("Stability check (reading twice, 0.3s apart)...")
        time.sleep(0.3)
        x2 = self._memory.read_value(root_ptr + loc_offset, "float")
        y2 = self._memory.read_value(root_ptr + loc_offset + 4, "float")
        z2 = self._memory.read_value(root_ptr + loc_offset + 8, "float")

        if x2 is None or y2 is None or z2 is None:
            self._log("FAILED: Second read returned null (chain unstable)")
            return None

        drift = abs(x2 - x) + abs(y2 - y)
        self._log(f"  Drift: {drift:.2f} (threshold: 2.0)")
        if drift > 2.0:
            self._log(f"FAILED: Excessive drift ({drift:.2f}) -- chain may be pointing at wrong data")
            return None

        result = ScanResult()
        result.success = True
        result.player_x = x2
        result.player_y = y2
        result.player_z = z2 if z2 is not None else 0.0
        result.base_module = module_name
        result.gworld_static_offset = gworld_static - module_base
        result.chain_offsets = list(chain_offsets)
        result.gworld_pattern_index = self._last_gworld_pattern_index
        result.path_type = "DumpVerified"
        result.module_base = module_base
        result.module_size = module_size

        self._log(f"SUCCESS! Dump-verified chain resolved")
        self._log(f"Position: ({result.player_x:.1f}, {result.player_y:.1f}, {result.player_z:.1f})")
        self._log(f"Base: {module_name}+0x{result.gworld_static_offset:X}")
        self._log(f"Chain: {' -> '.join(f'0x{o:X}' for o in chain_offsets)}")

        self._save_addresses(result)
        self._cached_gworld_static = gworld_static

        def _scan_extras():
            self.scan_fnamepool(module_base, module_size)
            self.scan_gobjects(module_base, module_size)
        threading.Thread(target=_scan_extras, daemon=True, name="ScanExtras").start()

        if hasattr(self, '_fnamepool_addr') and self._fnamepool_addr:
            result.fnamepool_addr = self._fnamepool_addr
        if hasattr(self, '_gobjects_addr') and self._gobjects_addr:
            result.gobjects_addr = self._gobjects_addr

        return result

    def check_chain_valid(self) -> bool:
        if not self._cached_gworld_static:
            return False
        gworld_ptr = self._read_ptr(self._cached_gworld_static)
        if not gworld_ptr:
            return False
        current_ptr = gworld_ptr
        for offset in DUMP_VERIFIED_CHAIN[:-1]:
            next_ptr = self._read_ptr(current_ptr + offset)
            if not next_ptr:
                return False
            current_ptr = next_ptr
        return True

    def get_gworld_ptr(self) -> int:
        if not self._cached_gworld_static:
            return 0
        return self._read_ptr(self._cached_gworld_static) or 0

    def read_zone_name(self) -> str:
        """Read current map/zone name via UWorld's UObject FName.

        UE4 UObject layout:
        - 0x18: NamePrivate (FName = ComparisonIndex int32 + Number int32)

        Path: GWorld static -> deref -> UWorld ptr -> +0x18 -> FName index -> FNamePool lookup

        Returns the world's short name (e.g. 'XZ_YuJinZhiXiBiNanSuo200') or empty string.
        Requires FNamePool to be resolved (background scan).
        """
        if not self._cached_gworld_static:
            return ""

        fnamepool = getattr(self, '_fnamepool_addr', 0)
        if not fnamepool:
            self._log("Cannot read zone name - FNamePool not resolved yet")
            return ""

        gworld_ptr = self._read_ptr(self._cached_gworld_static)
        if not gworld_ptr:
            return ""

        fname_index = self._memory.read_value(gworld_ptr + 0x18, "int")
        if fname_index is None or fname_index <= 0:
            self._log(f"Failed to read FName index at GWorld+0x18 (0x{gworld_ptr + 0x18:X})")
            return ""

        world_name = self._memory.read_fname(fnamepool, fname_index)
        if world_name:
            is_ui_zone = "UIMain" in world_name
            # Cache the last real (non-UI-overlay) zone so read_real_zone_name()
            # can return it when the GWorld static happens to point at the UI world.
            if not is_ui_zone:
                self._last_real_zone_name: str = world_name

            last_zone = getattr(self, '_last_logged_zone', '')
            if world_name != last_zone:
                if is_ui_zone:
                    # UIMainLevelV2 oscillates with the real map zone whenever any
                    # in-game UI is open (inventory, map device, etc.).  Throttle the
                    # log to at most once every 30 s to avoid log spam.
                    now = time.monotonic()
                    last_ui_log = getattr(self, '_last_ui_zone_log_at', 0.0)
                    if now - last_ui_log >= 30.0:
                        self._log(f"Zone FName[{fname_index}] = '{world_name}' (UI overlay, suppressing repeats for 30s)")
                        self._last_ui_zone_log_at: float = now
                    # Keep _last_logged_zone on the last REAL zone. If we overwrite
                    # it with UIMain, UI oscillation will force repeated real-zone
                    # logs on every bounce.
                else:
                    self._log(f"Zone FName[{fname_index}] = '{world_name}' (GWorld 0x{gworld_ptr:X})")
                    self._last_logged_zone = world_name
        else:
            self._log(f"FName resolve failed for index {fname_index}")

        return world_name

    def read_real_zone_name(self) -> str:
        """Like read_zone_name() but transparently filters UI overlay zones.

        When the game has a UI world (UIMainLevelV2) as the active GWorld —
        which happens whenever the player opens any in-game UI such as inventory,
        map device, or map selection — this method returns the last known real
        (non-UI) zone FName instead.  All bot features that must NOT react to
        UI overlay worlds should call this instead of read_zone_name().

        The ZoneWatcher is the ONLY caller that should use read_zone_name()
        directly, because it needs to count UIMain reads towards its exit threshold.
        """
        zone = self.read_zone_name()
        if zone and "UIMain" in zone:
            return getattr(self, '_last_real_zone_name', '')
        return zone

    def read_zone_outer_name(self) -> str:
        """Read the package/outer name of the current UWorld.

        Path: GWorld -> deref -> +0x20 (OuterPrivate) -> deref -> +0x18 (FName) -> resolve

        Returns the package name (e.g. the full map package path segment).
        """
        if not self._cached_gworld_static:
            return ""

        fnamepool = getattr(self, '_fnamepool_addr', 0)
        if not fnamepool:
            return ""

        gworld_ptr = self._read_ptr(self._cached_gworld_static)
        if not gworld_ptr:
            return ""

        outer_ptr = self._read_ptr(gworld_ptr + 0x20)
        if not outer_ptr:
            return ""

        fname_index = self._memory.read_value(outer_ptr + 0x18, "int")
        if fname_index is None or fname_index <= 0:
            return ""

        return self._memory.read_fname(fnamepool, fname_index)

    @property
    def fnamepool_addr(self) -> int:
        return self._fnamepool_addr if hasattr(self, '_fnamepool_addr') else 0

    @property
    def gobjects_addr(self) -> int:
        return self._gobjects_addr if hasattr(self, '_gobjects_addr') else 0

    def scan_fnamepool(self, module_base: int = 0, module_size: int = 0) -> int:
        """Find FNamePool address via sig scan. Returns address or 0."""
        if hasattr(self, '_fnamepool_addr') and self._fnamepool_addr:
            ptr = self._memory.read_value(self._fnamepool_addr + 0x10, "ulong")
            if ptr and 0x10000 < ptr < 0x7FFFFFFFFFFF:
                self._log(f"Using cached FNamePool at 0x{self._fnamepool_addr:X}")
                return self._fnamepool_addr

        if not module_base or not module_size:
            modules = self._memory.list_modules()
            if not modules:
                return 0
            module_base = modules[0][1]
            module_size = modules[0][2]

        self._log("Scanning for FNamePool...")

        for i, (pattern, mask) in enumerate(zip(FNAMEPOOL_PATTERNS, FNAMEPOOL_MASKS)):
            if self._cancelled:
                return 0

            self._log(f"  Trying FNamePool pattern {i + 1}/{len(FNAMEPOOL_PATTERNS)}...")
            results = self._pattern_scan(module_base, module_size, pattern, mask)

            lea_base = FNAMEPOOL_LEA_OFFSETS[i] if i < len(FNAMEPOOL_LEA_OFFSETS) else 0

            for addr in results:
                rip_offset = self._memory.read_value(addr + lea_base + 3, "int")
                if rip_offset is None:
                    continue

                fnamepool_addr = addr + lea_base + 7 + rip_offset

                block0_ptr = self._memory.read_value(fnamepool_addr + 0x10, "ulong")
                if not block0_ptr or block0_ptr < 0x10000 or block0_ptr > 0x7FFFFFFFFFFF:
                    continue

                valid_count = 0
                valid_name = ""
                valid_idx = 0
                for test_idx in [3, 5, 10, 20, 50]:
                    test_name = self._memory.read_fname(fnamepool_addr, test_idx)
                    if test_name and len(test_name) > 2 and test_name.isprintable():
                        valid_count += 1
                        if not valid_name:
                            valid_name = test_name
                            valid_idx = test_idx

                if valid_count >= 3:
                    self._log(f"  FNamePool found at 0x{fnamepool_addr:X} (pattern {i + 1})")
                    self._log(f"  Validation: {valid_count}/5 indices readable, FName[{valid_idx}] = '{valid_name}'")
                    self._fnamepool_addr = fnamepool_addr
                    return fnamepool_addr

        self._log("FNamePool not found via sig scan")
        return 0

    def scan_gobjects(self, module_base: int = 0, module_size: int = 0) -> int:
        """Find GObjects (FUObjectArray) address via sig scan. Returns address or 0."""
        if hasattr(self, '_gobjects_addr') and self._gobjects_addr:
            chunks_ptr = self._memory.read_value(self._gobjects_addr, "ulong")
            if chunks_ptr and 0x10000 < chunks_ptr < 0x7FFFFFFFFFFF:
                self._log(f"Using cached GObjects at 0x{self._gobjects_addr:X}")
                return self._gobjects_addr

        if not module_base or not module_size:
            modules = self._memory.list_modules()
            if not modules:
                return 0
            module_base = modules[0][1]
            module_size = modules[0][2]

        self._log("Scanning for GObjects...")

        for i, (pattern, mask) in enumerate(zip(GOBJECTS_PATTERNS, GOBJECTS_MASKS)):
            if self._cancelled:
                return 0

            self._log(f"  Trying GObjects pattern {i + 1}/{len(GOBJECTS_PATTERNS)}...")
            results = self._pattern_scan(module_base, module_size, pattern, mask)

            for addr in results:
                rip_offset = self._memory.read_value(addr + 3, "int")
                if rip_offset is None:
                    continue

                candidate_addr = addr + 7 + rip_offset

                is_lea = (pattern[0:2] == b"\x48\x8D")

                if is_lea:
                    gobjects_addr = candidate_addr
                else:
                    gobjects_addr = candidate_addr

                chunks_ptr = self._memory.read_value(gobjects_addr, "ulong")
                if not chunks_ptr or chunks_ptr < 0x10000 or chunks_ptr > 0x7FFFFFFFFFFF:
                    continue

                num_elements = self._memory.read_value(gobjects_addr + 0x14, "int")
                if num_elements is None or num_elements <= 0 or num_elements > 500000:
                    continue

                num_chunks = self._memory.read_value(gobjects_addr + 0x1C, "int")
                if num_chunks is None or num_chunks <= 0 or num_chunks > 100:
                    continue

                self._log(f"  GObjects found at 0x{gobjects_addr:X} (pattern {i + 1})")
                self._log(f"  NumElements={num_elements}, NumChunks={num_chunks}")
                self._gobjects_addr = gobjects_addr
                return gobjects_addr

        self._log("GObjects not found via sig scan")
        return 0

    def scan_all_globals(self) -> dict:
        """Scan for GWorld, FNamePool, and GObjects in one pass.
        Returns dict with 'gworld', 'fnamepool', 'gobjects' addresses.
        """
        result = {"gworld": 0, "fnamepool": 0, "gobjects": 0}

        modules = self._memory.list_modules()
        if not modules:
            return result

        module_base = modules[0][1]
        module_size = modules[0][2]
        self._log(f"Scanning globals in {modules[0][0]} (0x{module_base:X}, size=0x{module_size:X})")

        gworld = self._find_gworld(module_base, module_size)
        if gworld:
            result["gworld"] = gworld
            self._cached_gworld_static = gworld
            self._log(f"GWorld: 0x{gworld:X}")

        fnamepool = self.scan_fnamepool(module_base, module_size)
        if fnamepool:
            result["fnamepool"] = fnamepool

        gobjects = self.scan_gobjects(module_base, module_size)
        if gobjects:
            result["gobjects"] = gobjects

        return result

    def find_object_by_name(self, name: str) -> list:
        """Find UObject instances by name using GObjects + FNamePool.
        Must call scan_fnamepool() and scan_gobjects() first.
        Returns list of (address, name) tuples.
        """
        fnamepool = getattr(self, '_fnamepool_addr', 0)
        gobjects = getattr(self, '_gobjects_addr', 0)

        if not fnamepool or not gobjects:
            self._log("Cannot find objects - FNamePool or GObjects not resolved")
            return []

        return self._memory.find_gobject_by_name(gobjects, fnamepool, name)

    def _find_gworld(self, module_base: int, module_size: int, pattern_indices: Optional[List[int]] = None) -> Optional[int]:
        if pattern_indices is not None:
            patterns_to_try = [(i, UE4_GWORLD_PATTERNS[i], UE4_GWORLD_MASKS[i]) for i in pattern_indices if i < len(UE4_GWORLD_PATTERNS)]
        elif self._last_gworld_pattern_index >= 0:
            idx = self._last_gworld_pattern_index
            patterns_to_try = [(idx, UE4_GWORLD_PATTERNS[idx], UE4_GWORLD_MASKS[idx])]
            patterns_to_try += [(i, p, m) for i, (p, m) in enumerate(zip(UE4_GWORLD_PATTERNS, UE4_GWORLD_MASKS)) if i != idx]
        else:
            patterns_to_try = [(i, p, m) for i, (p, m) in enumerate(zip(UE4_GWORLD_PATTERNS, UE4_GWORLD_MASKS))]

        for pattern_idx, pattern, mask in patterns_to_try:
            if self._cancelled:
                return None

            self._log(f"Trying GWorld pattern {pattern_idx + 1}/{len(UE4_GWORLD_PATTERNS)}...")

            results = self._pattern_scan(module_base, module_size, pattern, mask)

            for addr in results:
                rip_offset = self._memory.read_value(addr + 3, "int")
                if rip_offset is None:
                    continue

                gworld_addr = addr + 7 + rip_offset
                ptr = self._read_ptr(gworld_addr)
                if ptr:
                    self._log(f"Pattern {pattern_idx + 1} match at 0x{addr:X}, GWorld at 0x{gworld_addr:X}")
                    self._last_gworld_pattern_index = pattern_idx
                    return gworld_addr

        self._log("No GWorld pattern matched")
        return None

    def _pattern_scan(self, base: int, size: int, pattern: bytes, mask: str) -> List[int]:
        import re
        regex_parts = []
        for j in range(len(pattern)):
            if mask[j] == 'x':
                regex_parts.append(re.escape(bytes([pattern[j]])))
            else:
                regex_parts.append(b'.')
        regex = b''.join(regex_parts)
        compiled = re.compile(regex, re.DOTALL)

        results = []
        chunk_size = 0x100000
        overlap = len(pattern) - 1
        offset = 0

        while offset < size:
            read_size = min(chunk_size + overlap, size - offset)
            data = self._memory.read_bytes(base + offset, read_size)
            if not data:
                offset += chunk_size
                continue

            for m in compiled.finditer(data):
                results.append(base + offset + m.start())

            offset += chunk_size

        return results

    def scan_egameplay_events(self) -> List[EventInfo]:
        """Scan for EGameplay event instances and identify their types.

        Finds all EGameplay actors via GObjects class search, then finds their
        ECfgComponent children to read CfgInfo.ID for event type identification.
        Currently CfgInfo.ID is zero at runtime — proper event type offset TBD.
        Random non-target events can spawn, so position alone is not sufficient.

        Returns list of EventInfo for each EGameplay that has an ECfgComponent.
        """
        fnamepool = getattr(self, '_fnamepool_addr', 0)
        gobjects = getattr(self, '_gobjects_addr', 0)

        if not fnamepool or not gobjects:
            self._log_debug("Cannot scan events - FNamePool or GObjects not resolved")
            return []

        egameplay_objects = self._memory.find_gobjects_by_class_name(gobjects, fnamepool, "EGameplay")
        if not egameplay_objects:
            self._log_debug("No EGameplay instances found in GObjects")
            return []

        self._log_debug(f"Found {len(egameplay_objects)} EGameplay instances (by class)")

        egameplay_addrs = {addr for addr, _ in egameplay_objects}

        ecfg_objects = self._memory.find_gobjects_by_class_name(gobjects, fnamepool, "ECfgComponent")
        if not ecfg_objects:
            self._log_debug("No ECfgComponent instances found in GObjects (by class)")
            return []

        self._log_debug(f"Found {len(ecfg_objects)} ECfgComponent instances (by class)")

        ecfg_by_outer = {}
        for ecfg_addr, ecfg_name in ecfg_objects:
            outer_ptr = self._read_ptr(ecfg_addr + UE4_UOBJECT_OUTER_OFFSET)
            if outer_ptr and outer_ptr in egameplay_addrs:
                ecfg_by_outer[outer_ptr] = ecfg_addr

        self._log_debug(f"Matched {len(ecfg_by_outer)} ECfgComponent->EGameplay pairs")

        events = []
        for eg_addr, eg_name in egameplay_objects:
            if eg_name.startswith("Default__"):
                continue
            ecfg_addr = ecfg_by_outer.get(eg_addr)
            if not ecfg_addr:
                self._log_debug(f"  {eg_name} at 0x{eg_addr:X}: no ECfgComponent found (base controller?)")
                continue

            event = self._read_event_info(eg_addr, ecfg_addr)
            if event:
                events.append(event)

        target_events = [e for e in events if e.is_target_event]
        other_events = [e for e in events if not e.is_target_event]

        if target_events:
            for e in target_events:
                self._log_debug(f"  TARGET: {e}")
        if other_events:
            for e in other_events:
                self._log_debug(f"  OTHER: {e}")

        return events

    def get_positioned_events(self) -> List[EventInfo]:
        """Return all live EGameplay instances that have a non-zero world position.

        Used for reactive event navigation when CfgInfo type identification is
        unavailable. Returns only instances whose position is already loaded
        (non-zero X or Y), which indicates the event is active and reachable.
        Instances at (0,0,0) are skipped (phantom or step-on events not yet loaded).
        """
        fnamepool = getattr(self, '_fnamepool_addr', 0)
        gobjects = getattr(self, '_gobjects_addr', 0)

        if not fnamepool or not gobjects:
            return []

        egameplay_objects = self._memory.find_gobjects_by_class_name(gobjects, fnamepool, "EGameplay")
        if not egameplay_objects:
            return []

        events = []
        for eg_addr, eg_name in egameplay_objects:
            if eg_name.startswith("Default__"):
                continue

            root_comp = self._read_ptr(eg_addr + UE4_OFFSETS["RootComponent"])
            if not root_comp:
                continue

            loc_offset = UE4_OFFSETS["RelativeLocation"]
            pos_data = self._memory.read_bytes(root_comp + loc_offset, 12)
            if not pos_data or len(pos_data) < 12:
                continue

            x, y, z = struct.unpack_from("<fff", pos_data, 0)
            if not self._is_plausible_coordinate(x) or not self._is_plausible_coordinate(y):
                continue

            event = EventInfo()
            event.address = eg_addr
            event.position = (x, y, z)

            # ⚠️ wave_counter UNRELIABLE — display only, do not use in logic
            wave_data = self._memory.read_bytes(eg_addr + 0x618, 4)
            if wave_data and len(wave_data) >= 4:
                event.wave_counter = struct.unpack_from("<i", wave_data, 0)[0]

            spawn_data = self._memory.read_bytes(eg_addr + 0x714, 4)
            if spawn_data and len(spawn_data) >= 4:
                event.spawn_index = struct.unpack_from("<i", spawn_data, 0)[0]

            # EEntity::bValid at +0x720 — goes 0 when the event ends/is invalidated.
            bvalid_data = self._memory.read_bytes(eg_addr + 0x720, 1)
            if bvalid_data:
                event.bvalid = bvalid_data[0]

            if fnamepool:
                sub_obj_ptr = self._read_ptr(eg_addr + 0x4E0)
                if sub_obj_ptr and sub_obj_ptr > 0x10000:
                    event.sub_object_name = self._memory.read_uobject_name(fnamepool, sub_obj_ptr)
                    sub_class_ptr = self._read_ptr(sub_obj_ptr + 0x10)
                    if sub_class_ptr:
                        event.sub_object_class = self._memory.read_uobject_name(fnamepool, sub_class_ptr)

            self._log_debug(f"Positioned event at ({x:.0f},{y:.0f}) sub='{event.sub_object_name}' spawn={event.spawn_index}")
            events.append(event)

        return events

    # Maximum world-unit distance between an EGameplay event and an
    # EMapCustomTrapS* vehicle to consider them the same Carjack encounter.
    # Confirmed Feb 24 2026: MapCustomTrap spawn indices are completely
    # independent from MapGamePlay spawn indices (e.g. MapGamePlay keys
    # 0x9/0xa/0xb vs MapCustomTrap keys 0x13/0x78 on the same map).
    # The only reliable link is world-position proximity.
    _CARJACK_POS_TOLERANCE: float = 200.0

    def get_typed_events(self) -> List[EventInfo]:
        """Return typed event info by reading FightMgr.MapGamePlay TMap directly.

        The TMap key is the runtime spawn_index (sequential instance ID), NOT a
        static event type enum value.

        Three-way classification (confirmed Feb 24 2026):
          MapCustomTrap (+0x7B0) is the ground truth for known event types:
            - EMapCustomTrapS* (seasonal S5/S7/S10/S11) = Carjack vehicle
            - EMapCustomTrap (exact base class, no suffix) = Sandlord arena platform
              EMapCustomTrap2/3/Attach = wave-spawn-trigger mechanics that appear
              DURING the Sandlord fight — confirmed Feb 24 2026 from live scan data;
              these are NOT navigable and must be ignored for platform detection.
              WARNING: only 1 Sandlord per map (hard game rule, confirmed by user).

          Carjack:  EGameplay world-pos is within _CARJACK_POS_TOLERANCE of a
                    Carjack vehicle.  is_target_event=True.
          Sandlord: EGameplay has pos=(0,0) AND at least one Sandlord platform is
                    present in MapCustomTrap.  is_target_event=True.  Exactly ONE
                    per map — the first pos=(0,0) EGameplay is the Sandlord; any
                    additional pos=(0,0) entries are classified Unknown.
                    platform_positions[0] is used for navigation; the wave counter
                    at EGameplay+0x618 is used for completion.
          Unknown:  Everything else — real pos not near any vehicle, OR pos=(0,0)
                    but no Sandlord platform found, OR extra pos=(0,0) entries
                    beyond the first.  is_target_event=False.  Bot ignores these.

        NOTE: MapCustomTrap spawn indices are independent from MapGamePlay spawn
        indices — spawn-index cross-reference does NOT work.

        Falls back to get_positioned_events() if FightMgr is not yet found.
        """
        if not self._fightmgr_ptr:
            self._fightmgr_ptr = self._find_fightmgr()
        if not self._fightmgr_ptr:
            return self.get_positioned_events()

        # Validate FightMgr ptr is still alive
        test = self._memory.read_value(self._fightmgr_ptr, "ulong")
        if not test or test < 0x10000:
            self._fightmgr_ptr = 0
            self._last_monster_scan_log = ""
            self._last_near_events_log = ""
            self._last_guard_targets_log = ""
            self._last_guard_targets_log_time = 0.0
            self._guard_debug_cache.clear()
            return self.get_positioned_events()

        events = self._read_tmap_events(self._fightmgr_ptr + FIGHTMGR_MAP_GAMEPLAY_OFFSET,
                                        silent=True)

        # Read MapCustomTrap entries.  Their TMap keys (spawn_index) are
        # independent from EGameplay spawn indices — match by position only.
        trap_events = self._read_tmap_events(
            self._fightmgr_ptr + FIGHTMGR_MAP_CUSTOMTRAP_OFFSET,
            silent=True,
        )

        # Build Carjack vehicle positions AND Sandlord platform positions.
        # MapCustomTrap holds BOTH seasonal Carjack vehicles (EMapCustomTrapS*)
        # AND Sandlord trigger platforms (base class EMapCustomTrap / EMapCustomTrap2 /
        # EMapCustomTrap3).
        # Discriminate by class name: "TrapS" suffix → Carjack vehicle;
        #   exact "EMapCustomTrap" (no suffix) → Sandlord arena platform (navigate here);
        #   "EMapCustomTrap2/3/Attach" → wave-spawn-trigger mechanics that appear
        #     DURING the Sandlord fight — confirmed Feb 24 2026 from live scan data.
        #     These are NOT navigable; ignoring them eliminates the false-positive
        #     platform issue entirely.
        # If FNamePool is not yet resolved, fall back to vehicle_positions only
        # (Sandlord platform assignment will be skipped).
        fnamepool = getattr(self, '_fnamepool_addr', 0)
        vehicle_positions: List[tuple] = []   # (x, y) from EMapCustomTrapS* — Carjack vehicles
        vehicle_entities: List[int] = []      # entity address parallel to vehicle_positions
        platform_positions: List[tuple] = []  # (x, y) from EMapCustomTrap only — Sandlord arena
        trap_s_count = 0
        trap_base_count = 0
        trap_other_count = 0
        trap_unresolved_count = 0
        tol = self._CARJACK_POS_TOLERANCE
        last_truck_pos = getattr(self, "_carjack_truck_pos", (0.0, 0.0))
        has_last_truck_pos = (
            isinstance(last_truck_pos, tuple)
            and len(last_truck_pos) >= 2
            and (abs(last_truck_pos[0]) > 1.0 or abs(last_truck_pos[1]) > 1.0)
        )
        # Collect pending diagnostic log lines; emitted only when classification changes.
        _pending_logs: List[str] = []
        for trap in trap_events:
            vx, vy = trap.position[0], trap.position[1]
            if abs(vx) < 1.0 and abs(vy) < 1.0:
                continue  # unspawned / origin — skip
            if fnamepool:
                class_ptr = self._read_ptr(trap.address + 0x10)
                class_name = (
                    self._memory.read_uobject_name(fnamepool, class_ptr)
                    if class_ptr else ""
                )
                if "TrapS" in class_name:
                    trap_s_count += 1
                    vehicle_positions.append((vx, vy))
                    vehicle_entities.append(trap.address)
                    # Log entity address for cross-referencing with Cheat Engine scans.
                    _pending_logs.append(
                        f"[typed_event] MapCustomTrap Carjack vehicle "
                        f"class='{class_name}' entity=0x{trap.address:X} pos=({vx:.0f},{vy:.0f})"
                    )
                elif class_name == "EMapCustomTrap":
                    trap_base_count += 1
                    # Only the exact base class is the real Sandlord arena platform.
                    platform_positions.append((vx, vy))
                    _pending_logs.append(
                        f"[typed_event] MapCustomTrap Sandlord arena "
                        f"class='{class_name}' pos=({vx:.0f},{vy:.0f})"
                    )
                elif (not class_name and has_last_truck_pos
                      and abs(vx - last_truck_pos[0]) <= tol
                      and abs(vy - last_truck_pos[1]) <= tol):
                    trap_unresolved_count += 1
                    vehicle_positions.append((vx, vy))
                    vehicle_entities.append(trap.address)
                    _pending_logs.append(
                        f"[typed_event] MapCustomTrap Carjack vehicle "
                        f"class='<unresolved>' entity=0x{trap.address:X} "
                        f"pos=({vx:.0f},{vy:.0f}) (continuity from last truck pos)"
                    )
                else:
                    trap_other_count += 1
                    # EMapCustomTrap2/3/Attach — fight-mechanic objects (wave triggers).
                    # Appear during the Sandlord fight; not a navigable platform.
                    _pending_logs.append(
                        f"[typed_event] MapCustomTrap fight mechanic (ignored) "
                        f"class='{class_name}' pos=({vx:.0f},{vy:.0f})"
                    )
            else:
                # No FNamePool — cannot discriminate; treat as Carjack vehicle (old fallback)
                trap_s_count += 1
                vehicle_positions.append((vx, vy))

        # Three-way classification:
        #   Carjack  — EGameplay pos near an EMapCustomTrapS* vehicle
        #   Sandlord — FIRST EGameplay with pos=(0,0), if Sandlord platform(s) present
        #              (exactly 1 per map; extra pos=(0,0) entries become Unknown)
        #   Unknown  — everything else; is_target_event=False so bot ignores it
        sandlord_found = False   # only 1 Sandlord per map — cap after first match
        for ev in events:
            px, py = ev.position[0], ev.position[1]

            # --- Carjack check (position proximity to vehicle) ---
            matched_vpos = None
            if abs(px) > 1.0 or abs(py) > 1.0:
                for vx, vy in vehicle_positions:
                    if abs(px - vx) <= tol and abs(py - vy) <= tol:
                        matched_vpos = (vx, vy)
                        break

            if matched_vpos is not None:
                ev.event_type = "Carjack"
                ev.is_target_event = True
                self._carjack_truck_pos = matched_vpos  # guard-tagging proximity gate
                if fnamepool:
                    for idx, (vx, vy) in enumerate(vehicle_positions):
                        if vx == matched_vpos[0] and vy == matched_vpos[1] and idx < len(vehicle_entities):
                            veh_addr = vehicle_entities[idx]
                            ev.carjack_vehicle_addr = veh_addr
                            self._carjack_vehicle_addr = veh_addr  # scanner-level store for guard roster
                            # Fire guard roster probe immediately when truck is classified.
                            # This makes TRAP-PROBE logs appear on every "Scan Events" click,
                            # not just during the bot chase loop — enables manual validation.
                            # Fire byte-dump diagnostics once per session; method always returns [].
                            self._read_truck_guard_roster(veh_addr, fnamepool)
                            _pending_logs.append(
                                f"[TRAP-PROBE] Guard positions via proximity cache "
                                f"(TArray@+0x128 confirmed dead v4.41.5)"
                            )
                            trap_info = self._read_custom_trap_info_silent(veh_addr, fnamepool)
                            if trap_info:
                                ev.carjack_cur_status_index = trap_info.get("cur_status_index", -1)
                                ev.carjack_cur_status = trap_info.get("cur_status", -1)
                                ev.carjack_trap_execute_state = trap_info.get("trap_execute_state", -1)
                                ev.carjack_wait_time = trap_info.get("wait_time", -1.0)
                                ev.carjack_hit_count = trap_info.get("hit_count", -1)
                                ev.carjack_work_count = trap_info.get("work_count", -1)
                                ev.carjack_max_work_count = trap_info.get("max_work_count", -1)
                                ev.carjack_skill_index = trap_info.get("skill_index", -1)
                                ev.carjack_trigger_index = trap_info.get("trigger_index", -1)
                                ev.carjack_player_enter = trap_info.get("player_enter", -1)
                                _pending_logs.append(
                                    f"[typed_event] Carjack trap info "
                                    f"veh=0x{veh_addr:X} hit={ev.carjack_hit_count} "
                                    f"work={ev.carjack_work_count}/{ev.carjack_max_work_count} "
                                    f"state={ev.carjack_trap_execute_state}/{ev.carjack_cur_status} "
                                    f"wait={ev.carjack_wait_time:.2f}"
                                )
                            break
                _pending_logs.append(
                    f"[typed_event] spawn={ev.cfg_id:#x} → Carjack "
                    f"(pos match) vehicle_pos=({matched_vpos[0]:.0f},{matched_vpos[1]:.0f})"
                )

            elif abs(px) < 1.0 and abs(py) < 1.0 and platform_positions and not sandlord_found:
                # Sandlord: pos=(0,0), Sandlord platform present, first occurrence only.
                # Use platform_positions[0] — the arena that loads before barrels/traps.
                plat = platform_positions[0]
                ev.event_type = "Sandlord"
                ev.is_target_event = True
                ev.position = (plat[0], plat[1], ev.position[2])
                sandlord_found = True
                _pending_logs.append(
                    f"[typed_event] spawn={ev.cfg_id:#x} → Sandlord "
                    f"(platform pos assigned) platform=({plat[0]:.0f},{plat[1]:.0f})"
                )

            else:
                # Unknown: real position not near any vehicle, OR pos=(0,0) but no
                # Sandlord platform exists, OR extra pos=(0,0) beyond the first.
                # Bot will ignore this event.
                ev.event_type = "Unknown"
                ev.is_target_event = False
                _pending_logs.append(
                    f"[typed_event] spawn={ev.cfg_id:#x} → Unknown (ignored) "
                    f"pos=({ev.position[0]:.0f},{ev.position[1]:.0f})"
                )

        # Only emit diagnostic logs when the classification result changes.
        # Prevents flooding the log file during the 20 Hz overlay polling loop.
        fp = "|".join(
            f"{e.cfg_id}:{e.event_type}:{e.position[0]:.0f},{e.position[1]:.0f}"
            for e in events
        )
        if fp != self._typed_events_fp:
            self._typed_events_fp = fp
            carjack_events = sum(1 for e in events if e.event_type == "Carjack")
            sandlord_events = sum(1 for e in events if e.event_type == "Sandlord")
            unknown_events = sum(1 for e in events if e.event_type == "Unknown")
            self._log_debug(
                "[CarjackPhase] "
                f"MapGamePlay={len(events)}(C:{carjack_events},S:{sandlord_events},U:{unknown_events}) "
                f"MapCustomTrap={len(trap_events)}(S:{trap_s_count},Base:{trap_base_count},Other:{trap_other_count},UnkClass:{trap_unresolved_count}) "
                f"Veh={len(vehicle_entities)} Plat={len(platform_positions)}"
            )
            for msg in _pending_logs:
                self._log_debug(msg)

        return events

    # ── Background EntityScan thread ─────────────────────────────────────────

    def _start_entity_scan_thread_if_needed(self) -> None:
        """Start the background EntityScan daemon thread if it is not already running.

        The thread scans MapRoleMonster TMap at 8 ms (~120 Hz) and emits ONE compact
        [EScan] log line per new entity address (address never seen before this
        FightMgr session) and ONE [EScan] RESOLVED line when a pending ABP is
        later resolved.  This keeps log size proportional to the number of UNIQUE
        entities seen rather than the number of scan cycles.

        Maximum log output per entity: 2 lines (NEW + RESOLVED).
        For a 1500-entity Carjack event: ≤ 3000 lines vs 30 000+ with verbose CompScan.
        """
        if self._entity_scan_thread and self._entity_scan_thread.is_alive():
            return
        self._entity_scan_thread_active = True
        t = threading.Thread(target=self._entity_scan_loop, daemon=True, name="EntityScan")
        self._entity_scan_thread = t
        t.start()
        log.info("[EScan] Background entity scan thread started (8 ms / ~120 Hz)")

    def _entity_scan_loop(self) -> None:
        """Background thread body.  Runs _entity_scan_tick() every 8 ms (~120 Hz)."""
        _INTERVAL = ENTITY_SCAN_INTERVAL_S   # 8 ms → ~120 Hz entity scanning
        while self._entity_scan_thread_active:
            t0 = time.monotonic()
            try:
                if self._fightmgr_ptr:
                    test = self._memory.read_value(self._fightmgr_ptr, "ulong")
                    if test and test >= 0x10000:
                        self._entity_scan_tick()
            except Exception as exc:
                log.debug(f"[EScan] tick error: {exc}")
            elapsed = time.monotonic() - t0
            sleep_for = _INTERVAL - elapsed
            if sleep_for > 0.001:
                time.sleep(sleep_for)

    # Guards always spawn within ~2000u of the truck.  Tightened to 2500u in
    # v4.38.0 after ABP pre-filter was removed.  Without ABP gating a 5000u radius
    # caught the full ~100-monster spawn cluster; 2500u keeps real guards while
    # shedding the outer attacker ring.  Sandlord-zone entities are typically
    # 5000–20000u away and are unaffected by this change.
    _GUARD_TRUCK_RADIUS_SQ: float = 2500.0 ** 2
    # Guard chase window: guards can flee far from the truck during the 24s event.
    # Keep a broader candidate pool for chase targeting while still bounded to
    # Carjack-local combat space.
    _GUARD_CHASE_RADIUS_SQ: float = 12000.0 ** 2
    # [EScanTrack] diagnostics tuning (active Carjack only)
    _ESCAN_TRACK_RADIUS_SQ: float = 4500.0 ** 2
    _ESCAN_TRACK_MOVE_SQ: float = 140.0 ** 2
    _ESCAN_TRACK_MIN_INTERVAL: float = 0.20

    def _maybe_log_escan_track(self, e: EventInfo) -> None:
        """Emit throttled per-address movement samples during active Carjack.

        This produces trajectory-ready logs (`[EScanTrack]`) for entities near
        the truck so post-run scripts can match user-provided waypoint sequences
        to stable addresses. Logging is gated by Carjack-active window, distance
        to truck, minimum time interval, and minimum movement delta.
        """
        now_mono = time.monotonic()
        if now_mono > self._carjack_active_until:
            return
        truck = self._carjack_truck_pos
        if truck is None:
            return
        px, py = e.position[0], e.position[1]
        tx, ty = truck
        dx_t = px - tx
        dy_t = py - ty
        dist_truck_sq = dx_t * dx_t + dy_t * dy_t
        if dist_truck_sq > self._ESCAN_TRACK_RADIUS_SQ:
            return

        last = self._escan_track_last.get(e.address)
        if last is not None:
            lx, ly, lt = last
            move_dx = px - lx
            move_dy = py - ly
            move_sq = move_dx * move_dx + move_dy * move_dy
            if (now_mono - lt) < self._ESCAN_TRACK_MIN_INTERVAL and move_sq < self._ESCAN_TRACK_MOVE_SQ:
                return

        self._escan_track_last[e.address] = (px, py, now_mono)
        # abp = self._abp_cache.get(e.address, "")
        # abp_str = f"'{abp}'" if abp else "PENDING"
        # log.info(
        #     f"[EScanTrack] 0x{e.address:X} "
        #     f"pos=({px:.0f},{py:.0f}) "
        #     f"dt={dist_truck_sq ** 0.5:.0f} "
        #     f"abp={abp_str}"
        # )

    def _entity_scan_tick(self) -> None:
        """Single 8 ms scan tick (~120 Hz).

        1. Reads MapRoleMonster TMap.
        2. Updates _entity_pos_history deques for every entity (used by get_fleeing_entities).
        3. For each NEW address: tries ABP via _read_abp_silent; logs ONE compact
           [EScan] line.  Tags first 3 entities within 2500u of truck in the
           GUARD_SEED_WINDOW_SECS window as [GuardSeed].  Unresolved ABPs go to
           pending-retry.
        4. For PENDING addresses still in TMap: retries ABP on resolution.
        5. Address-reuse detection (same logic as get_monster_entities).
        """
        fnamepool = self._fnamepool_addr
        if not fnamepool:
            return

        entities = self._read_tmap_events(
            self._fightmgr_ptr + FIGHTMGR_MAP_MONSTER_OFFSET,
            silent=True,
        )

        current_addrs: set = {e.address for e in entities}

        # Drop pending-retry entries that have left the TMap entirely.
        # Their entity is gone and ABP will never resolve — log at DEBUG only.
        gone_pending = self._pending_abp_retry - current_addrs
        for addr in gone_pending:
            self._pending_abp_retry.discard(addr)
            log.debug(f"[EScan] 0x{addr:X} left TMap with ABP unresolved")

        for e in entities:
            addr = e.address

            # ── Address-reuse detection ──────────────────────────────────
            if addr in self._abp_cache:
                last = self._abp_last_pos.get(addr)
                if last is not None:
                    dx = e.position[0] - last[0]
                    dy = e.position[1] - last[1]
                    dist_sq = dx * dx + dy * dy
                    if dist_sq > _ABP_REUSE_THRESHOLD_SQ:
                        old_abp = self._abp_cache.pop(addr)
                        self._map_icon_cache.pop(addr, None)
                        self._cfg_scan_cache.pop(addr, None)
                        self._comp_logged_ptrs.discard(addr)
                        self._ever_seen_addrs.discard(addr)
                        self._pending_abp_retry.discard(addr)
                        self._entity_pos_history.pop(addr, None)
                        self._entity_first_seen_t.pop(addr, None)
                        log.debug(f"[EScan] Addr 0x{addr:X} reused "
                                  f"(pos jump {dist_sq**0.5:.0f}u, was '{old_abp}')")
            if abs(e.position[0]) > 0.1 or abs(e.position[1]) > 0.1:
                self._abp_last_pos[addr] = (e.position[0], e.position[1])
            self._maybe_log_escan_track(e)
            # Position history: always update for flee-detection velocity calc.
            _now_tick = time.monotonic()
            self._entity_pos_history.setdefault(addr, deque(maxlen=16)).append(
                (_now_tick, e.position[0], e.position[1]))
            self._entity_first_seen_t.setdefault(addr, _now_tick)
            # Movement-data CSV: queue row if Carjack active and entity within 8 000u.
            if (self._movdata_open_done
                    and self._carjack_active_since > 0.0
                    and self._carjack_truck_pos is not None):
                _tx, _ty = self._carjack_truck_pos
                _dt = ((e.position[0] - _tx) ** 2 + (e.position[1] - _ty) ** 2) ** 0.5
                if _dt <= 8000.0:
                    self._movdata_queue.append((
                        int((_now_tick - self._movdata_session_t0) * 1000),
                        self._movdata_carjack_n,
                        int((_now_tick - self._carjack_active_since) * 1000),
                        f"0x{addr:X}",
                        round(e.position[0], 1),
                        round(e.position[1], 1),
                        self._abp_cache.get(addr, ""),
                        round(_dt, 1),
                        1 if addr in self._guard_seed_addrs else 0,
                    ))

            # ── New entity ───────────────────────────────────────────────
            if addr not in self._ever_seen_addrs:
                self._ever_seen_addrs.add(addr)
                abp, cfg_id, _ = self._read_abp_silent(addr, fnamepool)
                if abp:
                    self._abp_cache[addr] = abp
                else:
                    self._pending_abp_retry.add(addr)
                if isinstance(cfg_id, tuple) or cfg_id != -1:
                    self._cfg_scan_cache[addr] = cfg_id
                # [GuardSeed] — tag first GUARD_SEED_MAX entities within 2500u of truck
                # that appear in the GUARD_SEED_WINDOW_SECS window after Carjack activates.
                # These are the initial 3 guards the game spawns at event start.
                if (self._carjack_active_since > 0.0
                        and time.monotonic() < self._carjack_active_since + GUARD_SEED_WINDOW_SECS
                        and self._guard_seed_count < GUARD_SEED_MAX
                        and self._carjack_truck_pos is not None):
                    _tx, _ty = self._carjack_truck_pos
                    _d = ((e.position[0] - _tx) ** 2 + (e.position[1] - _ty) ** 2) ** 0.5
                    if _d < 2500.0:
                        self._guard_seed_addrs.add(addr)
                        self._guard_seed_count += 1
                        abp_str_seed = f"'{abp}'" if abp else "PENDING"
                        log.info(f"[GuardSeed] #{self._guard_seed_count} 0x{addr:X} abp={abp_str_seed} "
                                 f"pos=({e.position[0]:.0f},{e.position[1]:.0f}) dist_truck={_d:.0f}")
                abp_str = f"'{abp}'" if abp else "PENDING"
                log.debug(
                    f"[EScan] 0x{addr:X} "
                    f"pos=({e.position[0]:.0f},{e.position[1]:.0f}) "
                    f"abp={abp_str}"
                )

            # ── Pending retry ────────────────────────────────────────────
            elif addr in self._pending_abp_retry:
                abp, cfg_id, _ = self._read_abp_silent(addr, fnamepool)
                if abp:
                    self._abp_cache[addr] = abp
                    if isinstance(cfg_id, tuple) or cfg_id != -1:
                        self._cfg_scan_cache[addr] = cfg_id
                    self._pending_abp_retry.discard(addr)
                    log.debug(f"[EScan] RESOLVED 0x{addr:X} '{abp}'")

    def get_monster_entities(self) -> List[EventInfo]:
        """Return live monster entities from FightMgr.MapRoleMonster TMap.

        Lightweight hot path: reads TMap for current positions/bvalid and
        annotates from caches populated by the background EntityScan thread.
        ABP resolution and per-entity logging are handled off the overlay path
        by _entity_scan_tick() to keep log volume proportional to unique entity
        count (not scan frequency).
        """
        if not self._fightmgr_ptr:
            self._fightmgr_ptr = self._find_fightmgr()
        if not self._fightmgr_ptr:
            log.debug("[EntityScanner] FightMgr not found — not attached?")
            return []

        test = self._memory.read_value(self._fightmgr_ptr, "ulong")
        if not test or test < 0x10000:
            log.debug("[EntityScanner] FightMgr ptr invalid, resetting.")
            self._fightmgr_ptr = 0
            self._last_monster_scan_log = ""
            self._last_near_events_log = ""
            self._ever_seen_addrs.clear()
            self._pending_abp_retry.clear()
            self._monster_last_pos.clear()
            self._carjack_truck_pos = None
            self._carjack_vehicle_addr = 0
            self._truck_probe_done = False
            self._entity_pos_history.clear()
            self._entity_first_seen_t.clear()
            self._guard_seed_count = 0
            self._guard_seed_addrs.clear()
            self._carjack_active_since = 0.0
            self._carjack_active_until = 0.0
            self._flee_track_last_log.clear()
            return []

        # Ensure the background EntityScan thread is running.
        self._start_entity_scan_thread_if_needed()

        entities = self._read_tmap_events(
            self._fightmgr_ptr + FIGHTMGR_MAP_MONSTER_OFFSET,
            silent=True,
            read_class_names=True,
        )
        alive = sum(1 for e in entities if e.bvalid != 0)
        _scan_str = (f"[EntityScanner] MapRoleMonster scan: {len(entities)} total, {alive} alive"
                     f" (FightMgr=0x{self._fightmgr_ptr:X})")
        if _scan_str != self._last_monster_scan_log:
            log.debug(_scan_str)
            self._last_monster_scan_log = _scan_str

        # Annotate from cache (populated by background EntityScan thread).
        for e in entities:
            e.abp_class = self._abp_cache.get(e.address, "")
            e.map_icon  = self._map_icon_cache.get(e.address, -1)
            # Repurpose source_type for display: use cfg_id from ECfgComponent if available.
            raw_cfg = self._cfg_scan_cache.get(e.address, -1)
            e.source_type = raw_cfg[0] if isinstance(raw_cfg, tuple) else raw_cfg
            e.monster_point_id = -1   # preserved in EventInfo for display compat

        prev_pos_map = dict(self._monster_last_pos)

        # Log per-event nearby monster counts: "Near Carjack(x,y): N(G:3) | Near Sandlord(x,y): M"
        # This lets us directly correlate monster activity with each event from the log file.
        # Only include events with a valid world position (skip pos≈(0,0) = lazy-loaded events).
        # Deduplicate by (event_type, rounded position) to avoid the same event being listed
        # twice when get_typed_events() returns two MapGamePlay entries at the same vehicle.
        try:
            raw_events = [ev for ev in self.get_typed_events() if ev.is_target_event
                          and (abs(ev.position[0]) > 1.0 or abs(ev.position[1]) > 1.0)]
            target_events = []
            for ev in raw_events:
                dup = False
                for ex in target_events:
                    if ex.event_type != ev.event_type:
                        continue
                    dx = ex.position[0] - ev.position[0]
                    dy = ex.position[1] - ev.position[1]
                    if (dx * dx + dy * dy) <= (250.0 * 250.0):
                        dup = True
                        break
                if not dup:
                    target_events.append(ev)
            if target_events:
                now_mono = time.monotonic()
                if any(ev.event_type == "Carjack" for ev in target_events):
                    self._carjack_active_until = now_mono + 2.0
                    if self._carjack_active_since == 0.0:
                        self._carjack_active_since = now_mono
                        log.info(f"[GuardSeed] Carjack activated — seed window open for {GUARD_SEED_WINDOW_SECS:.1f}s")
                        self._movdata_open_session()   # no-op after first call
                        self._movdata_carjack_n += 1
                        log.info(f"[MovData] Carjack #{self._movdata_carjack_n} started")
                _r2 = 3000.0 ** 2   # same 3000u radius used by overlay Carjack guard scanner
                parts = []
                player_pos = self._read_player_xy()
                for ev in target_events:
                    ex, ey = ev.position[0], ev.position[1]
                    nearby_alive = [
                        m for m in entities
                        if m.bvalid != 0
                        and (m.position[0] - ex) ** 2 + (m.position[1] - ey) ** 2 <= _r2
                    ]
                    n_alive = len(nearby_alive)
                    label = f"{ev.event_type}({ex:.0f},{ey:.0f}):{n_alive}"
                    if ev.event_type == "Carjack":
                        # Guard detection: EServant/MapServant CONFIRMED FALSE POSITIVE (v4.61.0).
                        # EServant = player pet companion. MapServant = pet registry.
                        # Guards (押运保镖) are EMonster in MapRoleMonster — real class TBD.
                        # G:n = 0 until authoritative guard class is identified.
                        servants = []   # placeholder — MapServant reads player pets
                        n_servants = 0
                        auth_link_n = 0
                        if ev.carjack_vehicle_addr:
                            auth_link_n = len(
                                self._carjack_link_ptrs_by_vehicle.get(ev.carjack_vehicle_addr, set())
                            )
                        trap_progress = ""
                        if ev.carjack_hit_count >= 0:
                            trap_progress = (
                                f",CK:{ev.carjack_hit_count},CW:{ev.carjack_work_count}/{ev.carjack_max_work_count}"
                                f",TS:{ev.carjack_trap_execute_state},CS:{ev.carjack_cur_status}"
                            )
                        label += f"(A:{auth_link_n},G:{n_servants}{trap_progress})"
                        # No guard positions available — EServant approach reverted (was tracking pet).
                    parts.append(label)
                _near_str = f"[EntityScanner] Near events — {' | '.join(parts)}"
                if _near_str != self._last_near_events_log:
                    log.debug(_near_str)
                    self._last_near_events_log = _near_str
            else:
                if time.monotonic() > self._carjack_active_until:
                    self._carjack_active_until = 0.0
                self._last_guard_targets_log = ""
                self._last_guard_targets_log_time = 0.0
                self._carjack_active_since = 0.0
                self._guard_seed_count = 0
                self._guard_seed_addrs.clear()
                self._flee_track_last_log.clear()
                self._carjack_probe_prev_bytes.clear()
                self._carjack_probe_prev_links.clear()
                self._carjack_link_ptrs_by_vehicle.clear()
        except Exception:
            pass

        current_addrs = {e.address for e in entities}
        for e in entities:
            self._monster_last_pos[e.address] = (e.position[0], e.position[1])
        stale = set(self._monster_last_pos.keys()) - current_addrs
        stale_track = set(self._escan_track_last.keys()) - current_addrs
        for addr in stale_track:
            self._escan_track_last.pop(addr, None)
        for addr in stale:
            self._monster_last_pos.pop(addr, None)
            self._entity_pos_history.pop(addr, None)
            self._entity_first_seen_t.pop(addr, None)

        return entities

    def _read_player_xy(self) -> Optional[tuple]:
        """Read current player world XY from Pawn->RootComponent->RelativeLocation."""
        try:
            pawn = self._get_player_pawn()
            if not pawn:
                return None
            root = self._read_ptr(pawn + UE4_OFFSETS["RootComponent"])
            if not root:
                return None
            pos = self._memory.read_bytes(root + UE4_OFFSETS["RelativeLocation"], 12)
            if not pos or len(pos) < 8:
                return None
            x, y = struct.unpack_from("<ff", pos, 0)
            return (x, y)
        except Exception:
            return None

    # ── Movement-data CSV helpers (v4.66.0) ─────────────────────────────────

    def _movdata_open_session(self) -> None:
        """Open the per-session movement CSV file and start the writer thread.

        No-op after the first call — one file covers the whole bot session.
        Called on first Carjack activation.
        """
        if self._movdata_open_done:
            return
        try:
            os.makedirs("logs", exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.abspath(f"logs/mov_{ts}.csv")
            self._movdata_file = open(path, "w", newline="", encoding="utf-8")
            self._movdata_csv = csv.writer(self._movdata_file)
            self._movdata_csv.writerow([
                "t_abs_ms", "carjack_n", "t_carjack_ms",
                "addr", "x", "y", "abp", "dist_truck", "is_seed",
            ])
            self._movdata_session_t0 = time.monotonic()
            self._movdata_stop.clear()
            self._movdata_thread = threading.Thread(
                target=self._movdata_writer_loop, daemon=True, name="MovDataWriter"
            )
            self._movdata_thread.start()
            self._movdata_open_done = True
            log.info(f"[MovData] Session CSV opened: {path}")
        except Exception as exc:
            log.warning(f"[MovData] Failed to open CSV: {exc}")

    def _movdata_writer_loop(self) -> None:
        """Background writer thread: flushes queue to disk every 33 ms (~30 Hz)."""
        while not self._movdata_stop.wait(timeout=0.033):
            self._movdata_flush()
        # Final flush when stop is set
        self._movdata_flush()

    def _movdata_flush(self) -> None:
        """Drain the in-memory queue and write to CSV.  Called from writer thread."""
        if not self._movdata_csv or not self._movdata_queue:
            return
        batch = []
        try:
            while True:
                batch.append(self._movdata_queue.popleft())
        except IndexError:
            pass
        if batch:
            try:
                self._movdata_csv.writerows(batch)
                self._movdata_file.flush()
            except Exception as exc:
                log.debug(f"[MovData] Write error: {exc}")

    def _movdata_close_session(self) -> None:
        """Stop writer thread, flush remaining rows, close file."""
        if not self._movdata_open_done:
            return
        try:
            self._movdata_stop.set()
            if self._movdata_thread and self._movdata_thread.is_alive():
                self._movdata_thread.join(timeout=1.0)
            self._movdata_flush()
            if self._movdata_file:
                self._movdata_file.close()
        except Exception as exc:
            log.debug(f"[MovData] Close error: {exc}")
        finally:
            self._movdata_file = None
            self._movdata_csv = None
            self._movdata_open_done = False
            self._movdata_stop.clear()
            self._movdata_thread = None
            log.info("[MovData] Session CSV closed")

    def get_fleeing_entities(self) -> List[dict]:
        """Return entities whose movement classifies them as fleeing (v4.65.0).

        Uses _entity_pos_history deques (16 samples at ~120 Hz) to compute velocity.
        An entity is included when:
          - It is a GuardSeed address (first 3 entities near truck in seeding window), OR
          - Its speed >= GUARD_FLEE_MIN_SPEED AND it has survived >= GUARD_MIN_SURVIVE_SECS.

        Only returns entities within _GUARD_CHASE_RADIUS_SQ (12 000 u) of the truck.
        Throttled [FleeTrack] log entry per entity (once per 2 s).
        """
        result: List[dict] = []
        if not self._carjack_truck_pos:
            return result
        tx, ty = self._carjack_truck_pos
        now = time.monotonic()

        for addr, history in list(self._entity_pos_history.items()):
            if len(history) < 2:
                continue
            oldest_t, oldest_x, oldest_y = history[0]
            newest_t, newest_x, newest_y = history[-1]
            dt = newest_t - oldest_t
            if dt < 0.01:
                continue
            vx = newest_x - oldest_x
            vy = newest_y - oldest_y
            speed = (vx * vx + vy * vy) ** 0.5 / dt
            # survived = wall-clock time since the entity first appeared in the TMap
            # (NOT the history deque window, which is only ~133ms at 120 Hz).
            survived = newest_t - self._entity_first_seen_t.get(addr, newest_t)

            is_seed = addr in self._guard_seed_addrs
            include = (is_seed or
                       (speed >= GUARD_FLEE_MIN_SPEED and survived >= GUARD_MIN_SURVIVE_SECS))
            if not include:
                continue

            dist_truck = ((newest_x - tx) ** 2 + (newest_y - ty) ** 2) ** 0.5
            if dist_truck > 12000.0:
                continue

            abp = self._abp_cache.get(addr, "")
            result.append({
                "x": newest_x,
                "y": newest_y,
                "addr": addr,
                "abp": abp,
                "speed": speed,
                "dist_truck": dist_truck,
                "is_seed": is_seed,
            })

            last_log_t = self._flee_track_last_log.get(addr, 0.0)
            if now - last_log_t >= 2.0:
                self._flee_track_last_log[addr] = now
                seed_tag = "[SEED]" if is_seed else ""
                log.debug(
                    f"[FleeTrack]{seed_tag} 0x{addr:X} "
                    f"pos=({newest_x:.0f},{newest_y:.0f}) "
                    f"speed={speed:.0f}u/s dist_truck={dist_truck:.0f} abp='{abp}'"
                )
        return result

    def get_carjack_guard_positions(self) -> List[dict]:
        """Return Carjack guard positions for bot navigation (v4.65.0).

        Uses flee-detection (get_fleeing_entities) during active Carjack.
        Falls back to truck position so the bot chase loop stays near the truck.
        """
        if not self._carjack_truck_pos:
            return []
        if time.monotonic() > self._carjack_active_until + 2.0:
            return []
        fleeing = self.get_fleeing_entities()
        if fleeing:
            return [{"x": e["x"], "y": e["y"], "addr": e["addr"],
                     "abp": e["abp"], "dist_truck": e["dist_truck"]}
                    for e in fleeing]
        tx, ty = self._carjack_truck_pos
        log.debug(f"[GUARD-POS] No flee-detected guards — truck fallback ({tx:.0f},{ty:.0f})")
        return [{"x": tx, "y": ty, "addr": 0, "abp": "truck_fallback", "dist_truck": 0.0}]

    def get_carjack_guard_debug_snapshot(self) -> List[dict]:
        """Return guard positions for overlay diagnostics (v4.65.0)."""
        return self.get_fleeing_entities()

    def get_carjack_truck_position(self) -> Optional[Tuple[float, float]]:
        """Return last known Carjack truck position, if available."""
        if not self._carjack_truck_pos:
            return None
        tx, ty = self._carjack_truck_pos
        return (float(tx), float(ty))

    def _read_truck_guard_roster(self, truck_addr: int, fnamepool: int) -> List[dict]:
        """DIAGNOSTIC ONLY — fires one-shot byte dump from truck ueComponents.

        ⚠️ The TArray guard-roster hypothesis (S11Comp+0x128) is CONFIRMED DEAD
        as of v4.41.5 (live log bot_20260227_182858.log): data_ptr is always 0x0
        even with 6 guards alive.  The 0x0C (12) at +0x130 is an unrelated field.

        This method now only fires the one-shot [TRAP-PROBE] byte dump for future
        investigation and returns [] unconditionally.  guard positions are provided
        by get_carjack_guard_positions() via the _abp_last_pos proximity approach.

        Retained for Probe Events button (address_manager_tab._on_probe_events)
        and for the one-shot component byte dump.
        """
        TMAP_OFFSET   = 0x288
        STRIDE        = 24
        result: List[dict] = []   # always returns []
        try:
            # ── Step 1: locate both truck components ──────────────────────────────
            data_ptr = self._read_ptr(truck_addr + TMAP_OFFSET)
            num_raw  = self._memory.read_bytes(truck_addr + TMAP_OFFSET + 8, 4)
            num      = struct.unpack_from("<i", num_raw, 0)[0] if num_raw else 0
            if not (data_ptr and data_ptr > 0x10000 and 1 <= num <= 16):
                return result
            raw = self._memory.read_bytes(data_ptr, num * STRIDE)
            if not raw:
                return result

            s11_comp_ptr  = 0
            trap_comp_ptr = 0
            for i in range(num):
                off = i * STRIDE
                if off + 16 > len(raw):
                    break
                key_ptr  = struct.unpack_from("<Q", raw, off)[0]
                comp_ptr = struct.unpack_from("<Q", raw, off + 8)[0]
                if not (0x10000 < key_ptr  < 0x7FFFFFFFFFFF):
                    continue
                if not (0x10000 < comp_ptr < 0x7FFFFFFFFFFF):
                    continue
                key_name = self._memory.read_uobject_name(fnamepool, key_ptr)
                if "TrapS" in key_name:
                    s11_comp_ptr = comp_ptr
                elif "EMapCustomTrap" in key_name and "TrapS" not in key_name:
                    trap_comp_ptr = comp_ptr

            # ── Step 2: one-shot byte dump (diagnostic only) ──────────────────────
            if not self._truck_probe_done:
                self._truck_probe_done = True
                if s11_comp_ptr:
                    pb = self._memory.read_bytes(s11_comp_ptr + 0x118, 0x40)
                    if pb:
                        log.info(
                            f"[TRAP-PROBE] S11Comp=0x{s11_comp_ptr:X} "
                            f"bytes@+0x118 (64B): {pb.hex()}"
                        )
                if trap_comp_ptr:
                    pb = self._memory.read_bytes(trap_comp_ptr + 0x210, 0x50)
                    if pb:
                        log.info(
                            f"[TRAP-PROBE] TrapComp=0x{trap_comp_ptr:X} "
                            f"bytes@+0x210 (80B): {pb.hex()}"
                        )
                if not s11_comp_ptr:
                    log.debug(f"[TRAP-PROBE] No TrapS component on truck=0x{truck_addr:X}")

            # TArray reading removed — S11Comp+0x128 confirmed permanently null.
            # See v4.41.5 docstring for full investigation history.

        except Exception as exc:
            log.debug(f"[TRAP-PROBE] _read_truck_guard_roster exception: {exc}")
        return result

    def _read_custom_trap_info_silent(self, entity_addr: int, fnamepool: int) -> Optional[dict]:
        """Read kill-counter fields from EMapCustomTrapComponent on the truck entity.

        ⚠️ HISTORY: Originally searched for EQAInfoComponent — which is NEVER present
        on the truck entity.  The truck only carries EMapCustomTrapComponent (key
        "EMapCustomTrap") and EMapCustomTrapS11Component (key "EMapCustomTrapS10").
        EQAInfoComponent is confirmed absent; all prior calls returned None.

        Current behaviour (v4.41.0):
        - Finds EMapCustomTrapComponent (key "EMapCustomTrap") in truck's ueComponents TMap
        - SDK reflected properties end at ~+0x214; unreflected region is +0x214 to +0x260
        - Attempts to read WorkCount at CANDIDATE_WORK_COUNT_OFFSET within that region
        - Returns None until TRAP-PROBE bytes (logged by _read_truck_guard_roster) are
          analysed and the exact WorkCount offset is confirmed

        CANDIDATE_WORK_COUNT_OFFSET = 0x238 assumes the CustomTrapInfo sub-struct
        starts at +0x214 and WorkCount is at struct+0x24 (same SDK layout as inside
        EQAInfoComponent.CustomTrapInfo).  This must be verified against TRAP-PROBE logs.

        POST-UPDATE NOTE: If TRAP-PROBE logs show work_count is always -1, check the
        raw hex dump at TrapComp+0x210 for a small int32 that counts up to 51 during
        a Carjack run.  Update CANDIDATE_WORK_COUNT_OFFSET once confirmed.
        """
        TMAP_OFFSET = 0x288
        STRIDE      = 24
        # Candidate offset within EMapCustomTrapComponent for WorkCount.
        # +0x214 = likely start of unreflected game-state region.
        # +0x24 = WorkCount offset within CustomTrapInfo struct (SDK-confirmed for EQAInfoComponent).
        # Total candidate = 0x214 + 0x24 = 0x238.  UNCONFIRMED — needs TRAP-PROBE validation.
        CANDIDATE_WORK_COUNT_OFFSET     = 0x238
        CANDIDATE_MAX_WORK_COUNT_OFFSET = 0x23C
        CANDIDATE_HIT_COUNT_OFFSET      = 0x234
        try:
            data_ptr = self._read_ptr(entity_addr + TMAP_OFFSET)
            num_raw  = self._memory.read_bytes(entity_addr + TMAP_OFFSET + 8, 4)
            num      = struct.unpack_from("<i", num_raw, 0)[0] if num_raw else 0
            if not (data_ptr and data_ptr > 0x10000 and 1 <= num <= 16):
                return None
            raw = self._memory.read_bytes(data_ptr, num * STRIDE)
            if not raw:
                return None

            for i in range(num):
                off = i * STRIDE
                if off + 16 > len(raw):
                    break
                key_ptr  = struct.unpack_from("<Q", raw, off)[0]
                comp_ptr = struct.unpack_from("<Q", raw, off + 8)[0]
                if not (0x10000 < key_ptr  < 0x7FFFFFFFFFFF):
                    continue
                if not (0x10000 < comp_ptr < 0x7FFFFFFFFFFF):
                    continue
                key_name = self._memory.read_uobject_name(fnamepool, key_ptr)
                # Match base component: EMapCustomTrap or EMapCustomTrapComponent.
                # Exclude seasonal variants (TrapS) and fight-mechanic variants (2/3).
                if "EMapCustomTrap" not in key_name or "TrapS" in key_name:
                    continue
                if "2" in key_name or "3" in key_name:
                    continue

                # Read candidate WorkCount region (76 unreflected bytes, +0x214..+0x260)
                blob = self._memory.read_bytes(comp_ptr + 0x214, 0x4C)
                if not blob or len(blob) < 0x4C:
                    return None

                off_wc  = CANDIDATE_WORK_COUNT_OFFSET     - 0x214
                off_mwc = CANDIDATE_MAX_WORK_COUNT_OFFSET - 0x214
                off_hc  = CANDIDATE_HIT_COUNT_OFFSET      - 0x214
                work_count     = struct.unpack_from("<i", blob, off_wc)[0]
                max_work_count = struct.unpack_from("<i", blob, off_mwc)[0]
                hit_count      = struct.unpack_from("<i", blob, off_hc)[0]

                # Sanity-check: work_count should be 0–55, max_work_count 51+
                # If values are garbage (e.g. pointer-sized), offsets are wrong.
                if not (0 <= work_count <= 200) or not (0 <= max_work_count <= 200):
                    return None

                return {
                    "cur_status_index": -1,
                    "cur_status":       -1,
                    "trap_execute_state": -1,
                    "wait_time":        -1.0,
                    "hit_count":        hit_count,
                    "work_count":       work_count,
                    "max_work_count":   max_work_count,
                    "skill_index":      -1,
                    "trigger_index":    -1,
                    "player_enter":     -1,
                }
        except Exception:
            return None
        return None

    def count_nearby_monsters(self, x: float, y: float, radius: float = 2500.0) -> int:
        """Count valid monsters around a world position within a radius."""
        monsters = self.get_nearby_monsters(x, y, radius=radius, require_valid=True)
        return len(monsters)

    def get_nearby_interactive_items(self,
                                     x: float,
                                     y: float,
                                     radius: float = 3000.0,
                                     require_valid: bool = True) -> List[EventInfo]:
        """Return FightMgr.MapInteractiveItem entities near a world position.

        Used for Carjack post-completion strongbox pickup (`BaoXianXiang`) and
        future event handlers that need interactive-object scans.
        """
        if not self._fightmgr_ptr:
            self._fightmgr_ptr = self._find_fightmgr()
        if not self._fightmgr_ptr:
            return []

        items = self._read_tmap_events(
            self._fightmgr_ptr + FIGHTMGR_MAP_INTERACTIVE_OFFSET,
            silent=True,
            read_class_names=True,
        )
        if not items:
            return []

        r2 = radius * radius
        nearby: List[EventInfo] = []
        for it in items:
            if require_valid and it.bvalid == 0:
                continue
            dx = it.position[0] - x
            dy = it.position[1] - y
            if dx * dx + dy * dy <= r2:
                nearby.append(it)
        return nearby

    def get_nearby_monsters(self, x: float, y: float, radius: float = 2500.0,
                            require_valid: bool = True) -> List[EventInfo]:
        """Return monsters around a world position within a radius."""
        monsters = self.get_monster_entities()
        if not monsters:
            return []
        r2 = radius * radius
        nearby: List[EventInfo] = []
        for m in monsters:
            if require_valid and m.bvalid == 0:
                continue
            dx = m.position[0] - x
            dy = m.position[1] - y
            if (dx * dx + dy * dy) <= r2:
                nearby.append(m)
        return nearby

    def scan_boss_room(self) -> Optional[tuple]:
        """Scan GObjects for a MapBossRoom actor and return (x, y) world position.

        MapBossRoom is a static AActor placed in the level at map load time that
        marks the boss arena boundary.  The boss monster itself is NOT spawned
        until the player physically enters this area (confirmed live: 'Monsters
        Left: 0' → walk into arena → 'Monsters Left: 1').

        Because MapBossRoom is static (always present), this scan works from the
        moment the map loads — unlike scanning for the boss entity itself (which
        doesn't exist until arena entry).

        Position is read via the standard UE4 actor chain:
          actor → +0x130 (RootComponent) → +0x124 (RelativeLocation: X, Y, Z)

        Returns (x, y) world-space coordinates, or None if not found.

        POST-UPDATE NOTE: If this stops working after a game patch, search the
        Objects Dump for 'class MapBossRoom' to verify the class still exists.
        The GObjects scan uses class-name matching so no offset update is needed
        as long as the class name stays the same.
        """
        gobjects = self._gobjects_addr
        fnamepool = self._fnamepool_addr
        if not gobjects or not fnamepool:
            return None

        actors = self._memory.find_gobjects_by_class_name(gobjects, fnamepool, "MapBossRoom")
        if not actors:
            self._log_debug("[BossRoom] No MapBossRoom actor found in GObjects")
            return None

        # There should be exactly one MapBossRoom per map.  Take the first.
        actor_ptr = actors[0][0]
        root_comp = self._read_ptr(actor_ptr + UE4_OFFSETS["RootComponent"])
        if not root_comp:
            self._log_debug(f"[BossRoom] MapBossRoom at 0x{actor_ptr:X}: RootComponent null")
            return None

        pos_data = self._memory.read_bytes(root_comp + UE4_OFFSETS["RelativeLocation"], 12)
        if not pos_data or len(pos_data) < 12:
            return None

        x, y, z = struct.unpack_from("<fff", pos_data)
        if abs(x) < 1.0 and abs(y) < 1.0:
            # Actor exists but position is at world origin — not yet fully placed.
            self._log_debug("[BossRoom] MapBossRoom position is (0,0) — not yet placed")
            return None

        self._log(f"[BossRoom] MapBossRoom detected at ({x:.0f}, {y:.0f}, {z:.0f})")
        return (x, y)

    # ── MinimapSaveObject ───────────────────────────────────────────────────────

    def find_minimap_save_object(self) -> int:
        """Find the live MinimapSaveObject singleton in GObjects.

        MinimapSaveObject stores a persistent per-map record of all world
        positions visited by the player (MinimapSaveObject.Records.Pos).
        It lives at /Engine/Transient and is confirmed present in-map (Feb 25 2026
        in-map dump of YJ_XieDuYuZuo200: index 0x262D1 @ 0x000002F1D95DA600).

        Because the 12 map layouts are predefined and never change, the visited
        positions only need to be collected once and then cached permanently in
        wall_data.json — no re-scan is ever needed unless the cache is deleted.

        Returns the UObject pointer, or 0 if not found.

        POINTER CACHE: after the first successful GObjects scan (~0.5s), the
        returned pointer is cached.  Subsequent calls validate the cache with a
        single memory read (~1µs) and return immediately, making fast polling
        (e.g. every 500ms) free of GObjects overhead.  The cache auto-invalidates
        if the pointer is no longer readable (map unloaded / game restarted).

        POST-UPDATE NOTE: The class name is 'MinimapSaveObject' (confirmed in SDK
        dump).  If this scan returns nothing after a patch, verify the class name
        in the new Objects Dump by searching for 'MinimapSaveObject'.
        """
        # ── Fast path: validate cached pointer ──────────────────────────────
        if self._minimap_save_obj_ptr:
            test = self._memory.read_value(
                self._minimap_save_obj_ptr + 0x10, "ulong"
            )
            if test and 0x10000 < test < 0x7FFFFFFFFFFF:
                return self._minimap_save_obj_ptr
            # Cache invalid — object was unloaded; fall through to full scan
            log.debug("[MinimapScan] Cached MinimapSaveObject ptr invalid — re-scanning GObjects")
            self._minimap_save_obj_ptr = 0

        # ── Slow path: full GObjects scan ────────────────────────────────────
        gobjects  = self._gobjects_addr
        fnamepool = self._fnamepool_addr
        if not gobjects or not fnamepool:
            log.debug("[MinimapScan] GObjects/FNamePool not resolved — cannot find MinimapSaveObject")
            return 0

        candidates = self._memory.find_gobjects_by_class_name(
            gobjects, fnamepool, MINIMAP_SAVE_OBJECT_CLASS
        )

        if not candidates:
            log.warning("[MinimapScan] No MinimapSaveObject found in GObjects")
            return 0

        log.debug(f"[MinimapScan] Found {len(candidates)} MinimapSaveObject candidate(s)")

        # Filter for the live transient instance (not the CDO Default__MinimapSaveObject).
        # The CDO's outer name is the /Script/UE_game package; the live instance's outer
        # points to the transient package (/Engine/Transient).
        for obj_ptr, inst_name in candidates:
            if "Default__" in inst_name:
                log.debug(f"[MinimapScan]   Skipping CDO: '{inst_name}' @ 0x{obj_ptr:X}")
                continue
            outer_ptr = self._read_ptr(obj_ptr + UE4_UOBJECT_OUTER_OFFSET)
            outer_name = ""
            if outer_ptr and fnamepool:
                outer_name = self._memory.read_uobject_name(fnamepool, outer_ptr)
            log.debug(f"[MinimapScan]   Candidate '{inst_name}' @ 0x{obj_ptr:X}, outer='{outer_name}'")
            if "transient" in outer_name.lower():
                log.info(f"[MinimapScan] MinimapSaveObject live instance at 0x{obj_ptr:X} (outer: {outer_name})")
                self._minimap_save_obj_ptr = obj_ptr
                return obj_ptr

        # Fallback: take first non-CDO result if transient filter found nothing
        for obj_ptr, inst_name in candidates:
            if "Default__" not in inst_name:
                log.info(f"[MinimapScan] MinimapSaveObject fallback @ 0x{obj_ptr:X} ('{inst_name}')")
                self._minimap_save_obj_ptr = obj_ptr
                return obj_ptr

        log.warning("[MinimapScan] Only CDO found — MinimapSaveObject live instance not in GObjects yet")
        return 0

    def read_minimap_visited_positions(self, raw_zone_name: str) -> List[tuple]:
        """Read all visited world positions for a map from MinimapSaveObject.Records.

        MinimapSaveObject.Records is TMap<FString, MinMapRecord>.
        MinMapRecord.Pos is TArray<FVector> — every world position (X, Y, Z)
        the player has ever stood on in that map.

        This is the primary source for the walkable-area A* grid.  Since the
        12 TLI maps have predefined, never-changing layouts, this data only needs
        collecting once per map; subsequent bot runs load from wall_data.json.

        Parameters
        ----------
        raw_zone_name : str
            Internal map FName string as returned by read_zone_name(), e.g.
            'YJ_XieDuYuZuo200'.

        KEY MATCHING STRATEGY (important — TMap keys are NOT zone FNames):
            Confirmed Feb 25 2026: the TMap key is a numeric config ID like
            '5311_0', NOT the zone FName ('SD_GeBuLinYingDi').  Three match
            strategies are tried in order:
              1. FName exact/suffix match (kept for robustness; unlikely to hit)
              2. Cached key→zone mapping from MINIMAP_KEY_MAP_FILE
              3. Auto-detect: if only 1 valid entry and no match, use it and
                 auto-learn the mapping for future scans

        Returns
        -------
        list of (x, y) float tuples — one per visited FVector position.
        Empty list if MinimapSaveObject is not found, map has no record yet,
        or memory reads fail.

        TMap element layout (stride 0xD8, confirmed Feb 25 2026 SDK dump):
          MinMapRecord Size:0x0C0 per dump → stride = FString(0x10)+MinMapRecord(0xC0)+hash(0x08) = 0xD8
          +0x00  FString key data ptr  (ulong)
          +0x08  FString ArrayNum      (int32, includes null terminator)
          +0x0C  FString ArrayMax      (int32)
          +0x10  MinMapRecord.Timestamp (int64)
          +0x18  MinMapRecord.Pos data ptr   (ulong)
          +0x20  MinMapRecord.Pos ArrayNum   (int32)
          +0x24  MinMapRecord.Pos ArrayMax   (int32)
          +0x28  MinMapRecord.IconDataArray ptr (ulong)
          ... (remaining MinMapRecord fields up to +0xD0)
          +0xD0  HashNextId            (int32)
          +0xD4  HashIndex             (int32)

        POST-UPDATE NOTE: Offsets are SDK-dump-verified.  If reads return empty
        after a patch, verify MINIMAP_RECORDS_OFFSET (0x028) against new dump
        by searching 'MinimapSaveObject:Records'.  Verify TMAP_FSTRING_ELEM_STRIDE
        (0xD8) by checking FString(16) + MinMapRecord(192) + hash(8) = 216 = 0xD8.
        """
        if not raw_zone_name:
            log.debug("[MinimapScan] No raw_zone_name provided — cannot look up Records entry")
            return []

        obj_ptr = self.find_minimap_save_object()
        if not obj_ptr:
            return []

        tmap_addr = obj_ptr + MINIMAP_RECORDS_OFFSET
        data_ptr  = self._memory.read_value(tmap_addr + 0x00, "ulong")
        array_num = self._memory.read_value(tmap_addr + 0x08, "int")

        log.debug(
            f"[MinimapScan] Records TMap @ 0x{tmap_addr:X}: "
            f"data_ptr=0x{data_ptr or 0:X}, array_num={array_num} "
            f"(stride=0x{TMAP_FSTRING_ELEM_STRIDE:X} bytes/entry)"
        )

        if not data_ptr or not (0x10000 < data_ptr < 0x7FFFFFFFFFFF):
            log.warning(f"[MinimapScan] Records TMap data_ptr invalid (0x{data_ptr or 0:X})")
            return []
        if array_num is None or array_num <= 0 or array_num > 100:
            log.warning(f"[MinimapScan] Records TMap array_num suspicious: {array_num}")
            return []

        # Load cached numeric-key → zone-FName mapping
        key_map = self._load_minimap_key_map()
        log.debug(
            f"[MinimapScan] Looking for zone '{raw_zone_name}' in {array_num} TMap entries "
            f"(key_map has {len(key_map)} cached entries: {list(key_map.items())})"
        )

        # Read entire element array in one shot for efficiency
        total_bytes = array_num * TMAP_FSTRING_ELEM_STRIDE
        raw = self._memory.read_bytes(data_ptr, total_bytes)
        if not raw or len(raw) < total_bytes:
            log.warning(
                f"[MinimapScan] Failed to read TMap element array "
                f"({len(raw) if raw else 0}/{total_bytes} bytes)"
            )
            return []

        raw_zone_lower = raw_zone_name.lower()

        # ── Pass 1: collect all valid entries with full diagnostic logging ──────
        valid_entries = []   # list of (index, key_str, pos_data_ptr, pos_count, base_offset)
        for i in range(array_num):
            base = i * TMAP_FSTRING_ELEM_STRIDE

            key_data_ptr = struct.unpack_from("<Q", raw, base + MINIMAP_FSTRING_KEY_PTR)[0]
            key_len      = struct.unpack_from("<i", raw, base + MINIMAP_FSTRING_KEY_LEN)[0]

            if not (0x10000 < key_data_ptr < 0x7FFFFFFFFFFF) or key_len <= 0 or key_len > 256:
                log.debug(
                    f"[MinimapScan]   entry[{i}]: null/freed slot "
                    f"(key_ptr=0x{key_data_ptr:X}, key_len={key_len}) — skipping"
                )
                continue

            # FString on Windows = UTF-16LE; key_len includes null terminator
            key_bytes = self._memory.read_bytes(key_data_ptr, key_len * 2)
            if not key_bytes or len(key_bytes) < key_len * 2:
                log.debug(f"[MinimapScan]   entry[{i}]: failed to read key string ({key_len} chars)")
                continue

            try:
                key_str = key_bytes[:(key_len - 1) * 2].decode("utf-16-le", errors="ignore")
            except Exception:
                key_str = ""

            pos_data_ptr = struct.unpack_from("<Q", raw, base + MINIMAP_RECORD_POS_PTR)[0]
            pos_count    = struct.unpack_from("<i", raw, base + MINIMAP_RECORD_POS_NUM)[0]
            known_zone   = key_map.get(key_str, "<unknown>")

            # Raw hex dump of the element — critical for offset verification on first run
            elem_hex = raw[base:base + 0x30].hex(" ")
            log.debug(
                f"[MinimapScan]   entry[{i}]: raw bytes [0x00..0x2F] = {elem_hex}"
            )
            log.debug(
                f"[MinimapScan]   entry[{i}]: key='{key_str}' "
                f"| pos_count={pos_count} "
                f"| pos_ptr=0x{pos_data_ptr:X} "
                f"| cached_zone='{known_zone}'"
            )
            valid_entries.append((i, key_str, pos_data_ptr, pos_count, base))

        if not valid_entries:
            log.warning(
                f"[MinimapScan] No valid entries in TMap (all slots null/freed) — "
                f"MinimapSaveObject may be empty (never walked this session?)"
            )
            log.flush()
            return []

        # ── Pass 2: find the matching entry ─────────────────────────────────────
        match_entry = None   # (index, key_str, pos_data_ptr, pos_count, base)
        match_reason = ""

        for entry in valid_entries:
            i, key_str, pos_data_ptr, pos_count, base = entry
            # Strategy 1: FName exact / suffix match (format may change after patch)
            if key_str == raw_zone_name or key_str.lower() == raw_zone_lower:
                match_entry = entry
                match_reason = "exact FName match"
                break
            if raw_zone_lower in key_str.lower():
                match_entry = entry
                match_reason = f"suffix FName match ('{key_str}' contains '{raw_zone_name}')"
                break
            # Strategy 2: cached key→zone mapping
            if key_map.get(key_str) == raw_zone_name:
                match_entry = entry
                match_reason = f"cached key map ('{key_str}' → '{raw_zone_name}')"
                break

        # Strategy 3: auto-detect — single entry, no existing conflict → must be current map.
        # Guard: skip if the key is already mapped to a DIFFERENT zone — that indicates a
        # session-reused/dynamic key (seen in practice: key '110_0' re-used across zones).
        if match_entry is None and len(valid_entries) == 1:
            i, key_str = valid_entries[0][0], valid_entries[0][1]
            existing = key_map.get(key_str)
            if existing is not None and existing != raw_zone_name:
                log.warning(
                    f"[MinimapScan] Auto-learn skipped: key '{key_str}' already mapped to "
                    f"'{existing}' but current zone is '{raw_zone_name}' — "
                    f"key appears to be session-dynamic; not overwriting."
                )
            else:
                match_entry = valid_entries[0]
                match_reason = (
                    f"auto-detect (single entry '{key_str}', no cached mapping) — "
                    f"learning '{key_str}' → '{raw_zone_name}'"
                )
                # Persist the newly learned mapping
                key_map[key_str] = raw_zone_name
                self._save_minimap_key_map(key_map)
                log.info(
                    f"[MinimapScan] Auto-learned key mapping: '{key_str}' → '{raw_zone_name}' "
                    f"(saved to {MINIMAP_KEY_MAP_FILE})"
                )

        if match_entry is None:
            log.warning(
                f"[MinimapScan] No entry matched '{raw_zone_name}' in {len(valid_entries)} "
                f"valid entries — cannot determine which entry is the current map. "
                f"Available keys: {[e[1] for e in valid_entries]}. "
                f"Run bot in a single-map session so auto-learning can identify the key."
            )
            log.flush()
            return []

        i, key_str, pos_data_ptr, pos_count, base = match_entry
        log.debug(
            f"[MinimapScan] Matched entry[{i}] key='{key_str}' via {match_reason} — "
            f"Pos TArray @ 0x{pos_data_ptr:X}, declared count={pos_count}"
        )

        # ── Read position data ───────────────────────────────────────────────────
        # Check count first — a zero count means no positions regardless of ptr validity.
        if pos_count <= 0 or pos_count > 500_000:
            if pos_count == 0:
                log.warning(
                    f"[MinimapScan] Pos.ArrayNum = 0 for key='{key_str}' — the game has not "
                    f"yet committed any visited positions for this map in the current session. "
                    f"Walk around the map to reveal more minimap area, then retry the scan. "
                    f"(pos_ptr=0x{pos_data_ptr:X})"
                )
            else:
                log.warning(
                    f"[MinimapScan] Pos.ArrayNum suspicious: {pos_count} for key='{key_str}' "
                    f"— expected 0 < count < 500,000. Possible offset mismatch."
                )
            log.flush()
            return []
        if not (0x10000 < pos_data_ptr < 0x7FFFFFFFFFFF):
            log.warning(
                f"[MinimapScan] Pos data_ptr=0x{pos_data_ptr:X} invalid (but ArrayNum={pos_count}) "
                f"— possible TArray offset mismatch. Check DEBUG hex dump lines above for "
                f"offset verification. Expected: ptr at element+0x{MINIMAP_RECORD_POS_PTR:X}."
            )
            log.flush()
            return []

        pos_bytes_total = pos_count * FVECTOR_SIZE
        pos_raw = self._memory.read_bytes(pos_data_ptr, pos_bytes_total)
        if not pos_raw or len(pos_raw) < pos_bytes_total:
            log.warning(
                f"[MinimapScan] Failed to read Pos array "
                f"({len(pos_raw) if pos_raw else 0}/{pos_bytes_total} bytes)"
            )
            log.flush()
            return []

        positions = []
        for j in range(pos_count):
            x, y, _ = struct.unpack_from("<fff", pos_raw, j * FVECTOR_SIZE)
            # Discard (0,0,0) placeholder entries the game may write
            if abs(x) > 1.0 or abs(y) > 1.0:
                positions.append((x, y))

        zero_filtered = pos_count - len(positions)
        if positions:
            xs = [p[0] for p in positions]
            ys = [p[1] for p in positions]
            x_range = f"{min(xs):.0f}..{max(xs):.0f}"
            y_range = f"{min(ys):.0f}..{max(ys):.0f}"
            coverage = f"X:[{x_range}]  Y:[{y_range}]  span=({max(xs)-min(xs):.0f}×{max(ys)-min(ys):.0f}) world units"
        else:
            coverage = "no valid positions"

        count_changed = len(positions) != self._last_minimap_pos_count
        self._last_minimap_pos_count = len(positions)
        msg = (
            f"[MinimapScan] ✓ {len(positions)} valid visited positions "
            f"({zero_filtered} zero-origin filtered) for '{raw_zone_name}' | {coverage}"
        )
        if count_changed:
            log.info(msg)
        else:
            log.debug(msg)
        log.flush()
        return positions

    # ── MinimapSaveObject key-map persistence ────────────────────────────────────

    @staticmethod
    def _load_minimap_key_map() -> dict:
        """Load cached numeric-TMap-key → zone-FName mapping from JSON.

        Returns empty dict if file does not exist or cannot be parsed.
        Keys look like '5311_0'; values look like 'SD_GeBuLinYingDi'.
        """
        os.makedirs(os.path.dirname(MINIMAP_KEY_MAP_FILE), exist_ok=True)
        if not os.path.exists(MINIMAP_KEY_MAP_FILE):
            return {}
        try:
            with open(MINIMAP_KEY_MAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def _save_minimap_key_map(key_map: dict) -> None:
        """Persist numeric-TMap-key → zone-FName mapping to JSON."""
        os.makedirs(os.path.dirname(MINIMAP_KEY_MAP_FILE), exist_ok=True)
        try:
            with open(MINIMAP_KEY_MAP_FILE, "w", encoding="utf-8") as f:
                json.dump(key_map, f, indent=2)
        except Exception as e:
            log.warning(f"[MinimapScan] Failed to save key map: {e}")

    # ── FightMgr ────────────────────────────────────────────────────────────────

    def _find_fightmgr(self) -> int:
        """Find the live FightMgr singleton in GObjects. Returns ptr or 0.

        Two objects named 'FightMgr' exist: the UFightMgr class definition and
        the live instance at /Engine/Transient.  We must return the instance,
        not the class definition.  The instance has Outer pointing to a
        transient package; the class def has a non-transient outer.
        """
        results = self.find_object_by_name("FightMgr")
        if not results:
            return 0

        fnamepool = self._fnamepool_addr

        # Prefer the object whose Outer is the /Engine/Transient package
        for obj_ptr, _name in results:
            outer_ptr = self._memory.read_value(obj_ptr + 0x20, "ulong")
            if not outer_ptr or outer_ptr < 0x10000 or outer_ptr > 0x7FFFFFFFFFFF:
                continue
            if fnamepool:
                outer_name = self._memory.read_uobject_name(fnamepool, outer_ptr)
                if "transient" in outer_name.lower():
                    self._log(f"[Scanner] FightMgr live instance at 0x{obj_ptr:X} (outer: {outer_name})")
                    return obj_ptr
            else:
                # No FNamePool yet — fall back to first result
                break

        # Fallback: return first result and log a warning
        ptr = results[0][0]
        self._log(f"[Scanner] FightMgr fallback (no transient match) at 0x{ptr:X}")
        return ptr

    def get_fightmgr_ptr(self) -> int:
        """Return the live FightMgr singleton pointer, finding it via GObjects if needed.

        Public wrapper used by PortalDetector so both share the same cached ptr
        and avoid duplicating the transient-outer matching logic.
        """
        if not self._fightmgr_ptr:
            self._fightmgr_ptr = self._find_fightmgr()
        return self._fightmgr_ptr

    def _read_tmap_events(self, tmap_addr: int,
                          logic_id_filter: Optional[set] = None,
                          silent: bool = False,
                          read_class_names: bool = False) -> List[EventInfo]:
        """Read a FightMgr TMap<int32, EEntity*> and return EventInfo list.

        TMap element layout (stride=24, confirmed from portal_detector.py):
          +0x00 = int32  logic_id (entity type ID)
          +0x04 = int32  padding
          +0x08 = ptr64  EEntity* (EGameplay* or EMapCustomTrap*)
          +0x10 = int32  HashNext
          +0x14 = int32  HashIndex

        read_class_names: when True (and FNamePool is resolved), reads each
          entity's class FName (entity+0x10 → class_ptr → FName) and stores it
          in EventInfo.sub_object_class.  Also stores the entity's own object
          FName in EventInfo.sub_object_name.  Costs 2 extra memory reads per
          entity; callers that don't need it can leave this False.
        """
        TMAP_ELEMENT_STRIDE = 24

        data_ptr = self._memory.read_value(tmap_addr, "ulong")
        array_num = self._memory.read_value(tmap_addr + 0x08, "int")

        if not silent:
            log.debug(f"[Scanner] TMap at 0x{tmap_addr:X}: data_ptr=0x{data_ptr or 0:X} array_num={array_num}")

        if not data_ptr or data_ptr < 0x10000 or data_ptr > 0x7FFFFFFFFFFF:
            return []
        # cap raised from 512 → 4096 (v4.41.6):
        # A 370-monster map uses TMap capacity=512 (next power-of-2 for ~370/0.75≈494).
        # Larger maps (500+ monsters) would have capacity=1024 and the old cap returned []
        # silently. 4096 × 24 bytes = 98 KB read — trivially fast.
        if array_num is None or array_num <= 0 or array_num > 4096:
            return []

        raw = self._memory.read_bytes(data_ptr, array_num * TMAP_ELEMENT_STRIDE)
        if not raw:
            return []

        events = []
        seen_addrs: set = set()       # deduplicate: TMap may store multiple entries per entity
        for i in range(array_num):
            off = i * TMAP_ELEMENT_STRIDE
            if off + TMAP_ELEMENT_STRIDE > len(raw):
                break

            logic_id = struct.unpack_from("<i", raw, off)[0]
            eg_ptr   = struct.unpack_from("<Q", raw, off + 8)[0]
            # HashIndex at TMap element +0x14: -1 (INDEX_NONE) = tombstone / free slot.
            # Skip tombstones so they don't produce phantom entities or double counts.
            hash_index = struct.unpack_from("<i", raw, off + 20)[0]  # +0x14
            if hash_index == -1:
                continue

            if not (0x10000 < eg_ptr < 0x7FFFFFFFFFFF):
                continue
            if eg_ptr in seen_addrs:
                continue           # deduplicate same entity address from multiple TMap slots
            if logic_id_filter is not None and logic_id not in logic_id_filter:
                continue

            # Read world position via RootComponent → RelativeLocation
            root_comp = self._read_ptr(eg_ptr + UE4_OFFSETS["RootComponent"])
            if not root_comp:
                continue
            loc_offset = UE4_OFFSETS["RelativeLocation"]
            pos_data = self._memory.read_bytes(root_comp + loc_offset, 12)
            if not pos_data or len(pos_data) < 12:
                continue
            x, y, z = struct.unpack_from("<fff", pos_data)

            seen_addrs.add(eg_ptr)

            # NOTE: logic_id here is the runtime spawn_index (sequential instance ID),
            # NOT a static event type ID. Type is resolved after reading MapCustomTrap.
            event = EventInfo()
            event.address = eg_ptr
            event.cfg_id = logic_id          # stores spawn_index
            event.event_type = f"event_{logic_id:#x}"  # placeholder; reclassified in get_typed_events
            event.is_target_event = True     # all MapGamePlay entries are bot-actionable events
            event.position = (x, y, z)

            # ⚠️ wave_counter UNRELIABLE — display only, do not use in logic
            wave_data = self._memory.read_bytes(eg_ptr + 0x618, 4)
            if wave_data and len(wave_data) >= 4:
                event.wave_counter = struct.unpack_from("<i", wave_data, 0)[0]

            bvalid_data = self._memory.read_bytes(eg_ptr + 0x720, 1)
            if bvalid_data and len(bvalid_data) >= 1:
                event.bvalid = bvalid_data[0]

            if read_class_names:
                fnamepool = getattr(self, '_fnamepool_addr', 0)
                if fnamepool:
                    # Entity own FName (object instance name, e.g. "EMonster")
                    event.sub_object_name = self._memory.read_uobject_name(fnamepool, eg_ptr)
                    # Entity class FName (e.g. "EMonster", "EMapCustomTrap", …)
                    class_ptr = self._read_ptr(eg_ptr + 0x10)
                    if class_ptr:
                        event.sub_object_class = self._memory.read_uobject_name(fnamepool, class_ptr)

            events.append(event)
            if not silent:
                self._log_debug(f"[typed_event] spawn={logic_id:#x} pos=({x:.0f},{y:.0f}) addr=0x{eg_ptr:X}")

        return events

    def _read_event_info(self, egameplay_addr: int, ecfg_addr: int) -> Optional[EventInfo]:
        """Read event type and position from an EGameplay + ECfgComponent pair.

        Uses sub-object at EGameplay+0x4E0 for type identification since
        CfgInfo.ID is always zero at runtime. Also reads wave counter at
        +0x618 and spawn index at +0x714.
        """
        fnamepool = getattr(self, '_fnamepool_addr', 0)

        cfginfo_base = ecfg_addr + ECFGCOMPONENT_CFGINFO_OFFSET

        cfg_data = self._memory.read_bytes(cfginfo_base, 12)
        if not cfg_data or len(cfg_data) < 12:
            return None

        cfg_id = struct.unpack_from("<i", cfg_data, CFGINFO_ID_OFFSET)[0]
        cfg_type = struct.unpack_from("<i", cfg_data, CFGINFO_TYPE_OFFSET)[0]
        cfg_extend_id = struct.unpack_from("<i", cfg_data, CFGINFO_EXTENDID_OFFSET)[0]

        event = EventInfo()
        event.address = egameplay_addr
        event.ecfg_address = ecfg_addr
        event.cfg_id = cfg_id
        event.cfg_type = cfg_type
        event.cfg_extend_id = cfg_extend_id

        if cfg_id in EGAMEPLAY_EVENT_TYPES:
            event.event_type = EGAMEPLAY_EVENT_TYPES[cfg_id]
        else:
            event.event_type = f"Unknown_0x{cfg_id:X}"

        event.is_target_event = cfg_id in EGAMEPLAY_TARGET_IDS

        root_comp = self._read_ptr(egameplay_addr + UE4_OFFSETS["RootComponent"])
        if root_comp:
            loc_offset = UE4_OFFSETS["RelativeLocation"]
            pos_data = self._memory.read_bytes(root_comp + loc_offset, 12)
            if pos_data and len(pos_data) >= 12:
                x, y, z = struct.unpack_from("<fff", pos_data, 0)
                if self._is_plausible_coordinate(x) and self._is_plausible_coordinate(y):
                    event.position = (x, y, z)

        if fnamepool:
            sub_obj_ptr = self._read_ptr(egameplay_addr + 0x4E0)
            if sub_obj_ptr and sub_obj_ptr > 0x10000:
                event.sub_object_name = self._memory.read_uobject_name(fnamepool, sub_obj_ptr)
                sub_class_ptr = self._read_ptr(sub_obj_ptr + 0x10)
                if sub_class_ptr:
                    event.sub_object_class = self._memory.read_uobject_name(fnamepool, sub_class_ptr)

        # ⚠️ wave_counter UNRELIABLE — display only, do not use in logic
        wave_data = self._memory.read_bytes(egameplay_addr + 0x618, 4)
        if wave_data and len(wave_data) >= 4:
            event.wave_counter = struct.unpack_from("<i", wave_data, 0)[0]

        spawn_data = self._memory.read_bytes(egameplay_addr + 0x714, 4)
        if spawn_data and len(spawn_data) >= 4:
            event.spawn_index = struct.unpack_from("<i", spawn_data, 0)[0]

        return event

    def explore_egameplay(self, dump_bytes: int = 256) -> str:
        """Debug method: dump raw data from all EGameplay instances and their ECfgComponents.

        Dumps CfgInfo struct, surrounding memory, and position for each instance.
        Also shows ECfgComponent Outer chain and searches for Cfg-related objects.
        Used to discover unknown offsets and completion state fields at runtime.

        Returns formatted string of all findings.
        """
        fnamepool = getattr(self, '_fnamepool_addr', 0)
        gobjects = getattr(self, '_gobjects_addr', 0)

        if not fnamepool or not gobjects:
            return "ERROR: FNamePool or GObjects not resolved"

        egameplay_objects = self._memory.find_gobjects_by_class_name(gobjects, fnamepool, "EGameplay")
        if not egameplay_objects:
            return "No EGameplay instances found (searched by class name)"

        ecfg_objects = self._memory.find_gobjects_by_class_name(gobjects, fnamepool, "ECfgComponent")
        egameplay_addrs = {addr for addr, _ in egameplay_objects}

        ecfg_by_outer = {}
        ecfg_outer_info = {}
        for ecfg_addr, ecfg_name in ecfg_objects:
            outer_ptr = self._read_ptr(ecfg_addr + UE4_UOBJECT_OUTER_OFFSET)
            outer_name = ""
            outer_class = ""
            if outer_ptr:
                outer_name = self._memory.read_uobject_name(fnamepool, outer_ptr)
                class_ptr = self._read_ptr(outer_ptr + 0x10)
                if class_ptr:
                    outer_class = self._memory.read_uobject_name(fnamepool, class_ptr)
            ecfg_outer_info[ecfg_addr] = (outer_ptr, outer_name, outer_class)
            if outer_ptr and outer_ptr in egameplay_addrs:
                ecfg_by_outer[outer_ptr] = ecfg_addr

        lines = []
        lines.append(f"=== EGameplay Explorer ===")
        lines.append(f"Found {len(egameplay_objects)} EGameplay, {len(ecfg_objects)} ECfgComponent")
        lines.append(f"Matched pairs (by Outer): {len(ecfg_by_outer)}")
        lines.append("")

        if ecfg_objects:
            live_ecfg_matches = [(a, n) for a, n in ecfg_objects
                                 if ecfg_outer_info.get(a, (0, "", ""))[0] in egameplay_addrs
                                 and not ecfg_outer_info.get(a, (0, "", ""))[1].startswith("Default__")]
            cdo_ecfg = [(a, n) for a, n in ecfg_objects
                        if ecfg_outer_info.get(a, (0, "", ""))[1].startswith("Default__")]
            lines.append(f"--- ECfgComponent->EGameplay Matches (live only) ---")
            lines.append(f"  ({len(cdo_ecfg)} CDO ECfgComponents filtered out, {len(ecfg_objects) - len(cdo_ecfg) - len(live_ecfg_matches)} non-EGameplay)")
            for ecfg_addr, ecfg_name in live_ecfg_matches:
                outer_ptr, outer_name, outer_class = ecfg_outer_info.get(ecfg_addr, (0, "", ""))
                lines.append(f"  ECfg 0x{ecfg_addr:X} -> Outer 0x{outer_ptr:X} name='{outer_name}' class='{outer_class}'")
            lines.append("")

        live_egameplay = [(addr, name) for addr, name in egameplay_objects
                          if not name.startswith("Default__")]
        cdo_count = len(egameplay_objects) - len(live_egameplay)
        if cdo_count:
            lines.append(f"(Filtered out {cdo_count} CDO/Default__ objects)")
        lines.append(f"Live EGameplay instances: {len(live_egameplay)}")
        lines.append("")

        # Build a map of all GObjects whose Outer points to any live EGameplay.
        # This covers the ueComponents TMap (#1) without parsing TMap internals.
        live_eg_addrs = {addr for addr, _ in live_egameplay}
        all_children_by_eg: dict = {addr: [] for addr in live_eg_addrs}
        num_objects = self._memory.read_value(gobjects + 0x14, "int") or 0
        elements_per_chunk = 65536
        chunks_ptr = self._memory.read_value(gobjects, "ulong") or 0
        if chunks_ptr and num_objects > 0:
            for chunk_idx in range((num_objects + elements_per_chunk - 1) // elements_per_chunk):
                chunk_data_ptr = self._memory.read_value(chunks_ptr + chunk_idx * 8, "ulong")
                if not chunk_data_ptr:
                    continue
                chunk_count = min(elements_per_chunk, num_objects - chunk_idx * elements_per_chunk)
                for item_idx in range(chunk_count):
                    obj_ptr = self._memory.read_value(chunk_data_ptr + item_idx * 24, "ulong")
                    if not obj_ptr or obj_ptr < 0x10000:
                        continue
                    outer_ptr = self._read_ptr(obj_ptr + UE4_UOBJECT_OUTER_OFFSET)
                    if outer_ptr and outer_ptr in live_eg_addrs:
                        child_name = self._memory.read_uobject_name(fnamepool, obj_ptr)
                        if child_name and not child_name.startswith("Default__"):
                            cls_ptr = self._read_ptr(obj_ptr + 0x10)
                            child_class = self._memory.read_uobject_name(fnamepool, cls_ptr) if cls_ptr else ""
                            all_children_by_eg[outer_ptr].append((obj_ptr, child_name, child_class))

        for i, (eg_addr, eg_name) in enumerate(live_egameplay):
            lines.append(f"--- EGameplay #{i}: {eg_name} at 0x{eg_addr:X} ---")

            # UObject base fields: InternalIndex (+0x0C) and FName Number (+0x1C)
            internal_index_data = self._memory.read_bytes(eg_addr + 0x0C, 4)
            internal_index = struct.unpack_from("<I", internal_index_data, 0)[0] if internal_index_data and len(internal_index_data) >= 4 else -1
            fname_number_data = self._memory.read_bytes(eg_addr + 0x1C, 4)
            fname_number = struct.unpack_from("<I", fname_number_data, 0)[0] if fname_number_data and len(fname_number_data) >= 4 else -1
            lines.append(f"  InternalIndex@0x0C: {internal_index} | FName.Number@0x1C: {fname_number}")

            class_ptr = self._read_ptr(eg_addr + 0x10)
            if class_ptr:
                eg_class = self._memory.read_uobject_name(fnamepool, class_ptr)
                lines.append(f"  Class: '{eg_class}'")

            root_comp = self._read_ptr(eg_addr + UE4_OFFSETS["RootComponent"])
            if root_comp:
                loc_offset = UE4_OFFSETS["RelativeLocation"]
                pos_data = self._memory.read_bytes(root_comp + loc_offset, 12)
                if pos_data and len(pos_data) >= 12:
                    x, y, z = struct.unpack_from("<fff", pos_data, 0)
                    lines.append(f"  Position: ({x:.1f}, {y:.1f}, {z:.1f})")

            sub_obj_ptr = self._read_ptr(eg_addr + 0x4E0)
            if sub_obj_ptr and sub_obj_ptr > 0x10000:
                sub_name = self._memory.read_uobject_name(fnamepool, sub_obj_ptr)
                sub_class_ptr = self._read_ptr(sub_obj_ptr + 0x10)
                sub_class = ""
                if sub_class_ptr:
                    sub_class = self._memory.read_uobject_name(fnamepool, sub_class_ptr)
                sub_outer_ptr = self._read_ptr(sub_obj_ptr + 0x20)
                sub_outer = ""
                if sub_outer_ptr and sub_outer_ptr > 0x10000:
                    sub_outer = self._memory.read_uobject_name(fnamepool, sub_outer_ptr)
                # InternalIndex and FName.Number for the sub-object at 0x4E0
                sub_iidx_data = self._memory.read_bytes(sub_obj_ptr + 0x0C, 4)
                sub_iidx = struct.unpack_from("<I", sub_iidx_data, 0)[0] if sub_iidx_data and len(sub_iidx_data) >= 4 else -1
                sub_fnum_data = self._memory.read_bytes(sub_obj_ptr + 0x1C, 4)
                sub_fnum = struct.unpack_from("<I", sub_fnum_data, 0)[0] if sub_fnum_data and len(sub_fnum_data) >= 4 else -1
                lines.append(f"  SubObject@0x4E0: 0x{sub_obj_ptr:X} name='{sub_name}' class='{sub_class}' outer='{sub_outer}' iidx={sub_iidx} fnum={sub_fnum}")
            else:
                lines.append(f"  SubObject@0x4E0: NULL")

            wave_data = self._memory.read_bytes(eg_addr + 0x618, 4)
            wave_count = struct.unpack_from("<i", wave_data, 0)[0] if wave_data and len(wave_data) >= 4 else -1
            spawn_data = self._memory.read_bytes(eg_addr + 0x714, 4)
            spawn_idx = struct.unpack_from("<i", spawn_data, 0)[0] if spawn_data and len(spawn_data) >= 4 else -1
            lines.append(f"  WaveCounter@0x618: {wave_count} | SpawnIndex@0x714: {spawn_idx}")

            # ueComponents TMap at EGameplay+0x288 (EEntity::ueComponents).
            # Scan the element array for valid UObject pointers at multiple strides
            # (stride unknown — not relying on portal_detector as reference).
            # Goal: find component class names that might distinguish event types.
            uc_data_ptr = self._memory.read_value(eg_addr + 0x288, "ulong") or 0
            uc_num = self._memory.read_value(eg_addr + 0x290, "int") or 0
            lines.append(f"  ueComponents TMap@0x288: data_ptr=0x{uc_data_ptr:X} num={uc_num}")
            if uc_data_ptr and 0x10000 < uc_data_ptr < 0x7FFFFFFFFFFF and 0 < uc_num <= 32:
                uc_scan_size = uc_num * 48  # generous upper bound per element
                uc_raw = self._memory.read_bytes(uc_data_ptr, uc_scan_size)
                if uc_raw:
                    seen_ptrs: set[int] = set()
                    for byte_off in range(0, len(uc_raw) - 7, 8):
                        candidate = struct.unpack_from("<Q", uc_raw, byte_off)[0]
                        if candidate in seen_ptrs:
                            continue
                        if not (0x10000 < candidate < 0x7FFFFFFFFFFF):
                            continue
                        # heuristic: skip pointers into exe image (likely vtable/class ptrs)
                        cname = self._memory.read_uobject_name(fnamepool, candidate)
                        if not cname or cname.startswith("Default__"):
                            continue
                        ccls_ptr = self._read_ptr(candidate + 0x10)
                        cclass = self._memory.read_uobject_name(fnamepool, ccls_ptr) if ccls_ptr else ""
                        if cclass:  # only report objects with a valid class
                            seen_ptrs.add(candidate)
                            lines.append(f"    ueComp candidate@+0x{byte_off:03X}: 0x{candidate:X} "
                                         f"name='{cname}' class='{cclass}'")

            # All child GObjects with this EGameplay as Outer
            children = all_children_by_eg.get(eg_addr, [])
            lines.append(f"  All children (Outer=this, {len(children)} found):")
            for c_addr, c_name, c_class in children:
                c_iidx_data = self._memory.read_bytes(c_addr + 0x0C, 4)
                c_iidx = struct.unpack_from("<I", c_iidx_data, 0)[0] if c_iidx_data and len(c_iidx_data) >= 4 else -1
                c_fnum_data = self._memory.read_bytes(c_addr + 0x1C, 4)
                c_fnum = struct.unpack_from("<I", c_fnum_data, 0)[0] if c_fnum_data and len(c_fnum_data) >= 4 else -1
                lines.append(f"    0x{c_addr:X} name='{c_name}' class='{c_class}' iidx={c_iidx} fnum={c_fnum}")

            ecfg_addr = ecfg_by_outer.get(eg_addr)
            if ecfg_addr:
                self._dump_ecfg_component(lines, ecfg_addr, dump_bytes)
            else:
                lines.append(f"  No ECfgComponent matched via Outer")
                lines.append(f"  Scanning OwnedComponents array...")
                owned = self._scan_owned_components(eg_addr, fnamepool)
                if owned:
                    for comp_addr, comp_name, comp_class in owned:
                        lines.append(f"    Component: 0x{comp_addr:X} name='{comp_name}' class='{comp_class}'")
                        if "cfg" in comp_name.lower() or "cfg" in comp_class.lower():
                            lines.append(f"    ** Found Cfg component! Dumping...")
                            self._dump_ecfg_component(lines, comp_addr, dump_bytes)
                else:
                    lines.append(f"    No owned components found")

            eg_size = 0x728
            for dump_start in range(0x200, eg_size, 0x200):
                dump_len = min(0x200, eg_size - dump_start)
                eg_dump = self._memory.read_bytes(eg_addr + dump_start, dump_len)
                if eg_dump:
                    lines.append(f"  EGameplay memory (actor+0x{dump_start:X}, {len(eg_dump)} bytes):")
                    for offset in range(0, len(eg_dump), 16):
                        chunk = eg_dump[offset:offset + 16]
                        hex_str = " ".join(f"{b:02X}" for b in chunk)
                        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                        lines.append(f"    +0x{dump_start + offset:03X}: {hex_str:<48} {ascii_str}")

            lines.append("")

        lines.append("")
        lines.append("--- Quick Scan Summary ---")
        live_addrs = {addr for addr, name in live_egameplay}
        live_matches = {eg: ecfg for eg, ecfg in ecfg_by_outer.items() if eg in live_addrs}
        if live_matches:
            for eg_addr, ecfg_addr in live_matches.items():
                cfginfo_base = ecfg_addr + ECFGCOMPONENT_CFGINFO_OFFSET
                cfg_data = self._memory.read_bytes(cfginfo_base, 4)
                root_comp = self._read_ptr(eg_addr + UE4_OFFSETS["RootComponent"])
                pos_x, pos_y = 0.0, 0.0
                if root_comp:
                    pos_data = self._memory.read_bytes(root_comp + UE4_OFFSETS["RelativeLocation"], 8)
                    if pos_data and len(pos_data) >= 8:
                        pos_x, pos_y = struct.unpack_from("<ff", pos_data, 0)
                sub_obj_ptr = self._read_ptr(eg_addr + 0x4E0)
                sub_name = ""
                sub_class = ""
                if sub_obj_ptr and sub_obj_ptr > 0x10000:
                    sub_name = self._memory.read_uobject_name(fnamepool, sub_obj_ptr)
                    sub_cls_ptr = self._read_ptr(sub_obj_ptr + 0x10)
                    if sub_cls_ptr:
                        sub_class = self._memory.read_uobject_name(fnamepool, sub_cls_ptr)
                wave_data = self._memory.read_bytes(eg_addr + 0x618, 4)
                wave = struct.unpack_from("<i", wave_data, 0)[0] if wave_data and len(wave_data) >= 4 else -1
                spawn_data = self._memory.read_bytes(eg_addr + 0x714, 4)
                spawn = struct.unpack_from("<i", spawn_data, 0)[0] if spawn_data and len(spawn_data) >= 4 else -1
                if cfg_data and len(cfg_data) >= 4:
                    cfg_id = struct.unpack_from("<i", cfg_data, 0)[0]
                    type_name = EGAMEPLAY_EVENT_TYPES.get(cfg_id, f"UNKNOWN(0x{cfg_id:X})")
                    lines.append(f"  Event: {type_name} | CfgID=0x{cfg_id:X} | pos=({pos_x:.0f}, {pos_y:.0f}) | sub='{sub_name}'({sub_class}) | wave={wave} spawn={spawn} | EGameplay=0x{eg_addr:X}")
        else:
            lines.append("  No live events with ECfgComponent found")

        lines.append("")
        lines.append("--- FightMgr.MapGamePlay TMap (typed events) ---")
        typed_events = self.get_typed_events()
        if typed_events:
            for e in typed_events:
                tag = "TARGET" if e.is_target_event else "other"
                bv = f" bvalid={e.bvalid}" if e.bvalid != -1 else ""
                lines.append(f"  [{tag}] {e.event_type} | spawn_idx=0x{e.cfg_id:X} | "
                             f"pos=({e.position[0]:.0f}, {e.position[1]:.0f}) | "
                             f"wave={e.wave_counter}{bv} | EGameplay=0x{e.address:X}")
        else:
            lines.append("  No entries found (FightMgr not resolved or no map events)")

        # Raw MapCustomTrap TMap scan — Carjack vehicle entities.
        # Each entry = EMapCustomTrap (physical vehicle); its TMap key = spawn_index.
        # If any entries appear here, those spawn_indices correspond to Carjack events.
        lines.append("")
        lines.append("--- FightMgr.MapCustomTrap TMap (Carjack vehicles) ---")
        if self._fightmgr_ptr:
            ct_tmap_addr = self._fightmgr_ptr + FIGHTMGR_MAP_CUSTOMTRAP_OFFSET
            ct_data_ptr = self._memory.read_value(ct_tmap_addr, "ulong") or 0
            ct_num = self._memory.read_value(ct_tmap_addr + 0x08, "int") or 0
            lines.append(f"  MapCustomTrap data_ptr=0x{ct_data_ptr:X} num={ct_num}")
            if ct_num > 0 and 0x10000 < ct_data_ptr < 0x7FFFFFFFFFFF:
                ct_raw = self._memory.read_bytes(ct_data_ptr, ct_num * 24)
                if ct_raw:
                    for i in range(ct_num):
                        off = i * 24
                        if off + 16 > len(ct_raw):
                            break
                        ct_key = struct.unpack_from("<i", ct_raw, off)[0]
                        ct_ptr = struct.unpack_from("<Q", ct_raw, off + 8)[0]
                        if not (0x10000 < ct_ptr < 0x7FFFFFFFFFFF):
                            continue
                        # Read class name and position of the vehicle entity
                        ct_cls_ptr = self._read_ptr(ct_ptr + 0x10)
                        ct_cls = self._memory.read_uobject_name(fnamepool, ct_cls_ptr) if ct_cls_ptr else "?"
                        ct_root = self._read_ptr(ct_ptr + UE4_OFFSETS["RootComponent"])
                        ct_x, ct_y = 0.0, 0.0
                        if ct_root:
                            ct_pos = self._memory.read_bytes(ct_root + UE4_OFFSETS["RelativeLocation"], 8)
                            if ct_pos and len(ct_pos) >= 8:
                                ct_x, ct_y = struct.unpack_from("<ff", ct_pos)
                        lines.append(f"  [vehicle] spawn_idx=0x{ct_key:X} class='{ct_cls}' "
                                     f"pos=({ct_x:.0f},{ct_y:.0f}) ptr=0x{ct_ptr:X}")
            elif ct_num == 0:
                lines.append("  Empty — no Carjack vehicles on this map")
        else:
            lines.append("  FightMgr not resolved")

        return "\n".join(lines)

    def _dump_ecfg_component(self, lines: list, ecfg_addr: int, dump_bytes: int = 256):
        """Dump CfgInfo and memory from an ECfgComponent."""
        lines.append(f"  ECfgComponent at 0x{ecfg_addr:X}")

        cfginfo_base = ecfg_addr + ECFGCOMPONENT_CFGINFO_OFFSET
        cfg_data = self._memory.read_bytes(cfginfo_base, 32)
        if cfg_data:
            lines.append(f"  CfgInfo raw (ECfg+0x{ECFGCOMPONENT_CFGINFO_OFFSET:X}, 32 bytes):")
            hex_line = " ".join(f"{b:02X}" for b in cfg_data)
            lines.append(f"    {hex_line}")

            if len(cfg_data) >= 12:
                cfg_id = struct.unpack_from("<i", cfg_data, 0)[0]
                cfg_type = struct.unpack_from("<i", cfg_data, 4)[0]
                cfg_extend = struct.unpack_from("<i", cfg_data, 8)[0]
                type_name = EGAMEPLAY_EVENT_TYPES.get(cfg_id, "UNKNOWN")
                lines.append(f"  CfgInfo.ID = 0x{cfg_id:X} ({cfg_id}) -> {type_name}")
                lines.append(f"  CfgInfo.Type = 0x{cfg_type:X} ({cfg_type})")
                lines.append(f"  CfgInfo.ExtendId = 0x{cfg_extend:X} ({cfg_extend})")

        ecfg_full_size = 0x240
        for dump_start in range(0, ecfg_full_size, 0x100):
            dump_len = min(0x100, ecfg_full_size - dump_start)
            wider_data = self._memory.read_bytes(ecfg_addr + dump_start, dump_len)
            if wider_data:
                lines.append(f"  ECfgComponent memory (ECfg+0x{dump_start:X}, {len(wider_data)} bytes):")
                for offset in range(0, len(wider_data), 16):
                    chunk = wider_data[offset:offset + 16]
                    hex_str = " ".join(f"{b:02X}" for b in chunk)
                    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                    lines.append(f"    +0x{dump_start + offset:03X}: {hex_str:<48} {ascii_str}")

    def _read_abp_silent(self, monster_ptr: int, fnamepool: int) -> tuple:
        """Silent component read — no log output.  Used for retry attempts after the
        verbose CompScan log was already emitted on the first encounter.

        Follows the same ueComponents TMap path as _log_emonster_components but
        produces no log lines, so it can be called on every scan cycle without
        flooding the log file.

        Returns (abp_class: str, cfg_id: int, map_icon: int) where -1 = not found.
        """
        TMAP_OFFSET = 0x288
        STRIDE = 24
        uc_data_ptr = self._read_ptr(monster_ptr + TMAP_OFFSET)
        uc_num_raw = self._memory.read_bytes(monster_ptr + TMAP_OFFSET + 8, 4)
        uc_num = struct.unpack_from("<i", uc_num_raw, 0)[0] if uc_num_raw else 0
        if not (uc_data_ptr and uc_data_ptr > 0x10000 and 1 <= uc_num <= 64):
            return "", -1, -1
        raw = self._memory.read_bytes(uc_data_ptr, uc_num * STRIDE)
        if not raw:
            return "", -1, -1

        abp_result = ""
        cfg_id_result = -1
        map_icon_result = -1

        for i in range(uc_num):
            off = i * STRIDE
            if off + 16 > len(raw):
                break
            key_ptr = struct.unpack_from("<Q", raw, off)[0]
            comp_ptr = struct.unpack_from("<Q", raw, off + 8)[0]
            if not (0x10000 < key_ptr < 0x7FFFFFFFFFFF):
                continue
            if not (0x10000 < comp_ptr < 0x7FFFFFFFFFFF):
                continue
            key_name = self._memory.read_uobject_name(fnamepool, key_ptr)

            if "EAnime" in key_name and not abp_result:
                skel_ptr = self._read_ptr(comp_ptr + 0x128)
                if skel_ptr and skel_ptr > 0x10000:
                    for abp_off in (0x750, 0x758):
                        abp_ptr = self._read_ptr(skel_ptr + abp_off)
                        if not abp_ptr:
                            continue
                        name = self._memory.read_uobject_name(fnamepool, abp_ptr)
                        if name:
                            abp_result = name
                            break

            elif "ECfg" in key_name and cfg_id_result == -1:
                cfg_data = self._memory.read_bytes(comp_ptr + 0x120, 12)
                if cfg_data and len(cfg_data) >= 12:
                    cfg_id_result = struct.unpack_from("<i", cfg_data, 0)[0]
                    # Also cache type byte (+0x04) and extend_id (+0x08) via the cache dict
                    # as a tuple (id, type_byte, extend_id) so they survive to display.
                    # For backward compat cfg_id_result stays int32 ID only.
                    _cfg_type = struct.unpack_from("<i", cfg_data, 4)[0]
                    _cfg_eid  = struct.unpack_from("<i", cfg_data, 8)[0]
                    cfg_id_result = (cfg_id_result, _cfg_type, _cfg_eid)
                elif cfg_data and len(cfg_data) >= 4:
                    cfg_id_result = (struct.unpack_from("<i", cfg_data, 0)[0], -1, -1)

            elif "EMapIcon" in key_name and map_icon_result == -1:
                # Read 8 bytes: first int32 = icon type, second int32 = initialization probe.
                # When an entity is freshly spawned the EMapIconComponent memory is all-zeros
                # except for the first byte (0x01), so bytes 4-7 == 0 means not yet
                # initialized — return -1 so the caller retries on the next scan cycle.
                icon_data = self._memory.read_bytes(comp_ptr + 0x120, 8)
                if icon_data and len(icon_data) >= 8:
                    icon_int = struct.unpack_from("<i", icon_data, 0)[0]
                    init_probe = struct.unpack_from("<i", icon_data, 4)[0]
                    if icon_int == 1 and init_probe == 0:
                        pass  # not yet initialized — leave map_icon_result == -1 to retry
                    else:
                        map_icon_result = icon_int

            # Early exit if all three are resolved.
            if abp_result and cfg_id_result != -1 and map_icon_result != -1:
                break

        return abp_result, cfg_id_result, map_icon_result

    def _log_emonster_components(self, monster_ptr: int, fnamepool: int) -> tuple:
        """Log the component chain for one EMonster actor to establish the
        ESkeletalMeshComponent → AnimBlueprintGeneratedClass path at runtime.
        Also reads ECfgComponent.CfgInfo.ID and EMapIconComponent custom fields
        for guard/Sandlord identification.

        Primary path (SDK-confirmed, ueComponents TMap):
          EEntity::ueComponents TMap      @  entity + 0x288  (UClass* → EComponent*)
          EAnimeComponent::SkeletalMesh   @  EAnimeComponent + 0x128
          ESkeletalMesh::AnimBPClass      @  ESkeletalMeshComponent + 0x750  (ClassProperty)
          ESkeletalMesh::AnimClass        @  ESkeletalMeshComponent + 0x758  (ClassProperty)

        Supplemental reads (diagnostic + guard detection):
          ECfgComponent::CfgInfo.ID       @  ECfgComponent + 0x120  (int32)
          EMapIconComponent custom field  @  EMapIconComponent + 0x120 (int32, tentative)
            Hypothesis: EConfigMapIcon::E_shoulieshilian = 0xD5 = guard icon.
            Raw 32-byte hex dump logged for verification.

        InstanceComponents@+0x1F0 and OwnedComponents are confirmed always empty
        for EMonster at runtime (314 monsters tested, bot_20260226_104945.log).
        The game uses its own EEntity::ueComponents TMap instead.

        Called for the first time each monster address is seen.  Results are
        written to the log file so the user can confirm the ABP class names live.

        Returns (abp_class: str, cfg_id: int, map_icon: int) where -1 = not found.
        """
        log.info(f"[CompScan] ── EMonster 0x{monster_ptr:X} ──")
        found_abp: str = ""
        found_cfg_id: int = -1
        found_map_icon: int = -1

        def _try_component_ptr(comp_ptr: int, source_label: str) -> str:
            """Follow EAnimeComponent → +0x128 → ESkeletalMeshComponent → +0x750/+0x758.
            Returns ABP class name or empty string."""
            skel_ptr = self._read_ptr(comp_ptr + 0x128)
            if not skel_ptr or skel_ptr < 0x10000:
                log.info(f"[CompScan]   {source_label}: EAnime+0x128 → null (SkeletalMesh ptr not found)")
                return ""
            skel_cls_ptr = self._read_ptr(skel_ptr + 0x10)
            skel_cls = self._memory.read_uobject_name(fnamepool, skel_cls_ptr) if skel_cls_ptr else ""
            log.info(f"[CompScan]   {source_label}: EAnime+0x128 → 0x{skel_ptr:X}  class='{skel_cls}'")
            abp_ptr = self._read_ptr(skel_ptr + 0x750)
            abp_name = self._memory.read_uobject_name(fnamepool, abp_ptr) if abp_ptr else ""
            log.info(f"[CompScan]   {source_label}: ESkeletalMesh+0x750(AnimBPClass) → 0x{abp_ptr or 0:X}  name='{abp_name}'")
            if abp_name:
                return abp_name
            abp2_ptr = self._read_ptr(skel_ptr + 0x758)
            abp2_name = self._memory.read_uobject_name(fnamepool, abp2_ptr) if abp2_ptr else ""
            log.info(f"[CompScan]   {source_label}: ESkeletalMesh+0x758(AnimClass)  → 0x{abp2_ptr or 0:X}  name='{abp2_name}'")
            return abp2_name

        # ── Strategy 1: ueComponents TMap @ EEntity+0x288 (correct runtime path) ────────
        TMAP_OFFSET = 0x288
        STRIDE = 24
        uc_data_ptr = self._read_ptr(monster_ptr + TMAP_OFFSET)
        uc_num_raw = self._memory.read_bytes(monster_ptr + TMAP_OFFSET + 8, 4)
        uc_num = struct.unpack_from("<i", uc_num_raw, 0)[0] if uc_num_raw else 0

        if uc_data_ptr and uc_data_ptr > 0x10000 and 1 <= uc_num <= 64:
            log.info(f"[CompScan]   ueComponents TMap@+0x288  data=0x{uc_data_ptr:X}  num={uc_num}")
            raw = self._memory.read_bytes(uc_data_ptr, uc_num * STRIDE)
            if raw:
                anime_comp_ptr = 0
                ecfg_ptr = 0
                emapicon_ptr = 0
                for i in range(uc_num):
                    off = i * STRIDE
                    if off + 16 > len(raw):
                        break
                    key_ptr  = struct.unpack_from("<Q", raw, off)[0]
                    comp_ptr = struct.unpack_from("<Q", raw, off + 8)[0]
                    if not (0x10000 < key_ptr < 0x7FFFFFFFFFFF):
                        continue
                    if not (0x10000 < comp_ptr < 0x7FFFFFFFFFFF):
                        continue
                    key_name = self._memory.read_uobject_name(fnamepool, key_ptr)
                    log.info(f"[CompScan]     ueComp[{i}] key='{key_name}'  comp=0x{comp_ptr:X}")
                    if "EAnime" in key_name and not anime_comp_ptr:
                        anime_comp_ptr = comp_ptr
                    elif "ECfg" in key_name and not ecfg_ptr:
                        ecfg_ptr = comp_ptr
                    elif "EMapIcon" in key_name and not emapicon_ptr:
                        emapicon_ptr = comp_ptr

                if anime_comp_ptr:
                    found_abp = _try_component_ptr(anime_comp_ptr, "ueComponents TMap@+0x288")
                else:
                    log.info("[CompScan]   ueComponents TMap@+0x288: EAnimeComponent entry not found")

                # ── ECfgComponent: CfgInfo.ID + wider field dump (32B) ─────────────
                if ecfg_ptr and ecfg_ptr > 0x10000:
                    cfg_data = self._memory.read_bytes(ecfg_ptr + 0x120, 32)
                    if cfg_data and len(cfg_data) >= 4:
                        found_cfg_id = struct.unpack_from("<i", cfg_data, 0)[0]
                        cfg_hex = " ".join(f"{b:02X}" for b in cfg_data)
                        log.info(f"[CompScan]   ECfgComponent@0x{ecfg_ptr:X}+0x120 (32B): {cfg_hex}")
                        log.info(f"[CompScan]   ECfgComponent.CfgInfo.ID={found_cfg_id} (0x{found_cfg_id & 0xFFFFFFFF:X})")

                # ── EMapIconComponent: 128-byte raw dump @ comp+0x100 ───────────────
                # No reflected UPROPERTY fields — icon type is a non-reflected C++ member.
                # Scan 128 bytes from +0x100 (covers full custom-field region from +0x120).
                # Search for any byte == 0xD5 (EConfigMapIcon::E_shoulieshilian = guard icon).
                # Initialization check: if bytes 4-7 (after the first int32) are all zero
                # the component is freshly allocated and not yet populated — skip cache.
                if emapicon_ptr and emapicon_ptr > 0x10000:
                    icon_data = self._memory.read_bytes(emapicon_ptr + 0x100, 128)
                    if icon_data and len(icon_data) >= 8:
                        hex_str = " ".join(f"{b:02X}" for b in icon_data)
                        log.info(f"[CompScan]   EMapIconComponent@0x{emapicon_ptr:X}+0x100 (128B): {hex_str}")
                        # Check for any occurrence of 0xD5 in the dump.
                        d5_offsets = [i for i, b in enumerate(icon_data) if b == 0xD5]
                        if d5_offsets:
                            log.info(f"[CompScan]   *** 0xD5 found at relative offsets: {[f'+0x{o:03X}' for o in d5_offsets]} (abs from comp: +0x{0x100 + d5_offsets[0]:X}) ***")
                        # Initialization check at +0x120 region (offset 0x20 within 128B dump).
                        icon_int = struct.unpack_from("<i", icon_data, 0x20)[0]  # comp+0x120
                        init_probe = struct.unpack_from("<i", icon_data, 0x24)[0]  # comp+0x124
                        log.info(f"[CompScan]   EMapIconComponent+0x120 int32={icon_int} (0x{icon_int & 0xFFFFFFFF:08X}) init_probe={init_probe:#010x}")
                        if icon_int == 1 and init_probe == 0:
                            log.info("[CompScan]   EMapIconComponent: not yet initialized (probe==0) — will retry")
                            # don't cache: leave found_map_icon == -1
                        else:
                            found_map_icon = icon_int
            else:
                log.info("[CompScan]   ueComponents TMap@+0x288: could not read TMap data")
        else:
            log.info(f"[CompScan]   ueComponents TMap@+0x288: invalid"
                     f"  (data=0x{uc_data_ptr or 0:X}  num={uc_num})")

        if found_abp:
            guard_tag = ""  # ABP confirmed dead as guard discriminator (v4.38.0)
            log.info(f"[CompScan]   ✓ ABP class resolved: '{found_abp}'"
                     f"  cfg_id={found_cfg_id}  map_icon=0x{found_map_icon & 0xFFFFFFFF:08X}"
                     f"{guard_tag}")

        log.flush()
        return found_abp, found_cfg_id, found_map_icon

    def _scan_owned_components(self, actor_addr: int, fnamepool: int) -> list:
        """Scan an actor's OwnedComponents TArray for component objects.

        AActor::OwnedComponents is typically at offset 0x100 in UE4.
        TArray<UActorComponent*> = { Data*, Count, Max }
        """
        results = []
        for owned_offset in [0x100, 0xF0, 0xF8, 0x108, 0x110]:
            arr_data_ptr = self._read_ptr(actor_addr + owned_offset)
            if not arr_data_ptr or arr_data_ptr < 0x10000:
                continue
            arr_count_data = self._memory.read_bytes(actor_addr + owned_offset + 8, 4)
            if not arr_count_data:
                continue
            arr_count = struct.unpack_from("<i", arr_count_data, 0)[0]
            if arr_count <= 0 or arr_count > 100:
                continue

            for j in range(arr_count):
                comp_ptr = self._read_ptr(arr_data_ptr + j * 8)
                if not comp_ptr or comp_ptr < 0x10000:
                    continue
                comp_name = self._memory.read_uobject_name(fnamepool, comp_ptr)
                class_ptr = self._read_ptr(comp_ptr + 0x10)
                comp_class = ""
                if class_ptr:
                    comp_class = self._memory.read_uobject_name(fnamepool, class_ptr)
                if comp_name or comp_class:
                    results.append((comp_ptr, comp_name, comp_class))

            if results:
                return results
        return results

    # ── Player HP reading ─────────────────────────────────────────────────────────

    def _get_player_pawn(self) -> Optional[int]:
        """Re-walk GWorld → Pawn chain and return the pawn pointer (ERolePlayer).

        Offsets from DUMP_VERIFIED_CHAIN:
          GWorld +0x210 → GameInstance +0x038 → LocalPlayers[0] +0x030
          → PlayerController +0x250 → Pawn
        """
        if not self._cached_gworld_static:
            return self._last_pawn_ptr
        gw = self._read_ptr(self._cached_gworld_static)
        if not gw:
            return None
        for off in [0x210, 0x038, 0x0, 0x030, 0x250]:
            gw = self._read_ptr(gw + off)
            if not gw:
                return None
        self._last_pawn_ptr = gw
        return gw

    def _find_erole_component(self, pawn_ptr: int) -> Optional[int]:
        """Find the ERoleComponent instance in the pawn's ueComponents TMap.

        EEntity::ueComponents at actor+0x288 is TMap<UClass*, EComponent*>.
        In-game test (2026-02-26) confirmed that AActor::InstanceComponents
        (@+0x1F0) is always empty at runtime for all EEntity subclasses — the
        game uses its own ueComponents TMap instead.

        TMap element layout (stride=24): key=UClass*(+0x00), value=EComponent*(+0x08).
        Requires FNamePool to be resolved for class-name matching.
        Returns the ERoleComponent pointer or None.
        """
        if not self._fnamepool_addr:
            return None
        fnp = self._fnamepool_addr
        TMAP_OFFSET = 0x288
        STRIDE = 24
        data_ptr = self._memory.read_value(pawn_ptr + TMAP_OFFSET, "ulong")
        num = self._memory.read_value(pawn_ptr + TMAP_OFFSET + 8, "int")
        if not data_ptr or data_ptr < 0x10000 or data_ptr > 0x7FFFFFFFFFFF:
            log.debug(f"[HPScan] ueComponents TMap data_ptr invalid at pawn+0x288: 0x{data_ptr or 0:X}")
            return None
        if not num or num <= 0 or num > 64:
            log.debug(f"[HPScan] ueComponents TMap num suspicious: {num}")
            return None
        raw = self._memory.read_bytes(data_ptr, num * STRIDE)
        if not raw:
            return None
        for i in range(num):
            off = i * STRIDE
            if off + 16 > len(raw):
                break
            key_ptr = struct.unpack_from("<Q", raw, off)[0]       # UClass*
            comp_ptr = struct.unpack_from("<Q", raw, off + 8)[0]  # EComponent*
            if not (0x10000 < key_ptr < 0x7FFFFFFFFFFF):
                continue
            if not (0x10000 < comp_ptr < 0x7FFFFFFFFFFF):
                continue
            class_name = self._memory.read_uobject_name(fnp, key_ptr)
            if class_name == "ERoleComponent":
                log.info(f"[HPScan] Found ERoleComponent at 0x{comp_ptr:X} (ueComponents[{i}])")
                return comp_ptr
        log.debug(f"[HPScan] ERoleComponent not found in ueComponents TMap (num={num})")
        return None

    def _find_role_logic_offset(self, comp_ptr: int) -> int:
        """Scan ERoleComponent memory for the RoleLogic struct.

        RoleLogic layout (SDK dump confirmed):
          +0x000  LogicFrame     int32   (frame counter, 0–10M)
          +0x004  bIsDead        bool
          +0x005  bKilled        bool
          +0x008  DeadCauser     int32
          +0x010  Info           RoleInfo:
            +0x010  FirstSyncLogicFrame  int32
            +0x018  Hp   ViewFightFloat:
              +0x020  Hp.Base    int64   (current HP; 0 when dead)
            +0x030  HpMax ViewFightFloat:
              +0x038  HpMax.Base int64   (max HP, always > 0 in combat)

        Note: bIsDead/bKilled are NOT checked here — the player may have just died
        during the scan window.  We validate via HpMax > 0 and plausible LogicFrame
        values, then reuse the cached offset on every subsequent read.  Hp.Base == 0
        is valid when the player is dead.

        During initial discovery we require Hp > 0 (player is alive when running a
        map) to avoid false-positive matches on uninitialized memory patterns such as
        HpMax=0xFFFFFFFF (max uint32 garbage) combined with Hp=0.

        Returns byte offset of RoleLogic start within comp_ptr, or -1 if not found.
        """
        SCAN_START    = 0x100
        # ERoleComponent instance size is 0xA40 (confirmed from Default__ objects in
        # SDK dump).  Scanning past 0x800 is required — RoleLogic may be in the upper
        # half of the component (0x7B0–0xA00 range).  Use 0xB00 as a safe upper bound.
        SCAN_END      = 0xB00
        MIN_HP_MAX    = 1              # HpMax must be at least 1; real values may be
                                       # as low as 34 display units (glass-cannon builds)
        # Cap at 2 billion — avoids accepting 0xFFFFFFFF (uint32 max, all-bits-set
        # garbage pattern) as a valid HpMax.  Real endgame HP caps are well below 2B.
        MAX_HP_MAX    = 2_000_000_000
        data = self._memory.read_bytes(comp_ptr + SCAN_START, SCAN_END - SCAN_START)
        if not data or len(data) < 0x50:
            return -1
        for off in range(0, len(data) - 0x50, 8):
            try:
                logic_frame = struct.unpack_from("<i", data, off)[0]
                if not (0 <= logic_frame <= 10_000_000):
                    continue
                fsf = struct.unpack_from("<i", data, off + 0x10)[0]  # FirstSyncLogicFrame
                if not (0 <= fsf <= 10_000_000):
                    continue
                hm_base = struct.unpack_from("<q", data, off + 0x38)[0]
                if not (MIN_HP_MAX <= hm_base <= MAX_HP_MAX):
                    continue
                hp_base = struct.unpack_from("<q", data, off + 0x20)[0]
                # During discovery require Hp > 0: player must be alive during a map run.
                # The fast-path read_player_hp() still accepts hp == 0 (dead player).
                if not (1 <= hp_base <= hm_base):
                    continue
            except struct.error:
                continue
            log.info(f"[HPScan] RoleLogic candidate at comp+0x{SCAN_START + off:X}: "
                     f"LogicFrame={logic_frame} HpMax={hm_base:,} Hp={hp_base:,}")
            return SCAN_START + off
        log.debug(f"[HPScan] RoleLogic pattern not matched in range comp+0x{SCAN_START:X}..+0x{SCAN_END:X}")
        return -1

    def _scan_player_hp_async(self):
        """Background thread: locate ERoleComponent and RoleLogic offset, cache them."""
        try:
            pawn = self._get_player_pawn()
            if not pawn:
                log.debug("[HPScan] Pawn pointer not available — retrying later")
                self._hp_scan_failed_at = time.monotonic()
                return
            log.debug(f"[HPScan] Pawn at 0x{pawn:X}, scanning ueComponents for ERoleComponent...")
            comp = self._find_erole_component(pawn)
            if not comp:
                log.info("[HPScan] ERoleComponent not found — FNamePool may not be resolved yet, retrying")
                self._hp_scan_failed_at = time.monotonic()
                return
            offset = self._find_role_logic_offset(comp)
            if offset >= 0:
                self._erole_comp_ptr   = comp
                self._role_logic_offset = offset
                log.info(f"[HPScan] RoleLogic found: ERoleComponent=0x{comp:X}  offset=+0x{offset:X}")
            else:
                log.info("[HPScan] RoleLogic pattern not found in ERoleComponent scan range")
                self._hp_scan_failed_at = time.monotonic()
        except Exception as e:
            log.debug(f"[HPScan] Scan error: {e}")
            self._hp_scan_failed_at = time.monotonic()
        finally:
            self._hp_pending = False

    def read_player_hp(self) -> Optional[tuple]:
        """Return (current_hp, max_hp) for the local player, or None if unavailable.

        Fast path (after first discovery): two int64 reads from cached addresses.
        Slow path (first call): triggers a background thread to scan for the
        ERoleComponent → RoleLogic offset; returns None until scan completes.

        HP values are the int64 Base field of ViewFightFloat (RoleInfo.Hp.Base /
        RoleInfo.HpMax.Base).  Actual displayed HP = Base + Frac (float 0–1),
        but Base alone is sufficient for a dashboard counter.

        hp=0 is valid (player dead) — returns (0, hm) without invalidating cache.
        """
        if self._erole_comp_ptr and self._role_logic_offset >= 0:
            try:
                off = self._role_logic_offset
                hp_b = self._memory.read_bytes(self._erole_comp_ptr + off + 0x20, 8)
                hm_b = self._memory.read_bytes(self._erole_comp_ptr + off + 0x38, 8)
                if hp_b and hm_b:
                    hp = struct.unpack("<q", hp_b)[0]
                    hm = struct.unpack("<q", hm_b)[0]
                    # hp == 0 is valid when player is dead; hm must always be positive
                    if 0 <= hp <= hm and 0 < hm <= 100_000_000_000:
                        return (int(hp), int(hm))
            except Exception:
                pass
            # Cached values no longer valid — reset and trigger re-scan
            self._erole_comp_ptr    = 0
            self._role_logic_offset = -1

        if not self._hp_pending:
            # Debounce: don't retry within 5 s of a previous failed scan
            if self._hp_scan_failed_at and (time.monotonic() - self._hp_scan_failed_at) < 5.0:
                return None
            self._hp_pending = True
            t = threading.Thread(target=self._scan_player_hp_async,
                                 daemon=True, name="HPScan")
            t.start()
        return None

    def _is_plausible_coordinate(self, value: float) -> bool:
        if value != value:
            return False
        if abs(value) < 0.001:
            return False
        if abs(value) > 1000000:
            return False
        return True

    def _save_addresses(self, result: ScanResult):
        self._log("Saving discovered addresses...")

        x_offsets = list(result.chain_offsets)

        y_offsets = list(result.chain_offsets)
        y_offsets[-1] = y_offsets[-1] + 4

        z_offsets = list(result.chain_offsets)
        z_offsets[-1] = z_offsets[-1] + 8

        suffix = " (auto-discovered)"

        self._addresses.set_address(
            name="player_x",
            base_module=result.base_module,
            base_offset=result.gworld_static_offset,
            offsets=x_offsets,
            value_type="float",
            description=f"Player X position{suffix}",
            verified=True,
        )
        self._addresses.set_address(
            name="player_y",
            base_module=result.base_module,
            base_offset=result.gworld_static_offset,
            offsets=y_offsets,
            value_type="float",
            description=f"Player Y position{suffix}",
            verified=True,
        )
        self._addresses.set_address(
            name="player_z",
            base_module=result.base_module,
            base_offset=result.gworld_static_offset,
            offsets=z_offsets,
            value_type="float",
            description=f"Player Z position{suffix}",
            verified=True,
        )

        self._log("Addresses saved to Address Setup")
