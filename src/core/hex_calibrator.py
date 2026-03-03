from __future__ import annotations

import json
import os
import time
from typing import Optional

from src.utils.constants import HEX_POSITIONS, MAP_NODE_NAMES
from src.utils.logger import log

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import numpy as np
except ImportError:
    np = None

MIN_MATCHES = 6
MATCH_THRESHOLD = 80
GLOW_SIZE = 70
INTERIOR_SIZE = 40

HEX_HALF_WIDTH = 35
HEX_HALF_HEIGHT = 57
HEX_SIDE_RATIO = 0.49
GLOW_ABOVE_PAD = 10

TEMPLATE_HALF_W = 40
TEMPLATE_HALF_H = 47
TEMPLATE_MATCH_THRESHOLD = 0.65
TEMPLATE_NMS_DIST = 40

HISTORY_FILE = "debug/calibration_history.json"
MAX_HISTORY_ENTRIES = 200
MAX_DEBUG_IMAGES = 50

REF_INACTIVE_WIDTH = 70
REF_INACTIVE_HEIGHT = 110
REF_INACTIVE_AREA = 5775
REF_INACTIVE_ASPECT = REF_INACTIVE_WIDTH / REF_INACTIVE_HEIGHT

REF_ACTIVE_WIDTH = 71
REF_ACTIVE_HEIGHT = 114
REF_ACTIVE_AREA = 6156
REF_ACTIVE_ASPECT = REF_ACTIVE_WIDTH / REF_ACTIVE_HEIGHT

REF_HEX_ASPECT = (REF_INACTIVE_ASPECT + REF_ACTIVE_ASPECT) / 2
REF_HEX_AREA_MIN = REF_INACTIVE_AREA
REF_HEX_AREA_MAX = REF_ACTIVE_AREA
REF_HEX_AREA_MID = (REF_INACTIVE_AREA + REF_ACTIVE_AREA) / 2
REF_SIDE_VERTEX_RATIO = 0.50

ASPECT_TOLERANCE = 0.18
AREA_TOLERANCE_FACTOR = 0.45
VERTEX_COUNT_MIN = 4
VERTEX_COUNT_MAX = 10
SOLIDITY_MIN = 0.70
SIDE_VERTEX_TOLERANCE = 0.15


