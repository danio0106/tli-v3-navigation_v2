import sys
import os
import platform

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.logger import log


def _launch_tk_app():
    from src.gui.app import BotApp
    app = BotApp()
    app.run()


def _launch_qt_app_or_fallback() -> int:
    """Launch Qt app without automatic fallback to legacy tkinter."""
    try:
        from src.gui_qt.app import run_qt_app
        return int(run_qt_app())
    except Exception as e:
        log.error(f"[GUI] Qt launch failed: {e}")
        log.error("[GUI] Legacy tkinter fallback is disabled in normal runtime")
        return 1


def position_console_window():
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.MoveWindow(hwnd, 10, 950, 580, 170, True)
    except Exception as e:
        log.warning(f"Failed to position console window: {e}")


def _set_console_visibility(show: bool):
    if platform.system() != "Windows":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            # 5=SW_SHOW, 0=SW_HIDE
            ctypes.windll.user32.ShowWindow(hwnd, 5 if show else 0)
    except Exception as e:
        log.warning(f"Failed to set console visibility: {e}")


def main():
    hide_console = os.environ.get("TLI_HIDE_CONSOLE", "1").strip().lower() not in {"0", "false", "no", "off"}
    if hide_console:
        _set_console_visibility(False)
    else:
        position_console_window()

    from src.utils.constants import APP_NAME, APP_VERSION
    log.info(f"=== {APP_NAME} {APP_VERSION} starting ===")
    backend = os.environ.get("TLI_GUI_BACKEND", "qt").strip().lower()
    allow_legacy_tk = os.environ.get("TLI_ALLOW_LEGACY_TK", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }
    log.info(f"[GUI] Backend selected: {backend}")

    if backend == "tk":
        if allow_legacy_tk:
            log.warning("[GUI] Launching legacy tkinter (explicitly enabled)")
            _launch_tk_app()
            return 0
        log.warning("[GUI] Ignoring TLI_GUI_BACKEND=tk (legacy tkinter disabled)")
        log.warning("[GUI] To force legacy tkinter temporarily, set TLI_ALLOW_LEGACY_TK=1")

    return _launch_qt_app_or_fallback()


if __name__ == "__main__":
    main()
