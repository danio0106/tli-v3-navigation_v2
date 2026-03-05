import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / "main.py"
STATE_DIR = ROOT / "data"
STATE_FILE = STATE_DIR / "launcher_state.json"

if str(ROOT) not in sys.path:
    # Running as `python scripts/fast_launcher.py` sets sys.path[0] to `scripts/`.
    # Add project root so `src.native.tli_native` can be discovered reliably.
    sys.path.insert(0, str(ROOT))

# import_name -> pip_name
REQUIRED = [
    ("PySide6", "PySide6"),
    ("pymem", "pymem"),
    ("psutil", "psutil"),
    ("cv2", "opencv-python-headless"),
    ("numpy", "numpy"),
    ("mss", "mss"),
    # Native build toolchain (v5.71.0+ strict-native workflow).
    ("cmake", "cmake"),
    ("ninja", "ninja"),
    ("pybind11", "pybind11"),
    ("scikit_build_core", "scikit-build-core"),
]

# Keep startup fast: only check/update periodically, and do it after bot exits.
UPDATE_CHECK_INTERVAL_S = 24 * 60 * 60


def _is_admin_windows() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _is_uac_disabled_windows() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "EnableLUA")
            return int(value) == 0
    except Exception:
        return False


def _ensure_elevated_windows() -> int:
    """
    Return codes:
      0 -> already elevated (or non-Windows)
      1 -> elevation request failed
      2 -> elevation request launched; caller should exit parent process
      3 -> cannot elevate because UAC is disabled and current token is non-admin
    """
    if os.name != "nt" or _is_admin_windows():
        return 0

    if _is_uac_disabled_windows():
        return 3

    try:
        import ctypes

        script_path = str(Path(__file__).resolve())
        args = f'"{script_path}" --elevated-child'
        rc = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            args,
            str(ROOT),
            1,
        )
        return 2 if int(rc) > 32 else 1
    except Exception:
        return 1


def _find_missing_packages():
    missing = []
    for import_name, pip_name in REQUIRED:
        if importlib.util.find_spec(import_name) is None:
            missing.append(pip_name)
    return missing


def _has_native_module() -> bool:
    # Fast path: if a built extension artifact is present in package directory,
    # treat native module as available.
    native_dir = ROOT / "src" / "native"
    try:
        if native_dir.exists() and any(native_dir.glob("tli_native*.pyd")):
            return True
    except Exception:
        pass

    # Strict-native runtime expects one of these import paths to resolve.
    candidates = ("tli_native", "src.native.tli_native")
    for name in candidates:
        try:
            if importlib.util.find_spec(name) is not None:
                return True
        except Exception:
            continue
    return False


def _run_pip_install(packages, upgrade=False):
    if not packages:
        return 0
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
    ]
    if upgrade:
        cmd.append("--upgrade")
    cmd.extend(packages)
    return subprocess.call(cmd, cwd=str(ROOT))


def _read_state():
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_state(state):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def _should_check_updates():
    state = _read_state()
    last = float(state.get("last_update_check_ts", 0.0) or 0.0)
    return (time.time() - last) >= UPDATE_CHECK_INTERVAL_S


def _mark_update_check():
    state = _read_state()
    state["last_update_check_ts"] = time.time()
    _write_state(state)


def _get_outdated_required_packages():
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "list",
        "--outdated",
        "--format=json",
        "--disable-pip-version-check",
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        return []

    try:
        payload = json.loads(proc.stdout.strip() or "[]")
    except Exception:
        return []

    required_names = {pip_name.lower() for _, pip_name in REQUIRED}
    outdated = []
    for item in payload:
        name = str(item.get("name", "")).strip()
        if name.lower() in required_names:
            outdated.append(name)
    return outdated


def _launch_bot():
    return subprocess.call([sys.executable, str(MAIN_PY)], cwd=str(ROOT))


def main():
    if os.name == "nt" and not _is_admin_windows():
        elev_code = _ensure_elevated_windows()
        if elev_code == 2:
            # Parent process exits; elevated child continues launcher flow.
            return 0
        if elev_code == 3:
            print("[Launcher] ERROR: UAC is disabled (EnableLUA=0) and process is not elevated.")
            print("[Launcher] Attach will fail with access denied in this mode.")
            print("[Launcher] Fix one of these:")
            print("  1. Enable UAC (EnableLUA=1), reboot, then approve elevation prompt.")
            print("  2. Run from an Administrator account.")
            print("  3. Run game and bot at same non-admin privilege level.")
            return 1
        if elev_code != 0:
            print("[Launcher] ERROR: Failed to request administrator privileges.")
            print("[Launcher] Please relaunch and approve the UAC prompt.")
            return 1

    if os.name == "nt" and not _is_admin_windows():
        # Should be rare; kept as a safety warning.
        print("[Launcher] Warning: not running as administrator. Attach may fail on some systems.")

    print("[Launcher] Python:", sys.executable)
    print("[Launcher] Quick dependency check...")

    missing = _find_missing_packages()
    if missing:
        print(f"[Launcher] Missing packages detected: {', '.join(missing)}")
        print("[Launcher] Installing missing packages...")
        code = _run_pip_install(missing, upgrade=False)
        if code != 0:
            print("[Launcher] ERROR: Failed to install required dependencies.")
            return code
    else:
        print("[Launcher] All required packages are present.")

    if not _has_native_module():
        print("[Launcher] ERROR: Native module 'tli_native' is not available.")
        print("[Launcher] Strict-native runtime is enabled, so startup cannot continue.")
        print("[Launcher] Next steps:")
        print("  1. Install Visual Studio Build Tools (Desktop development with C++).")
        print("  2. Run: ./scripts/build_native.ps1")
        print("  3. Re-launch the bot.")
        return 1

    print("[Launcher] Starting bot now...")
    bot_code = _launch_bot()

    # Post-run maintenance: keep startup fast by checking/updating after exit.
    if _should_check_updates():
        print("[Launcher] Checking for dependency updates (post-run)...")
        outdated = _get_outdated_required_packages()
        if outdated:
            print(f"[Launcher] Updating: {', '.join(outdated)}")
            _run_pip_install(outdated, upgrade=True)
        _mark_update_check()

    return bot_code


if __name__ == "__main__":
    raise SystemExit(main())
