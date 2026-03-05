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

# import_name -> pip_name
REQUIRED = [
    ("PySide6", "PySide6"),
    ("pymem", "pymem"),
    ("psutil", "psutil"),
    ("cv2", "opencv-python-headless"),
    ("numpy", "numpy"),
    ("mss", "mss"),
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


def _ensure_elevated_windows() -> int:
    if os.name != "nt" or _is_admin_windows():
        return 0
    try:
        import ctypes

        params = f'"{__file__}"'
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, str(ROOT), 1)
        return 0 if int(rc) > 32 else 1
    except Exception:
        return 1


def _find_missing_packages():
    missing = []
    for import_name, pip_name in REQUIRED:
        if importlib.util.find_spec(import_name) is None:
            missing.append(pip_name)
    return missing


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
    elev_code = _ensure_elevated_windows()
    if elev_code != 0:
        print("[Launcher] ERROR: Administrator privileges are required.")
        return elev_code
    if os.name == "nt" and not _is_admin_windows():
        # Elevated instance has been requested; current non-admin process exits.
        return 0

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
