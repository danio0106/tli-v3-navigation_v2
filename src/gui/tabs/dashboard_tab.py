import os
import json
import customtkinter as ctk
from src.gui.theme import COLORS, FONTS, create_card_frame, create_label, create_accent_button
from src.utils.logger import log


class DashboardTab(ctk.CTkFrame):
    def __init__(self, parent, bot_engine):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._engine = bot_engine
        self._update_id = None
        self._build_ui()

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(10, 8))

        create_label(top, "Dashboard", "heading").pack(side="left")

        btn_frame = ctk.CTkFrame(top, fg_color="transparent")
        btn_frame.pack(side="right")

        self._start_btn = create_accent_button(
            btn_frame, "Start Bot", self._on_start, color="accent_green", width=90
        )
        self._start_btn.grid(row=0, column=0, padx=2, pady=2)

        self._demo_btn = create_accent_button(
            btn_frame, "Demo Mode", self._on_demo, color="accent_purple", width=90
        )
        self._demo_btn.grid(row=0, column=1, padx=2, pady=2)

        self._pause_btn = create_accent_button(
            btn_frame, "Pause", self._on_pause, color="accent_orange", width=90
        )
        self._pause_btn.grid(row=1, column=0, padx=2, pady=2)
        self._pause_btn.configure(state="disabled")

        self._stop_btn = create_accent_button(
            btn_frame, "Stop", self._on_stop, color="accent_red", width=90
        )
        self._stop_btn.grid(row=1, column=1, padx=2, pady=2)
        self._stop_btn.configure(state="disabled")

        status_card = create_card_frame(self)
        status_card.pack(fill="x", padx=10, pady=8)

        status_header = ctk.CTkFrame(status_card, fg_color="transparent")
        status_header.pack(fill="x", padx=10, pady=(12, 4))

        self._status_indicator = ctk.CTkLabel(
            status_header, text="\u25CF", font=("Segoe UI", 16),
            text_color=COLORS["text_muted"]
        )
        self._status_indicator.pack(side="left")

        self._status_label = create_label(
            status_header, "IDLE", "subheading", "text_secondary"
        )
        self._status_label.pack(side="left", padx=8)

        self._attached_label = create_label(
            status_header, "Not Attached", "small", "text_muted"
        )
        self._attached_label.pack(side="right")

        stats_frame = ctk.CTkFrame(status_card, fg_color="transparent")
        stats_frame.pack(fill="x", padx=10, pady=(4, 12))
        stats_frame.columnconfigure((0, 1), weight=1)

        self._stat_labels = {}
        stats = [
            ("Maps Completed", "maps_completed", 0),
            ("Runtime", "runtime", 1),
        ]

        for name, key, col in stats:
            frame = ctk.CTkFrame(stats_frame, fg_color=COLORS["bg_light"], corner_radius=6)
            frame.grid(row=0, column=col, padx=4, pady=4, sticky="nsew")

            create_label(frame, name, "small", "text_muted").pack(pady=(8, 0))
            lbl = create_label(frame, "0", "subheading", "text_primary")
            lbl.pack(pady=(0, 8))
            self._stat_labels[key] = lbl

        player_card = create_card_frame(self)
        player_card.pack(fill="x", padx=10, pady=8)

        create_label(player_card, "Player State", "subheading").pack(
            anchor="w", padx=10, pady=(12, 4)
        )

        player_grid = ctk.CTkFrame(player_card, fg_color="transparent")
        player_grid.pack(fill="x", padx=10, pady=(0, 12))
        player_grid.columnconfigure((0, 1, 2), weight=1)

        self._player_labels = {}
        player_info = [
            ("Position", "position", 0, 0),
            ("Health", "health", 0, 1),
            ("Zone", "map_info", 0, 2),
            ("Map Select", "map_select", 1, 0),
            ("Portals", "portal_info", 1, 1),
            ("Native", "native_info", 2, 0),
        ]

        for name, key, row, col in player_info:
            frame = ctk.CTkFrame(player_grid, fg_color=COLORS["bg_light"], corner_radius=6)
            colspan = 2 if key == "portal_info" else 1
            frame.grid(row=row, column=col, columnspan=colspan, padx=4, pady=4, sticky="nsew")

            create_label(frame, name, "small", "text_muted").pack(pady=(6, 0))
            lbl = create_label(frame, "---", "mono_small", "text_secondary")
            lbl.pack(pady=(0, 6))
            self._player_labels[key] = lbl

        self._explore_card = create_card_frame(self)
        self._explore_card.pack(fill="x", padx=10, pady=(0, 8))
        self._explore_card.pack_forget()
        create_label(self._explore_card, "Explorer Coverage", "subheading").pack(
            anchor="w", padx=10, pady=(10, 2)
        )
        self._explore_progress_label = create_label(
            self._explore_card,
            "Idle",
            "small",
            "text_secondary",
        )
        self._explore_progress_label.pack(anchor="w", padx=10, pady=(0, 10))

        log_card = create_card_frame(self)
        self._log_card = log_card
        log_card.pack(fill="both", expand=True, padx=10, pady=(8, 10))

        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.pack(fill="x", padx=10, pady=(12, 4))

        create_label(log_header, "Activity Log", "subheading").pack(side="left")

        self._log_path_label = create_label(log_header, "", "small", "text_muted")
        self._log_path_label.pack(side="left", padx=(8, 0))

        self._save_log_btn = create_accent_button(
            log_header, "Save Log", self._on_save_log, color="accent_cyan", width=80
        )
        self._save_log_btn.pack(side="right")

        self._log_text = ctk.CTkTextbox(
            log_card,
            fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_secondary"],
            font=FONTS["mono_small"],
            corner_radius=6,
            height=150,
        )
        self._log_default_height = 150
        self._log_compact_height = 110
        self._log_text.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        self._log_text.configure(state="disabled")

        self._log_path_label.configure(text=os.path.basename(log.log_filepath))

        self._start_update()

    def _on_start(self):
        if self._engine.start():
            self._start_btn.configure(state="disabled")
            self._demo_btn.configure(state="disabled")
            self._pause_btn.configure(state="normal")
            self._stop_btn.configure(state="normal")

    def _on_demo(self):
        if self._engine.start_demo():
            self._start_btn.configure(state="disabled")
            self._demo_btn.configure(state="disabled")
            self._pause_btn.configure(state="normal")
            self._stop_btn.configure(state="normal")

    def _on_pause(self):
        self._engine.pause()
        if self._engine.is_paused:
            self._pause_btn.configure(text="Resume")
        else:
            self._pause_btn.configure(text="Pause")

    def _on_stop(self):
        self._engine.stop()
        self.set_explorer_progress("", active=False)
        self._start_btn.configure(state="normal")
        self._demo_btn.configure(state="normal")
        self._pause_btn.configure(state="disabled", text="Pause")
        self._stop_btn.configure(state="disabled")

    def set_log_compact(self, compact: bool):
        h = self._log_compact_height if compact else self._log_default_height
        self._log_text.configure(height=h)

    def set_explorer_progress(self, text: str, active: bool):
        if active:
            if not self._explore_card.winfo_manager():
                self._explore_card.pack(fill="x", padx=10, pady=(0, 8), before=self._log_card)
            self._explore_progress_label.configure(text=text or "Running...")
            self.set_log_compact(True)
            return

        if self._explore_card.winfo_manager():
            self._explore_card.pack_forget()
        self.set_log_compact(False)

    def _on_save_log(self):
        log.flush()
        filepath = log.log_filepath
        try:
            size = os.path.getsize(filepath)
            if size < 1024:
                size_str = f"{size} B"
            else:
                size_str = f"{size / 1024:.1f} KB"
            self._save_log_btn.configure(text=f"Saved ({size_str})")
            self.add_log("INFO", f"Log saved: {filepath} ({size_str})")
            self.after(3000, lambda: self._save_log_btn.configure(text="Save Log"))
        except Exception as e:
            self.add_log("ERROR", f"Save log failed: {e}")

    ZONE_MAP_FILE = os.path.join("data", "zone_name_mapping.json")
    _zone_mapping_cache: dict = {}
    _zone_mapping_mtime: float = 0.0

    def _translate_zone_name(self, internal_name: str) -> str:
        try:
            if os.path.exists(self.ZONE_MAP_FILE):
                mtime = os.path.getmtime(self.ZONE_MAP_FILE)
                if mtime != self._zone_mapping_mtime:
                    with open(self.ZONE_MAP_FILE, "r") as f:
                        self._zone_mapping_cache = json.load(f)
                    self._zone_mapping_mtime = mtime
                english = self._zone_mapping_cache.get(internal_name, "")
                if english:
                    return english
        except (json.JSONDecodeError, IOError, OSError):
            pass
        return internal_name

    def add_log(self, level, message):
        try:
            self._log_text.configure(state="normal")

            self._log_text.insert("end", f"[{level}] {message}\n")
            self._log_text.see("end")

            lines = int(self._log_text.index("end-1c").split(".")[0])
            if lines > 500:
                self._log_text.delete("1.0", "100.0")

            self._log_text.configure(state="disabled")
        except Exception:
            pass

    def _start_update(self):
        self._update_stats()

    def _update_stats(self):
        try:
            stats = self._engine.stats
            state = stats["state"]

            state_colors = {
                "IDLE": COLORS["text_muted"],
                "PAUSED": COLORS["accent_orange"],
                "ERROR": COLORS["accent_red"],
                "STOPPING": COLORS["accent_red"],
            }
            color = state_colors.get(state, COLORS["accent_green"])

            self._status_indicator.configure(text_color=color)
            self._status_label.configure(text=state)

            attached_text = "Attached" if stats["attached"] else "Not Attached"
            attached_color = COLORS["accent_green"] if stats["attached"] else COLORS["text_muted"]
            self._attached_label.configure(text=attached_text, text_color=attached_color)

            self._stat_labels["maps_completed"].configure(text=str(stats["maps_completed"]))

            runtime = stats["runtime"]
            if runtime > 0:
                mins, secs = divmod(int(runtime), 60)
                hours, mins = divmod(mins, 60)
                self._stat_labels["runtime"].configure(text=f"{hours}:{mins:02d}:{secs:02d}")
            else:
                self._stat_labels["runtime"].configure(text="0:00:00")

            gs = self._engine.game_state
            if self._engine.memory.is_attached:
                gs.update()
            if self._engine.memory.is_attached and hasattr(self._engine, '_scanner') and self._engine._scanner:
                scanner = self._engine._scanner
                if hasattr(scanner, '_fnamepool_addr') and scanner._fnamepool_addr:
                    try:
                        zone = scanner.read_real_zone_name()
                        if zone:
                            gs.set_zone_name(zone)
                    except Exception:
                        pass

            m = gs.map
            if m.zone_name:
                zone_display = self._translate_zone_name(m.zone_name)
                if m.is_in_hideout:
                    map_text = f"Hideout ({zone_display})"
                elif m.is_in_map:
                    map_text = f"Map: {zone_display}"
                else:
                    map_text = zone_display
                self._player_labels["map_info"].configure(text=map_text)

            if gs.is_valid:
                p = gs.player
                self._player_labels["position"].configure(
                    text=f"({p.position.x:.1f}, {p.position.y:.1f})"
                )

                # HP: try scanner-based RoleLogic reading first (SDK dump chain),
                # fall back to gs.player health chains if configured.
                hp_text = "---"
                scanner = getattr(self._engine, '_scanner', None)
                if scanner:
                    try:
                        hp_result = scanner.read_player_hp()
                        if hp_result:
                            hp, hpmax = hp_result
                            pct = int(hp * 100 // hpmax) if hpmax > 0 else 0
                            hp_text = f"{hp:,} / {hpmax:,} ({pct:.0f}%)"
                    except Exception:
                        pass
                if hp_text == "---" and p.max_health > 0:
                    hp_text = f"{p.health} / {p.max_health}"
                self._player_labels["health"].configure(text=hp_text)

                if not m.zone_name:
                    if m.is_in_hideout:
                        map_text = "Hideout"
                    elif m.is_in_map:
                        map_text = f"Map (ID: {m.map_id})"
                    else:
                        map_text = "Unknown"
                    self._player_labels["map_info"].configure(text=map_text)

                map_step = stats.get("map_selection_step", "")
                if map_step:
                    self._player_labels["map_select"].configure(text=map_step)
                else:
                    self._player_labels["map_select"].configure(text="---")

                portal_text = stats.get("portal_status", "")
                if portal_text:
                    self._player_labels["portal_info"].configure(text=portal_text)
                else:
                    self._player_labels["portal_info"].configure(text="---")

                native_text = stats.get("native_status_label", "python")
                native_error = stats.get("native_error", "")
                if native_error:
                    native_text = f"{native_text} | {native_error}"
                self._player_labels["native_info"].configure(text=native_text)

        except Exception as e:
            log.warning(f"Dashboard stats update failed: {e}")

        if not self._engine.explorer_running and self._explore_card.winfo_manager():
            self.set_explorer_progress("", active=False)

        self._update_id = self.after(500, self._update_stats)

    def destroy(self):
        if self._update_id:
            self.after_cancel(self._update_id)
        super().destroy()
