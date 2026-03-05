import threading
import time
from collections import deque
from typing import Optional, Tuple, Any, Dict


class NativeScannerAdapter:
    """Wrap a scanner instance with a high-rate cached position feed + metrics.

    The adapter preserves the full scanner surface via attribute delegation while
    overriding hot-path position reads to return cached samples captured at an
    internal 120 Hz cadence.
    """

    _TARGET_HZ = 120.0
    _TARGET_DT = 1.0 / _TARGET_HZ

    def __init__(self, scanner: Any):
        self._scanner = scanner
        self._lock = threading.RLock()

        self._last_pos: Optional[Tuple[float, float]] = None
        self._last_pos_ts: float = 0.0

        self._interval_samples: deque = deque(maxlen=240)
        self._last_sample_ts: float = 0.0

        self._sample_count: int = 0
        self._stale_count: int = 0

        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="NativePos120Hz",
        )
        self._thread.start()

    def __getattr__(self, name: str):
        return getattr(self._scanner, name)

    def _loop(self):
        while self._running:
            t0 = time.monotonic()
            pos = None
            try:
                if hasattr(self._scanner, "read_player_xy"):
                    pos = self._scanner.read_player_xy()
            except Exception:
                pos = None
            now = time.monotonic()

            with self._lock:
                if pos is not None:
                    self._last_pos = (float(pos[0]), float(pos[1]))
                    self._last_pos_ts = now
                    self._sample_count += 1
                    if self._last_sample_ts > 0.0:
                        self._interval_samples.append(now - self._last_sample_ts)
                    self._last_sample_ts = now
                else:
                    self._stale_count += 1

            elapsed = time.monotonic() - t0
            sleep_for = self._TARGET_DT - elapsed
            if sleep_for > 0.0005:
                time.sleep(sleep_for)

    def _read_player_xy(self) -> Optional[Tuple[float, float]]:
        with self._lock:
            if self._last_pos is not None:
                return self._last_pos
        if hasattr(self._scanner, "read_player_xy"):
            return self._scanner.read_player_xy()
        return None

    def get_native_metrics(self) -> Dict[str, float]:
        with self._lock:
            intervals = list(self._interval_samples)
            stale_count = self._stale_count
            sample_count = self._sample_count
            age_ms = (time.monotonic() - self._last_pos_ts) * 1000.0 if self._last_pos_ts > 0.0 else 0.0

        hz = 0.0
        jitter_ms = 0.0
        if intervals:
            avg_dt = sum(intervals) / len(intervals)
            if avg_dt > 0.0:
                hz = 1.0 / avg_dt
            jitter = sum(abs(dt - self._TARGET_DT) for dt in intervals) / len(intervals)
            jitter_ms = jitter * 1000.0

        return {
            "hz": round(hz, 2),
            "jitter_ms": round(jitter_ms, 3),
            "stale_frames": float(stale_count),
            "samples": float(sample_count),
            "age_ms": round(age_ms, 2),
        }

    def cancel(self):
        self._running = False
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=0.5)
        if hasattr(self._scanner, "cancel"):
            self._scanner.cancel()

    def __del__(self):
        self._running = False
