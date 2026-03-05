import importlib
import threading
from typing import Any, Callable, Dict

from src.core.native_scanner_adapter import NativeScannerAdapter
from src.utils.logger import log


class NativeRuntimeManager:
    """Native-only runtime loader (fail-fast, no Python fallback)."""

    def __init__(self, config):
        self._config = config
        self._lock = threading.Lock()
        self._module = None
        self._module_name = ""
        self._init_attempted = False
        self._last_error = ""
        self._scanner_backend = "native:uninitialized"
        self._overlay_backend = "native:pending"
        self._overlay_worker_alive = False
        self._scanner_obj = None

    def initialize(self) -> None:
        with self._lock:
            if self._init_attempted:
                return
            self._init_attempted = True

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

        log.error(f"[Native] Module load failed: {self._last_error}")
        raise RuntimeError(f"native module import failed: {self._last_error}")

    def create_scanner(self, memory, addresses, progress_callback: Callable[[str], None]) -> Any:
        self.initialize()

        with self._lock:
            module = self._module
            module_name = self._module_name
        if module is None:
            self._last_error = "native module not loaded"
            raise RuntimeError("native module not loaded")
        if not hasattr(module, "create_scanner"):
            self._last_error = "native module missing create_scanner"
            raise RuntimeError("native module missing create_scanner")

        # Phase-B transition: scanner construction responsibility is moved to
        # runtime manager. Native module no longer imports Python scanner
        # classes directly, while map-cycle behavior remains stable until full
        # native scanner methods are ported.
        backend_scanner = None
        try:
            scanner_mod = importlib.import_module("src.core.scanner")
            scanner_cls = getattr(scanner_mod, "UE4Scanner")
            backend_scanner = scanner_cls(memory, addresses, progress_callback)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            raise RuntimeError(f"python scanner backend init failed: {exc}") from exc

        try:
            scanner = module.create_scanner(memory, addresses, progress_callback, backend_scanner)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            raise RuntimeError(f"native scanner init failed: {exc}") from exc

        if scanner is None:
            self._last_error = "native create_scanner returned null"
            raise RuntimeError("native create_scanner returned null")

        # Strict contract for production runtime map cycle.
        # Keep this list focused on APIs used by core bot flow (attach -> map run -> return),
        # not debug-only utilities.
        required_scanner_methods = (
            "scan_dump_chain",
            "scan_fnamepool",
            "scan_gobjects",
            "set_cached_gworld_static",
            "clear_fightmgr_cache",
            "check_chain_valid",
            "get_gworld_ptr",
            "read_player_xy",
            "read_zone_name",
            "read_real_zone_name",
            "get_typed_events",
            "get_monster_entities",
            "count_nearby_monsters",
            "get_carjack_truck_position",
            "get_carjack_guard_positions",
            "get_nearby_interactive_items",
            "scan_boss_room",
            "read_minimap_visited_positions",
            "get_nav_collision_markers",
            "get_fightmgr_ptr",
            "find_object_by_name",
            "cancel",
        )
        missing = [name for name in required_scanner_methods if not hasattr(scanner, name)]
        if missing:
            miss = ", ".join(missing)
            self._last_error = f"native scanner missing required API: {miss}"
            raise RuntimeError(f"native scanner missing required API: {miss}")

        # Optional diagnostics/UI helpers: warn only, do not block runtime.
        optional_scanner_methods = (
            "read_player_hp",
            "set_fnamepool_addr",
            "_read_truck_guard_roster",
        )
        missing_optional = [name for name in optional_scanner_methods if not hasattr(scanner, name)]
        if missing_optional:
            log.warning(
                "[Native] Scanner optional diagnostics API missing: "
                + ", ".join(missing_optional)
            )

        wrapped_scanner = NativeScannerAdapter(scanner)

        with self._lock:
            self._scanner_backend = f"native:{module_name}"
            self._scanner_obj = wrapped_scanner
            self._last_error = ""
        log.info(f"[Native] Scanner backend active: {module_name}")
        return wrapped_scanner

    def set_overlay_worker_state(self, backend: str, alive: bool) -> None:
        with self._lock:
            self._overlay_backend = backend or "native:pending"
            self._overlay_worker_alive = bool(alive)

    def get_status_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            metrics = {}
            if self._scanner_obj is not None and hasattr(self._scanner_obj, "get_native_metrics"):
                try:
                    metrics = self._scanner_obj.get_native_metrics() or {}
                except Exception:
                    metrics = {}
            return {
                "runtime_enabled": True,
                "module_loaded": self._module is not None,
                "module_name": self._module_name,
                "scanner_backend": self._scanner_backend,
                "overlay_backend": self._overlay_backend,
                "overlay_worker_alive": self._overlay_worker_alive,
                "last_error": self._last_error,
                "scanner_metrics": metrics,
            }
