from __future__ import annotations

import os
import time
from typing import Optional, List

from src.utils.logger import log

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import numpy as np
except ImportError:
    np = None

from src.utils.constants import (
    CARD_SLOTS,
    RARITY_PRIORITY,
    ATTEMPTS_REGION,
    ATTEMPTS_VERIFY_MAX_R,
    ATTEMPTS_VERIFY_MIN_BR_DIFF,
)

ICE_BACKGROUND_MIN = 160
POPPED_UP_INTERIOR_MAX = 45
INACTIVE_TOP_FLOOR = 50

CARD_UI_TEMPLATE_PATH = "assets/card_ui_text.png"
CARD_UI_TEXT_REGION = (1087, 619, 1222, 639)
CARD_UI_MATCH_THRESHOLD = 0.7

CAPTURE_FRAMES = 5
CAPTURE_DELAY_MS = 60


class CardDetector:
    def __init__(self, screen_capture=None, hex_calibrator=None):
        self._screen_capture = screen_capture
        self._hex_calibrator = hex_calibrator
        self._last_result = None

    def detect_cards(self, frame, debug=True) -> dict:
        if cv2 is None or np is None:
            log.warning("[CardDetector] OpenCV/numpy not available")
            return {"active_indices": [], "rarities": {}, "details": {}}

        t_start = time.perf_counter()
        h_img, w_img = frame.shape[:2]
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        log.info("[CardDetector] === Detection Start === frame={}x{}".format(w_img, h_img))

        details = {}
        active_indices = []
        unknown_indices = []
        rarities = {}

        GLOW_SAT_ACTIVE = 70

        for idx in range(12):
            slot = CARD_SLOTS[idx]
            verts_active = self._compute_hex_vertices(slot, "active")
            verts_inactive = self._compute_hex_vertices(slot, "inactive")
            vert_names = ["top", "tr", "br", "bot", "bl", "tl"]

            vert_grays = []
            for vx, vy in verts_active:
                g = self._sample_patch_stats(gray_frame, vx, vy, size=5)["mean"]
                vert_grays.append(g)

            ix, iy = slot["inactive_top"]
            i_gray = self._sample_patch_stats(gray_frame, ix, iy, size=5)["mean"]

            mean_r, mean_g, mean_b, glow_px, glow_hsv = self._sample_glow_chevron_full(frame, hsv_frame, idx)
            glow_sat = glow_hsv["s"]

            ice_count = sum(1 for g in vert_grays if g >= 150)
            on_card_count = sum(1 for g in vert_grays if g < 130)

            glow_val = glow_hsv["v"]
            has_glow = glow_sat > GLOW_SAT_ACTIVE and glow_val > 120
            popped_up = i_gray < POPPED_UP_INTERIOR_MAX

            if ice_count >= 4:
                state = "inactive"
                reason = "{}/6 vertices show ice (>=150)".format(ice_count)
            elif has_glow and on_card_count >= 5:
                state = "active"
                reason = "glow sat={:.0f} val={:.0f} (bright glow) AND {}/6 on card".format(
                    glow_sat, glow_val, on_card_count)
                active_indices.append(idx)
            elif has_glow and popped_up:
                state = "active"
                reason = "glow sat={:.0f} val={:.0f} (bright glow) AND inactive_top {:.1f} < {} (popped up)".format(
                    glow_sat, glow_val, i_gray, POPPED_UP_INTERIOR_MAX)
                active_indices.append(idx)
            elif popped_up and on_card_count >= 4:
                state = "active"
                reason = "inactive_top {:.1f} < {} (popped up) AND {}/6 on card".format(
                    i_gray, POPPED_UP_INTERIOR_MAX, on_card_count)
                active_indices.append(idx)
            elif not has_glow and i_gray >= INACTIVE_TOP_FLOOR:
                state = "inactive"
                reason = "no glow (sat {:.0f}) AND inactive_top {:.1f} >= {} (not popped up)".format(
                    glow_sat, i_gray, INACTIVE_TOP_FLOOR)
            elif ice_count >= 3:
                state = "inactive"
                reason = "{}/6 vertices show ice".format(ice_count)
            elif not has_glow:
                state = "inactive"
                reason = "no glow (sat {:.0f}), {}/6 ice, {}/6 on-card".format(
                    glow_sat, ice_count, on_card_count)
            elif has_glow and on_card_count < 4 and not popped_up:
                state = "inactive"
                reason = "glow present (sat {:.0f}) but insufficient evidence: {}/6 on-card, not popped up (it={:.1f})".format(
                    glow_sat, on_card_count, i_gray)
            else:
                state = "unknown"
                unknown_indices.append(idx)
                reason = "glow_sat={:.0f} ice={}/6 card={}/6 it={:.1f}".format(
                    glow_sat, ice_count, on_card_count, i_gray)

            vert_detail = " ".join(
                "{}={:.0f}".format(vert_names[i], vert_grays[i]) for i in range(6))

            rarity = "UNKNOWN"
            b_minus_r = mean_b - mean_r
            if state == "active":
                rarity = self._classify_rarity(mean_r, mean_g, mean_b)
                rarities[idx] = {
                    "rarity": rarity,
                    "rgb": (mean_r, mean_g, mean_b),
                    "b_minus_r": b_minus_r,
                    "hsv": glow_hsv,
                    "pixel_count": glow_px,
                }

            details[idx] = {
                "state": state,
                "reason": reason,
                "vertex_grays": dict(zip(vert_names, vert_grays)),
                "inactive_top_gray": i_gray,
                "glow_sat": glow_sat,
                "glow_rgb": (mean_r, mean_g, mean_b),
                "vertices_active": verts_active,
                "vertices_inactive": verts_inactive,
            }

            log.info(
                "[CardDetector] Card {} ({}) -> {}"
                "\n  vertices(active): {}"
                "\n  inactive_top=({},{}) gray={:.1f}"
                "\n  glow: sat={:.0f} RGB=({:.0f},{:.0f},{:.0f})"
                "\n  ice={}/6 on_card={}/6"
                "\n  reason: {}"
                "{}".format(
                    idx, slot["name"], state.upper(),
                    vert_detail,
                    ix, iy, i_gray,
                    glow_sat, mean_r, mean_g, mean_b,
                    ice_count, on_card_count,
                    reason,
                    "\n  RARITY: {} B-R={:.1f}".format(rarity, b_minus_r) if state == "active" else "",
                ))

        t_elapsed = (time.perf_counter() - t_start) * 1000
        unknown_str = " unknown={}".format(unknown_indices) if unknown_indices else ""
        log.info("[CardDetector] === Detection Complete === active={}{} time={:.0f}ms".format(
            active_indices, unknown_str, t_elapsed))

        result = {
            "active_indices": active_indices,
            "unknown_indices": unknown_indices,
            "rarities": rarities,
            "details": details,
        }

        if debug:
            self._save_debug(frame, gray_frame, details, active_indices, rarities)

        return result

    def detect_active_cards(self, debug=True):
        if cv2 is None or np is None:
            log.warning("[CardDetector] OpenCV/numpy not available, cannot detect cards")
            return None, []

        frame = None
        if self._screen_capture is not None:
            try:
                rect = self._screen_capture.window_manager.get_client_rect()
                log.info("[CardDetector] client_rect={}".format(rect))
            except Exception as e:
                log.warning("[CardDetector] Could not get client_rect: {}".format(e))
            frame = self._screen_capture.capture_multi_frame(
                count=CAPTURE_FRAMES, delay_ms=CAPTURE_DELAY_MS)

        if frame is None:
            log.warning("[CardDetector] Failed to capture frame")
            return None, []

        h_frame, w_frame = frame.shape[:2]
        log.info("[CardDetector] frame={}x{}".format(w_frame, h_frame))

        result = self.detect_cards(frame, debug=debug)
        self._last_result = result
        return result["active_indices"], result.get("unknown_indices", [])

    def _compute_hex_vertices(self, slot, state="active"):
        prefix = state + "_"
        top = slot[prefix + "top"]
        tl = slot[prefix + "tl"]
        tr = slot[prefix + "tr"]
        center_y = 2 * tl[1] - top[1]
        bl = (tl[0], 2 * center_y - tl[1])
        br = (tr[0], 2 * center_y - tr[1])
        bot = (top[0], 2 * center_y - top[1])
        return [top, tr, br, bot, bl, tl]

    def _sample_patch_stats(self, gray_frame, x, y, size=5):
        h, w = gray_frame.shape[:2]
        half = size // 2
        x1 = max(0, x - half)
        y1 = max(0, y - half)
        x2 = min(w, x + half + 1)
        y2 = min(h, y + half + 1)
        patch = gray_frame[y1:y2, x1:x2]
        if patch.size == 0:
            return {"mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0}
        return {
            "mean": float(np.mean(patch)),
            "median": float(np.median(patch)),
            "min": float(np.min(patch)),
            "max": float(np.max(patch)),
        }

    def _sample_patch_rgb(self, frame, x, y, size=5):
        h, w = frame.shape[:2]
        half = size // 2
        x1 = max(0, x - half)
        y1 = max(0, y - half)
        x2 = min(w, x + half + 1)
        y2 = min(h, y + half + 1)
        patch = frame[y1:y2, x1:x2]
        if patch.size == 0:
            return {"r": 0.0, "g": 0.0, "b": 0.0}
        return {
            "r": float(np.mean(patch[:, :, 2])),
            "g": float(np.mean(patch[:, :, 1])),
            "b": float(np.mean(patch[:, :, 0])),
        }

    def _sample_patch_hsv(self, hsv_frame, x, y, size=5):
        h_img, w = hsv_frame.shape[:2]
        half = size // 2
        x1 = max(0, x - half)
        y1 = max(0, y - half)
        x2 = min(w, x + half + 1)
        y2 = min(h_img, y + half + 1)
        patch = hsv_frame[y1:y2, x1:x2]
        if patch.size == 0:
            return {"h": 0.0, "s": 0.0, "v": 0.0}
        return {
            "h": float(np.mean(patch[:, :, 0])),
            "s": float(np.mean(patch[:, :, 1])),
            "v": float(np.mean(patch[:, :, 2])),
        }

    def _build_glow_polygon(self, card_idx, inset=15):
        slot = CARD_SLOTS[card_idx]
        tr = slot["active_tr"]
        top = slot["active_top"]
        tl = slot["active_tl"]
        dx_l = top[0] - tl[0]
        dy_l = top[1] - tl[1]
        dist_l = max(1, (dx_l**2 + dy_l**2) ** 0.5)
        frac_l = min(inset / dist_l, 1.0)
        tl_in = (round(tl[0] + dx_l * frac_l), round(tl[1] + dy_l * frac_l))
        dx_r = top[0] - tr[0]
        dy_r = top[1] - tr[1]
        dist_r = max(1, (dx_r**2 + dy_r**2) ** 0.5)
        frac_r = min(inset / dist_r, 1.0)
        tr_in = (round(tr[0] + dx_r * frac_r), round(tr[1] + dy_r * frac_r))
        return [
            (tr_in[0], tr_in[1]),
            (top[0], top[1]),
            (tl_in[0], tl_in[1]),
            (tl_in[0], tl_in[1] - 10),
            (top[0], top[1] - 10),
            (tr_in[0], tr_in[1] - 10),
        ]

    def _sample_glow_chevron_full(self, frame, hsv_frame, card_idx):
        h_img, w_img = frame.shape[:2]
        glow_verts = self._build_glow_polygon(card_idx)
        pts = np.array(glow_verts, dtype=np.int32)

        x_min = max(0, int(np.min(pts[:, 0])))
        y_min = max(0, int(np.min(pts[:, 1])))
        x_max = min(w_img, int(np.max(pts[:, 0])) + 1)
        y_max = min(h_img, int(np.max(pts[:, 1])) + 1)

        if x_max <= x_min or y_max <= y_min:
            return (0.0, 0.0, 0.0, 0, {"h": 0.0, "s": 0.0, "v": 0.0})

        roi_bgr = frame[y_min:y_max, x_min:x_max]
        roi_hsv = hsv_frame[y_min:y_max, x_min:x_max]
        local_pts = pts - np.array([x_min, y_min])
        mask = np.zeros((y_max - y_min, x_max - x_min), dtype=np.uint8)
        cv2.fillPoly(mask, [local_pts], 255)

        bgr_pixels = roi_bgr[mask > 0]
        hsv_pixels = roi_hsv[mask > 0]
        if len(bgr_pixels) == 0:
            return (0.0, 0.0, 0.0, 0, {"h": 0.0, "s": 0.0, "v": 0.0})

        mean_b = float(np.mean(bgr_pixels[:, 0]))
        mean_g = float(np.mean(bgr_pixels[:, 1]))
        mean_r = float(np.mean(bgr_pixels[:, 2]))

        hsv_stats = {
            "h": float(np.mean(hsv_pixels[:, 0])),
            "s": float(np.mean(hsv_pixels[:, 1])),
            "v": float(np.mean(hsv_pixels[:, 2])),
        }

        return (mean_r, mean_g, mean_b, len(bgr_pixels), hsv_stats)

    def _classify_rarity(self, mean_r, mean_g, mean_b):
        b_minus_r = mean_b - mean_r
        if b_minus_r > 80:
            return "BLUE"
        if b_minus_r < -60:
            return "ORANGE"
        if mean_r > 100 and mean_b > 120 and mean_g < 130 and abs(b_minus_r) < 50:
            return "PURPLE"
        return "UNKNOWN"

    def _save_debug(self, frame, gray_frame, details, active_indices, rarities):
        try:
            os.makedirs("debug", exist_ok=True)
            debug_img = frame.copy()
            vert_names = ["top", "tr", "br", "bot", "bl", "tl"]

            for idx in range(12):
                slot = CARD_SLOTS[idx]
                d = details[idx]
                state = d["state"]
                vg = d["vertex_grays"]
                i_gray = d["inactive_top_gray"]

                verts = d["vertices_active"] if state == "active" else d["vertices_inactive"]

                if state == "active":
                    color = (0, 255, 0)
                    thickness = 2
                else:
                    color = (128, 128, 128)
                    thickness = 1

                hex_pts = np.array(verts, dtype=np.int32)
                cv2.polylines(debug_img, [hex_pts], True, color, thickness)

                for vi, (vx, vy) in enumerate(d["vertices_active"]):
                    g = vg[vert_names[vi]]
                    dot_color = (0, 255, 0) if g < 130 else (0, 0, 255)
                    cv2.circle(debug_img, (vx, vy), 3, dot_color, -1)

                ix, iy = slot["inactive_top"]
                cv2.circle(debug_img, (ix, iy), 3, (255, 128, 0), -1)

                tl = verts[5]
                gs = d.get("glow_sat", 0)
                label1 = "{}: {} gs={:.0f} it={:.0f}".format(idx, state, gs, i_gray)
                cv2.putText(debug_img, label1, (tl[0] - 10, tl[1] + 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.30, (255, 255, 255), 1)

                glow_verts = self._build_glow_polygon(idx)
                chev_pts = np.array(glow_verts, dtype=np.int32)
                glow_color = (255, 0, 255) if state == "active" else (80, 80, 80)
                cv2.polylines(debug_img, [chev_pts], True, glow_color, 1)

                if idx in rarities:
                    r = rarities[idx]
                    rlabel = "{} B-R={:.0f} RGB({:.0f},{:.0f},{:.0f})".format(
                        r["rarity"], r["b_minus_r"], r["rgb"][0], r["rgb"][1], r["rgb"][2])
                    cv2.putText(debug_img, rlabel, (tl[0] - 10, tl[1] + 32),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.30, (255, 0, 255), 1)

            debug_path = "debug/detection_debug.png"
            cv2.imwrite(debug_path, debug_img)
            log.info("[CardDetector] Debug image saved to {}".format(debug_path))
        except Exception as e:
            log.error("[CardDetector] Failed to save debug image: {}".format(e))

    def verify_active_card_selected(self) -> bool:
        if np is None or cv2 is None:
            log.warning("[CardDetector] numpy/cv2 not available for verification")
            return False
        if self._screen_capture is None:
            log.warning("[CardDetector] No screen capture for verification")
            return False
        try:
            frame = self._screen_capture.capture_window()
            if frame is None:
                log.warning("[CardDetector] Verification capture failed")
                return False
            x1, y1, x2, y2 = ATTEMPTS_REGION
            h_img, w_img = frame.shape[:2]
            x1 = max(0, min(x1, w_img - 1))
            x2 = max(0, min(x2, w_img))
            y1 = max(0, min(y1, h_img - 1))
            y2 = max(0, min(y2, h_img))
            patch = frame[y1:y2, x1:x2]
            if patch.size == 0:
                log.warning("[CardDetector] Verification patch empty")
                return False
            mean_b = float(np.mean(patch[:, :, 0]))
            mean_g = float(np.mean(patch[:, :, 1]))
            mean_r = float(np.mean(patch[:, :, 2]))
            br_diff = mean_b - mean_r
            is_active = mean_r < ATTEMPTS_VERIFY_MAX_R and br_diff > ATTEMPTS_VERIFY_MIN_BR_DIFF
            log.info(
                f"[CardDetector] Attempts verification: RGB=({mean_r:.0f},{mean_g:.0f},{mean_b:.0f}) "
                f"B-R={br_diff:.1f} -> {'CONFIRMED active' if is_active else 'FAILED (inactive or no text)'}"
            )
            return is_active
        except Exception as e:
            log.error(f"[CardDetector] Verification error: {e}")
            return False

    def is_map_ui_open(self, frame=None) -> bool:
        if cv2 is None or np is None:
            log.warning("[CardDetector] CV2/numpy not available — assuming UI is open")
            return True

        if frame is None:
            if self._screen_capture is None:
                log.warning("[CardDetector] No screen capture — assuming UI is open")
                return True
            frame = self._screen_capture.capture_window()
            if frame is None:
                log.warning("[CardDetector] Capture failed — assuming UI is open")
                return True

        if not hasattr(self, '_card_ui_template'):
            self._card_ui_template = None
            if os.path.exists(CARD_UI_TEMPLATE_PATH):
                self._card_ui_template = cv2.imread(CARD_UI_TEMPLATE_PATH)
                if self._card_ui_template is not None:
                    log.info(f"[CardDetector] Loaded card UI template: {self._card_ui_template.shape}")
                else:
                    log.warning("[CardDetector] Could not read card UI template image")
            else:
                log.warning(f"[CardDetector] Template not found at {CARD_UI_TEMPLATE_PATH}")

        if self._card_ui_template is not None:
            return self._check_card_ui_template(frame)

        log.warning("[CardDetector] No card UI template — falling back to pixel check")
        return self._check_card_ui_pixels(frame)

    def _check_card_ui_template(self, frame) -> bool:
        h_img, w_img = frame.shape[:2]
        x1, y1, x2, y2 = CARD_UI_TEXT_REGION
        th, tw = self._card_ui_template.shape[:2]

        margin = 30
        search_x1 = max(0, x1 - margin)
        search_y1 = max(0, y1 - margin)
        search_x2 = min(w_img, x2 + margin)
        search_y2 = min(h_img, y2 + margin)

        if (search_x2 - search_x1) < tw or (search_y2 - search_y1) < th:
            log.warning("[CardDetector] Search region too small for template")
            return False

        search_region = frame[search_y1:search_y2, search_x1:search_x2]
        result = cv2.matchTemplate(search_region, self._card_ui_template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        is_open = max_val >= CARD_UI_MATCH_THRESHOLD

        log.info(
            "[CardDetector] UI check (template): score={:.3f} threshold={} loc=({},{}) -> {}".format(
                max_val, CARD_UI_MATCH_THRESHOLD,
                max_loc[0] + search_x1, max_loc[1] + search_y1,
                "OPEN" if is_open else "NOT OPEN"))

        return is_open

    def _check_card_ui_pixels(self, frame) -> bool:
        h_img, w_img = frame.shape[:2]
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        check_indices = [0, 2, 5, 7, 9, 11]
        ice_count = 0
        border_count = 0
        sample_details = []

        for idx in check_indices:
            slot = CARD_SLOTS[idx]
            ix, iy = slot["inactive_top"]
            if iy >= h_img or ix >= w_img:
                continue

            gray = self._sample_patch_stats(gray_frame, ix, iy, size=5)["mean"]
            rgb = self._sample_patch_rgb(frame, ix, iy, size=5)

            is_ice = gray > 120 and rgb["b"] >= rgb["r"]
            is_border = (55 <= gray <= 110
                         and rgb["b"] >= rgb["r"]
                         and rgb["b"] >= rgb["g"])

            if is_ice:
                ice_count += 1
            if is_border:
                border_count += 1

            sample_details.append(
                "card{}=g{:.0f} rgb({:.0f},{:.0f},{:.0f}) {}".format(
                    idx, gray, rgb["r"], rgb["g"], rgb["b"],
                    "ICE" if is_ice else ("BRD" if is_border else "???")))

        is_open = ice_count >= 4 or (ice_count >= 2 and (ice_count + border_count) >= 4)

        log.info(
            "[CardDetector] UI check (pixels): ice={} border={} -> {}\n  {}".format(
                ice_count, border_count,
                "OPEN" if is_open else "NOT OPEN",
                " | ".join(sample_details)))

        return is_open

    def get_last_result(self):
        return self._last_result

    def get_rarities(self):
        if self._last_result:
            return self._last_result.get("rarities")
        return None
