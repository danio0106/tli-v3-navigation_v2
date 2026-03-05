import customtkinter as ctk
import threading
import time
from typing import Callable, List, Optional, Set

from src.core.waypoint import Waypoint
from src.gui.theme import COLORS, FONTS, create_card_frame, create_label, create_accent_button, create_entry
from src.utils.constants import MAP_NAMES


class PathsTab(ctk.CTkFrame):
    def __init__(self, parent, bot_engine):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._engine = bot_engine
        self._last_auto_map: str = ""
        self._map_poll_id = None
        self._record_update_id = None
        self._loaded_waypoints: List[Waypoint] = []
        self._selected_indices: Set[int] = set()
        self._wp_buttons: List[ctk.CTkButton] = []
        self._overlay_callback = None
        self._grid_overlay_cb: Optional[Callable] = None
        self._manual_stop_event: Optional[threading.Event] = None
        self._manual_thread: Optional[threading.Thread] = None
        self._is_paused = False
        # Default to auto-navigation mode on startup.
        self._nav_mode = "auto"
        self._engine.config.set("nav_mode", "auto")
        self._build_ui()

    def _get_active_map(self) -> str:
        """Return the English map name currently detected from game memory."""
        try:
            return self._engine._resolve_current_map() or ""
        except Exception:
            return ""

    def _start_map_poll(self):
        """Begin polling detected map every 2 s; auto-load waypoints on map change."""
        self._map_poll_id = self.after(2000, self._map_poll_tick)

    def _map_poll_tick(self):
        """Fire a background thread to resolve map name — never blocks the UI thread."""
        def _bg():
            try:
                name = self._get_active_map()
            except Exception:
                name = ""
            try:
                self.after(0, lambda n=name: self._on_map_poll_result(n))
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True, name="MapPollBG").start()

    def _on_map_poll_result(self, name: str):
        """UI-thread callback with the resolved map name."""
        try:
            if name:
                self._map_name_label.configure(text=name, text_color=COLORS.get("accent_blue", "#58a6ff"))
                if name != self._last_auto_map:
                    self._last_auto_map = name
                    self._load_waypoints_for_map(name)
            else:
                self._map_name_label.configure(text="Not detected", text_color=COLORS.get("text_muted", "#8b949e"))
        except Exception:
            pass
        # Schedule next poll only after result is processed
        try:
            self._map_poll_id = self.after(2000, self._map_poll_tick)
        except Exception:
            pass

    def set_overlay_callback(self, callback):
        self._overlay_callback = callback

    def set_grid_overlay_callback(self, callback: Callable):
        """Called by app with (walkable_xy, frontier_xy, cell_size) → pushes to DebugOverlay."""
        self._grid_overlay_cb = callback

    def _sync_recorder(self):
        recorder = self._engine.path_recorder
        if recorder and recorder.is_recording:
            recorder._waypoints = list(self._loaded_waypoints)

    def _notify_overlay(self):
        self._sync_recorder()
        if self._overlay_callback:
            self._overlay_callback(self._loaded_waypoints)

    def _setup_hotkeys(self):
        root = self.winfo_toplevel()
        root.bind("<p>", lambda e: self._on_mark_portal())
        root.bind("<P>", lambda e: self._on_mark_portal())

    def _build_ui(self):
        self._setup_hotkeys()

        container = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_dark"])
        container.pack(fill="both", expand=True, padx=0, pady=0)

        top = ctk.CTkFrame(container, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(10, 8))
        create_label(top, "Map Paths", "heading").pack(side="left")

        map_card = create_card_frame(container)
        map_card.pack(fill="x", padx=10, pady=(0, 8))

        map_row = ctk.CTkFrame(map_card, fg_color="transparent")
        map_row.pack(fill="x", padx=10, pady=(10, 10))
        create_label(map_row, "Current Map:", "subheading").pack(side="left", padx=(0, 8))
        self._map_name_label = create_label(map_row, "Reading...", "body", "accent_blue")
        self._map_name_label.pack(side="left")
        self._start_map_poll()

        # ── Mode toggle ────────────────────────────────────────────────
        mode_card = create_card_frame(container)
        mode_card.pack(fill="x", padx=10, pady=(0, 8))

        mode_row = ctk.CTkFrame(mode_card, fg_color="transparent")
        mode_row.pack(fill="x", padx=10, pady=(10, 10))
        create_label(mode_row, "Mode:", "subheading").pack(side="left", padx=(0, 8))

        self._mode_record_btn = create_accent_button(
            mode_row, "🎙 Recording", self._on_set_record_mode, color="accent_green", width=130
        )
        self._mode_record_btn.pack(side="left", padx=(0, 4))

        self._mode_auto_btn = create_accent_button(
            mode_row, "🤖 Auto Navigation", self._on_set_auto_mode, color="accent_purple", width=150
        )
        self._mode_auto_btn.pack(side="left")

        self._mode_info = create_label(mode_row, "", "small", "text_muted")
        self._mode_info.pack(side="left", padx=(12, 0))

        # ── Recording section ──────────────────────────────────────────
        self._rec_card = create_card_frame(container)
        self._rec_card.pack(fill="x", padx=10, pady=(0, 8))
        create_label(self._rec_card, "Recording", "subheading").pack(anchor="w", padx=10, pady=(10, 4))

        self._rec_status = create_label(self._rec_card, "Status: Idle", "body", "text_secondary")
        self._rec_status.pack(anchor="w", padx=10, pady=2)
        self._rec_count = create_label(self._rec_card, "Points: 0", "body", "text_secondary")
        self._rec_count.pack(anchor="w", padx=10, pady=(2, 8))

        rec_btns = ctk.CTkFrame(self._rec_card, fg_color="transparent")
        rec_btns.pack(fill="x", padx=10, pady=(0, 10))

        rec_row1 = ctk.CTkFrame(rec_btns, fg_color="transparent")
        rec_row1.pack(fill="x", pady=2)
        self._record_btn = create_accent_button(rec_row1, "Start Recording (F5)", self._on_record, color="accent_green")
        self._record_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._pause_btn = create_accent_button(rec_row1, "Pause (F6)", self._on_pause_record, color="accent_orange")
        self._pause_btn.pack(side="left", fill="x", expand=True, padx=(2, 0))
        self._pause_btn.configure(state="disabled")

        rec_row2 = ctk.CTkFrame(rec_btns, fg_color="transparent")
        rec_row2.pack(fill="x", pady=2)
        self._portal_btn = create_accent_button(rec_row2, "Mark Portal (P)", self._on_mark_portal, color="accent_orange")
        self._portal_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._save_btn = create_accent_button(rec_row2, "Save Path", self._on_save, color="accent_blue")
        self._save_btn.pack(side="left", fill="x", expand=True, padx=(2, 0))

        # ── Auto-navigation section ─────────────────────────────────────
        self._auto_card = create_card_frame(container)
        self._auto_card.pack(fill="x", padx=10, pady=(0, 8))
        create_label(self._auto_card, "Auto-Navigation (A*)", "subheading").pack(anchor="w", padx=10, pady=(10, 4))
        auto_info = create_label(
            self._auto_card,
            "The bot navigates autonomously using an A* grid built from\n"
            "your previously visited positions (MinimapSaveObject).\n"
            "Data is collected automatically as you run maps normally — no\n"
            "special exploration needed. Cached permanently (maps never change).\n"
            "First run on a new map: A* falls back to direct navigation.",
            "small", "text_muted"
        )
        auto_info.pack(anchor="w", padx=10, pady=(0, 6))

        behavior_row = ctk.CTkFrame(self._auto_card, fg_color="transparent")
        behavior_row.pack(fill="x", padx=10, pady=(0, 6))
        create_label(behavior_row, "Auto behavior:", "small").pack(side="left", padx=(0, 6))
        self._auto_behavior_menu = ctk.CTkOptionMenu(
            behavior_row,
            values=["Rush Events", "Kill All", "Boss Rush"],
            width=170,
            fg_color=COLORS["bg_light"],
            button_color=COLORS["border"],
            button_hover_color=COLORS["bg_medium"],
            text_color=COLORS["text_primary"],
            font=FONTS["small"],
            command=self._on_auto_behavior_change,
        )
        self._auto_behavior_menu.pack(side="left")

        # Walkable-area cache section
        wall_row = ctk.CTkFrame(self._auto_card, fg_color="transparent")
        wall_row.pack(fill="x", padx=10, pady=(4, 2))
        create_label(wall_row, "Walkable data:", "small").pack(side="left", padx=(0, 6))
        self._wall_status_label = create_label(wall_row, "—", "small", "text_muted")
        self._wall_status_label.pack(side="left", fill="x", expand=True)

        # ── Coverage overview ──────────────────────────────────────────
        cov_sep = ctk.CTkFrame(self._auto_card, fg_color=COLORS["border"], height=1)
        cov_sep.pack(fill="x", padx=10, pady=(6, 6))

        cov_header_row = ctk.CTkFrame(self._auto_card, fg_color="transparent")
        cov_header_row.pack(fill="x", padx=10, pady=(0, 4))
        create_label(cov_header_row, "Coverage Overview", "subheading").pack(side="left")
        self._cov_refresh_btn = create_accent_button(
            cov_header_row, "↻", self._refresh_coverage_overview, color="accent_blue", width=30
        )
        self._cov_refresh_btn.pack(side="right")

        self._cov_frame = ctk.CTkScrollableFrame(
            self._auto_card, fg_color=COLORS["bg_dark"], corner_radius=4, height=180
        )
        self._cov_frame.pack(fill="x", padx=10, pady=(0, 6))
        self._cov_row_labels: dict = {}  # map_name → (count_label, bar_label)
        self._build_coverage_rows()

        # ── Map Explorer ───────────────────────────────────────────────
        explore_sep = ctk.CTkFrame(self._auto_card, fg_color=COLORS["border"], height=1)
        explore_sep.pack(fill="x", padx=10, pady=(6, 6))

        create_label(self._auto_card, "Map Explorer", "subheading").pack(
            anchor="w", padx=10, pady=(4, 2)
        )
        explore_info = create_label(
            self._auto_card,
            "Automatically explores the current map until coverage is complete.\n"
            "Frontier targets refresh live as new walkable data is discovered.\n"
            "Coverage % is an estimate and may go backward when map-size estimate\n"
            "grows. Press F10 anytime to force-stop exploration.",
            "small", "text_muted"
        )
        explore_info.pack(anchor="w", padx=10, pady=(0, 6))

        # Progress row
        explore_dur_row = ctk.CTkFrame(self._auto_card, fg_color="transparent")
        explore_dur_row.pack(fill="x", padx=10, pady=(0, 4))
        self._explore_progress_label = create_label(
            explore_dur_row, "Idle", "small", "text_muted"
        )
        self._explore_progress_label.pack(side="left", fill="x", expand=True)

        # Start / Stop buttons
        explore_btns = ctk.CTkFrame(self._auto_card, fg_color="transparent")
        explore_btns.pack(fill="x", padx=10, pady=(0, 10))
        self._explore_start_btn = create_accent_button(
            explore_btns, "🗺 Explore Map", self._on_explore_start, color="accent_purple"
        )
        self._explore_start_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._explore_stop_btn = create_accent_button(
            explore_btns, "Stop", self._on_explore_stop, color="accent_red", width=70
        )
        self._explore_stop_btn.configure(state="disabled")
        self._explore_stop_btn.pack(side="left")

        self._explore_poll_id = None  # after() poll job handle

        # ── Manual Explore section ────────────────────────────────────────
        manual_card = create_card_frame(container)
        manual_card.pack(fill="x", padx=10, pady=(0, 8))
        create_label(manual_card, "Manual Explore", "subheading").pack(anchor="w", padx=10, pady=(10, 4))
        create_label(
            manual_card,
            "Walk the map yourself while the bot samples your position.\n"
            "A live coverage grid is drawn on the overlay so you can see\n"
            "unexplored areas (green = frontier, dark = explored).",
            "small", "text_muted"
        ).pack(anchor="w", padx=10, pady=(0, 6))

        self._manual_status_label = create_label(manual_card, "Idle", "small", "text_muted")
        self._manual_status_label.pack(anchor="w", padx=10, pady=(0, 4))

        manual_btns = ctk.CTkFrame(manual_card, fg_color="transparent")
        manual_btns.pack(fill="x", padx=10, pady=(0, 10))
        self._manual_start_btn = create_accent_button(
            manual_btns, "📍 Start Manual Explore", self._on_manual_explore_start, color="accent_blue"
        )
        self._manual_start_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._manual_stop_btn = create_accent_button(
            manual_btns, "Stop", self._on_manual_explore_stop, color="accent_red", width=70
        )
        self._manual_stop_btn.configure(state="disabled")
        self._manual_stop_btn.pack(side="left")



        # ── Waypoints section ──────────────────────────────────────────
        self._wp_card = create_card_frame(container)
        self._wp_card.pack(fill="x", padx=10, pady=(0, 8))
        wp_header = ctk.CTkFrame(self._wp_card, fg_color="transparent")
        wp_header.pack(fill="x", padx=10, pady=(10, 4))
        create_label(wp_header, "Waypoints", "subheading").pack(side="left")
        self._wp_count_label = create_label(wp_header, "(0)", "small", "text_muted")
        self._wp_count_label.pack(side="left", padx=(6, 0))

        self._wp_listbox = ctk.CTkScrollableFrame(self._wp_card, fg_color=COLORS["bg_dark"], corner_radius=4, height=220)
        self._wp_listbox.pack(fill="x", padx=10, pady=(0, 6))

        edit_frame = ctk.CTkFrame(self._wp_card, fg_color="transparent")
        edit_frame.pack(fill="x", padx=10, pady=(0, 6))

        row1 = ctk.CTkFrame(edit_frame, fg_color="transparent")
        row1.pack(fill="x", pady=2)
        self._btn_delete = create_accent_button(row1, "Delete Selected", self._on_delete_selected, color="accent_red")
        self._btn_delete.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._btn_delete_all = create_accent_button(row1, "Delete All", self._on_delete_all, color="accent_red")
        self._btn_delete_all.pack(side="left", fill="x", expand=True, padx=(2, 0))

        row2 = ctk.CTkFrame(edit_frame, fg_color="transparent")
        row2.pack(fill="x", pady=2)
        self._btn_move_up = create_accent_button(row2, "Move Up", self._on_move_up, color="accent_blue")
        self._btn_move_up.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._btn_move_down = create_accent_button(row2, "Move Down", self._on_move_down, color="accent_blue")
        self._btn_move_down.pack(side="left", fill="x", expand=True, padx=(2, 0))

        row3 = ctk.CTkFrame(edit_frame, fg_color="transparent")
        row3.pack(fill="x", pady=2)
        self._btn_set_node = create_accent_button(row3, "Set Node", self._on_set_node, color="accent_blue")
        self._btn_set_node.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._btn_set_stand = create_accent_button(row3, "Set Stand", self._on_set_stand, color="accent_orange")
        self._btn_set_stand.pack(side="left", fill="x", expand=True, padx=(2, 0))
        self._btn_toggle_portal = create_accent_button(row3, "Toggle Portal", self._on_toggle_portal, color="accent_purple")
        self._btn_toggle_portal.pack(side="left", fill="x", expand=True, padx=(2, 0))

        edit_fields = ctk.CTkFrame(self._wp_card, fg_color="transparent")
        edit_fields.pack(fill="x", padx=10, pady=(0, 6))

        coord_row = ctk.CTkFrame(edit_fields, fg_color="transparent")
        coord_row.pack(fill="x", pady=2)
        create_label(coord_row, "X:", "small").pack(side="left")
        self._edit_x = create_entry(coord_row, placeholder="X", width=80)
        self._edit_x.pack(side="left", padx=(2, 8))
        create_label(coord_row, "Y:", "small").pack(side="left")
        self._edit_y = create_entry(coord_row, placeholder="Y", width=80)
        self._edit_y.pack(side="left", padx=(2, 8))
        create_label(coord_row, "Wait:", "small").pack(side="left")
        self._edit_wait = create_entry(coord_row, placeholder="0.0", width=60)
        self._edit_wait.pack(side="left", padx=(2, 4))
        self._btn_apply = create_accent_button(coord_row, "Apply", self._on_apply_edit, color="accent_green")
        self._btn_apply.pack(side="left", padx=(4, 0))

        add_row = ctk.CTkFrame(edit_fields, fg_color="transparent")
        add_row.pack(fill="x", pady=2)
        create_label(add_row, "Add WP:", "small").pack(side="left")
        self._add_type = ctk.CTkOptionMenu(add_row, values=["node", "stand"], width=80,
                                            fg_color=COLORS["bg_light"], button_color=COLORS["border"],
                                            font=FONTS["small"])
        self._add_type.pack(side="left", padx=(2, 4))
        self._btn_add_wp = create_accent_button(add_row, "Add at Player Pos", self._on_add_waypoint, color="accent_green")
        self._btn_add_wp.pack(side="left", padx=(4, 0))

        # ── Actions section ────────────────────────────────────────────
        self._actions_card = create_card_frame(container)
        self._actions_card.pack(fill="x", padx=10, pady=(0, 8))
        create_label(self._actions_card, "Actions", "subheading").pack(anchor="w", padx=10, pady=(10, 4))

        action_btns = ctk.CTkFrame(self._actions_card, fg_color="transparent")
        action_btns.pack(fill="x", padx=10, pady=(0, 10))

        self._delete_path_btn = create_accent_button(action_btns, "Delete Path", self._on_delete_path, color="accent_red")
        self._delete_path_btn.pack(fill="x", pady=2)

        # Refresh status on initial load and apply mode
        self._sync_auto_behavior_ui_from_config()
        self._update_mode_ui()
        self._update_recording_sections_visibility()

    # ── Mode toggle helpers ────────────────────────────────────────────

    def _on_set_record_mode(self):
        self._nav_mode = "record"
        self._engine.config.set("nav_mode", "manual")
        self._update_mode_ui()

    def _on_set_auto_mode(self):
        self._nav_mode = "auto"
        self._engine.config.set("nav_mode", "auto")
        self._update_mode_ui()

    def _on_auto_behavior_change(self, choice: str):
        label_to_value = {
            "Rush Events": "rush_events",
            "Kill All": "kill_all",
            "Boss Rush": "boss_rush",
        }
        self._engine.config.set("auto_behavior", label_to_value.get(choice, "rush_events"))

    def _sync_auto_behavior_ui_from_config(self):
        value_to_label = {
            "rush_events": "Rush Events",
            "kill_all": "Kill All",
            "boss_rush": "Boss Rush",
        }
        raw = (self._engine.config.get("auto_behavior", "rush_events") or "rush_events").strip().lower()
        self._auto_behavior_menu.set(value_to_label.get(raw, "Rush Events"))

    def _update_mode_ui(self):
        """Show recording or auto-navigation section based on current mode."""
        if self._nav_mode == "record":
            self._rec_card.pack(fill="x", padx=10, pady=(0, 8))
            self._auto_card.pack_forget()
            self._mode_record_btn.configure(fg_color=COLORS["accent_green"])
            self._mode_auto_btn.configure(fg_color=COLORS["bg_light"])
            self._mode_info.configure(text="Walk to record waypoints")
        else:
            self._rec_card.pack_forget()
            self._auto_card.pack(fill="x", padx=10, pady=(0, 8))
            self._mode_record_btn.configure(fg_color=COLORS["bg_light"])
            self._mode_auto_btn.configure(fg_color=COLORS["accent_purple"])
            self._mode_info.configure(text="Bot navigates autonomously via A*")
        self._update_recording_sections_visibility()
        self._refresh_wall_status()

    def _update_recording_sections_visibility(self):
        """Show waypoint/actions sections while Recording mode is selected.

        Legacy waypoint editing tools stay visible in recording mode even when
        the recorder is idle, and remain visible during active recording.
        """
        recorder = self._engine.path_recorder
        is_recording = bool(recorder and recorder.is_recording)
        show_legacy_panels = (self._nav_mode == "record") or is_recording
        if show_legacy_panels:
            if not self._wp_card.winfo_manager():
                self._wp_card.pack(fill="x", padx=10, pady=(0, 8))
            if not self._actions_card.winfo_manager():
                self._actions_card.pack(fill="x", padx=10, pady=(0, 8))
        else:
            if self._wp_card.winfo_manager():
                self._wp_card.pack_forget()
            if self._actions_card.winfo_manager():
                self._actions_card.pack_forget()

    def _refresh_wall_status(self):
        """Update walkable-area cache status label for the current map."""
        map_name = self._get_active_map()
        if not map_name:
            self._wall_status_label.configure(text="—")
            return
        status = self._engine.get_wall_data_status(map_name)
        self._wall_status_label.configure(text=status)

    # ── Coverage Overview helpers ──────────────────────────────────────

    _COVERAGE_GOOD = 2000    # ≥ this → green
    _COVERAGE_SPARSE = 500   # ≥ this → orange; < this (>0) → red

    def _build_coverage_rows(self):
        """Build one row per map in the coverage overview frame."""
        from src.utils.constants import MAP_NAMES
        for i, name in enumerate(MAP_NAMES):
            row = ctk.CTkFrame(self._cov_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            name_lbl = create_label(row, name, "small")
            name_lbl.pack(side="left")
            count_lbl = create_label(row, "—", "small", "text_muted")
            count_lbl.pack(side="right")
            self._cov_row_labels[name] = count_lbl
        self._refresh_coverage_overview()

    def _refresh_coverage_overview(self):
        """Refresh the coverage count labels for all maps."""
        coverage = self._engine.get_all_coverage()
        for name, count in coverage.items():
            lbl = self._cov_row_labels.get(name)
            if lbl is None:
                continue
            if count >= self._COVERAGE_GOOD:
                color = COLORS.get("accent_green", "#2ea043")
                text = f"{count:,} pts ✓"
            elif count >= self._COVERAGE_SPARSE:
                color = COLORS.get("accent_orange", "#d29922")
                text = f"{count:,} pts"
            elif count > 0:
                color = COLORS.get("accent_red", "#f85149")
                text = f"{count:,} pts ⚠"
            else:
                color = COLORS.get("text_muted", "#484f58")
                text = "no data"
            lbl.configure(text=text, text_color=color)

    # ── Map Explorer helpers ───────────────────────────────────────────

    def _switch_to_dashboard_for_activity(self):
        try:
            app = self.winfo_toplevel()
            if hasattr(app, "_switch_tab"):
                app._switch_tab("dashboard")
        except Exception:
            pass

    def _push_dashboard_explore_status(self, text: str, active: bool):
        def _apply():
            try:
                app = self.winfo_toplevel()
                dashboard = getattr(app, "_tabs", {}).get("dashboard") if app else None
                if dashboard and hasattr(dashboard, "set_explorer_progress"):
                    dashboard.set_explorer_progress(text, active)
            except Exception:
                pass

        try:
            self.after(0, _apply)
        except Exception:
            _apply()

    def _on_explore_start(self):
        """Start MapExplorer session."""

        def _progress(elapsed, total, targets, positions, cov_pct=0.0,
                      covered=0, estimated=0, frontier=0, force=False):
            trend = "estimating"
            if estimated > 0:
                trend = f"{cov_pct:.1f}%"
            txt = (
                f"🧭 Running until complete | "
                f"Coverage: {trend} ({covered}/{estimated}) | "
                f"Frontier: {frontier} | "
                f"Targets: {targets} | Pos: {positions}"
            )
            # Update label from any thread via after(0)
            try:
                self._explore_progress_label.after(0, lambda t=txt: self._explore_progress_label.configure(text=t))
            except Exception:
                pass
            self._push_dashboard_explore_status(txt, True)
            # Re-enable buttons and refresh wall status when done
            if force or not self._engine.explorer_running:
                try:
                    self._explore_progress_label.after(0, self._on_explore_done)
                except Exception:
                    pass

        ok = self._engine.start_map_explorer(duration_s=None, progress_cb=_progress)
        if ok:
            self._switch_to_dashboard_for_activity()
            self._explore_start_btn.configure(state="disabled")
            self._explore_stop_btn.configure(state="normal")
            self._explore_progress_label.configure(text="🧭 Running until complete")
            self._push_dashboard_explore_status("🧭 Running until complete", True)
            # Poll every 2 s to refresh wall status label while running
            self._explore_poll_id = self._explore_progress_label.after(
                2000, self._explore_poll
            )
        elif self._engine.is_running:
            self._explore_progress_label.configure(
                text="Stop the bot first — Explorer can't run alongside the bot loop"
            )
        else:
            self._explore_progress_label.configure(text="Error — not attached or already running")

    def _on_explore_stop(self):
        """Stop a running MapExplorer session."""
        self._engine.stop_map_explorer()
        self._on_explore_done()

    def _on_explore_done(self):
        """Reset UI after exploration finishes."""
        self._explore_start_btn.configure(state="normal")
        self._explore_stop_btn.configure(state="disabled")
        self._push_dashboard_explore_status("", False)
        if self._explore_poll_id:
            try:
                self._explore_progress_label.after_cancel(self._explore_poll_id)
            except Exception:
                pass
            self._explore_poll_id = None
        self._refresh_wall_status()
        self._refresh_coverage_overview()

    def _explore_poll(self):
        """Periodic after() callback to refresh wall-status while explorer runs."""
        if not self._engine.explorer_running:
            self._on_explore_done()
            return
        self._refresh_wall_status()
        self._explore_poll_id = self._explore_progress_label.after(
            2000, self._explore_poll
        )

    # ── Manual Explore helpers ─────────────────────────────────────────

    def _on_manual_explore_start(self):
        """Start the manual exploration sampler thread."""
        map_name = self._get_active_map()
        if not map_name:
            self._manual_status_label.configure(
                text="Map not detected — attach the bot first.", text_color=COLORS.get("accent_red", "#f85149")
            )
            return
        if self._manual_thread and self._manual_thread.is_alive():
            return  # already running
        stop_ev = threading.Event()
        self._manual_stop_event = stop_ev
        t = threading.Thread(
            target=self._manual_explore_thread_fn,
            args=(map_name, stop_ev),
            daemon=True,
        )
        self._manual_thread = t
        self._manual_start_btn.configure(state="disabled")
        self._manual_stop_btn.configure(state="normal")
        self._manual_status_label.configure(
            text="Running — walk the map on screen.",
            text_color=COLORS.get("accent_green", "#2ea043")
        )
        t.start()

    def _on_manual_explore_stop(self):
        """Signal the sampler thread to stop."""
        if self._manual_stop_event:
            self._manual_stop_event.set()
        self._manual_start_btn.configure(state="normal")
        self._manual_stop_btn.configure(state="disabled")
        self._manual_status_label.configure(
            text="Stopped.", text_color=COLORS.get("text_muted", "#8b949e")
        )

    def _manual_explore_thread_fn(self, map_name: str, stop_ev: threading.Event):
        """Background thread: sample player position + push grid updates to overlay."""
        from src.core.map_explorer import MapExplorer
        from src.utils.constants import (
            MAP_EXPLORER_POSITION_SAMPLE_DIST,
            MAP_EXPLORER_POSITION_POLL_S,
            MAP_EXPLORER_POSITION_FLUSH_EVERY,
            MAP_EXPLORER_POSITION_FLUSH_S,
        )
        sample_dist_sq = MAP_EXPLORER_POSITION_SAMPLE_DIST ** 2

        # Lightweight MapExplorer surrogate (same as bot_engine ZoneWatcher)
        sampler = MapExplorer.__new__(MapExplorer)
        sampler._map_name = map_name
        sampler._cancelled = False
        sampler._sampler_last_pos = None
        existing_keys = sampler._load_existing_keys()
        pending: list = []
        last_flush = time.time()
        last_pushed_key_count = -1  # -1 forces the very first push

        while not stop_ev.is_set():
            try:
                x = self._engine.game_state.read_chain("player_x") or 0.0
                y = self._engine.game_state.read_chain("player_y") or 0.0
            except Exception:
                time.sleep(MAP_EXPLORER_POSITION_POLL_S)
                continue

            if abs(x) > 1.0 or abs(y) > 1.0:
                key = sampler._pos_key(x, y)
                if key not in existing_keys:
                    if sampler._sampler_last_pos is None:
                        sampler._sampler_last_pos = (x, y)
                        existing_keys.add(key)
                        pending.append((x, y))
                    else:
                        dx = x - sampler._sampler_last_pos[0]
                        dy = y - sampler._sampler_last_pos[1]
                        if dx * dx + dy * dy >= sample_dist_sq:
                            sampler._sampler_last_pos = (x, y)
                            existing_keys.add(key)
                            pending.append((x, y))

            now = time.time()
            if pending and (
                len(pending) >= MAP_EXPLORER_POSITION_FLUSH_EVERY
                or now - last_flush >= MAP_EXPLORER_POSITION_FLUSH_S
            ):
                sampler._flush_positions(pending)
                pending.clear()
                last_flush = now

            # Only push the grid to the overlay when new cells were actually added.
            # Time-based polling would set grid_dirty every 0.2 s even when standing
            # still, causing the mini-panel to flash on every push.
            if len(existing_keys) != last_pushed_key_count:
                self._manual_refresh_grid(map_name, x, y)
                last_pushed_key_count = len(existing_keys)

            time.sleep(MAP_EXPLORER_POSITION_POLL_S)

        if pending:
            sampler._flush_positions(pending)
        # Final grid push after stop
        try:
            lx = self._engine.game_state.read_chain("player_x") or 0.0
            ly = self._engine.game_state.read_chain("player_y") or 0.0
        except Exception:
            lx, ly = 0.0, 0.0
        self._manual_refresh_grid(map_name, lx, ly)
        try:
            self.after(0, lambda: self._manual_status_label.configure(
                text="Done — grid saved.",
                text_color=COLORS.get("text_muted", "#8b949e")
            ))
        except Exception:
            pass

    def _manual_refresh_grid(self, map_name: str, px: float = 0.0, py: float = 0.0):
        """Load wall_data.json, build grid, push walkable+frontier to overlay."""
        if not self._grid_overlay_cb:
            return
        try:
            from src.core.wall_scanner import WallScanner, WallPoint
            from src.utils.constants import WALL_GRID_HALF_SIZE, WALL_GRID_CELL_SIZE
            raw_points = WallScanner._load_json().get(map_name, [])
            points = [WallPoint.from_dict(p) for p in raw_points if isinstance(p, dict)]
            if not points:
                return
            ws = WallScanner.__new__(WallScanner)
            grid = ws.build_walkable_grid(points, px, py,
                                          half_size=WALL_GRID_HALF_SIZE,
                                          cell_size=WALL_GRID_CELL_SIZE)
            # Collect walkable world positions
            walkable_xy = []
            for row in range(grid.rows):
                for col in range(grid.cols):
                    if not grid.is_blocked(row, col):
                        walkable_xy.append(grid.grid_to_world(row, col))
            frontier_xy = grid.get_frontier_world_positions(max_samples=500)
            self._grid_overlay_cb(walkable_xy, frontier_xy, grid.cell_size)
        except Exception:
            pass



    def _load_waypoints_for_map(self, map_name: str):
        recorder = self._engine.path_recorder
        if not recorder:
            self._loaded_waypoints = []
            self._refresh_waypoint_list()
            self._refresh_wall_status()
            return

        waypoints = recorder.load_path(map_name)
        self._loaded_waypoints = waypoints if waypoints else []
        self._selected_indices.clear()
        self._refresh_waypoint_list()
        self._refresh_wall_status()
        self._notify_overlay()

    def _refresh_waypoint_list(self):
        for widget in self._wp_listbox.winfo_children():
            widget.destroy()
        self._wp_buttons = []

        self._wp_count_label.configure(text=f"({len(self._loaded_waypoints)})")

        for i, wp in enumerate(self._loaded_waypoints):
            is_selected = i in self._selected_indices
            display_num = i + 1
            type_tag = wp.wp_type[0].upper()
            portal_tag = " P" if wp.is_portal else ""
            wait_tag = f" w{wp.wait_time:.1f}s" if wp.wait_time > 0 else ""
            text = f" {display_num:3d}  ({wp.x:.0f}, {wp.y:.0f})  [{type_tag}{portal_tag}]{wait_tag}"

            if wp.is_portal:
                txt_color = COLORS["accent_orange"]
            elif wp.wp_type == "stand":
                txt_color = COLORS["accent_orange"]
            else:
                txt_color = COLORS["accent_blue"]

            bg = COLORS["bg_light"] if is_selected else "transparent"

            btn = ctk.CTkButton(
                self._wp_listbox, text=text, anchor="w",
                fg_color=bg, hover_color=COLORS["bg_light"],
                text_color=txt_color, font=FONTS["mono_small"], height=24,
                corner_radius=2, command=lambda idx=i: self._on_select_waypoint(idx),
            )
            btn.pack(fill="x", pady=0)
            self._wp_buttons.append(btn)

        if len(self._selected_indices) == 1:
            idx = next(iter(self._selected_indices))
            wp = self._loaded_waypoints[idx]
            self._edit_x.delete(0, "end")
            self._edit_x.insert(0, f"{wp.x:.1f}")
            self._edit_y.delete(0, "end")
            self._edit_y.insert(0, f"{wp.y:.1f}")
            self._edit_wait.delete(0, "end")
            self._edit_wait.insert(0, f"{wp.wait_time:.1f}")

    def _on_select_waypoint(self, idx: int):
        if idx in self._selected_indices:
            self._selected_indices.discard(idx)
        elif self._selected_indices:
            self._selected_indices.add(idx)
        else:
            self._selected_indices = {idx}

        self._refresh_waypoint_list()
        self._notify_overlay()

    def _on_delete_selected(self):
        if not self._selected_indices:
            return
        for idx in sorted(self._selected_indices, reverse=True):
            if 0 <= idx < len(self._loaded_waypoints):
                self._loaded_waypoints.pop(idx)
        self._selected_indices.clear()
        self._refresh_waypoint_list()
        self._notify_overlay()

    def _on_delete_all(self):
        self._loaded_waypoints.clear()
        self._selected_indices.clear()
        self._refresh_waypoint_list()
        self._notify_overlay()

    def _on_move_up(self):
        if len(self._selected_indices) != 1:
            return
        idx = next(iter(self._selected_indices))
        if idx <= 0 or idx >= len(self._loaded_waypoints):
            return
        self._loaded_waypoints[idx], self._loaded_waypoints[idx - 1] = \
            self._loaded_waypoints[idx - 1], self._loaded_waypoints[idx]
        self._selected_indices = {idx - 1}
        self._refresh_waypoint_list()
        self._notify_overlay()

    def _on_move_down(self):
        if len(self._selected_indices) != 1:
            return
        idx = next(iter(self._selected_indices))
        if idx < 0 or idx >= len(self._loaded_waypoints) - 1:
            return
        self._loaded_waypoints[idx], self._loaded_waypoints[idx + 1] = \
            self._loaded_waypoints[idx + 1], self._loaded_waypoints[idx]
        self._selected_indices = {idx + 1}
        self._refresh_waypoint_list()
        self._notify_overlay()

    def _on_set_node(self):
        for idx in self._selected_indices:
            if 0 <= idx < len(self._loaded_waypoints):
                self._loaded_waypoints[idx].wp_type = "node"
                self._loaded_waypoints[idx].wait_time = 0.0
        self._refresh_waypoint_list()
        self._notify_overlay()

    def _on_set_stand(self):
        for idx in self._selected_indices:
            if 0 <= idx < len(self._loaded_waypoints):
                self._loaded_waypoints[idx].wp_type = "stand"
        self._refresh_waypoint_list()
        self._notify_overlay()

    def _on_toggle_portal(self):
        for idx in self._selected_indices:
            if 0 <= idx < len(self._loaded_waypoints):
                self._loaded_waypoints[idx].is_portal = not self._loaded_waypoints[idx].is_portal
        self._refresh_waypoint_list()
        self._notify_overlay()

    def _on_apply_edit(self):
        if len(self._selected_indices) != 1:
            return
        idx = next(iter(self._selected_indices))
        if idx < 0 or idx >= len(self._loaded_waypoints):
            return

        wp = self._loaded_waypoints[idx]
        try:
            x_val = self._edit_x.get().strip()
            y_val = self._edit_y.get().strip()
            w_val = self._edit_wait.get().strip()
            if x_val:
                wp.x = float(x_val)
            if y_val:
                wp.y = float(y_val)
            if w_val:
                wp.wait_time = float(w_val)
        except ValueError:
            pass

        self._refresh_waypoint_list()
        self._notify_overlay()

    def _on_add_waypoint(self):
        gs = self._engine.game_state
        if not gs:
            return

        try:
            gs.update()
        except Exception:
            pass

        if not hasattr(gs, 'player') or not gs.player:
            return
        pos = gs.player.position
        if not pos or (pos.x == 0 and pos.y == 0):
            return

        wp_type = self._add_type.get()
        wp = Waypoint(x=pos.x, y=pos.y, wp_type=wp_type)

        if self._selected_indices:
            insert_idx = max(self._selected_indices) + 1
            self._loaded_waypoints.insert(insert_idx, wp)
        else:
            self._loaded_waypoints.append(wp)

        self._refresh_waypoint_list()
        self._notify_overlay()

    def _on_record(self):
        map_name = self._get_active_map()
        if not map_name:
            return

        recorder = self._engine.path_recorder
        if not recorder:
            return

        if recorder.is_recording:
            recorder.stop_recording()
            self._loaded_waypoints = list(recorder.waypoints)
            self._record_btn.configure(text="Start Recording (F5)")
            self._pause_btn.configure(text="Pause (F6)", state="disabled")
            self._rec_status.configure(text="Status: Stopped", text_color=COLORS["text_secondary"])
            self._rec_count.configure(text=f"Points: {recorder.waypoint_count}")
            self._is_paused = False
            self._refresh_waypoint_list()
            self._notify_overlay()
            self._update_recording_sections_visibility()
        else:
            recorder.start_recording(map_name)
            self._record_btn.configure(text="Stop Recording (F5)")
            self._pause_btn.configure(text="Pause (F6)", state="normal")
            self._rec_status.configure(text="Status: Recording...", text_color=COLORS["accent_green"])
            self._is_paused = False
            self._update_recording_sections_visibility()
            self._start_record_update()

    def _on_pause_record(self):
        recorder = self._engine.path_recorder
        if not recorder or not recorder.is_recording:
            return

        self._is_paused = not self._is_paused
        if self._is_paused:
            if self._record_update_id:
                self.after_cancel(self._record_update_id)
                self._record_update_id = None
            self._pause_btn.configure(text="Resume (F6)")
            self._rec_status.configure(text="Status: Paused", text_color=COLORS["accent_orange"])
        else:
            self._pause_btn.configure(text="Pause (F6)")
            self._rec_status.configure(text="Status: Recording...", text_color=COLORS["accent_green"])
            self._start_record_update()

    def _start_record_update(self):
        recorder = self._engine.path_recorder
        if recorder and recorder.is_recording and not self._is_paused:
            recorder.record_tick()
            count = recorder.waypoint_count
            self._rec_count.configure(text=f"Points: {count}")
            self._loaded_waypoints = list(recorder.waypoints)
            self._refresh_waypoint_list()
            self._notify_overlay()
            interval = int(recorder._record_interval * 1000)
            self._record_update_id = self.after(interval, self._start_record_update)

    def _on_mark_portal(self):
        recorder = self._engine.path_recorder
        if recorder and recorder.is_recording:
            recorder.add_portal_waypoint()
            self._rec_count.configure(text=f"Points: {recorder.waypoint_count}")
            self._loaded_waypoints = list(recorder.waypoints)
            self._refresh_waypoint_list()
            self._notify_overlay()

    def _on_save(self):
        map_name = self._get_active_map()
        if not map_name:
            return

        recorder = self._engine.path_recorder
        if not recorder:
            return

        if recorder.is_recording:
            recorder.stop_recording()
            self._record_btn.configure(text="Start Recording (F5)")
            self._pause_btn.configure(text="Pause (F6)", state="disabled")
            self._rec_status.configure(text="Status: Stopped", text_color=COLORS["text_secondary"])
            self._is_paused = False
            self._update_recording_sections_visibility()

        if self._loaded_waypoints:
            recorder._waypoints = list(self._loaded_waypoints)

        if recorder.save_path(map_name):
            self._rec_status.configure(text="Status: Saved!", text_color=COLORS["accent_green"])

    def _on_delete_path(self):
        map_name = self._get_active_map()
        if not map_name:
            return
        recorder = self._engine.path_recorder
        if recorder and recorder.delete_path(map_name):
            self._loaded_waypoints = []
            self._selected_indices.clear()
            self._refresh_waypoint_list()
            self._notify_overlay()

    def destroy(self):
        if self._map_poll_id:
            self.after_cancel(self._map_poll_id)
            self._map_poll_id = None
        if self._record_update_id:
            self.after_cancel(self._record_update_id)
            self._record_update_id = None
        if self._manual_stop_event:
            self._manual_stop_event.set()
        super().destroy()
