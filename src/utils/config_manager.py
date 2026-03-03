import json
import os
from typing import Any

from src.utils.constants import CONFIG_FILE, DEFAULT_SETTINGS
from src.utils.logger import log


class ConfigManager:
    _instance = None

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
                log.info(f"Config loaded from {self._path}")
            except Exception as e:
                log.error(f"Failed to load config: {e}")
        else:
            log.info("No config file found, using defaults")
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
        self._config[key] = value
        self.save()

    def get_all(self) -> dict:
        return dict(self._config)

    def reset(self):
        self._config = dict(DEFAULT_SETTINGS)
        self.save()
        log.info("Config reset to defaults")