class HexCalibrator:
    def __init__(self, screen_capture):
        self._screen_capture = screen_capture
        self._calibration_data: Optional[dict] = None
        self._learned_metrics = self._load_history()

    def is_calibrated(self) -> bool:
        return self._calibration_data is not None

    def get_hex_data(self) -> Optional[dict]:
        return self._calibration_data

    def calibrate(self, debug: bool = True) -> Optional[dict]:
        log.info("[HexCalibrator] Starting calibration...")

        frame = None
        if self._screen_capture is not None:
            wm = self._screen_capture.window_manager
            if wm and wm.hwnd:
                log.info(f"[HexCalibrator] Capturing from HWND={wm.hwnd}")
                frame = self._screen_capture.capture_multi_frame(count=25, delay_ms=33)

        detected_centers = []
        detected_contours = []
        detected_metrics = []
        rejected_contours = []
        if frame is not None and cv2 is not None and np is not None:
            detected_centers, detected_contours, detected_metrics, rejected_contours = self._detect_hexagons(frame)
            log.info(f"[HexCalibrator] Detected {len(detected_centers)} valid hexagons, rejected {len(rejected_contours)} non-hexagons")

        matched_pairs = []
        source = "hardcoded"
        calibrated_positions = dict(HEX_POSITIONS)
        scale_tuple = None

        if len(detected_centers) >= MIN_MATCHES and np is not None:
            matched_pairs = self._match_to_reference(detected_centers)
            log.info(f"[HexCalibrator] Matched {len(matched_pairs)} hexagons to reference positions")

            if len(matched_pairs) >= MIN_MATCHES:
                transform = self._compute_transform(matched_pairs)
                if transform is not None:
                    sx, sy, tx, ty = transform
                    log.info(f"[HexCalibrator] Transform: scale=({sx:.4f}, {sy:.4f}) offset=({tx:.1f}, {ty:.1f})")
                    calibrated_positions = self._apply_transform(transform)
                    source = "cv"
                    scale_tuple = (sx, sy)

                    matched_metrics = []
                    for _, _, _, det_idx in matched_pairs:
                        if det_idx < len(detected_metrics):
                            matched_metrics.append(detected_metrics[det_idx])
                    self._save_to_history(matched_metrics, transform)
                else:
                    log.warning("[HexCalibrator] Transform computation failed, using hardcoded")
            else:
                log.warning(f"[HexCalibrator] Only {len(matched_pairs)} matches (need {MIN_MATCHES}), using hardcoded")
        else:
            log.info("[HexCalibrator] Insufficient detections or CV unavailable, using hardcoded positions")

        hexagons = self._build_hexagon_data(
            calibrated_positions, scale=scale_tuple,
            detected_contours=detected_contours, matched_pairs=matched_pairs
        )

        debug_image_path = None
        if debug and cv2 is not None and np is not None:
            if frame is None:
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            debug_image_path = self._save_debug_image(
                frame, hexagons, detected_contours, detected_centers,
                matched_pairs, source, rejected_contours, detected_metrics
            )

        active_count = sum(1 for m in detected_metrics if m.get("hex_type") == "active")
        inactive_count = sum(1 for m in detected_metrics if m.get("hex_type") == "inactive")
        log.info(f"[HexCalibrator] === Calibration Results (source: {source}) ===")
        log.info(f"[HexCalibrator] Detected: {active_count} active + {inactive_count} inactive = {len(detected_metrics)} hexagons")
        for idx in sorted(hexagons.keys()):
            h = hexagons[idx]
            name = MAP_NODE_NAMES.get(idx, f"Unknown {idx}")
            log.info(f"[HexCalibrator] Node {idx} ({name}): center={h['center']} glow={h['glow_region']}")

        learned = self._get_learned_thresholds()
        if learned:
            log.info(f"[HexCalibrator] Learned thresholds from {learned['count']} samples: "
                     f"aspect={learned['aspect_mean']:.3f}+/-{learned['aspect_std']:.3f} "
                     f"area={learned['area_mean']:.0f}+/-{learned['area_std']:.0f}")
        log.info("[HexCalibrator] ================================================")

        self._calibration_data = {
            "hexagons": hexagons,
            "source": source,
            "debug_image_path": debug_image_path,
        }

        return self._calibration_data

    def _detect_via_template_matching(self, frame):
        centers = []
        valid_contours = []
        metrics_list = []
        rejected = []

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        th, tw = TEMPLATE_HALF_H * 2, TEMPLATE_HALF_W * 2
        fh, fw = gray.shape[:2]

        template = None
        template_src_pos = None
        for idx, (px, py) in sorted(HEX_POSITIONS.items()):
            x1 = max(0, px - TEMPLATE_HALF_W)
            y1 = max(0, py - TEMPLATE_HALF_H)
            x2 = min(fw, px + TEMPLATE_HALF_W)
            y2 = min(fh, py + TEMPLATE_HALF_H)
            if x2 - x1 < tw or y2 - y1 < th:
                log.info(f"[HexCalibrator] [template] Skipping pos {idx} ({px},{py}): crop too small ({x2-x1}x{y2-y1})")
                continue
            crop = gray[y1:y2, x1:x2]
            std_val = float(np.std(crop))
            log.info(f"[HexCalibrator] [template] Pos {idx} ({px},{py}): crop {crop.shape}, std={std_val:.1f}")
            if std_val > 15:
                template = crop
                template_src_pos = (idx, px, py)
                break

        if template is None:
            log.info("[HexCalibrator] [template] No valid template found from any hardcoded position")
            return centers, valid_contours, metrics_list, rejected

        t_mean = float(np.mean(template))
        t_std = float(np.std(template))
        log.info(f"[HexCalibrator] [template] Using template from pos {template_src_pos[0]} ({template_src_pos[1]},{template_src_pos[2]}): "
                 f"size={template.shape[1]}x{template.shape[0]}, mean={t_mean:.1f}, std={t_std:.1f}")

        src_idx = template_src_pos[0]
        src_x, src_y = template_src_pos[1], template_src_pos[2]
        self_score = float(cv2.matchTemplate(
            gray[src_y - TEMPLATE_HALF_H:src_y + TEMPLATE_HALF_H,
                 src_x - TEMPLATE_HALF_W:src_x + TEMPLATE_HALF_W],
            template, cv2.TM_CCOEFF_NORMED)[0][0]) if (
            src_y - TEMPLATE_HALF_H >= 0 and src_x - TEMPLATE_HALF_W >= 0
        ) else 1.0
        if self_score < 0.9:
            log.info(f"[HexCalibrator] [template] WARNING: template self-match score={self_score:.3f} < 0.9, template may be invalid")

        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= TEMPLATE_MATCH_THRESHOLD)
        raw_matches = list(zip(locations[1], locations[0], result[locations[0], locations[1]]))
        log.info(f"[HexCalibrator] [template] Raw matches above {TEMPLATE_MATCH_THRESHOLD}: {len(raw_matches)}")

        if not raw_matches:
            return centers, valid_contours, metrics_list, rejected

        raw_matches.sort(key=lambda m: m[2], reverse=True)

        nms_matches = []
        for mx, my, score in raw_matches:
            is_dup = False
            for nmx, nmy, _ in nms_matches:
                dist = ((mx - nmx) ** 2 + (my - nmy) ** 2) ** 0.5
                if dist < TEMPLATE_NMS_DIST:
                    is_dup = True
                    break
            if not is_dup:
                nms_matches.append((mx, my, score))

        log.info(f"[HexCalibrator] [template] After NMS (dist={TEMPLATE_NMS_DIST}): {len(nms_matches)} unique matches")

        hw, hh = TEMPLATE_HALF_W, TEMPLATE_HALF_H
        side_h = int(hh * 0.5)
        proximity_limit = MATCH_THRESHOLD + 40

        for mx, my, score in nms_matches:
            cx = mx + template.shape[1] // 2
            cy = my + template.shape[0] // 2

            near_any = False
            for _, (hx, hy) in HEX_POSITIONS.items():
                d = ((cx - hx) ** 2 + (cy - hy) ** 2) ** 0.5
                if d < proximity_limit:
                    near_any = True
                    break
            if not near_any:
                log.info(f"[HexCalibrator] [template] SKIP ({cx},{cy}) score={score:.3f}: not near any known position")
                continue

            log.info(f"[HexCalibrator] [template] ACCEPT ({cx},{cy}) score={score:.3f}")

            hex_pts = np.array([
                [cx, cy - hh],
                [cx + hw, cy - side_h],
                [cx + hw, cy + side_h],
                [cx, cy + hh],
                [cx - hw, cy + side_h],
                [cx - hw, cy - side_h],
            ], dtype=np.int32)

            centers.append((cx, cy))
            valid_contours.append(hex_pts.reshape(-1, 1, 2))
            metrics_list.append({
                "area": float(hw * 2 * hh * 2 * 0.75),
                "aspect": float(hw * 2) / float(hh * 2),
                "solidity": 1.0,
                "vertex_count": 6,
                "width": hw * 2,
                "height": hh * 2,
                "center": (cx, cy),
                "hex_type": "inactive",
                "template_score": float(score),
            })

        return centers, valid_contours, metrics_list, rejected

    def _detect_hexagons(self, frame):
        tmpl_centers, tmpl_contours, tmpl_metrics, tmpl_rejected = self._detect_via_template_matching(frame)
        if len(tmpl_centers) >= MIN_MATCHES:
            log.info(f"[HexCalibrator] Template matching found {len(tmpl_centers)} matches (>= {MIN_MATCHES}), using template results")
            return tmpl_centers, tmpl_contours, tmpl_metrics, tmpl_rejected
        log.info(f"[HexCalibrator] Template matching found {len(tmpl_centers)} (< {MIN_MATCHES}), falling back to HSV detection")

        centers = []
        valid_contours = []
        metrics_list = []
        rejected = []

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h_ch, s_ch, v_ch = cv2.split(hsv)

        mask_sat = s_ch < 100
        mask_val = (v_ch >= 35) & (v_ch <= 210)
        mask = (mask_sat & mask_val).astype(np.uint8) * 255

        mask_pixels = int(np.sum(mask > 0))
        total_pixels = mask.shape[0] * mask.shape[1]
        log.info(f"[HexCalibrator] Primary mask: {mask_pixels}/{total_pixels} pixels ({100*mask_pixels/total_pixels:.1f}%) pass filter (sat<100, val 35-210)")

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

        contours_primary, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        log.info(f"[HexCalibrator] Primary: found {len(contours_primary)} contours from mask")

        for cnt in contours_primary:
            result = self._validate_hexagon(cnt, " [primary]")
            if result is not None:
                center, contour, metrics = result
                centers.append(center)
                valid_contours.append(contour)
                metrics_list.append(metrics)
            else:
                area = cv2.contourArea(cnt)
                if area > 500:
                    rejected.append(cnt)

        if len(centers) < MIN_MATCHES:
            log.info(f"[HexCalibrator] Primary found {len(centers)}, trying edge backup...")
            edge_c, edge_cnt, edge_m, edge_r = self._detect_hexagons_edge(frame)
            for ec, evc, em in zip(edge_c, edge_cnt, edge_m):
                is_dup = False
                for existing in centers:
                    dist = ((ec[0] - existing[0]) ** 2 + (ec[1] - existing[1]) ** 2) ** 0.5
                    if dist < 40:
                        is_dup = True
                        break
                if not is_dup:
                    centers.append(ec)
                    valid_contours.append(evc)
                    metrics_list.append(em)
            rejected.extend(edge_r)

        return centers, valid_contours, metrics_list, rejected

    def _detect_hexagons_edge(self, frame):
        centers = []
        valid_contours = []
        metrics_list = []
        rejected = []

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=2)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        log.info(f"[HexCalibrator] Edge backup: found {len(contours)} contours from Canny edges")

        for cnt in contours:
            result = self._validate_hexagon(cnt, " [edge]")
            if result is not None:
                center, contour, metrics = result
                centers.append(center)
                valid_contours.append(contour)
                metrics_list.append(metrics)
            else:
                area = cv2.contourArea(cnt)
                if area > 500:
                    rejected.append(cnt)

        return centers, valid_contours, metrics_list, rejected

    def _validate_hexagon(self, cnt, log_prefix=""):
        area = cv2.contourArea(cnt)
        if area < 1000 or area > 20000:
            if area > 300:
                log.info(f"[HexCalibrator]{log_prefix} REJECT area={area:.0f} (need 1000-20000)")
            return None

        x, y, w, h = cv2.boundingRect(cnt)
        if w == 0 or h == 0:
            return None

        aspect = w / h

        learned = self._get_learned_thresholds()
        if learned and learned["count"] >= 20 and learned["aspect_std"] > 0.001 and learned["area_std"] > 1:
            aspect_lo = learned["aspect_mean"] - 3 * learned["aspect_std"] - 0.05
            aspect_hi = learned["aspect_mean"] + 3 * learned["aspect_std"] + 0.05
            area_lo = max(1000, learned["area_mean"] - 3 * learned["area_std"])
            area_hi = learned["area_mean"] + 3 * learned["area_std"]
            log.info(f"[HexCalibrator]{log_prefix} Using learned thresholds: aspect=[{aspect_lo:.3f}-{aspect_hi:.3f}] area=[{area_lo:.0f}-{area_hi:.0f}]")
        else:
            aspect_lo = REF_HEX_ASPECT - ASPECT_TOLERANCE
            aspect_hi = REF_HEX_ASPECT + ASPECT_TOLERANCE
            area_lo = REF_HEX_AREA_MIN * (1 - AREA_TOLERANCE_FACTOR)
            area_hi = REF_HEX_AREA_MAX * (1 + AREA_TOLERANCE_FACTOR)

        if aspect < aspect_lo or aspect > aspect_hi:
            log.info(f"[HexCalibrator]{log_prefix} REJECT aspect={aspect:.3f} (need {aspect_lo:.3f}-{aspect_hi:.3f}) area={area:.0f} bbox=({x},{y},{w},{h})")
            return None
        if area < area_lo or area > area_hi:
            log.info(f"[HexCalibrator]{log_prefix} REJECT area={area:.0f} (need {area_lo:.0f}-{area_hi:.0f}) aspect={aspect:.3f} bbox=({x},{y},{w},{h})")
            return None

        hull = cv2.convexHull(cnt)
        hull_area = cv2.contourArea(hull)
        if hull_area == 0:
            return None
        solidity = area / hull_area
        if solidity < SOLIDITY_MIN:
            log.info(f"[HexCalibrator]{log_prefix} REJECT solidity={solidity:.3f} (need >={SOLIDITY_MIN}) area={area:.0f} aspect={aspect:.3f} bbox=({x},{y},{w},{h})")
            return None

        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.03 * peri, True)
        vertex_count = len(approx)

        if vertex_count < VERTEX_COUNT_MIN or vertex_count > VERTEX_COUNT_MAX:
            log.info(f"[HexCalibrator]{log_prefix} REJECT vertices={vertex_count} (need {VERTEX_COUNT_MIN}-{VERTEX_COUNT_MAX}) area={area:.0f} aspect={aspect:.3f} bbox=({x},{y},{w},{h})")
            return None

        if vertex_count >= 4 and h > 0:
            passes_shape = self._check_hex_shape(approx, x, y, w, h)
            if not passes_shape:
                log.info(f"[HexCalibrator]{log_prefix} REJECT shape_check failed vertices={vertex_count} area={area:.0f} aspect={aspect:.3f} bbox=({x},{y},{w},{h})")
                return None

        M = cv2.moments(cnt)
        if M["m00"] == 0:
            return None
        cx = int(M["m10"] / M["m00"])
        cy = int(M["m01"] / M["m00"])

        active_area_mid = (REF_ACTIVE_AREA + REF_INACTIVE_AREA) / 2
        hex_type = "active" if area > active_area_mid else "inactive"

        metrics = {
            "area": float(area),
            "aspect": float(aspect),
            "solidity": float(solidity),
            "vertex_count": int(vertex_count),
            "width": int(w),
            "height": int(h),
            "center": (cx, cy),
            "hex_type": hex_type,
        }

        log.info(f"[HexCalibrator]{log_prefix} ACCEPT {hex_type} center=({cx},{cy}) area={area:.0f} aspect={aspect:.3f} solidity={solidity:.3f} vertices={vertex_count} bbox=({x},{y},{w},{h})")

        return (cx, cy), cnt, metrics

    def _check_hex_shape(self, approx, bx, by, bw, bh):
        pts = approx.reshape(-1, 2)
        cx_box = bx + bw / 2.0

        top_zone_y = by + bh * 0.20
        bottom_zone_y = by + bh * 0.80

        top_pts = [p for p in pts if p[1] < top_zone_y]
        bottom_pts = [p for p in pts if p[1] > bottom_zone_y]

        if len(top_pts) < 1 or len(bottom_pts) < 1:
            return False

        top_pt = min(top_pts, key=lambda p: p[1])
        bottom_pt = max(bottom_pts, key=lambda p: p[1])

        top_x_offset = abs(top_pt[0] - cx_box) / bw
        bottom_x_offset = abs(bottom_pt[0] - cx_box) / bw

        if top_x_offset > 0.35 or bottom_x_offset > 0.35:
            return False

        mid_zone_lo = by + bh * 0.3
        mid_zone_hi = by + bh * 0.7
        mid_pts = [p for p in pts if mid_zone_lo < p[1] < mid_zone_hi]

        if len(mid_pts) < 2:
            return False

        return True

    def _match_to_reference(self, detected_centers):
        matched = []
        used_detected = set()

        for idx, (rx, ry) in HEX_POSITIONS.items():
            best_dist = MATCH_THRESHOLD
            best_det_idx = -1

            for di, (dx, dy) in enumerate(detected_centers):
                if di in used_detected:
                    continue
                dist = ((dx - rx) ** 2 + (dy - ry) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_det_idx = di

            if best_det_idx >= 0:
                used_detected.add(best_det_idx)
                matched.append((idx, HEX_POSITIONS[idx], detected_centers[best_det_idx], best_det_idx))

        return matched

    def _compute_transform(self, matched_pairs):
        if len(matched_pairs) < 2:
            return None

        ref_xs = np.array([r[0] for _, r, _, _ in matched_pairs], dtype=np.float64)
        ref_ys = np.array([r[1] for _, r, _, _ in matched_pairs], dtype=np.float64)
        det_xs = np.array([d[0] for _, _, d, _ in matched_pairs], dtype=np.float64)
        det_ys = np.array([d[1] for _, _, d, _ in matched_pairs], dtype=np.float64)

        ref_x_mean = np.mean(ref_xs)
        ref_y_mean = np.mean(ref_ys)
        det_x_mean = np.mean(det_xs)
        det_y_mean = np.mean(det_ys)

        ref_xs_c = ref_xs - ref_x_mean
        ref_ys_c = ref_ys - ref_y_mean
        det_xs_c = det_xs - det_x_mean
        det_ys_c = det_ys - det_y_mean

        denom_x = np.sum(ref_xs_c ** 2)
        denom_y = np.sum(ref_ys_c ** 2)

        if denom_x < 1e-6 or denom_y < 1e-6:
            sx = 1.0
            sy = 1.0
        else:
            sx = float(np.sum(ref_xs_c * det_xs_c) / denom_x)
            sy = float(np.sum(ref_ys_c * det_ys_c) / denom_y)

        if sx < 0.7 or sx > 1.3 or sy < 0.7 or sy > 1.3:
            log.warning(f"[HexCalibrator] Scale out of range: ({sx:.4f}, {sy:.4f}), clamping")
            sx = max(0.8, min(1.2, sx))
            sy = max(0.8, min(1.2, sy))

        tx = float(det_x_mean - sx * ref_x_mean)
        ty = float(det_y_mean - sy * ref_y_mean)

        return (sx, sy, tx, ty)

    def _apply_transform(self, transform):
        sx, sy, tx, ty = transform
        calibrated = {}
        for idx, (rx, ry) in HEX_POSITIONS.items():
            new_x = int(round(sx * rx + tx))
            new_y = int(round(sy * ry + ty))
            calibrated[idx] = (new_x, new_y)
        return calibrated

    def _build_hexagon_data(self, positions, scale=None, detected_contours=None, matched_pairs=None):
        hexagons = {}

        match_map = {}
        if matched_pairs and detected_contours:
            for idx, ref_pos, det_pos, det_idx in matched_pairs:
                if det_idx < len(detected_contours):
                    match_map[idx] = detected_contours[det_idx]

        learned = self._get_learned_thresholds()
        if learned and learned["count"] >= 10:
            fallback_hw = int(learned["width_mean"] / 2)
            fallback_hh = int(learned["height_mean"] / 2)
        else:
            fallback_hw = HEX_HALF_WIDTH
            fallback_hh = HEX_HALF_HEIGHT

        if scale is not None:
            sx, sy = scale
            fallback_hw = int(fallback_hw * sx)
            fallback_hh = int(fallback_hh * sy)

        glow_pad = GLOW_ABOVE_PAD

        for idx, (cx, cy) in positions.items():
            if idx in match_map:
                cnt = match_map[idx]
                peri = cv2.arcLength(cnt, True)
                approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
                raw_verts = approx.reshape(-1, 2)
                M = cv2.moments(cnt)
                if M["m00"] > 0:
                    det_cx = int(M["m10"] / M["m00"])
                    det_cy = int(M["m01"] / M["m00"])
                else:
                    det_cx, det_cy = int(np.mean(raw_verts[:, 0])), int(np.mean(raw_verts[:, 1]))
                dx = cx - det_cx
                dy = cy - det_cy
                hex_verts = [(int(v[0] + dx), int(v[1] + dy)) for v in raw_verts]
                x, y, w, h = cv2.boundingRect(cnt)
                hw = w // 2
                hh = h // 2
            else:
                hw = fallback_hw
                hh = fallback_hh
                side_y = int(hh * HEX_SIDE_RATIO)
                hex_verts = [
                    (cx, cy - hh),
                    (cx + hw, cy - side_y),
                    (cx + hw, cy + side_y),
                    (cx, cy + hh),
                    (cx - hw, cy + side_y),
                    (cx - hw, cy - side_y),
                ]

            pts_arr = np.array(hex_verts)
            sorted_by_y = pts_arr[pts_arr[:, 1].argsort()]
            top3 = sorted_by_y[:3]
            glow_top = int(np.min(top3[:, 1])) - glow_pad
            glow_bottom = int(np.max(top3[:, 1]))
            glow_left = int(np.min(top3[:, 0]))
            glow_right = int(np.max(top3[:, 0]))

            hexagons[idx] = {
                "center": (cx, cy),
                "vertices": hex_verts,
                "hex_half_width": hw,
                "hex_half_height": hh,
                "detected": idx in match_map,
                "glow_region": (cx - GLOW_SIZE // 2, cy - GLOW_SIZE // 2, GLOW_SIZE, GLOW_SIZE),
                "interior_region": (cx - INTERIOR_SIZE // 2, cy - INTERIOR_SIZE // 2, INTERIOR_SIZE, INTERIOR_SIZE),
                "glow_search_box": (glow_left, glow_top, glow_right - glow_left, glow_bottom - glow_top),
            }
            detected_str = "CV-detected" if idx in match_map else "fallback"
            log.info(f"[HexCalibrator] Hex {idx}: {detected_str} center=({cx},{cy}) hw={hw} hh={hh} vertices={len(hex_verts)} glow_box=({glow_left},{glow_top},{glow_right-glow_left},{glow_bottom-glow_top})")
        return hexagons

    def _load_history(self) -> list:
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
        except Exception as e:
            log.warning(f"[HexCalibrator] Failed to load history: {e}")
        return []

    def _save_to_history(self, metrics_list, transform):
        try:
            os.makedirs("debug", exist_ok=True)
            entry = {
                "timestamp": time.time(),
                "transform": {
                    "sx": transform[0],
                    "sy": transform[1],
                    "tx": transform[2],
                    "ty": transform[3],
                },
                "detections": [],
            }
            for m in metrics_list:
                entry["detections"].append({
                    "area": m.get("area", 0),
                    "aspect": m.get("aspect", 0),
                    "solidity": m.get("solidity", 0),
                    "vertex_count": m.get("vertex_count", 0),
                    "width": m.get("width", 0),
                    "height": m.get("height", 0),
                    "center": list(m.get("center", (0, 0))),
                })

            self._learned_metrics.append(entry)

            if len(self._learned_metrics) > MAX_HISTORY_ENTRIES:
                self._learned_metrics = self._learned_metrics[-MAX_HISTORY_ENTRIES:]

            with open(HISTORY_FILE, "w") as f:
                json.dump(self._learned_metrics, f, indent=2)

            total_detections = sum(len(e.get("detections", [])) for e in self._learned_metrics)
            log.info(f"[HexCalibrator] Saved history: {len(self._learned_metrics)} runs, {total_detections} total detections")

        except Exception as e:
            log.warning(f"[HexCalibrator] Failed to save history: {e}")

    def _get_learned_thresholds(self) -> Optional[dict]:
        all_detections = []
        for entry in self._learned_metrics:
            for d in entry.get("detections", []):
                if d.get("area", 0) > 0 and d.get("aspect", 0) > 0:
                    all_detections.append(d)

        if len(all_detections) < 10:
            return None

        areas = [d["area"] for d in all_detections]
        aspects = [d["aspect"] for d in all_detections]
        widths = [d["width"] for d in all_detections]
        heights = [d["height"] for d in all_detections]

        def mean_std(vals):
            n = len(vals)
            m = sum(vals) / n
            variance = sum((v - m) ** 2 for v in vals) / n
            return m, variance ** 0.5

        area_mean, area_std = mean_std(areas)
        aspect_mean, aspect_std = mean_std(aspects)
        width_mean, width_std = mean_std(widths)
        height_mean, height_std = mean_std(heights)

        return {
            "count": len(all_detections),
            "area_mean": area_mean,
            "area_std": area_std,
            "aspect_mean": aspect_mean,
            "aspect_std": aspect_std,
            "width_mean": width_mean,
            "width_std": width_std,
            "height_mean": height_mean,
            "height_std": height_std,
        }

    def _save_debug_image(self, image, hexagons, detected_contours, detected_centers,
                          matched_pairs, source, rejected_contours=None,
                          detected_metrics=None):
        try:
            os.makedirs("debug", exist_ok=True)
            debug_img = image.copy()

            for i, cnt in enumerate(detected_contours or []):
                hex_type = "?"
                if detected_metrics and i < len(detected_metrics):
                    hex_type = detected_metrics[i].get("hex_type", "?")
                color = (0, 200, 255) if hex_type == "active" else (255, 100, 0)
                cv2.drawContours(debug_img, [cnt], -1, color, 2)

            for i, dc in enumerate(detected_centers):
                hex_type = "?"
                if detected_metrics and i < len(detected_metrics):
                    hex_type = detected_metrics[i].get("hex_type", "?")
                color = (0, 200, 255) if hex_type == "active" else (255, 100, 0)
                cv2.circle(debug_img, (int(dc[0]), int(dc[1])), 6, color, -1)
                label = "A" if hex_type == "active" else "I"
                cv2.putText(debug_img, label, (int(dc[0]) + 8, int(dc[1]) - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)

            for idx, (rx, ry) in HEX_POSITIONS.items():
                cv2.circle(debug_img, (rx, ry), 5, (0, 255, 255), 2)
                cv2.putText(debug_img, f"R{idx}", (rx + 8, ry - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)

            for idx, ref_pos, det_pos, _ in matched_pairs:
                cv2.line(debug_img, (int(ref_pos[0]), int(ref_pos[1])),
                         (int(det_pos[0]), int(det_pos[1])), (0, 200, 200), 1)

            for idx in sorted(hexagons.keys()):
                h = hexagons[idx]
                cx, cy = h["center"]
                name = MAP_NODE_NAMES.get(idx, f"Node {idx}")

                cv2.circle(debug_img, (cx, cy), 8, (0, 255, 0), -1)

                if h.get("vertices"):
                    pts = np.array(h["vertices"], dtype=np.int32)
                    if h.get("detected"):
                        cv2.polylines(debug_img, [pts], True, (0, 255, 0), 2)
                    else:
                        cv2.polylines(debug_img, [pts], True, (0, 180, 0), 1)

                if "glow_search_box" in h:
                    bx, by, bw, bh_box = h["glow_search_box"]
                    bx = max(0, bx)
                    by = max(0, by)
                    bx2 = min(image.shape[1], bx + bw)
                    by2 = min(image.shape[0], by + bh_box)
                    roi = image[by:by2, bx:bx2]
                    if roi.size > 0:
                        hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                        sat = hsv_roi[:, :, 1]
                        bg_median = float(np.median(sat))
                        glow_thresh = max(80, bg_median + 40)
                        glow_mask = (sat > glow_thresh).astype(np.uint8) * 255
                        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
                        glow_mask = cv2.morphologyEx(glow_mask, cv2.MORPH_OPEN, kernel)
                        glow_mask = cv2.morphologyEx(glow_mask, cv2.MORPH_CLOSE, kernel)
                        contours_glow, _ = cv2.findContours(glow_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        for gc in contours_glow:
                            if cv2.contourArea(gc) > 20:
                                gc_shifted = gc.copy()
                                gc_shifted[:, :, 0] += bx
                                gc_shifted[:, :, 1] += by
                                cv2.drawContours(debug_img, [gc_shifted], -1, (255, 0, 255), 1)

                label = f"{idx}: {name}"
                cv2.putText(debug_img, label, (cx - 40, cy - 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

            active_cnt = sum(1 for m in (detected_metrics or []) if m.get("hex_type") == "active")
            inactive_cnt = sum(1 for m in (detected_metrics or []) if m.get("hex_type") == "inactive")
            learned = self._get_learned_thresholds()
            learned_str = f" | Learned: {learned['count']} samples" if learned else " | No learned data"
            info_text = f"Source: {source} | Matches: {len(matched_pairs)}/12 | Active: {active_cnt} Inactive: {inactive_cnt}{learned_str}"
            cv2.putText(debug_img, info_text, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            debug_path = f"debug/calibration_{timestamp}.png"
            cv2.imwrite(debug_path, debug_img)

            symlink_path = "debug/calibration_debug.png"
            try:
                if os.path.exists(symlink_path):
                    os.remove(symlink_path)
                cv2.imwrite(symlink_path, debug_img)
            except Exception:
                pass

            self._cleanup_old_debug_images()

            log.info(f"[HexCalibrator] Debug image saved to {debug_path}")
            return debug_path

        except Exception as e:
            log.error(f"[HexCalibrator] Failed to save debug image: {e}")
            return None

    def _cleanup_old_debug_images(self):
        try:
            debug_dir = "debug"
            images = sorted([
                f for f in os.listdir(debug_dir)
                if f.startswith("calibration_2") and f.endswith(".png")
            ])
            if len(images) > MAX_DEBUG_IMAGES:
                for old_img in images[:-MAX_DEBUG_IMAGES]:
                    try:
                        os.remove(os.path.join(debug_dir, old_img))
                    except Exception:
                        pass
        except Exception:
            pass
