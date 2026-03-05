import json
import os
from typing import Any

from src.utils.constants import CONFIG_FILE, DEFAULT_SETTINGS
from src.utils.logger import log


class ConfigManager:
    _instance = None
    _FORCED_POLICY = {
        "nav_collision_grid_inflate_u": 0.0,
        "nav_collision_grid_gap_bridge_enabled": False,
        "nav_collision_overlay_show_bridges": False,
        "nav_collision_overlay_inflate_debug": False,
    }
    _DEPRECATED_KEYS = {
        "native_runtime_enabled",
        "native_scanner_enabled",
        "native_overlay_worker_enabled",
        "native_strict_mode",
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._config = dict(DEFAULT_SETTINGS)
        self._path = CONFIG_FILE
        self.load()

    def load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r") as f:
                    saved = json.load(f)
                self._config.update(saved)
                pruned = self._prune_deprecated_keys(log_changes=True)
                if pruned or self._enforce_policy(log_changes=True):
                    self.save()
                log.info(f"Config loaded from {self._path}")
            except Exception as e:
                log.error(f"Failed to load config: {e}")
        else:
            log.info("No config file found, using defaults")
            self._prune_deprecated_keys(log_changes=False)
            self._enforce_policy(log_changes=False)
            self.save()

    def save(self):
        try:
            with open(self._path, "w") as f:
                json.dump(self._config, f, indent=2)
            log.debug("Config saved")
        except Exception as e:
            log.error(f"Failed to save config: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def set(self, key: str, value: Any):
        if key in self._FORCED_POLICY:
            forced = self._FORCED_POLICY[key]
            self._config[key] = forced
            log.info(f"[Config] '{key}' is policy-locked → forcing {forced}")
        else:
            self._config[key] = value
        self._prune_deprecated_keys(log_changes=False)
        self._enforce_policy(log_changes=False)
        self.save()

    def get_all(self) -> dict:
        return dict(self._config)

    def reset(self):
        self._config = dict(DEFAULT_SETTINGS)
        self._prune_deprecated_keys(log_changes=False)
        self._enforce_policy(log_changes=False)
        self.save()
        log.info("Config reset to defaults")

    def _prune_deprecated_keys(self, log_changes: bool = False) -> bool:
        changed = False
        for key in self._DEPRECATED_KEYS:
            if key in self._config:
                del self._config[key]
                changed = True
                if log_changes:
                    log.info(f"[Config] Removed deprecated key: {key}")
        return changed

    def _enforce_policy(self, log_changes: bool = False) -> bool:
        changed = False
        for key, forced in self._FORCED_POLICY.items():
            if self._config.get(key) != forced:
                self._config[key] = forced
                changed = True
                if log_changes:
                    log.info(f"[Config] Policy lock applied: {key}={forced}")
        return changed
