import json
import os
from typing import Optional, Dict

from src.core.memory_reader import PointerChain
from src.utils.constants import ADDRESSES_FILE
from src.utils.logger import log


DEFAULT_ADDRESSES = {
    "player_x": {
        "base_module": "torchlight_infinite.exe",
        "base_offset": 0x0,
        "offsets": [],
        "value_type": "float",
        "description": "Player X position",
        "verified": False,
    },
    "player_y": {
        "base_module": "torchlight_infinite.exe",
        "base_offset": 0x0,
        "offsets": [],
        "value_type": "float",
        "description": "Player Y position",
        "verified": False,
    },
    "player_z": {
        "base_module": "torchlight_infinite.exe",
        "base_offset": 0x0,
        "offsets": [],
        "value_type": "float",
        "description": "Player Z position (optional)",
        "verified": False,
    },
    "player_health": {
        "base_module": "torchlight_infinite.exe",
        "base_offset": 0x0,
        "offsets": [],
        "value_type": "int",
        "description": "Player current HP",
        "verified": False,
    },
    "player_max_health": {
        "base_module": "torchlight_infinite.exe",
        "base_offset": 0x0,
        "offsets": [],
        "value_type": "int",
        "description": "Player max HP",
        "verified": False,
    },
    "map_id": {
        "base_module": "torchlight_infinite.exe",
        "base_offset": 0x0,
        "offsets": [],
        "value_type": "int",
        "description": "Current zone ID",
        "verified": False,
    },
    "is_hideout": {
        "base_module": "torchlight_infinite.exe",
        "base_offset": 0x0,
        "offsets": [],
        "value_type": "byte",
        "description": "1 if in hideout, 0 otherwise",
        "verified": False,
    },
    "is_in_map": {
        "base_module": "torchlight_infinite.exe",
        "base_offset": 0x0,
        "offsets": [],
        "value_type": "byte",
        "description": "1 if in a map instance",
        "verified": False,
    },
}


class AddressManager:
    def __init__(self, filepath: str = ADDRESSES_FILE):
        self._filepath = filepath
        self._addresses: Dict[str, dict] = {}
        self.load()

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r") as f:
                    data = json.load(f)
                self._addresses = data.get("addresses", {})
                log.info(f"Loaded {len(self._addresses)} addresses from {self._filepath}")
            except Exception as e:
                log.error(f"Failed to load addresses: {e}")
                self._addresses = dict(DEFAULT_ADDRESSES)
        else:
            self._addresses = dict(DEFAULT_ADDRESSES)
            self.save()
            log.info("Created default address file")

    def save(self):
        try:
            data = {
                "addresses": self._addresses,
            }
            with open(self._filepath, "w") as f:
                json.dump(data, f, indent=2)
            log.debug("Addresses saved")
        except Exception as e:
            log.error(f"Failed to save addresses: {e}")

    def get_chain(self, name: str) -> Optional[PointerChain]:
        addr = self._addresses.get(name)
        if not addr:
            return None
        if addr["base_offset"] == 0 and not addr["offsets"]:
            return None
        return PointerChain(
            base_module=addr["base_module"],
            base_offset=addr["base_offset"],
            offsets=addr["offsets"],
            value_type=addr.get("value_type", "int"),
        )

    def set_address(self, name: str, base_module: str, base_offset: int,
                    offsets: list, value_type: str = "int",
                    description: str = "", verified: bool = False):
        self._addresses[name] = {
            "base_module": base_module,
            "base_offset": base_offset,
            "offsets": offsets,
            "value_type": value_type,
            "description": description,
            "verified": verified,
        }
        self.save()

    def remove_address(self, name: str):
        if name in self._addresses:
            del self._addresses[name]
            self.save()

    def get_all_addresses(self) -> Dict[str, dict]:
        return dict(self._addresses)

    def get_address(self, name: str) -> Optional[dict]:
        return self._addresses.get(name)

    def mark_verified(self, name: str, verified: bool = True):
        if name in self._addresses:
            self._addresses[name]["verified"] = verified
            self.save()

    def reset_to_defaults(self):
        self._addresses = dict(DEFAULT_ADDRESSES)
        self.save()
        log.info("Addresses reset to defaults")
