import copy
import time
import threading
from dataclasses import dataclass, field
from typing import Optional

from src.core.memory_reader import MemoryReader, PointerChain
from src.core.address_manager import AddressManager
from src.utils.logger import log


@dataclass
class Position:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def distance_to(self, other: "Position") -> float:
        dx = self.x - other.x
        dy = self.y - other.y
        return (dx * dx + dy * dy) ** 0.5


@dataclass
class PlayerState:
    position: Position = field(default_factory=Position)
    health: int = 0
    max_health: int = 0
    is_alive: bool = True
    is_moving: bool = False


@dataclass
class MapState:
    map_id: int = 0
    map_name: str = ""
    zone_name: str = ""
    is_in_hideout: bool = False
    is_in_map: bool = False
    is_map_complete: bool = False
    map_addresses_available: bool = False


class GameState:
    def __init__(self, memory_reader: MemoryReader, address_manager: AddressManager):
        self._reader = memory_reader
        self._addresses = address_manager
        self._lock = threading.RLock()
        self._player = PlayerState()
        self._map = MapState()
        self._last_update = 0.0
        self._update_count = 0
        self._is_valid = False
        self._demo_mode = False
        self._demo_tick = 0

    @property
    def player(self) -> PlayerState:
        with self._lock:
            return copy.deepcopy(self._player)

    @property
    def map(self) -> MapState:
        with self._lock:
            return copy.deepcopy(self._map)

    @property
    def is_valid(self) -> bool:
        with self._lock:
            return self._is_valid

    @property
    def update_count(self) -> int:
        return self._update_count

    @property
    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._player), copy.deepcopy(self._map)

    def reset(self):
        self._player = PlayerState()
        self._map = MapState()
        self._is_valid = False
        self._demo_mode = False
        self._demo_tick = 0
        self._update_count = 0

    def enable_demo_mode(self):
        self.reset()
        self._demo_mode = True
        self._player.health = 5000
        self._player.max_health = 5000
        self._map.is_in_hideout = True
        self._is_valid = True
        log.info("Demo mode enabled - simulating game state")

    def update(self) -> bool:
        with self._lock:
            if self._demo_mode:
                return self._update_demo()

            if not self._reader.is_attached:
                self._is_valid = False
                return False

            try:
                self._update_player()
                self._update_map()
                self._last_update = time.time()
                self._update_count += 1
                self._is_valid = True
                return True
            except Exception as e:
                log.error(f"Game state update failed: {e}")
                self._is_valid = False
                return False

    def _update_demo(self) -> bool:
        import random
        self._demo_tick += 1

        self._player.position.x += random.uniform(-2.0, 2.0)
        self._player.position.y += random.uniform(-2.0, 2.0)

        phase = (self._demo_tick // 20) % 4

        if phase == 0:
            self._map.is_in_hideout = True
            self._map.is_in_map = False
        elif phase == 1:
            self._map.is_in_hideout = False
            self._map.is_in_map = True
            self._map.map_id = 1042
        elif phase == 2:
            self._player.health = max(1000, self._player.health - random.randint(0, 200))
        elif phase == 3:
            self._map.is_in_map = False
            self._map.is_map_complete = False
            self._map.is_in_hideout = True
            self._player.health = self._player.max_health

        self._last_update = time.time()
        self._update_count += 1
        self._is_valid = True
        return True

    def read_chain(self, address_name: str):
        return self._read_chain(address_name)

    def set_zone_name(self, zone_name: str):
        with self._lock:
            self._map.zone_name = zone_name

    def _read_chain(self, address_name: str) -> Optional[any]:
        chain = self._addresses.get_chain(address_name)
        if chain is None:
            return None
        return self._reader.read_pointer_chain(chain)

    def validate_addresses(self) -> tuple:
        if self._demo_mode:
            return (True, "Demo mode - no validation needed")

        if not self._reader.is_attached:
            return (False, "Not attached to game process")

        chain_x = self._addresses.get_chain("player_x")
        chain_y = self._addresses.get_chain("player_y")
        if chain_x is None or chain_y is None:
            return (False, "player_x/y: no address configured - run auto-scanner first")

        errors = []
        warnings = []

        x1 = self._read_chain("player_x")
        y1 = self._read_chain("player_y")

        if x1 is None:
            errors.append("player_x: chain failed to resolve")
        elif not self._is_plausible_coord(x1):
            errors.append(f"player_x: implausible value {x1}")

        if y1 is None:
            errors.append("player_y: chain failed to resolve")
        elif not self._is_plausible_coord(y1):
            errors.append(f"player_y: implausible value {y1}")

        if errors:
            return (False, "; ".join(errors))

        z = self._read_chain("player_z")
        if z is None:
            warnings.append("player_z not configured (optional)")

        hp = self._read_chain("player_health")
        if hp is not None and (hp < 0 or hp > 1000000):
            warnings.append(f"player_health implausible: {hp}")

        time.sleep(0.4)

        x2 = self._read_chain("player_x")
        y2 = self._read_chain("player_y")

        if x2 is not None and y2 is not None and x1 is not None and y1 is not None:
            dx = abs(x2 - x1)
            dy = abs(y2 - y1)
            if dx == 0 and dy == 0:
                warnings.append("WARNING: coordinates unchanged (may be stale or character is stationary)")

        addr_x = self._addresses.get_address("player_x")
        addr_y = self._addresses.get_address("player_y")
        if addr_x and not addr_x.get("verified", False):
            warnings.append("player_x not movement-verified")
        if addr_y and not addr_y.get("verified", False):
            warnings.append("player_y not movement-verified")

        msg = f"OK: pos=({x1:.0f}, {y1:.0f})"
        if warnings:
            msg = f"WARNING: {'; '.join(warnings)} | pos=({x1:.0f}, {y1:.0f})"

        return (True, msg)

    def _is_plausible_coord(self, value) -> bool:
        if value is None:
            return False
        try:
            f = float(value)
            if f != f:
                return False
            if abs(f) > 1000000:
                return False
            return True
        except (TypeError, ValueError):
            return False

    def _update_player(self):
        x = self._read_chain("player_x")
        y = self._read_chain("player_y")
        z = self._read_chain("player_z")
        if x is not None:
            self._player.position.x = x
        if y is not None:
            self._player.position.y = y
        if z is not None:
            self._player.position.z = z

        hp = self._read_chain("player_health")
        if hp is not None:
            self._player.health = hp

        max_hp = self._read_chain("player_max_health")
        if max_hp is not None:
            self._player.max_health = max_hp

        self._player.is_alive = self._player.health > 0 if self._player.max_health > 0 else True

    def has_map_addresses(self) -> bool:
        return (
            self._addresses.get_chain("map_id") is not None
            or self._addresses.get_chain("is_hideout") is not None
            or self._addresses.get_chain("is_in_map") is not None
        )

    def _update_map(self):
        has_any_map_addr = False

        map_id = self._read_chain("map_id")
        if map_id is not None:
            self._map.map_id = map_id
            has_any_map_addr = True

        hideout_flag = self._read_chain("is_hideout")
        if hideout_flag is not None:
            self._map.is_in_hideout = bool(hideout_flag)
            has_any_map_addr = True
        elif self._addresses.get_chain("is_hideout") is None:
            self._map.is_in_hideout = False

        in_map_flag = self._read_chain("is_in_map")
        if in_map_flag is not None:
            self._map.is_in_map = bool(in_map_flag)
            has_any_map_addr = True
        elif self._addresses.get_chain("is_in_map") is None:
            self._map.is_in_map = False

        self._map.map_addresses_available = has_any_map_addr
