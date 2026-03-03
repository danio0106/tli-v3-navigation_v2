"""position_poller.py — Single dedicated 120 Hz player-position reader.

Problem solved
--------------
Previously every navigation system independently walked the player position
pointer chain from game memory:

  • app.py  _position_poll_loop  — 60 Hz, read_chain()  → overlay only
  • RTNavigator _read_pos_direct — 60 Hz, read_chain()  → steering
  • Navigator  _navigate_to_waypoint — 20 Hz, gs.update() → manual nav
  • MapExplorer _player_pos        — 20 Hz, gs.update() → exploration
  • bot_engine (many one-shots)   — read_chain() / gs.update()

The two hot consumers (RTNavigator + overlay feed) were issuing duplicate
reads at the same instant from two threads, producing subtly different
snapshots.  Together they made ~120 memory reads / sec total.

Solution
--------
One background thread reads position once every ~8.33 ms (120 Hz) via the
lightweight read_chain() path.  Every consumer calls get_pos() and reads
the cached (x, y) tuple.  Pointer chain walked exactly once per tick.

120 Hz reasoning
----------------
Pre-consolidation total throughput was 60 + 60 = 120 reads/sec.  Running a
single reader at 120 Hz matches that budget while eliminating the race
between the two old threads.  RTNavigator ticks at 60 Hz — feeding it data
sampled at 120 Hz halves worst-case staleness from ~16 ms to ~8 ms.

Thread-safety
-------------
Python's GIL makes tuple assignment atomic for small objects, so get_pos()
reads the (float, float) tuple without an explicit lock.  Writes happen only
from the single poll thread.  Start/stop bookkeeping uses a lock + Event.
"""

import threading
import time
from typing import Optional, Tuple


class PositionPoller:
    """Dedicated 120 Hz background reader for player world position.

    Usage
    -----
        poller = PositionPoller(game_state)
        poller.start()                       # call once after memory attach
        ...
        x, y = poller.get_pos()             # any thread, any time
        ...
        poller.stop()                        # optional; daemon thread auto-dies
    """

    _INTERVAL = 1.0 / 120.0  # 8.33 ms per tick — 120 Hz

    def __init__(self, game_state):
        self._gs = game_state

        # Written by the poll thread, read by any thread.
        # Tuple assignment is atomic under the GIL — no lock needed for reads.
        self._pos: Tuple[float, float] = (0.0, 0.0)

        self._lock    = threading.Lock()   # for start/stop only
        self._stop    = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self):
        """Start the 60 Hz poll thread.  Safe to call more than once."""
        with self._lock:
            if self._running:
                return
            self._stop.clear()
            self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="PlayerPosPoll"
        )
        self._thread.start()

    def stop(self):
        """Signal the poll thread to exit and wait up to 1 s for it."""
        self._stop.set()
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    # ── Position read ──────────────────────────────────────────────────────

    def get_pos(self) -> Tuple[float, float]:
        """Return the latest sampled player world position.

        Thread-safe.  Returns (0.0, 0.0) before the first successful read
        or when the game is not attached.
        """
        return self._pos

    @property
    def x(self) -> float:
        return self._pos[0]

    @property
    def y(self) -> float:
        return self._pos[1]

    # ── Poll loop ──────────────────────────────────────────────────────────

    def _loop(self):
        gs  = self._gs
        interval = self._INTERVAL  # 8.33 ms
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                x = gs.read_chain("player_x")
                y = gs.read_chain("player_y")
                if x is not None and y is not None:
                    # Atomic tuple write — safe without a lock under CPython's GIL
                    self._pos = (float(x), float(y))
            except Exception:
                pass  # game disconnected / memory read failed — keep last value
            elapsed = time.monotonic() - t0
            rem = interval - elapsed
            if rem > 0.0002:
                time.sleep(rem)
