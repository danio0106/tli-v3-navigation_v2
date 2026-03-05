"""Native scanner parity smoke checks for strict-runtime API compatibility.

This script validates the pybind surface and selected behavior parity for
NativeScanner using deterministic fake backend/memory objects.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


class FakeMemory:
    def __init__(self) -> None:
        self._values: Dict[Tuple[int, str], object] = {}
        self._class_names: Dict[int, str] = {}
        self._fname: Dict[int, str] = {}
        self._boss_results: List[tuple] = []
        self._find_name_results: List[tuple] = []

    def set_value(self, addr: int, value_type: str, value: object) -> None:
        self._values[(addr, value_type)] = value

    def set_class_name(self, class_ptr: int, class_name: str) -> None:
        self._class_names[class_ptr] = class_name

    def read_value(self, addr: int, value_type: str):
        return self._values.get((addr, value_type), 0)

    def read_uobject_name(self, fnamepool_addr: int, class_ptr: int) -> str:
        if not fnamepool_addr:
            return ""
        return self._class_names.get(class_ptr, "")

    def set_fname(self, idx: int, value: str) -> None:
        self._fname[int(idx)] = value

    def read_fname(self, fnamepool_addr: int, idx: int) -> str:
        if not fnamepool_addr:
            return ""
        return self._fname.get(int(idx), "")

    def set_boss_results(self, results: List[tuple]) -> None:
        self._boss_results = list(results)

    def find_gobjects_by_class_name(self, gobjects_base: int, fnamepool_base: int, class_name: str):
        if not gobjects_base or not fnamepool_base:
            return []
        if class_name == "MapBossRoom":
            return list(self._boss_results)
        return []

    def set_find_name_results(self, results: List[tuple]) -> None:
        self._find_name_results = list(results)

    def find_gobject_by_name(self, gobjects_base: int, fnamepool_base: int, name: str):
        if not gobjects_base or not fnamepool_base:
            return []
        return list(self._find_name_results)


class FakeBackend:
    def __init__(self) -> None:
        self.calls: List[object] = []
        self.fnamepool_addr = 0x12345678
        self.gobjects_addr = 0x23456789
        self._memory = None
        self._fightmgr_ptr = 0
        self._carjack_truck_pos = (0.0, 0.0)
        self._carjack_vehicle_addr = 0
        self._nav_collision_boxes: List[dict] = []

    def set_nav_collision_probe(self, enabled, interval_s=2.0):
        self.calls.append(("set_nav_collision_probe", bool(enabled), float(interval_s)))
        return None

    def count_nearby_monsters(self, x, y, radius=2500.0):
        self.calls.append(("count_nearby_monsters", x, y, radius))
        return 7

    def get_nearby_interactive_items(self, x, y, radius=3000.0, require_valid=True):
        self.calls.append(("get_nearby_interactive_items", x, y, radius, require_valid))
        return ["backend_item"]

    def _read_truck_guard_roster(self, truck_addr, fnamepool):
        self.calls.append(("_read_truck_guard_roster", truck_addr, fnamepool))
        return []

    def get_typed_events(self):
        self.calls.append("get_typed_events")
        return ["backend_events"]

    def read_minimap_visited_positions(self, raw_zone_name=""):
        self.calls.append(("read_minimap_visited_positions", raw_zone_name))
        return [(1.0, 2.0)]

    def get_fightmgr_ptr(self):
        return self._fightmgr_ptr

    # Required API stubs
    def scan_dump_chain(self, use_cache=True):
        return None

    def scan_fnamepool(self, module_base=0, module_size=0):
        return 0

    def scan_gobjects(self, module_base=0, module_size=0):
        return 0

    def set_cached_gworld_static(self, value):
        return None

    def clear_fightmgr_cache(self):
        return None

    def check_chain_valid(self):
        return True

    def get_gworld_ptr(self):
        return 0

    def read_player_xy(self):
        return (0.0, 0.0)

    def read_zone_name(self):
        return ""

    def read_real_zone_name(self):
        return ""

    def get_monster_entities(self):
        return []

    def get_carjack_truck_position(self):
        return None

    def get_carjack_guard_positions(self):
        return []

    def scan_boss_room(self):
        return None

    def get_nav_collision_markers(self):
        return []

    def find_object_by_name(self, name):
        return []

    def read_player_hp(self):
        return None

    def set_fnamepool_addr(self, value):
        self.fnamepool_addr = int(value)
        return None

    def cancel(self):
        return None


def _build_native_event_fixture(mem: FakeMemory, backend: FakeBackend) -> None:
    # Build one gameplay event and one Carjack trap entry at matching position.
    fightmgr = 0x100000
    backend._fightmgr_ptr = fightmgr

    # Import offsets from project constants used by native module.
    constants = importlib.import_module("src.utils.constants")
    gameplay_off = int(constants.FIGHTMGR_MAP_GAMEPLAY_OFFSET)
    customtrap_off = int(constants.FIGHTMGR_MAP_CUSTOMTRAP_OFFSET)

    gameplay_tmap = fightmgr + gameplay_off
    trap_tmap = fightmgr + customtrap_off

    gameplay_data = 0x200000
    trap_data = 0x300000

    mem.set_value(gameplay_tmap + 0x0, "ulong", gameplay_data)
    mem.set_value(gameplay_tmap + 0x8, "int", 1)
    mem.set_value(trap_tmap + 0x0, "ulong", trap_data)
    mem.set_value(trap_tmap + 0x8, "int", 1)

    # TMap element layout: key@+0x0 (int), value@+0x8 (ptr), stride=0x18
    gameplay_entity = 0x400000
    trap_entity = 0x500000
    mem.set_value(gameplay_data + 0x0, "int", 11)
    mem.set_value(gameplay_data + 0x8, "ulong", gameplay_entity)
    mem.set_value(trap_data + 0x0, "int", 9)
    mem.set_value(trap_data + 0x8, "ulong", trap_entity)

    # Gameplay event fields
    mem.set_value(gameplay_entity + 0x618, "int", 2)      # wave_counter
    mem.set_value(gameplay_entity + 0x714, "int", 11)     # spawn_index
    mem.set_value(gameplay_entity + 0x720, "byte", 1)     # bvalid
    gp_root = 0x401000
    mem.set_value(gameplay_entity + 0x130, "ulong", gp_root)
    mem.set_value(gp_root + 0x124, "float", 2500.0)
    mem.set_value(gp_root + 0x128, "float", -950.0)
    mem.set_value(gp_root + 0x12C, "float", 0.0)

    # Trap event fields and class
    mem.set_value(trap_entity + 0x618, "int", -1)
    mem.set_value(trap_entity + 0x714, "int", 9)
    mem.set_value(trap_entity + 0x720, "byte", 1)
    tp_root = 0x501000
    mem.set_value(trap_entity + 0x130, "ulong", tp_root)
    mem.set_value(tp_root + 0x124, "float", 2500.0)
    mem.set_value(tp_root + 0x128, "float", -950.0)
    mem.set_value(tp_root + 0x12C, "float", 0.0)

    trap_class_ptr = 0x510000
    mem.set_value(trap_entity + 0x10, "ulong", trap_class_ptr)
    mem.set_class_name(trap_class_ptr, "EMapCustomTrapS11")

    # Add one monster in MapRoleMonster for native get_monster_entities fast path.
    monster_off = int(constants.FIGHTMGR_MAP_MONSTER_OFFSET)
    monster_tmap = fightmgr + monster_off
    monster_data = 0x600000
    mem.set_value(monster_tmap + 0x0, "ulong", monster_data)
    mem.set_value(monster_tmap + 0x8, "int", 1)

    monster_entity = 0x700000
    mem.set_value(monster_data + 0x0, "int", 21)
    mem.set_value(monster_data + 0x8, "ulong", monster_entity)
    mem.set_value(monster_entity + 0x618, "int", -1)
    mem.set_value(monster_entity + 0x714, "int", 21)
    mem.set_value(monster_entity + 0x720, "byte", 1)

    m_root = 0x701000
    mem.set_value(monster_entity + 0x130, "ulong", m_root)
    mem.set_value(m_root + 0x124, "float", 100.0)
    mem.set_value(m_root + 0x128, "float", 200.0)
    mem.set_value(m_root + 0x12C, "float", 0.0)

    m_class_ptr = 0x710000
    mem.set_value(monster_entity + 0x10, "ulong", m_class_ptr)
    mem.set_class_name(m_class_ptr, "EMonster")

    # World FName fixture for read_zone_name / read_real_zone_name
    gworld = 0x900000
    mem.set_value(gworld + 0x18, "int", 77)
    mem.set_fname(77, "YJ_TestMap200")

    # backend get_gworld_ptr is used by native wrapper
    def _gworld_ptr():
        return gworld
    backend.get_gworld_ptr = _gworld_ptr

    # Boss room fixture (MapBossRoom actor at non-origin position)
    boss_actor = 0xA00000
    boss_root = 0xA01000
    mem.set_value(boss_actor + 0x130, "ulong", boss_root)
    mem.set_value(boss_root + 0x124, "float", 321.0)
    mem.set_value(boss_root + 0x128, "float", 654.0)
    mem.set_value(boss_root + 0x12C, "float", 0.0)
    mem.set_boss_results([(boss_actor, "MapBossRoom_0")])

    # find_object_by_name fixture
    mem.set_find_name_results([(0xB00000, "FightMgr")])


def run_checks() -> List[CheckResult]:
    native_mod = importlib.import_module("src.native.tli_native")

    backend = FakeBackend()
    mem = FakeMemory()
    backend._memory = mem
    scanner = native_mod.create_scanner(mem, object(), lambda msg: None, backend)

    results: List[CheckResult] = []

    # API compatibility checks
    results.append(CheckResult(
        "has set_nav_collision_probe",
        hasattr(scanner, "set_nav_collision_probe"),
    ))

    # kwargs dispatch checks
    try:
        v = scanner.count_nearby_monsters(1.0, 2.0, radius=3.0)
        results.append(CheckResult("kwargs count_nearby_monsters", bool(v == 7), f"value={v}"))
    except Exception as exc:
        results.append(CheckResult("kwargs count_nearby_monsters", False, str(exc)))

    try:
        v = scanner.get_nearby_interactive_items(1.0, 2.0, radius=3.0, require_valid=True)
        ok = isinstance(v, list) and v == ["backend_item"]
        results.append(CheckResult("kwargs get_nearby_interactive_items", ok, f"value={v}"))
    except Exception as exc:
        results.append(CheckResult("kwargs get_nearby_interactive_items", False, str(exc)))

    # Signature parity
    try:
        v = scanner._read_truck_guard_roster(111, 222)
        results.append(CheckResult("_read_truck_guard_roster args", isinstance(v, list), f"value={v}"))
    except Exception as exc:
        results.append(CheckResult("_read_truck_guard_roster args", False, str(exc)))

    # Private alias exposure
    props_ok = all([
        hasattr(scanner, "_memory"),
        hasattr(scanner, "_fnamepool_addr"),
        hasattr(scanner, "_gobjects_addr"),
        hasattr(scanner, "_scanner"),
    ])
    results.append(CheckResult("private alias properties", props_ok))

    # Minimap signature parity
    try:
        v = scanner.read_minimap_visited_positions("YJ_TEST")
        ok = isinstance(v, list) and len(v) == 1
        results.append(CheckResult("read_minimap_visited_positions(raw_zone_name)", ok, f"value={v}"))
    except Exception as exc:
        results.append(CheckResult("read_minimap_visited_positions(raw_zone_name)", False, str(exc)))

    # Native get_typed_events path check
    _build_native_event_fixture(mem, backend)
    try:
        events = scanner.get_typed_events()
        if isinstance(events, list) and events:
            ev0 = events[0]
            ok = (
                getattr(ev0, "event_type", "") == "Carjack"
                and bool(getattr(ev0, "is_target_event", False))
                and tuple(getattr(ev0, "position", (0, 0, 0)))[:2] == (2500.0, -950.0)
            )
            results.append(CheckResult("native get_typed_events classification", ok, f"event_type={getattr(ev0, 'event_type', '')}"))
        else:
            results.append(CheckResult("native get_typed_events classification", False, f"events={events}"))
    except Exception as exc:
        results.append(CheckResult("native get_typed_events classification", False, str(exc)))

    # Native carjack truck position should resolve from typed-event cache.
    try:
        pos = scanner.get_carjack_truck_position()
        ok = isinstance(pos, tuple) and len(pos) == 2 and pos == (2500.0, -950.0)
        results.append(CheckResult("native get_carjack_truck_position", ok, f"value={pos}"))
    except Exception as exc:
        results.append(CheckResult("native get_carjack_truck_position", False, str(exc)))

    # Native get_monster_entities fast path shape.
    try:
        monsters = scanner.get_monster_entities()
        ok = (
            isinstance(monsters, list)
            and len(monsters) >= 1
            and getattr(monsters[0], "event_type", "") == "Monster"
            and tuple(getattr(monsters[0], "position", (0, 0, 0)))[:2] == (100.0, 200.0)
            and int(getattr(monsters[0], "bvalid", 0) or 0) == 1
        )
        results.append(CheckResult("native get_monster_entities", ok, f"count={len(monsters) if isinstance(monsters, list) else 'n/a'}"))
    except Exception as exc:
        results.append(CheckResult("native get_monster_entities", False, str(exc)))

    # Native zone read path.
    try:
        z = scanner.read_zone_name()
        zr = scanner.read_real_zone_name()
        ok = z == "YJ_TestMap200" and zr == "YJ_TestMap200"
        results.append(CheckResult("native zone reads", ok, f"zone={z} real={zr}"))
    except Exception as exc:
        results.append(CheckResult("native zone reads", False, str(exc)))

    # Native boss room scan path.
    try:
        boss = scanner.scan_boss_room()
        ok = isinstance(boss, tuple) and boss == (321.0, 654.0)
        results.append(CheckResult("native scan_boss_room", ok, f"value={boss}"))
    except Exception as exc:
        results.append(CheckResult("native scan_boss_room", False, str(exc)))

    # Native find_object_by_name path.
    try:
        hits = scanner.find_object_by_name("FightMgr")
        ok = isinstance(hits, list) and len(hits) == 1 and hits[0][1] == "FightMgr"
        results.append(CheckResult("native find_object_by_name", ok, f"value={hits}"))
    except Exception as exc:
        results.append(CheckResult("native find_object_by_name", False, str(exc)))

    # Native get_carjack_guard_positions and get_nav_collision_markers paths.
    try:
        backend._nav_collision_boxes = [{"x": 123.0, "y": 456.0, "label": "NAV"}]
        _ = scanner.get_carjack_guard_positions()
        guards = scanner.get_carjack_guard_positions()
        nav = scanner.get_nav_collision_markers()
        guard_ok = (
            isinstance(guards, list)
            and len(guards) >= 1
            and isinstance(guards[0], dict)
            and all(k in guards[0] for k in ("x", "y", "addr"))
        )
        nav_ok = (
            isinstance(nav, list)
            and len(nav) == 1
            and isinstance(nav[0], dict)
            and nav[0].get("label") == "NAV"
        )
        ok = guard_ok and nav_ok
        results.append(
            CheckResult(
                "native guards/nav markers",
                ok,
                f"guards={guards[:1] if isinstance(guards, list) else guards} nav={nav}",
            )
        )
    except Exception as exc:
        results.append(CheckResult("native guards/nav markers", False, str(exc)))

    # Native overlay worker API coverage.
    try:
        has_api = all(
            hasattr(scanner, name)
            for name in ("start_overlay_worker", "stop_overlay_worker", "get_overlay_snapshot", "overlay_worker_alive")
        )
        results.append(CheckResult("native overlay worker api", has_api))
        if has_api:
            scanner.start_overlay_worker(0.05)
            import time
            time.sleep(0.12)
            snap = scanner.get_overlay_snapshot()
            alive_mid = bool(scanner.overlay_worker_alive)
            scanner.stop_overlay_worker()
            alive_end = bool(scanner.overlay_worker_alive)
            ok_shape = isinstance(snap, dict) and all(
                k in snap
                for k in (
                    "event_markers",
                    "guard_markers",
                    "entity_markers",
                    "nav_collision_markers",
                    "dropped_event_markers",
                    "dropped_guard_markers",
                    "updated_at",
                )
            )
            ok = ok_shape and alive_mid and (not alive_end)
            results.append(CheckResult("native overlay worker behavior", ok, f"alive_mid={alive_mid} alive_end={alive_end} keys={list(snap.keys()) if isinstance(snap, dict) else type(snap)}"))
    except Exception as exc:
        results.append(CheckResult("native overlay worker behavior", False, str(exc)))

    return results


def main() -> int:
    results = run_checks()
    failed = [r for r in results if not r.passed]

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        suffix = f" | {r.detail}" if r.detail else ""
        print(f"[{status}] {r.name}{suffix}")

    if failed:
        print(f"\nFAILED: {len(failed)}/{len(results)} checks")
        return 1

    print(f"\nOK: {len(results)}/{len(results)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
