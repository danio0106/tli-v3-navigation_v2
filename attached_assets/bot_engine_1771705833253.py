import time
import math
import random
import threading
from enum import Enum, auto
from src.modules.map_navigator import MapNavigator


class BotState(Enum):
    IDLE = auto()
    IN_HIDEOUT = auto()
    SELECTING_MAP_ZONE = auto()
    SELECTING_CARD = auto()
    CARD_DETAIL = auto()
    ADDING_AFFIXES = auto()
    OPENING_PORTAL = auto()
    WAITING_MAP_LOAD = auto()
    RUNNING_MAP = auto()
    SEARCHING_EXIT = auto()
    EXITING_MAP = auto()
    RETURNING_HIDEOUT = auto()
    MOVING = auto()
    STUCK = auto()
    PAUSED = auto()
    STOPPED = auto()
    LOADING = auto()


class MovementPattern(Enum):
    SPIRAL = "spiral"
    ZIGZAG = "zigzag"
    RANDOM_WALK = "random_walk"
    MINIMAP_GUIDED = "minimap_guided"


class BotEngine:
    def __init__(self, input_controller, screen_reader, config_manager, window_manager=None, log_callback=None):
        self.input = input_controller
        self.screen = screen_reader
        self.config = config_manager
        self.window = window_manager
        self.log = log_callback or (lambda msg, level="info": None)

        self._state = BotState.IDLE
        self._previous_state = BotState.IDLE
        self._running = False
        self._paused = False
        self._thread = None
        self._lock = threading.Lock()

        self._stats = {
            "maps_completed": 0,
            "total_clicks": 0,
            "runtime_seconds": 0,
            "stuck_count": 0,
            "start_time": None,
        }

        self._movement_index = 0
        self._spiral_angle = 0
        self._spiral_radius = 100
        self._last_position = None
        self._stuck_timer = 0
        self._last_move_time = 0
        self._automation_available = None

        self._affix_clicks = 0
        self._map_run_start = 0
        self._e_spam_timer = 0
        self._retry_count = 0
        self._max_retries = 5

        self.navigator = MapNavigator(
            screen_reader=self.screen,
            input_controller=self.input,
            window_manager=self.window,
            config=self.config,
            log_func=self.log,
        )

    @property
    def state(self):
        with self._lock:
            return self._state

    @state.setter
    def state(self, value):
        with self._lock:
            self._state = value

    @property
    def stats(self):
        with self._lock:
            return dict(self._stats)

    @property
    def is_running(self):
        return self._running

    @property
    def is_paused(self):
        return self._paused

    def _check_automation_available(self):
        if self._automation_available is None:
            pag = self.input._get_pyautogui()
            self._automation_available = pag is not None
        return self._automation_available

    def _get_game_center(self):
        if self.window:
            center = self.window.get_window_center()
            if center:
                return center
        screen_cfg = self.config.get("screen")
        return (screen_cfg.get("center_x", 960), screen_cfg.get("center_y", 540))

    def _get_game_rect(self):
        if self.window:
            rect = self.window.get_window_rect()
            if rect:
                return rect
        screen_cfg = self.config.get("screen")
        w, h = screen_cfg.get("game_resolution", [1920, 1080])
        return {"left": 0, "top": 0, "width": w, "height": h, "right": w, "bottom": h}

    def _game_click(self, game_x, game_y, delay=0.3):
        if self.window:
            screen_x, screen_y = self.window.game_to_screen_coords(game_x, game_y)
        else:
            screen_x, screen_y = game_x, game_y
        self.input.click(screen_x, screen_y)
        with self._lock:
            self._stats["total_clicks"] += 1
        if delay > 0:
            time.sleep(delay)

    def _game_press(self, key, delay=0.15):
        self.input.press_key(key)
        if delay > 0:
            time.sleep(delay)

    def start(self):
        if self._running:
            return

        if not self._check_automation_available():
            self.log("Screen automation not available - pyautogui could not connect to display. "
                     "Make sure the game is running and a display is available.", "error")
            return

        if self.window:
            status = self.window.get_status_info()
            if not status["found"]:
                self.log(f"Game window not found: '{status['title']}'. "
                         "Make sure the game is running.", "error")
                return
            self.log(f"Game window found: {status['message']}", "success")

        self._running = True
        self._paused = False
        self._retry_count = 0
        self._affix_clicks = 0
        with self._lock:
            self._stats["start_time"] = time.time()
        self.input.enabled = True

        if self.screen.is_in_hideout(log_func=self.log):
            self.state = BotState.IN_HIDEOUT
            self.log("Bot started - in hideout, beginning map selection flow", "success")
        else:
            map_name = self.screen.read_location_text()
            if map_name and map_name.strip():
                self.log(f"Bot started - detected location text: '{map_name}'", "info")
                matched_name = self._match_map_name(map_name)
                if matched_name:
                    self.log(f"Bot started - matched to map '{matched_name}', skipping to navigation", "success")
                    self.state = BotState.RUNNING_MAP
                    self.navigator.reset(map_name=matched_name)
                    self._map_run_start = time.time()
                else:
                    self.state = BotState.RUNNING_MAP
                    self.navigator.reset(map_name=map_name)
                    self._map_run_start = time.time()
                    self.log(f"Bot started - in map '{map_name}' (no config match), using wall avoidance", "warning")
            else:
                self.state = BotState.IN_HIDEOUT
                self.log("Bot started - could not determine location, assuming hideout", "info")

        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._paused = False
        self.state = BotState.STOPPED
        self.input.enabled = False
        if hasattr(self, 'navigator') and self.navigator:
            self.navigator.stop_scanner()
        import threading
        if self._thread and self._thread.is_alive() and self._thread != threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None
        self.log("Bot stopped", "warning")

    def pause(self):
        if not self._running:
            return
        self._paused = True
        with self._lock:
            self._previous_state = self._state
            self._state = BotState.PAUSED
        self.input.enabled = False
        self.log("Bot paused", "warning")

    def resume(self):
        if not self._running or not self._paused:
            return
        self._paused = False
        with self._lock:
            self._state = self._previous_state
        self.input.enabled = True
        self.log("Bot resumed", "success")

    def _main_loop(self):
        while self._running:
            if self._paused:
                time.sleep(0.1)
                continue

            try:
                current_state = self.state
                if current_state == BotState.IN_HIDEOUT:
                    self._handle_in_hideout()
                elif current_state == BotState.SELECTING_MAP_ZONE:
                    self._handle_selecting_map_zone()
                elif current_state == BotState.SELECTING_CARD:
                    self._handle_selecting_card()
                elif current_state == BotState.CARD_DETAIL:
                    self._handle_card_detail()
                elif current_state == BotState.ADDING_AFFIXES:
                    self._handle_adding_affixes()
                elif current_state == BotState.OPENING_PORTAL:
                    self._handle_opening_portal()
                elif current_state == BotState.WAITING_MAP_LOAD:
                    self._handle_waiting_map_load()
                elif current_state == BotState.RUNNING_MAP:
                    self._handle_running_map()
                elif current_state == BotState.SEARCHING_EXIT:
                    self._handle_searching_exit()
                elif current_state == BotState.EXITING_MAP:
                    self._handle_exiting_map()
                elif current_state == BotState.RETURNING_HIDEOUT:
                    self._handle_returning_hideout()
                elif current_state == BotState.MOVING:
                    self._handle_running_map()
                elif current_state == BotState.STUCK:
                    self._handle_stuck()
                else:
                    time.sleep(0.1)
            except Exception as e:
                self.log(f"Error in bot loop: {str(e)}", "error")
                time.sleep(1)

            self._update_stats()

    def _handle_in_hideout(self):
        self.log("In hideout - pressing F to interact with map device...", "info")
        time.sleep(0.5)

        map_cfg = self.config.get("map_selection")
        if self.screen.is_in_hideout(log_func=self.log):
            self.log("Hideout confirmed via location text", "success")
        else:
            self.log("Location text not detected as hideout, proceeding anyway...", "warning")

        self._game_press('f', delay=0.8)

        time.sleep(1.0)

        if self.screen.is_netherrealm_open():
            self.log("Netherrealm map selection opened!", "success")
            self._retry_count = 0
            self.state = BotState.SELECTING_MAP_ZONE
        else:
            self._retry_count += 1
            if self._retry_count >= self._max_retries:
                self.log("Failed to open Netherrealm after multiple attempts. Stopping.", "error")
                self.stop()
            else:
                self.log(f"Netherrealm not detected, retrying... ({self._retry_count}/{self._max_retries})", "warning")
                time.sleep(1.0)

    def _handle_selecting_map_zone(self):
        map_cfg = self.config.get("map_selection")
        target = map_cfg.get("target_map", "Glacial Abyss")
        click_pos = map_cfg.get("glacial_abyss_click", [530, 599])

        self.log(f"Clicking on {target} at ({click_pos[0]}, {click_pos[1]})...", "info")
        self._game_click(click_pos[0], click_pos[1], delay=1.5)

        time.sleep(1.5)

        self.log("Checking screen state after click...", "info")
        if self.screen.is_card_selection_screen(log_func=self.log):
            self.log(f"{target} card selection screen opened!", "success")
            self._retry_count = 0
            self.state = BotState.SELECTING_CARD
        elif self.screen.is_netherrealm_open(log_func=self.log):
            self._retry_count += 1
            if self._retry_count >= self._max_retries:
                self.log(f"Still on zone selection after {self._max_retries} clicks. Check click coordinates for {target}.", "error")
                self.stop()
            else:
                self.log(f"Still on zone selection (red zone icons visible), retrying... ({self._retry_count}/{self._max_retries})", "warning")
                time.sleep(0.5)
        else:
            self._retry_count += 1
            if self._retry_count >= self._max_retries:
                self.log(f"Unknown screen state after clicking {target}. Stopping.", "error")
                self.stop()
            else:
                self.log(f"Screen state unclear, retrying click... ({self._retry_count}/{self._max_retries})", "warning")
                time.sleep(0.5)

    def _handle_selecting_card(self):
        map_cfg = self.config.get("map_selection")
        prefer_rarest = map_cfg.get("prefer_rarest_card", True)
        card_positions = map_cfg.get("card_positions", {})

        if hasattr(self, '_pending_card') and self._pending_card is not None:
            chosen = self._pending_card
            click_x, click_y = chosen['game_x'], chosen['game_y'] + 58
            self.log(f"Re-clicking {chosen.get('card_name', 'card')} at ({click_x}, {click_y})...", "info")
            self._game_click(click_x, click_y, delay=1.0)
            time.sleep(1.5)

            if self.screen.detect_right_panel_open(log_func=self.log):
                self.log("Card detail panel opened!", "success")
                self._retry_count = 0
                self._pending_card = None
                self.state = BotState.CARD_DETAIL
            else:
                self._retry_count += 1
                if self._retry_count >= 5:
                    self.log("Card detail panel still not detected after 5 tries, re-scanning...", "warning")
                    self._retry_count = 0
                    self._pending_card = None
                time.sleep(0.5)
            return

        self.log("Scanning for active map cards...", "info")
        time.sleep(0.5)

        cards = self.screen.find_active_cards(card_positions=card_positions, log_func=self.log)

        if not cards:
            self._retry_count += 1
            if self._retry_count >= self._max_retries:
                self.log("No active cards found after scanning. Stopping.", "error")
                self.stop()
            else:
                self.log(f"No active cards detected, rescanning... ({self._retry_count}/{self._max_retries})", "warning")
                time.sleep(1.0)
            return

        card_names = [c.get('card_name', '?') for c in cards]
        self.log(f"Found {len(cards)} active card(s): {', '.join(card_names)}", "info")

        if prefer_rarest:
            chosen = cards[0]
            name = chosen.get('card_name', 'unknown')
            self.log(f"Picking rarest: {name} (score: {chosen['rarity_score']:.0f}, "
                     f"hue: {chosen.get('border_hue', chosen.get('mean_hue', 0)):.0f})", "info")
        else:
            chosen = cards[0]
            name = chosen.get('card_name', 'unknown')
            self.log(f"Picking first available: {name}", "info")

        click_x, click_y = chosen['game_x'], chosen['game_y'] + 58
        self.log(f"Clicking {name} at ({click_x}, {click_y})...", "info")
        self._game_click(click_x, click_y, delay=1.0)

        time.sleep(1.5)

        if self.screen.detect_right_panel_open(log_func=self.log):
            self.log("Card detail panel opened!", "success")
            current, total = self.screen.read_attempts(log_func=self.log)
            if current is not None and total is not None:
                self.log(f"Attempt {current}/{total}", "info")
            self._retry_count = 0
            self._pending_card = None
            self.state = BotState.CARD_DETAIL
        else:
            self.log("Card detail panel not detected, will retry same card...", "warning")
            self._pending_card = chosen
            self._retry_count = 1
            time.sleep(0.5)

    def _handle_card_detail(self):
        map_cfg = self.config.get("map_selection")
        next_pos = map_cfg.get("next_button", [1750, 1000])

        btn_text = self.screen.detect_button_text(log_func=self.log)
        next_ratio = self.screen._fuzzy_match(btn_text, "next")
        portal_ratio = self.screen._fuzzy_match(btn_text, "open")

        if portal_ratio >= 0.6:
            self.log("Button already shows 'Open Portal' - skipping to affix/portal stage", "success")
            self._affix_clicks = 0
            self._retry_count = 0
            self.state = BotState.ADDING_AFFIXES
            return

        if next_ratio >= 0.6:
            self.log("Clicking Next button...", "info")
            self._game_click(next_pos[0], next_pos[1], delay=1.0)
            time.sleep(1.5)

            btn_after = self.screen.detect_button_text(log_func=self.log)
            portal_after = self.screen._fuzzy_match(btn_after, "open")
            next_after = self.screen._fuzzy_match(btn_after, "next")

            if portal_after >= 0.6 or (next_after < 0.6 and len(btn_after.strip()) > 0):
                self.log("Button changed - affix stage reached!", "success")
                self._affix_clicks = 0
                self._retry_count = 0
                self.state = BotState.ADDING_AFFIXES
            else:
                self._retry_count += 1
                if self._retry_count >= self._max_retries:
                    self.log("Button didn't change after multiple attempts. Stopping.", "error")
                    self.stop()
                else:
                    self.log(f"Button still shows Next, retrying... ({self._retry_count}/{self._max_retries})", "warning")
                    time.sleep(0.5)
        else:
            self._retry_count += 1
            if self._retry_count >= self._max_retries:
                self.log("Cannot read button text. Stopping.", "error")
                self.stop()
            else:
                self.log(f"Button text unclear, retrying... ({self._retry_count}/{self._max_retries})", "warning")
                time.sleep(0.5)

    def _handle_adding_affixes(self):
        map_cfg = self.config.get("map_selection")
        add_pos = map_cfg.get("add_affix_button", [120, 819])
        reset_pos = map_cfg.get("reset_affix_button", [634, 810])
        max_affixes = map_cfg.get("affix_count", 5)
        detect_red = map_cfg.get("detect_red_affix", False)

        if self._affix_clicks == 0:
            drop_pct = self.screen.read_drop_quantity_pct()
            if drop_pct is not None and drop_pct >= 100:
                self.log(f"Affixes already full from previous attempt (drop +{drop_pct}%) - skipping to Open Portal!", "success")
                self._affix_clicks = max_affixes
                self._retry_count = 0
                self.state = BotState.OPENING_PORTAL
                return

        if self._affix_clicks >= max_affixes:
            self.log(f"All {max_affixes} affixes added!", "success")

            drop_pct = self.screen.read_drop_quantity_pct()
            if drop_pct is not None:
                self.log(f"  Drop quantity confirmed: +{drop_pct}%", "info")

            red_lines = self.screen.read_red_affix_text()
            if red_lines:
                self.log(f"Red affix text found ({len(red_lines)} line(s)):", "warning")
                for line in red_lines:
                    self.log(f"  RED: {line}", "warning")
            else:
                self.log("No red affix text detected", "info")

            if detect_red and red_lines:
                self.log("Red affix detected! Clicking Reset Affix...", "warning")
                self._game_click(reset_pos[0], reset_pos[1], delay=0.8)
                self._affix_clicks = 0
                time.sleep(0.5)
                return

            self._retry_count = 0
            self.state = BotState.OPENING_PORTAL
            return

        self._affix_clicks += 1
        self.log(f"Adding affix {self._affix_clicks}/{max_affixes} at ({add_pos[0]}, {add_pos[1]})...", "info")
        self._game_click(add_pos[0], add_pos[1], delay=0.6)
        time.sleep(0.4)

    def _handle_opening_portal(self):
        map_cfg = self.config.get("map_selection")
        portal_pos = map_cfg.get("open_portal_button", [1750, 1000])

        if self.screen.detect_open_portal_button():
            self.log("Open Portal button detected, clicking...", "info")
        else:
            self.log("Open Portal button not clearly visible, clicking position anyway...", "warning")

        self._game_click(portal_pos[0], portal_pos[1], delay=1.0)

        self.log("Waiting for portal transition...", "info")
        transition_detected = False
        for check in range(20):
            if not self._running or self._paused:
                return
            time.sleep(0.5)

            drop_pct = self.screen.read_drop_quantity_pct()
            if drop_pct is None:
                self.log("Drop quantity no longer visible - portal transition started!", "success")
                transition_detected = True
                break

            if self.screen.detect_loading_screen():
                self.log("Loading screen detected - portal transition started!", "success")
                transition_detected = True
                break

        if transition_detected:
            self._retry_count = 0
            self.state = BotState.WAITING_MAP_LOAD
        else:
            self._retry_count += 1
            if self._retry_count >= self._max_retries:
                self.log("Portal button didn't respond after multiple attempts. Stopping.", "error")
                self.stop()
            else:
                self.log(f"Portal transition not detected, retrying click... ({self._retry_count}/{self._max_retries})", "warning")

    def _handle_waiting_map_load(self):
        timing_cfg = self.config.get("timing")
        timeout = timing_cfg.get("loading_screen_timeout", 30.0)

        start = time.time()
        self.log("Waiting for map to load (checking location text)...", "info")

        while self._running and not self._paused:
            elapsed = time.time() - start
            if elapsed > timeout:
                self.log("Map load timeout reached, continuing anyway", "warning")
                break

            if self.screen.is_map_loaded(log_func=self.log):
                self.log(f"Location text detected after {elapsed:.1f}s - in map!", "success")
                break

            time.sleep(0.5)

        time.sleep(1.0)

        self._read_and_log_map_name()

        self.log("Map entered successfully! Starting navigation...", "success")
        map_name = self.screen.read_location_text()
        self.navigator.reset(map_name=map_name)
        self._map_run_start = time.time()
        self.state = BotState.RUNNING_MAP

    def _read_and_log_map_name(self):
        map_name = self.screen.read_location_text()
        if map_name:
            self.log(f"Map name: {map_name}", "success")
        else:
            self.log("Could not read map name from top-left corner", "warning")

    def _match_map_name(self, ocr_text):
        import re
        ocr_clean = re.sub(r'[^a-z\s]', '', ocr_text.strip().lower())
        self.log(f"  [map match] OCR cleaned: '{ocr_clean}'", "info")

        best_name = None
        best_score = 0

        from src.modules.map_navigator import MAP_CONFIGS
        for config_key in MAP_CONFIGS:
            config_clean = re.sub(r'[^a-z\s]', '', config_key)
            if config_clean == ocr_clean or (len(ocr_clean) >= 5 and (config_clean in ocr_clean or ocr_clean in config_clean)):
                self.log(f"  [map match] Direct match: '{config_key}' (100%)", "success")
                return config_key

            config_words = config_clean.split()
            ocr_words = ocr_clean.split()
            if config_words:
                matches = sum(1 for w in config_words if any(self.screen._fuzzy_match(ow, w) > 0.7 for ow in ocr_words))
                score = matches / len(config_words)
                if score > best_score:
                    best_score = score
                    best_name = config_key

        recorded_maps = self.navigator._recorded_path_manager.get_all_maps()
        for rmap in recorded_maps:
            rmap_clean = re.sub(r'[^a-z\s]', '', rmap.strip().lower()).strip()
            if not rmap_clean or len(rmap_clean) < 3:
                continue
            if rmap_clean == ocr_clean or (len(ocr_clean) >= 5 and len(rmap_clean) >= 5 and (rmap_clean in ocr_clean or ocr_clean in rmap_clean)):
                self.log(f"  [map match] Direct match to recorded path: '{rmap}' (100%)", "success")
                return rmap

            rmap_words = rmap_clean.split()
            if rmap_words:
                matches = sum(1 for w in rmap_words if any(self.screen._fuzzy_match(ow, w) > 0.7 for ow in ocr_clean.split()))
                score = matches / len(rmap_words)
                if score > best_score:
                    best_score = score
                    best_name = rmap

        if best_name and best_score >= 0.5:
            self.log(f"  [map match] Best fuzzy match: '{best_name}' ({best_score:.0%})", "info")
            return best_name

        if best_name:
            self.log(f"  [map match] Best match '{best_name}' too low ({best_score:.0%})", "warning")
        return None

    def _handle_running_map(self):
        status = self.navigator.tick()

        if status == "timeout":
            self.log("Map run timed out. Searching for exit portal...", "warning")
            self.navigator.stop_scanner()
            self.state = BotState.SEARCHING_EXIT
            return

        if status == "interacting":
            time.sleep(0.3)
            return

        time.sleep(0.1)

    def _handle_searching_exit(self):
        self.log("Searching for exit portal...", "info")
        search_start = time.time()
        search_timeout = 30.0

        while self._running and not self._paused:
            elapsed = time.time() - search_start
            if elapsed > search_timeout:
                self.log("Exit search timeout. Returning to map run...", "warning")
                self.state = BotState.RUNNING_MAP
                return

            self.navigator.spam_loot_pickup()

            if self.screen.detect_interact_icon():
                self.log("Exit portal interact prompt found! Pressing F...", "success")
                self._game_press('f', delay=1.0)
                self.state = BotState.EXITING_MAP
                return

            if self.screen.is_in_hideout():
                self.log("Already back in hideout!", "success")
                self.state = BotState.RETURNING_HIDEOUT
                return

            time.sleep(0.15)

    def _handle_exiting_map(self):
        self.navigator.stop_scanner()
        self.log("Exiting map, waiting for transition...", "info")
        time.sleep(2.0)

        timeout = 30.0
        start = time.time()

        while self._running and not self._paused:
            if time.time() - start > timeout:
                self.log("Exit transition timeout, checking location...", "warning")
                break

            if self.screen.is_in_hideout():
                self.log("Back in hideout!", "success")
                self.state = BotState.RETURNING_HIDEOUT
                return

            if self.screen.detect_loading_screen():
                time.sleep(0.5)
                continue

            time.sleep(0.5)

        self.state = BotState.RETURNING_HIDEOUT

    def _handle_returning_hideout(self):
        with self._lock:
            self._stats["maps_completed"] += 1
            maps_done = self._stats["maps_completed"]
        self.log(f"Map #{maps_done} completed!", "success")

        restart_cfg = self.config.get("map_restart")
        max_runs = restart_cfg.get("max_runs", 0)
        if max_runs > 0 and maps_done >= max_runs:
            self.log(f"Reached max runs ({max_runs}). Stopping.", "warning")
            self.stop()
            return

        if restart_cfg.get("enabled", True):
            delay = restart_cfg.get("delay_between_runs", 2.0)
            self.log(f"Waiting {delay}s before next map...", "info")
            time.sleep(delay)

            timeout = 15.0
            start = time.time()
            while self._running and not self._paused:
                if time.time() - start > timeout:
                    self.log("Hideout detection timeout, proceeding to map selection...", "warning")
                    break
                if self.screen.is_in_hideout():
                    self.log("Confirmed in hideout, starting next map!", "success")
                    break
                time.sleep(0.5)

            self._retry_count = 0
            self._affix_clicks = 0
            self.state = BotState.IN_HIDEOUT
        else:
            self.log("Auto-restart disabled. Stopping.", "info")
            self.stop()

    def _handle_stuck(self):
        center_x, center_y = self._get_game_center()
        move_cfg = self.config.get("movement")
        max_attempts = move_cfg.get("unstuck_attempts", 3)
        self.log("Attempting to get unstuck...", "warning")
        with self._lock:
            self._stats["stuck_count"] += 1

        for attempt in range(max_attempts):
            if not self._running:
                return
            angle = random.uniform(0, 2 * math.pi)
            dist = random.randint(200, 500)
            x = center_x + int(dist * math.cos(angle))
            y = center_y + int(dist * math.sin(angle))
            self.input.click(x, y)
            time.sleep(0.5)

        self._spiral_angle = random.uniform(0, 2 * math.pi)
        self._stuck_timer = 0
        self._last_position = None
        self.state = BotState.RUNNING_MAP
        self.log("Unstuck attempt complete, resuming movement", "info")

    def _get_spiral_position(self, cx, cy, max_radius):
        self._spiral_angle += 0.4
        self._spiral_radius += 2
        if self._spiral_radius > max_radius:
            self._spiral_radius = 100
            self._spiral_angle += math.pi / 3

        x = cx + int(self._spiral_radius * math.cos(self._spiral_angle))
        y = cy + int(self._spiral_radius * math.sin(self._spiral_angle))
        return x, y

    def _get_zigzag_position(self, cx, cy, max_radius):
        step = self._movement_index
        direction = 1 if (step // 20) % 2 == 0 else -1
        x_offset = direction * (step % 20) * (max_radius // 10)
        y_offset = (step % 40 - 20) * (max_radius // 20)
        x = cx + x_offset
        y = cy + y_offset
        return x, y

    def _get_random_position(self, cx, cy, max_radius):
        angle = random.uniform(0, 2 * math.pi)
        dist = random.randint(100, max_radius)
        x = cx + int(dist * math.cos(angle))
        y = cy + int(dist * math.sin(angle))
        return x, y

    def _get_minimap_guided_position(self, cx, cy, max_radius, detection_cfg):
        minimap_region = detection_cfg.get("minimap_region", [0, 0, 250, 250])
        minimap_data = self.screen.get_minimap_data(minimap_region)

        if minimap_data and minimap_data.get("unexplored_direction") is not None:
            angle = math.radians(minimap_data["unexplored_direction"])
            dist = max_radius * 0.8
            x = cx + int(dist * math.cos(angle))
            y = cy + int(dist * math.sin(angle))
            return x, y
        return self._get_spiral_position(cx, cy, max_radius)

    def _check_stuck(self, move_cfg):
        current_pos = self.input.get_mouse_position()
        threshold = move_cfg.get("stuck_threshold", 3.0)

        if self._last_position:
            dx = abs(current_pos[0] - self._last_position[0])
            dy = abs(current_pos[1] - self._last_position[1])
            if dx < 5 and dy < 5:
                self._stuck_timer += move_cfg.get("click_interval", 0.15)
                if self._stuck_timer > threshold:
                    self.state = BotState.STUCK
                    self.log("Character appears stuck!", "warning")
            else:
                self._stuck_timer = 0
        self._last_position = current_pos

    def _reset_movement(self):
        self._movement_index = 0
        self._spiral_angle = 0
        self._spiral_radius = 100
        self._last_position = None
        self._stuck_timer = 0

    def _update_stats(self):
        with self._lock:
            if self._stats["start_time"]:
                self._stats["runtime_seconds"] = time.time() - self._stats["start_time"]

    def get_status_text(self):
        state_labels = {
            BotState.IDLE: "Idle",
            BotState.IN_HIDEOUT: "In Hideout",
            BotState.SELECTING_MAP_ZONE: "Selecting Map",
            BotState.SELECTING_CARD: "Picking Card",
            BotState.CARD_DETAIL: "Card Detail",
            BotState.ADDING_AFFIXES: "Adding Affixes",
            BotState.OPENING_PORTAL: "Opening Portal",
            BotState.WAITING_MAP_LOAD: "Loading Map",
            BotState.RUNNING_MAP: "Running Map",
            BotState.SEARCHING_EXIT: "Finding Exit",
            BotState.EXITING_MAP: "Exiting Map",
            BotState.RETURNING_HIDEOUT: "Returning",
            BotState.MOVING: "Moving",
            BotState.STUCK: "Stuck - Recovering",
            BotState.PAUSED: "Paused",
            BotState.STOPPED: "Stopped",
            BotState.LOADING: "Loading",
        }
        return state_labels.get(self.state, "Unknown")

    def get_runtime_formatted(self):
        stats = self.stats
        seconds = int(stats.get("runtime_seconds", 0))
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
