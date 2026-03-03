"""
One-shot helper — run this while the Pirates dual-interaction UI is visible on screen.

It grabs the portal-icon region (client coords 832,786 55×54) and saves it to
assets/pirates_portal_icon.png so the bot can template-match against it.

Usage:
    python scripts/capture_pirates_template.py

Stand next to the pirates portal so both interaction buttons are visible, then
run the script. It waits 3 s so you can alt-tab back to the game.
"""

import sys
import os
import time

# Allow running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import mss
    import numpy as np
    import cv2
except ImportError as e:
    print(f"ERROR: missing dependency — {e}")
    print("Run:  pip install mss numpy opencv-python-headless")
    sys.exit(1)

try:
    import ctypes
    # Find the game window to get its client-area origin
    hwnd = ctypes.windll.user32.FindWindowW(None, "Torchlight: Infinite")
    if not hwnd:
        print("ERROR: 'Torchlight: Infinite' window not found — is the game running?")
        sys.exit(1)

    from ctypes import wintypes
    rect = wintypes.RECT()
    ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wintypes.POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt))
    client_origin_x, client_origin_y = pt.x, pt.y
except Exception as e:
    print(f"ERROR: could not get window position — {e}")
    sys.exit(1)

# Region in client coords (from user pixel measurements, title-bar (1,31) subtracted)
CLIENT_X, CLIENT_Y, W, H = 832, 786, 55, 54

screen_x = client_origin_x + CLIENT_X
screen_y = client_origin_y + CLIENT_Y

print(f"Game client origin: ({client_origin_x}, {client_origin_y})")
print(f"Capturing screen region: left={screen_x} top={screen_y} w={W} h={H}")
print()
print("Make sure the Pirates dual-interaction UI is visible (both buttons showing).")
print("Capturing in 3 seconds...")
time.sleep(3)

with mss.mss() as sct:
    monitor = {"left": screen_x, "top": screen_y, "width": W, "height": H}
    raw = sct.grab(monitor)
    frame = np.array(raw)[:, :, :3]   # drop alpha, keep BGR

out_path = os.path.join(os.path.dirname(__file__), "..", "assets", "pirates_portal_icon.png")
out_path = os.path.normpath(out_path)

os.makedirs(os.path.dirname(out_path), exist_ok=True)
cv2.imwrite(out_path, frame)

print(f"Template saved to: {out_path}")
print(f"Image size: {frame.shape[1]}×{frame.shape[0]} px")
print(f"Mean RGB: ({frame[:,:,2].mean():.0f}, {frame[:,:,1].mean():.0f}, {frame[:,:,0].mean():.0f})")
print()
print("Done. The bot will now use this template to detect the Pirates portal icon.")
