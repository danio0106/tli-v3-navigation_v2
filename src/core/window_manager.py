import sys
from typing import Optional, Tuple

from src.utils.logger import log
from src.utils.constants import GAME_WINDOW_TITLE

IS_WINDOWS = sys.platform == "win32"

UE4_WINDOW_CLASS = "UnrealWindow"

if IS_WINDOWS:
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
    except Exception:
        user32 = None
else:
    user32 = None


class WindowManager:
    def __init__(self):
        self._hwnd: Optional[int] = None
        self._window_title = GAME_WINDOW_TITLE
        self._target_pid: Optional[int] = None

    @property
    def hwnd(self) -> Optional[int]:
        return self._hwnd

    @property
    def is_found(self) -> bool:
        return self._hwnd is not None

    def set_target_pid(self, pid: int):
        self._target_pid = pid

    def find_window(self, title: Optional[str] = None) -> bool:
        if title:
            self._window_title = title

        if not IS_WINDOWS or not user32:
            log.warning("Window management requires Windows")
            return False

        try:
            hwnd = self._find_by_class_and_pid()
            if hwnd:
                self._hwnd = hwnd
                log.info(f"Found game window by class '{UE4_WINDOW_CLASS}' + PID {self._target_pid} (HWND: {hwnd})")
                return True

            hwnd = self._find_by_class_only()
            if hwnd:
                self._hwnd = hwnd
                log.info(f"Found game window by class '{UE4_WINDOW_CLASS}' (HWND: {hwnd})")
                return True

            hwnd = user32.FindWindowW(None, self._window_title)
            if hwnd:
                self._hwnd = hwnd
                log.info(f"Found game window by title: '{self._window_title}' (HWND: {hwnd})")
                return True

            alternate_titles = [
                "Torchlight: Infinite",
                "Torchlight Infinite",
            ]
            for alt_title in alternate_titles:
                if alt_title == self._window_title:
                    continue
                hwnd = user32.FindWindowW(None, alt_title)
                if hwnd:
                    self._hwnd = hwnd
                    self._window_title = alt_title
                    log.info(f"Found game window by alternate title: '{alt_title}' (HWND: {hwnd})")
                    return True

            log.warning(f"Game window not found (tried class '{UE4_WINDOW_CLASS}', title '{self._window_title}')")
            self._hwnd = None
            return False
        except Exception as e:
            log.error(f"Error finding window: {e}")
            return False

    def _find_by_class_and_pid(self) -> Optional[int]:
        if not self._target_pid:
            return None

        try:
            EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            found_hwnd = [None]

            def callback(hwnd, lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                class_buf = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, class_buf, 64)
                if class_buf.value != UE4_WINDOW_CLASS:
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == self._target_pid:
                    found_hwnd[0] = hwnd
                    return False
                return True

            user32.EnumWindows(EnumWindowsProc(callback), 0)
            return found_hwnd[0]
        except Exception:
            return None

    def _find_by_class_only(self) -> Optional[int]:
        try:
            hwnd = user32.FindWindowW(UE4_WINDOW_CLASS, None)
            if hwnd and user32.IsWindowVisible(hwnd):
                return hwnd
            return None
        except Exception:
            return None

    def get_window_rect(self) -> Optional[Tuple[int, int, int, int]]:
        if not IS_WINDOWS or not user32 or not self._hwnd:
            return None

        try:
            rect = wintypes.RECT()
            user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
            return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            return None

    def get_client_rect(self) -> Optional[Tuple[int, int, int, int]]:
        if not IS_WINDOWS or not user32 or not self._hwnd:
            return None

        try:
            rect = wintypes.RECT()
            user32.GetClientRect(self._hwnd, ctypes.byref(rect))
            point = wintypes.POINT(rect.left, rect.top)
            user32.ClientToScreen(self._hwnd, ctypes.byref(point))
            return (point.x, point.y, point.x + rect.right, point.y + rect.bottom)
        except Exception:
            return None

    def focus_window(self) -> bool:
        if not IS_WINDOWS or not user32 or not self._hwnd:
            return False

        try:
            user32.SetForegroundWindow(self._hwnd)
            return True
        except Exception:
            return False

    def is_foreground(self) -> bool:
        if not IS_WINDOWS or not user32 or not self._hwnd:
            return False

        try:
            return user32.GetForegroundWindow() == self._hwnd
        except Exception:
            return False

    def get_window_size(self) -> Optional[Tuple[int, int]]:
        rect = self.get_client_rect()
        if rect:
            return (rect[2] - rect[0], rect[3] - rect[1])
        return None

    def screen_to_game(self, x: int, y: int) -> Optional[Tuple[int, int]]:
        rect = self.get_client_rect()
        if not rect:
            return None
        return (x - rect[0], y - rect[1])

    def game_to_screen(self, x: int, y: int) -> Optional[Tuple[int, int]]:
        rect = self.get_client_rect()
        if not rect:
            return None
        return (x + rect[0], y + rect[1])
