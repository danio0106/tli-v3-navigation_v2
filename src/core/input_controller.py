import sys
import time
import random
from typing import Optional, Tuple

from src.utils.logger import log

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        MOUSEEVENTF_MOVE = 0x0001
        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        MOUSEEVENTF_RIGHTDOWN = 0x0008
        MOUSEEVENTF_RIGHTUP = 0x0010
        MOUSEEVENTF_ABSOLUTE = 0x8000

        WM_KEYDOWN = 0x0100
        WM_KEYUP = 0x0101
        WM_CHAR = 0x0102
        WM_LBUTTONDOWN = 0x0201
        WM_LBUTTONUP = 0x0202
        WM_RBUTTONDOWN = 0x0204
        WM_RBUTTONUP = 0x0205
        WM_MOUSEMOVE = 0x0200
        MK_LBUTTON = 0x0001
        MK_RBUTTON = 0x0002

        INPUT_MOUSE = 0
        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002

        WM_NAMES = {
            WM_KEYDOWN: "WM_KEYDOWN", WM_KEYUP: "WM_KEYUP",
            WM_LBUTTONDOWN: "WM_LBUTTONDOWN", WM_LBUTTONUP: "WM_LBUTTONUP",
            WM_RBUTTONDOWN: "WM_RBUTTONDOWN", WM_RBUTTONUP: "WM_RBUTTONUP",
            WM_MOUSEMOVE: "WM_MOUSEMOVE",
        }

        VK_MAP = {
            "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45,
            "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A,
            "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F,
            "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54,
            "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59,
            "z": 0x5A,
            "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
            "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
            "space": 0x20, "enter": 0x0D, "escape": 0x1B, "tab": 0x09,
            "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
            "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
            "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
            "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
        }

        user32 = ctypes.windll.user32
        HAS_INPUT = True
    except Exception as e:
        log.warning(f"Windows input subsystem failed to initialize: {e}")
        HAS_INPUT = False
else:
    HAS_INPUT = False

INPUT_MODE_VIRTUAL_POST = "virtual_post"
INPUT_MODE_VIRTUAL_SEND = "virtual_send"
INPUT_MODE_HARDWARE = "hardware"
VALID_INPUT_MODES = [INPUT_MODE_VIRTUAL_POST, INPUT_MODE_VIRTUAL_SEND, INPUT_MODE_HARDWARE]


