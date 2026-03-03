import json
import os
import time
from enum import Enum, auto
from typing import Optional, List, Tuple, Callable
from dataclasses import dataclass, field

from src.core.input_controller import InputController
from src.core.game_state import GameState
from src.core.screen_capture import ScreenCapture
from src.core.hex_calibrator import HexCalibrator
from src.core.card_detector import CardDetector
from src.utils.constants import (
    CHARACTER_CENTER, MAP_NAMES, HIDEOUT_POSITION,
    NEXT_BUTTON, ADD_AFFIX_BUTTON, OPEN_PORTAL_BUTTON_POS,
    HEX_POSITIONS, MAP_NODE_NAMES,
    TIP_POPUP_DIALOG_REGION, TIP_POPUP_CONFIRM_BUTTON,
    TIP_POPUP_DONT_SHOW_CHECKBOX, TIP_POPUP_WHITE_THRESHOLD,
)
from src.utils.logger import log
from src.utils.config_manager import ConfigManager

# Optional memory-based imports (available when scanner + database are wired in)
try:
    from src.core.memory_card_selector import MemoryCardSelector, DetectedCard
except ImportError:
    MemoryCardSelector = None
    DetectedCard = None


class CardRarity(Enum):
    BLUE = 0
    PURPLE = 1
    ORANGE = 2
    RAINBOW = 3

    @property
    def display_name(self) -> str:
        return self.name.capitalize()


RARITY_PRIORITY = [CardRarity.RAINBOW, CardRarity.ORANGE, CardRarity.PURPLE, CardRarity.BLUE]


@dataclass
class MapCard:
    map_name: str
    hex_position: int
    rarity: CardRarity = CardRarity.BLUE
    attempts_remaining: int = 4
    is_active: bool = False


GLACIAL_ABYSS_ZONE_CLICK = (537, 598)
GLACIAL_ABYSS_TEXT_REGION = (475, 585, 598, 611)
GLACIAL_ABYSS_TEMPLATE_PATH = "assets/glacial_abyss_text.png"
GLACIAL_ABYSS_MATCH_THRESHOLD = 0.7
MAP_DEVICE_WALK_POS = (960, 400)
PORTAL_WALK_POS = (960, 350)

HEX_CLICK_ORDER = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]

MAP_UI_SETTLE_TIME = 0.15
CLICK_SETTLE_TIME = 0.08

CARD_STATE_FILE = "card_state.json"


class SelectionStep(Enum):
    OPEN_DEVICE = auto()
    SELECT_ZONE = auto()
    SELECT_CARD = auto()
    OPEN_PORTAL = auto()


