import importlib
import threading
from typing import Any, Callable, Dict

from src.core.scanner import UE4Scanner
from src.utils.logger import log


class NativeRuntimeManager:
    """Optional native runtime loader with fail-open Python fallback."""

    def __init__(self, config):
        self._config = config
        self._lock = threading.Lock()
        self._module = None
        self._module_name = ""
        self._init_attempted = False
        self._last_error = ""
        self._scanner_backend = "python"
        self._overlay_backend = "python"
        self._overlay_worker_alive = False

    def initialize(self) -> None:
        with self._lock:
            if self._init_attempted:
                return
            self._init_attempted = True

        if not bool(self._config.get("native_runtime_enabled", False)):
            return

        preferred = str(self._config.get("native_preferred_module", "tli_native") or "tli_native").strip()
        candidates = [c for c in (preferred, "src.native.tli_native", "tli_native") if c]

        last_error = ""
        for module_name in candidates:
            try:
                module = importlib.import_module(module_name)
                with self._lock:
                    self._module = module
                    self._module_name = module_name
                    self._last_error = ""
                log.info(f"[Native] Loaded module: {module_name}")
                return
            except Exception as exc:
                last_error = str(exc)

        with self._lock:
            self._module = None
            self._module_name = ""
            self._last_error = last_error or "native module import failed"

        if bool(self._config.get("native_strict_mode", False)):
            log.error(f"[Native] Strict mode requested, module load failed: {self._last_error}")
        else:
            log.warning(f"[Native] Module unavailable, falling back to Python runtime: {self._last_error}")

    def create_scanner(self, memory, addresses, progress_callback: Callable[[str], None]) -> Any:
        self.initialize()

        runtime_enabled = bool(self._config.get("native_runtime_enabled", False))
        scanner_enabled = bool(self._config.get("native_scanner_enabled", False))
        if runtime_enabled and scanner_enabled:
            with self._lock:
                module = self._module
                module_name = self._module_name
            if module is not None and hasattr(module, "create_scanner"):
                try:
                    scanner = module.create_scanner(memory, addresses, progress_callback)
                    if scanner is not None:
                        with self._lock:
                            self._scanner_backend = f"native:{module_name}"
                            self._last_error = ""
                        log.info(f"[Native] Scanner backend active: {module_name}")
                        return scanner
                except Exception as exc:
                    with self._lock:
                        self._last_error = str(exc)
                    log.warning(f"[Native] Native scanner init failed, fallback to Python: {exc}")

        with self._lock:
            self._scanner_backend = "python"
        return UE4Scanner(memory, addresses, progress_callback)

    def set_overlay_worker_state(self, backend: str, alive: bool) -> None:
        with self._lock:
            self._overlay_backend = backend or "python"
            self._overlay_worker_alive = bool(alive)

    def get_status_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "runtime_enabled": bool(self._config.get("native_runtime_enabled", False)),
                "module_loaded": self._module is not None,
                "module_name": self._module_name,
                "scanner_backend": self._scanner_backend,
                "overlay_backend": self._overlay_backend,
                "overlay_worker_alive": self._overlay_worker_alive,
                "last_error": self._last_error,
            }