class InputController:
    def __init__(self, input_mode: str = INPUT_MODE_HARDWARE):
        self._humanize = True
        self._min_delay = 0.005
        self._max_delay = 0.015
        self._target_hwnd = None
        self._debug_input = True
        self._input_mode = input_mode if input_mode in VALID_INPUT_MODES else INPUT_MODE_HARDWARE
        self._msg_count = 0
        log.info(f"[Input] Initialized with mode: {self._input_mode}")

    @property
    def input_mode(self) -> str:
        return self._input_mode

    @input_mode.setter
    def input_mode(self, mode: str):
        if mode not in VALID_INPUT_MODES:
            log.warning(f"[Input] Invalid mode '{mode}', keeping '{self._input_mode}'")
            return
        old = self._input_mode
        self._input_mode = mode
        log.info(f"[Input] Mode changed: {old} -> {mode}")

    @property
    def debug_input(self) -> bool:
        return self._debug_input

    @debug_input.setter
    def debug_input(self, enabled: bool):
        self._debug_input = enabled
        log.info(f"[Input] Debug logging: {'ON' if enabled else 'OFF'}")

    def set_target_window(self, hwnd):
        self._target_hwnd = hwnd
        log.info(f"[Input] Target window set: HWND={hwnd}, mode={self._input_mode}")
        if self._input_mode in (INPUT_MODE_VIRTUAL_POST, INPUT_MODE_VIRTUAL_SEND):
            log.info("[Input] Virtual mode: coordinates must be CLIENT AREA (not screen)")
        else:
            log.info("[Input] Hardware mode: will focus window before each input")

    def set_humanize(self, enabled: bool, min_delay: float = 0.005, max_delay: float = 0.015):
        self._humanize = enabled
        self._min_delay = min_delay
        self._max_delay = max_delay

    @property
    def use_virtual(self) -> bool:
        return (self._input_mode in (INPUT_MODE_VIRTUAL_POST, INPUT_MODE_VIRTUAL_SEND)
                and self._target_hwnd is not None and HAS_INPUT)

    def _delay(self):
        if self._humanize:
            time.sleep(random.uniform(self._min_delay, self._max_delay))

    def _jitter(self, value: int, amount: int = 2) -> int:
        if self._humanize:
            return value + random.randint(-amount, amount)
        return value

    def _send_msg(self, msg: int, wparam: int, lparam: int, label: str = ""):
        if not HAS_INPUT or not self._target_hwnd:
            return False

        self._msg_count += 1
        msg_name = WM_NAMES.get(msg, f"0x{msg:04X}") if IS_WINDOWS else f"0x{msg:04X}"

        if self._input_mode == INPUT_MODE_VIRTUAL_SEND:
            result = user32.SendMessageW(self._target_hwnd, msg, wparam, lparam)
        else:
            result = user32.PostMessageW(self._target_hwnd, msg, wparam, lparam)

        if self._debug_input:
            fn = "SendMessageW" if self._input_mode == INPUT_MODE_VIRTUAL_SEND else "PostMessageW"
            lp_x = lparam & 0xFFFF
            lp_y = (lparam >> 16) & 0xFFFF
            coord_str = ""
            if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MOUSEMOVE):
                coord_str = f" xy=({lp_x},{lp_y})"
            log.info(
                f"[Input] #{self._msg_count} {fn}(HWND={self._target_hwnd}, "
                f"{msg_name}, wP=0x{wparam:X}, lP=0x{lparam:X}{coord_str}) "
                f"-> {result} {label}"
            )

        if self._input_mode == INPUT_MODE_VIRTUAL_POST and result == 0:
            err = ctypes.GetLastError() if IS_WINDOWS else 0
            log.warning(f"[Input] PostMessageW FAILED for {msg_name}! GetLastError={err}")
            return False
        return True

    def _focus_game(self):
        if not HAS_INPUT or not self._target_hwnd:
            return
        try:
            user32.SetForegroundWindow(self._target_hwnd)
            time.sleep(0.05)
        except Exception:
            pass

    def _client_to_screen(self, x: int, y: int) -> Tuple[int, int]:
        if not HAS_INPUT or not self._target_hwnd:
            return (x, y)
        point = ctypes.wintypes.POINT(x, y)
        if user32.ClientToScreen(self._target_hwnd, ctypes.byref(point)):
            return (point.x, point.y)
        return (x, y)

    def move_mouse(self, x: int, y: int):
        if self.use_virtual:
            self._virtual_move_mouse(x, y)
            return

        if not HAS_INPUT:
            log.debug(f"[SIM] Mouse move to ({x}, {y})")
            return

        self._focus_game()
        sx, sy = self._client_to_screen(x, y)
        sx = self._jitter(sx)
        sy = self._jitter(sy)
        ctypes.windll.user32.SetCursorPos(sx, sy)
        if self._debug_input:
            log.info(f"[Input] Hardware mouse move: client({x},{y}) -> screen({sx},{sy})")
        self._delay()

    def click(self, x: Optional[int] = None, y: Optional[int] = None, button: str = "left"):
        if self.use_virtual:
            if x is not None and y is not None:
                self._virtual_click(x, y, button)
            else:
                self._virtual_click_at_cursor(button)
            return

        if not HAS_INPUT:
            log.debug(f"[SIM] Click {button}")
            return

        self._focus_game()
        if x is not None and y is not None:
            self.move_mouse(x, y)

        if button == "left":
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            self._delay()
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        elif button == "right":
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            self._delay()
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)

        if self._debug_input:
            log.info(f"[Input] Hardware click {button} at ({x},{y})")
        self._delay()

    def press_key(self, key: str):
        if self.use_virtual:
            self._virtual_press_key(key)
            return

        if not HAS_INPUT:
            log.debug(f"[SIM] Key press: {key}")
            return

        self._focus_game()
        vk = VK_MAP.get(key.lower())
        if vk is None:
            log.warning(f"Unknown key: {key}")
            return

        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        self._delay()
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        if self._debug_input:
            log.info(f"[Input] Hardware key '{key}' (VK=0x{vk:02X})")
        self._delay()

    def hold_key(self, key: str, duration: float = 0.1):
        if self.use_virtual:
            self._virtual_hold_key(key, duration)
            return

        if not HAS_INPUT:
            log.debug(f"[SIM] Key hold: {key} for {duration}s")
            return

        self._focus_game()
        vk = VK_MAP.get(key.lower())
        if vk is None:
            return

        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        time.sleep(duration)
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        if self._debug_input:
            log.info(f"[Input] Hardware hold '{key}' (VK=0x{vk:02X}) for {duration}s")
        self._delay()

    def move_mouse_smooth(self, x: int, y: int, steps: int = 10):
        if self.use_virtual:
            for i in range(1, steps + 1):
                t = i / steps
                t = t * t * (3 - 2 * t)
                self._virtual_move_mouse(x, y)
                time.sleep(0.01)
            return

        if not HAS_INPUT:
            log.debug(f"[SIM] Smooth move to ({x}, {y})")
            return

        self._focus_game()
        sx, sy = self._client_to_screen(x, y)
        point = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        start_x, start_y = point.x, point.y

        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)
            cx = int(start_x + (sx - start_x) * t)
            cy = int(start_y + (sy - start_y) * t)
            ctypes.windll.user32.SetCursorPos(
                self._jitter(cx, 1), self._jitter(cy, 1)
            )
            time.sleep(0.01)

    def move_click(self, x: int, y: int, button: str = "left", smooth: bool = False):
        if self.use_virtual:
            self._virtual_click(x, y, button)
            return

        self._focus_game()
        if smooth:
            self.move_mouse_smooth(x, y)
        else:
            self.move_mouse(x, y)
        self._delay()
        self.click(button=button)

    def type_text(self, text: str):
        for char in text:
            self.press_key(char)
            time.sleep(random.uniform(0.03, 0.08))

    def _virtual_press_key(self, key: str):
        if not HAS_INPUT or not self._target_hwnd:
            log.debug(f"[SIM] Virtual key press: {key}")
            return
        vk = VK_MAP.get(key.lower())
        if vk is None:
            log.warning(f"Unknown key: {key}")
            return
        scan_code = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        lparam_down = (scan_code << 16) | 1
        lparam_up = (scan_code << 16) | 1 | (1 << 30) | (1 << 31)
        self._send_msg(WM_KEYDOWN, vk, lparam_down, label=f"key='{key}' scan=0x{scan_code:02X}")
        self._delay()
        self._send_msg(WM_KEYUP, vk, lparam_up, label=f"key='{key}'")
        self._delay()

    def _virtual_hold_key(self, key: str, duration: float = 0.1):
        if not HAS_INPUT or not self._target_hwnd:
            log.debug(f"[SIM] Virtual key hold: {key} for {duration}s")
            return
        vk = VK_MAP.get(key.lower())
        if vk is None:
            return
        scan_code = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        lparam_down = (scan_code << 16) | 1
        lparam_up = (scan_code << 16) | 1 | (1 << 30) | (1 << 31)
        self._send_msg(WM_KEYDOWN, vk, lparam_down, label=f"hold key='{key}' scan=0x{scan_code:02X}")
        time.sleep(duration)
        self._send_msg(WM_KEYUP, vk, lparam_up, label=f"hold key='{key}' release")
        self._delay()

    def _virtual_click(self, x: int, y: int, button: str = "left"):
        if not HAS_INPUT or not self._target_hwnd:
            log.debug(f"[SIM] Virtual click at ({x}, {y})")
            return
        x = self._jitter(x)
        y = self._jitter(y)
        lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        if button == "left":
            self._send_msg(WM_LBUTTONDOWN, MK_LBUTTON, lparam, label=f"L-click ({x},{y})")
            self._delay()
            self._send_msg(WM_LBUTTONUP, 0, lparam, label=f"L-release ({x},{y})")
        elif button == "right":
            self._send_msg(WM_RBUTTONDOWN, MK_RBUTTON, lparam, label=f"R-click ({x},{y})")
            self._delay()
            self._send_msg(WM_RBUTTONUP, 0, lparam, label=f"R-release ({x},{y})")
        self._delay()

    def _virtual_click_at_cursor(self, button: str = "left"):
        if not HAS_INPUT or not self._target_hwnd:
            log.debug(f"[SIM] Virtual click {button} at last known pos")
            return
        lparam = 0
        if button == "left":
            self._send_msg(WM_LBUTTONDOWN, MK_LBUTTON, lparam, label="L-click@cursor")
            self._delay()
            self._send_msg(WM_LBUTTONUP, 0, lparam, label="L-release@cursor")
        elif button == "right":
            self._send_msg(WM_RBUTTONDOWN, MK_RBUTTON, lparam, label="R-click@cursor")
            self._delay()
            self._send_msg(WM_RBUTTONUP, 0, lparam, label="R-release@cursor")
        self._delay()

    def _virtual_move_mouse(self, x: int, y: int):
        if not HAS_INPUT or not self._target_hwnd:
            log.debug(f"[SIM] Virtual mouse move to ({x}, {y})")
            return
        x = self._jitter(x)
        y = self._jitter(y)
        lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        self._send_msg(WM_MOUSEMOVE, 0, lparam, label=f"move ({x},{y})")
        self._delay()

    def virtual_press_key(self, key: str):
        self._virtual_press_key(key)

    def virtual_hold_key(self, key: str, duration: float = 0.1):
        self._virtual_hold_key(key, duration)

    def virtual_click(self, x: int, y: int, button: str = "left"):
        self._virtual_click(x, y, button)

    def virtual_move_mouse(self, x: int, y: int):
        self._virtual_move_mouse(x, y)

    def get_status(self) -> dict:
        return {
            "mode": self._input_mode,
            "hwnd": self._target_hwnd,
            "has_input": HAS_INPUT,
            "use_virtual": self.use_virtual,
            "debug": self._debug_input,
            "humanize": self._humanize,
            "msg_count": self._msg_count,
        }
