import sys
import os
import platform

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.logger import log


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


def main():
    position_console_window()

    from src.utils.constants import APP_NAME, APP_VERSION
    log.info(f"=== {APP_NAME} v{APP_VERSION} starting ===")

    from src.gui.app import BotApp
    app = BotApp()
    app.run()


if __name__ == "__main__":
    main()