class MapSelector:
    def __init__(self, input_ctrl: InputController, game_state: GameState,
                 config: ConfigManager,
                 screen_capture: Optional[ScreenCapture] = None,
                 hex_calibrator: Optional[HexCalibrator] = None,
                 memory_card_selector=None):
        self._input = input_ctrl
        self._game_state = game_state
        self._config = config
        self._screen_capture = screen_capture
        self._hex_calibrator = hex_calibrator
        self._mem_selector = memory_card_selector  # MemoryCardSelector (primary)
        self._card_detector = None
        if screen_capture:
            self._card_detector = CardDetector(screen_capture, hex_calibrator)
        self._calibrated = False
        self._cards: List[MapCard] = []
        self._active_positions: List[int] = []
        self._last_selected: int = -1
        self._step_callback: Optional[Callable] = None
        self._cancelled = False
        self._current_step: Optional[SelectionStep] = None
        self._step_retries: int = 0
        self._max_retries: int = 3

        self._load_card_state()

    def set_step_callback(self, callback: Callable):
        self._step_callback = callback

    def set_memory_card_selector(self, mem_selector):
        """Set or replace the MemoryCardSelector (called when scanner becomes available)."""
        self._mem_selector = mem_selector
        if mem_selector:
            self._log("Memory card selector attached")

    def cancel(self):
        self._cancelled = True

    def _log(self, msg: str):
        log.info(f"[MapSelector] {msg}")
        if self._step_callback:
            self._step_callback(msg)

    def _set_step(self, step: SelectionStep):
        self._current_step = step
        self._step_retries = 0
        self._log(f"Step: {step.name}")

    @property
    def current_step(self) -> Optional[SelectionStep]:
        return self._current_step

    @property
    def is_calibrated(self) -> bool:
        if self._hex_calibrator and self._hex_calibrator.is_calibrated():
            self._calibrated = True
        return self._calibrated

    def calibrate(self, debug: bool = True) -> bool:
        if self._hex_calibrator is None:
            log.warning("[MapSelector] No calibrator available")
            return False
        result = self._hex_calibrator.calibrate(debug=debug)
        if result is not None:
            self._calibrated = True
            return True
        return False

    @property
    def cards(self) -> List[MapCard]:
        return self._cards

    @property
    def active_positions(self) -> List[int]:
        return self._active_positions

    def set_active_cards(self, positions: List[int], rarities: Optional[List[CardRarity]] = None):
        self._active_positions = positions

        for card in self._cards:
            card.is_active = card.hex_position in self._active_positions

        if rarities:
            for i, pos in enumerate(self._active_positions):
                if i < len(rarities):
                    card = self._find_card_at(pos)
                    if card:
                        card.rarity = rarities[i]

        self._save_card_state()
        self._log(f"Active cards set: positions={self._active_positions}")

    def select_best_map(self) -> Optional[int]:
        if not self._active_positions:
            self._log("ERROR: No active card positions configured")
            self._log("Set active cards in Settings before running")
            return None

        best_pos = None
        best_rarity = -1

        for pos in self._active_positions:
            card = self._find_card_at(pos)
            if card and card.attempts_remaining > 0:
                if card.rarity.value > best_rarity:
                    best_rarity = card.rarity.value
                    best_pos = pos

        if best_pos is None:
            self._log("All active cards exhausted, resetting attempts")
            for pos in self._active_positions:
                card = self._find_card_at(pos)
                if card:
                    card.attempts_remaining = 4
            self._save_card_state()
            best_pos = self._active_positions[0]

        card = self._find_card_at(best_pos)
        rarity = card.rarity.display_name if card else "Unknown"
        attempts = card.attempts_remaining if card else 0
        self._log(f"Selected position {best_pos} ({rarity}, {attempts} attempts left)")

        return best_pos

    def _find_card_at(self, position: int) -> Optional[MapCard]:
        for card in self._cards:
            if card.hex_position == position:
                return card
        return None

    def execute_map_selection(self) -> bool:
        self._cancelled = False
        start_time = time.time()

        self._set_step(SelectionStep.OPEN_DEVICE)
        if not self._open_map_device():
            return False

        self._set_step(SelectionStep.SELECT_ZONE)
        if not self._select_zone():
            return False

        self._set_step(SelectionStep.SELECT_CARD)
        if not self._select_card():
            return False

        self._set_step(SelectionStep.OPEN_PORTAL)
        if not self._open_portal_sequence():
            return False

        elapsed = time.time() - start_time
        self._log(f"Map selection complete in {elapsed:.1f}s")
        return True

    def _verify_position_changed(self, initial_pos, expected_direction: str = "") -> bool:
        self._game_state.update()
        current = self._game_state.player.position
        dx = abs(current.x - initial_pos.x)
        dy = abs(current.y - initial_pos.y)
        moved = dx > 10 or dy > 10
        if moved:
            self._log(f"  Position changed: delta=({dx:.0f}, {dy:.0f})")
        return moved

    def _check_ui_open(self, context: str = "map device") -> bool:
        # Primary: memory-based check (fast, no CV dependency)
        if self._mem_selector is not None:
            try:
                is_open = self._mem_selector.is_card_ui_open()
                if is_open:
                    self._log(f"  {context} UI confirmed open (memory)")
                    return True
                else:
                    self._log(f"  {context} UI NOT open (memory) — will also check CV")
            except Exception as e:
                self._log(f"  Memory UI check failed: {e} — falling back to CV")

        # Fallback: CV-based check
        if self._card_detector is None:
            self._log(f"  No card detector — skipping {context} UI check")
            return True
        is_open = self._card_detector.is_map_ui_open()
        if not is_open:
            self._log(f"  WARNING: {context} UI does NOT appear to be open")
        else:
            self._log(f"  {context} UI confirmed open (CV)")
        return is_open

    def _check_region_selection_open(self) -> bool:
        if self._screen_capture is None:
            self._log("  No screen capture — skipping region selection check")
            return True
        try:
            import cv2
            import numpy as np
        except ImportError:
            self._log("  CV2/numpy not available — skipping region selection check")
            return True

        if not hasattr(self, '_ga_template') or self._ga_template is None:
            import os
            if os.path.exists(GLACIAL_ABYSS_TEMPLATE_PATH):
                self._ga_template = cv2.imread(GLACIAL_ABYSS_TEMPLATE_PATH)
                if self._ga_template is not None:
                    self._log(f"  Loaded Glacial Abyss template: {self._ga_template.shape}")
                else:
                    self._log("  WARNING: Could not read template image")
            else:
                self._ga_template = None
                self._log(f"  WARNING: Template not found at {GLACIAL_ABYSS_TEMPLATE_PATH}")

        frame = self._screen_capture.capture_window()
        if frame is None:
            self._log("  Capture failed — skipping region selection check")
            return True

        if self._ga_template is None:
            self._log("  No template available — falling back to pixel check")
            return self._check_region_selection_fallback(frame)

        h_img, w_img = frame.shape[:2]
        x1, y1, x2, y2 = GLACIAL_ABYSS_TEXT_REGION
        th, tw = self._ga_template.shape[:2]

        margin = 30
        search_x1 = max(0, x1 - margin)
        search_y1 = max(0, y1 - margin)
        search_x2 = min(w_img, x2 + margin)
        search_y2 = min(h_img, y2 + margin)

        if (search_x2 - search_x1) < tw or (search_y2 - search_y1) < th:
            self._log("  Search region too small for template")
            return False

        search_region = frame[search_y1:search_y2, search_x1:search_x2]
        result = cv2.matchTemplate(search_region, self._ga_template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        is_open = max_val >= GLACIAL_ABYSS_MATCH_THRESHOLD

        self._log(
            "  Region selection check (template): score={:.3f} threshold={} loc=({},{}) -> {}".format(
                max_val, GLACIAL_ABYSS_MATCH_THRESHOLD,
                max_loc[0] + search_x1, max_loc[1] + search_y1,
                "OPEN" if is_open else "NOT OPEN"))

        return is_open

    def _check_region_selection_fallback(self, frame) -> bool:
        import cv2
        import numpy as np

        h_img, w_img = frame.shape[:2]
        x1, y1, x2, y2 = GLACIAL_ABYSS_TEXT_REGION
        x1 = max(0, min(x1, w_img - 1))
        x2 = max(0, min(x2, w_img))
        y1 = max(0, min(y1, h_img - 1))
        y2 = max(0, min(y2, h_img))

        patch = frame[y1:y2, x1:x2]
        if patch.size == 0:
            return True

        gray_patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        mean_b = float(np.mean(patch[:, :, 0]))
        mean_g = float(np.mean(patch[:, :, 1]))
        mean_r = float(np.mean(patch[:, :, 2]))
        mean_gray = (mean_r + mean_g + mean_b) / 3.0
        median_gray = float(np.median(gray_patch))

        is_dark_panel = (
            30 < mean_gray < 95
            and median_gray < 55
            and mean_b >= mean_r
            and mean_b >= mean_g
        )

        self._log(
            "  Region selection fallback: RGB=({:.0f},{:.0f},{:.0f}) gray={:.0f} median={:.0f} -> {}".format(
                mean_r, mean_g, mean_b, mean_gray, median_gray,
                "OPEN" if is_dark_panel else "NOT OPEN"))

        return is_dark_panel

    def _open_map_device(self) -> bool:
        if self._cancelled:
            return False

        interact_key = self._config.get("interact_key", "f")
        max_retries = 3

        for attempt in range(1, max_retries + 1):
            self._log(f"  Pressing {interact_key.upper()} to open map device (attempt {attempt}/{max_retries})")
            self._input.press_key(interact_key)
            time.sleep(0.8)

            if self._check_region_selection_open():
                return True

            self._log("  Region selection UI not detected — will retry")
            self._input.press_key("escape")
            time.sleep(0.3)

        self._log("  FAILED: Could not open map device after all retries")
        return False

    def _select_zone(self) -> bool:
        if self._cancelled:
            return False

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            self._log(f"  Clicking Glacial Abyss zone (attempt {attempt}/{max_retries})")
            self._input.click(*GLACIAL_ABYSS_ZONE_CLICK)
            time.sleep(0.8)

            if self._check_ui_open("card selection"):
                return True

            self._log("  Card UI not visible after zone click — retrying")
            if attempt < max_retries:
                self._input.press_key("escape")
                time.sleep(0.3)
                interact_key = self._config.get("interact_key", "f")
                self._log(f"  Re-pressing {interact_key.upper()} to re-open map device")
                self._input.press_key(interact_key)
                time.sleep(0.8)

        self._log("  FAILED: Card selection UI never appeared after zone clicks")
        return False

    def _select_card(self) -> bool:
        if self._cancelled:
            return False

        # ── Primary path: memory-based card selection ──────────────────────
        if self._mem_selector is not None:
            result = self._select_card_memory()
            if result is not None:
                return result
            # result is None → memory system unavailable, fall through to CV
            self._log("  Memory card selection unavailable — falling back to CV")

        # ── Fallback: CV-based card selection (old approach) ───────────────
        return self._select_card_cv()

    def _select_card_memory(self) -> Optional[bool]:
        """Select best card using memory reading.

        Returns:
            True  — card successfully selected
            False — card selection failed (no cards / all verification failed)
            None  — memory system unavailable, caller should fall back to CV
        """
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            if self._cancelled:
                return False

            self._log(f"  Memory card selection attempt {attempt}/{max_attempts}")

            try:
                # Step 1: Verify UI is open via memory
                if not self._mem_selector.is_card_ui_open():
                    self._log("  Card UI not open (memory) — aborting to avoid wrong screen")
                    return False

                # Step 2: Detect all cards via memory
                best, all_cards, elapsed = self._mem_selector.select_best_card()
                if not all_cards:
                    self._log(f"  No cards detected by memory ({elapsed:.0f}ms) — aborting")
                    return False

                self._log(f"  Memory detected {len(all_cards)} cards in {elapsed:.0f}ms")
                for c in all_cards:
                    name = f"{c.clean_texture} -> {c.card_name}" if c.card_name else c.clean_texture
                    rank = f"rank={c.priority_rank}" if c.priority_rank < 9999 else "unranked"
                    self._log(f"    {name} ({c.rarity_name}) {rank}")

                if not best:
                    self._log("  No best card determined — aborting")
                    return False

                target_name = f"{best.clean_texture} -> {best.card_name}" if best.card_name else best.clean_texture
                self._log(f"  Target: {target_name} ({best.rarity_name}, rank={best.priority_rank})")

                hex_data = self._hex_calibrator.get_hex_data()
                if hex_data is None:
                    self._log("  No calibration data — cannot click hexes")
                    return None  # Signal fallback to CV
                hexagons = hex_data.get("hexagons", {})

                # Check for any unknown cards and snap screenshots for database updates
                self._screenshot_unknown_cards(all_cards, hexagons)

                # Step 3: Direct CV-free Pure Memory click
                resolved_hex = best.hex_slot_index

                if resolved_hex >= 0 and resolved_hex in hexagons:
                    center = hexagons[resolved_hex]["center"]
                    self._input.click(*center)
                    time.sleep(0.3)
                    if self._verify_card_selected():
                        self._last_selected = resolved_hex
                        self._log(f"  [MEMORY DIRECT] Clicked hex {resolved_hex} ({target_name})")
                        return True
                    self._log(f"  Direct click on hex {resolved_hex} failed to verify")

                # Fallback: Try all other detected cards if the best one didn't activate
                for fb_card in all_cards[1:]:
                    if self._cancelled: return False
                    h_idx = fb_card.hex_slot_index
                    if h_idx < 0 or h_idx not in hexagons: continue
                    center = hexagons[h_idx]["center"]
                    self._input.click(*center)
                    time.sleep(0.3)
                    if self._verify_card_selected():
                        self._last_selected = h_idx
                        self._log(f"  [MEMORY DIRECT FALLBACK] Clicked hex {h_idx}")
                        return True

                self._log("  No memory hex mapping activated successfully — throwing to CV fallback")

                # All hexes tried, none had active cards
                self._log(f"  No active card found across {len(all_cards)} memory cards — retrying")
                self._input.press_key("escape")
                time.sleep(0.4)

            except Exception as e:
                self._log(f"  Memory card selection error: {e} — pressing ESC")
                self._input.press_key("escape")
                time.sleep(0.4)

        self._log("  All memory card selection attempts failed")
        return False

    def _get_hex_candidates(self) -> List[int]:
        """Get ordered list of hex positions to try.

        Uses CV detect_active_cards() to narrow candidates if available,
        otherwise returns all 12 positions.
        """
        # Try CV active detection for speed (identifies ~3 active hexes from screenshot)
        if self._card_detector is not None:
            try:
                active, unknown = self._card_detector.detect_active_cards(debug=False)
                if active is None:
                    active = []

                # Combine active + unknown, then append remaining positions
                candidates = list(active) + [u for u in (unknown or []) if u not in active]
                remaining = [i for i in range(12) if i not in candidates]
                all_positions = candidates + remaining
                self._log(f"  CV hint: {len(active)} active, {len(unknown or [])} unknown → trying active/unknown first")
                return all_positions
            except Exception as e:
                self._log(f"  CV hex detection failed: {e} — trying all positions")

        # Fallback: all 12 positions in standard order
        return list(range(12))

    def _verify_card_selected(self) -> bool:
        """Verify a card was successfully selected (attempts text visible).

        Uses CV-based verification as the definitive check that a real card
        is selected (shows "X Attempts" text in blue).
        """
        if self._card_detector is not None:
            return self._card_detector.verify_active_card_selected()

        # No CV detector — optimistic: assume selection worked
        self._log("  WARNING: No CV detector for attempts verification — assuming ok")
        return True

    def _screenshot_unknown_cards(self, all_cards, hexagons) -> None:
        """Click and screenshot any unknown cards for DB updates."""
        unknowns = [c for c in all_cards if c.priority_rank >= 9999]
        if not unknowns:
            return

        import os, time, cv2
        os.makedirs("debug", exist_ok=True)
        
        for c in unknowns:
            if self._cancelled:
                break
                
            hex_idx = c.hex_slot_index
            if hex_idx < 0 or hex_idx not in hexagons:
                continue
                
            h = hexagons[hex_idx]
            cx, cy = h["center"]
            
            self._log(f"  [Unknown Card] Inspecting {c.clean_texture} ({c.rarity_name}) at hex {hex_idx}")
            self._input.click(int(cx), int(cy))
            
            # wait for details to appear
            time.sleep(0.5)
            
            # screenshot
            shot = self._screen.get_screen()
            fname = f"debug/unknown_card_{c.clean_texture}_{c.rarity_name}_{int(time.time())}.png"
            cv2.imwrite(fname, cv2.cvtColor(shot, cv2.COLOR_RGB2BGR))
            self._log(f"  Saved details screenshot: {fname}")
            
            # Press Esc to close the modal/tooltip
            self._input.press_key('escape')
            time.sleep(0.5)

    def _select_card_cv(self) -> bool:
        """Legacy CV-based card selection (fallback when memory unavailable)."""

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            if self._cancelled:
                return False

            self._log(f"  Card selection attempt {attempt}/{max_attempts}")

            if self._card_detector is None:
                self._log("  No card detector available — aborting")
                return False

            if not self._card_detector.is_map_ui_open():
                self._log("  Card UI not open — aborting card detection to avoid scanning wrong screen")
                return False

            try:
                active, unknown = self._card_detector.detect_active_cards(debug=True)
                if active is None:
                    active = []

                if len(active) == 0 and len(unknown) == 0:
                    self._log("  No active or unknown cards detected — aborting (inactive card = penalty run)")
                    return False

                candidates = active
                is_unknown_fallback = False
                if len(active) == 0 and len(unknown) > 0:
                    self._log(f"  No confirmed ACTIVE cards, but {len(unknown)} UNKNOWN candidates: {unknown}")
                    self._log(f"  Will try UNKNOWN cards with attempts verification as safety gate")
                    candidates = unknown
                    is_unknown_fallback = True

                rarities = self._card_detector.get_rarities() or {}
                if not is_unknown_fallback:
                    self.set_active_cards(candidates)

                if rarities and not is_unknown_fallback:
                    rarity_map = {}
                    for idx, r_info in rarities.items():
                        r_name = r_info.get("rarity", "BLUE")
                        try:
                            rarity_map[idx] = CardRarity[r_name]
                        except KeyError:
                            rarity_map[idx] = CardRarity.BLUE

                    for idx in candidates:
                        card = self._find_card_at(idx)
                        if card and idx in rarity_map:
                            card.rarity = rarity_map[idx]

                if is_unknown_fallback:
                    best_pos = candidates[0]
                else:
                    best_pos = self.select_best_map()
                    if best_pos is None:
                        best_pos = candidates[0]

                hex_data = self._hex_calibrator.get_hex_data()["hexagons"][best_pos]
                self._input.click(*hex_data["center"])
                time.sleep(0.3)

                source_label = "UNKNOWN fallback" if is_unknown_fallback else "ACTIVE"
                rarity_label = "Unknown"
                if best_pos in rarities:
                    rarity_label = rarities[best_pos].get("rarity", "Unknown")
                self._log(f"  Smart select: clicked hex {best_pos} ({rarity_label}) [{source_label}]")

                if self._card_detector.verify_active_card_selected():
                    self._log(f"  Attempts text CONFIRMED — card is active")
                    return True

                if is_unknown_fallback:
                    self._log(f"  UNKNOWN card {best_pos} FAILED attempts verification — confirmed inactive, skipping")
                    self._input.press_key("escape")
                    time.sleep(0.3)
                    remaining_unknown = [u for u in unknown if u != best_pos]
                    for fallback_pos in remaining_unknown:
                        if self._cancelled:
                            return False
                        hex_data = self._hex_calibrator.get_hex_data()["hexagons"][fallback_pos]
                        self._input.click(*hex_data["center"])
                        time.sleep(0.3)
                        self._log(f"  Trying next UNKNOWN card: hex {fallback_pos}")
                        if self._card_detector.verify_active_card_selected():
                            self._log(f"  Attempts text CONFIRMED — UNKNOWN card {fallback_pos} is active!")
                            return True
                        self._log(f"  UNKNOWN card {fallback_pos} also FAILED verification")
                        self._input.press_key("escape")
                        time.sleep(0.3)

                self._log(f"  Attempts text NOT found — pressing ESC to dismiss and retry")
                self._input.press_key("escape")
                time.sleep(0.4)

            except Exception as e:
                self._log(f"  Card detection error: {e} — pressing ESC and retrying")
                self._input.press_key("escape")
                time.sleep(0.4)

        self._log("  All card selection attempts failed — aborting (no brute-force fallback)")
        return False

    def _open_portal_sequence(self) -> bool:
        if self._cancelled:
            return False

        self._log("  Clicking Next")
        self._input.click(*NEXT_BUTTON)
        time.sleep(0.5)

        self._log("  Adding 5 affixes")
        for i in range(5):
            if self._cancelled:
                return False
            self._input.click(*ADD_AFFIX_BUTTON)
            time.sleep(0.1)

        time.sleep(0.5)

        self._log("  Opening Portal (instant teleport)")
        self._input.click(*OPEN_PORTAL_BUTTON_POS)
        time.sleep(0.35)
        self._log("  Checking one-time Survival popup after Open Portal")
        self.check_and_dismiss_tip_popup()
        self._input.click(*OPEN_PORTAL_BUTTON_POS)
        time.sleep(0.3)

        return True

    def check_and_dismiss_tip_popup(self) -> bool:
        if self._screen_capture is None:
            return False

        try:
            import numpy as np
        except ImportError:
            return False

        x, y, w, h = TIP_POPUP_DIALOG_REGION
        threshold = TIP_POPUP_WHITE_THRESHOLD

        frame = self._screen_capture.capture_region(x, y, w, h)
        if frame is None:
            return False

        mean_rgb = np.mean(frame, axis=(0, 1))
        r, g, b = mean_rgb[0], mean_rgb[1], mean_rgb[2]
        self._log(f"  Popup check — mean RGB: ({r:.0f}, {g:.0f}, {b:.0f})")

        if r >= threshold and g >= threshold and b >= threshold:
            self._log("  Tip popup detected — dismissing")
            time.sleep(0.1)
            self._input.click(*TIP_POPUP_DONT_SHOW_CHECKBOX)
            time.sleep(0.3)
            self._input.click(*TIP_POPUP_CONFIRM_BUTTON)
            time.sleep(0.5)
            self._log("  Tip popup dismissed")
            return True

        return False

    def update_card_state(self, position: int):
        card = self._find_card_at(position)
        if card:
            card.attempts_remaining = max(0, card.attempts_remaining - 1)
            self._log(f"Card at position {position}: {card.attempts_remaining} attempts remaining")
            self._save_card_state()

    def initialize_cards(self, active_positions: Optional[List[int]] = None,
                         rarities: Optional[List[CardRarity]] = None):
        self._cards = []
        for i, name in enumerate(MAP_NAMES):
            if i < 12:
                is_active = i in (active_positions or [])
                card = MapCard(
                    map_name=name,
                    hex_position=i,
                    rarity=CardRarity.BLUE,
                    attempts_remaining=4,
                    is_active=is_active,
                )
                self._cards.append(card)

        if active_positions:
            self._active_positions = active_positions[:3]
            if rarities:
                for i, pos in enumerate(self._active_positions):
                    if i < len(rarities):
                        card = self._find_card_at(pos)
                        if card:
                            card.rarity = rarities[i]

        self._save_card_state()
        self._log(f"Initialized {len(self._cards)} map cards")

    def _save_card_state(self):
        state = {
            "active_positions": self._active_positions,
            "cards": [],
        }
        for card in self._cards:
            state["cards"].append({
                "map_name": card.map_name,
                "hex_position": card.hex_position,
                "rarity": card.rarity.name,
                "attempts_remaining": card.attempts_remaining,
                "is_active": card.is_active,
            })

        try:
            with open(CARD_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save card state: {e}")

    def _load_card_state(self):
        if not os.path.exists(CARD_STATE_FILE):
            self.initialize_cards()
            return

        try:
            with open(CARD_STATE_FILE, "r") as f:
                state = json.load(f)

            self._active_positions = state.get("active_positions", [])
            self._cards = []
            for card_data in state.get("cards", []):
                rarity_name = card_data.get("rarity", "BLUE")
                try:
                    rarity = CardRarity[rarity_name]
                except KeyError:
                    rarity = CardRarity.BLUE

                card = MapCard(
                    map_name=card_data.get("map_name", ""),
                    hex_position=card_data.get("hex_position", 0),
                    rarity=rarity,
                    attempts_remaining=card_data.get("attempts_remaining", 4),
                    is_active=card_data.get("is_active", False),
                )
                self._cards.append(card)

            self._log(f"Loaded card state: {len(self._cards)} cards, active={self._active_positions}")
        except Exception as e:
            log.error(f"Failed to load card state: {e}")
            self.initialize_cards()
