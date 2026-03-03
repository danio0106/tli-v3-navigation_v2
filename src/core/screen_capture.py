from __future__ import annotations

import sys
import time
from typing import Optional, TYPE_CHECKING

from src.utils.logger import log

IS_WINDOWS = sys.platform == "win32"

if TYPE_CHECKING:
    import numpy as np

try:
    import numpy as np
except ImportError:
    np = None

try:
    import mss
except ImportError:
    mss = None


class ScreenCapture:
    def __init__(self, window_manager):
        self.window_manager = window_manager
        self._local = None
        if IS_WINDOWS and mss is not None:
            import threading
            self._local = threading.local()

    @property
    def mss_instance(self):
        if self._local is None or mss is None:
            return None
        if not hasattr(self._local, 'sct'):
            self._local.sct = mss.mss()
        return self._local.sct

    def capture_window(self):
        if not IS_WINDOWS or mss is None or np is None:
            return None
        sct = self.mss_instance
        if sct is None:
            return None

        client_rect = self.window_manager.get_client_rect()
        if client_rect is None:
            log.warning("Failed to get client rect for window capture")
            return None

        left, top, right, bottom = client_rect
        width = right - left
        height = bottom - top

        try:
            monitor = {"left": left, "top": top, "width": width, "height": height}
            screenshot = sct.grab(monitor)
            frame = np.array(screenshot)
            frame = frame[:, :, :3]
            return frame
        except Exception as e:
            log.error(f"Error capturing window: {e}")
            return None

    def capture_region(self, x: int, y: int, w: int, h: int):
        if not IS_WINDOWS or mss is None or np is None:
            return None
        sct = self.mss_instance
        if sct is None:
            return None

        client_rect = self.window_manager.get_client_rect()
        if client_rect is None:
            log.warning("Failed to get client rect for region capture")
            return None

        left, top, _, _ = client_rect
        monitor_left = left + x
        monitor_top = top + y

        try:
            monitor = {"left": monitor_left, "top": monitor_top, "width": w, "height": h}
            screenshot = self.mss_instance.grab(monitor)
            frame = np.array(screenshot)
            frame = frame[:, :, :3]
            return frame
        except Exception as e:
            log.error(f"Error capturing region: {e}")
            return None

    def capture_multi_frame(self, count: int = 25, delay_ms: int = 33):
        if not IS_WINDOWS or self.mss_instance is None or np is None:
            return None

        frames = []
        delay_sec = delay_ms / 1000.0

        for i in range(count):
            frame = self.capture_window()
            if frame is None:
                log.error(f"Failed to capture frame {i + 1}/{count}")
                return None
            frames.append(frame)
            if i < count - 1:
                time.sleep(delay_sec)

        try:
            frames_array = np.stack(frames, axis=0)
            median_frame = np.median(frames_array, axis=0).astype(np.uint8)
            return median_frame
        except Exception as e:
            log.error(f"Error processing multi-frame capture: {e}")
            return None

    def capture_multi_frame_region(
        self, x: int, y: int, w: int, h: int, count: int = 25, delay_ms: int = 33
    ):
        if not IS_WINDOWS or self.mss_instance is None or np is None:
            return None

        frames = []
        delay_sec = delay_ms / 1000.0

        for i in range(count):
            frame = self.capture_region(x, y, w, h)
            if frame is None:
                log.error(f"Failed to capture region frame {i + 1}/{count}")
                return None
            frames.append(frame)
            if i < count - 1:
                time.sleep(delay_sec)

        try:
            frames_array = np.stack(frames, axis=0)
            median_frame = np.median(frames_array, axis=0).astype(np.uint8)
            return median_frame
        except Exception as e:
            log.error(f"Error processing multi-frame region capture: {e}")
            return None
