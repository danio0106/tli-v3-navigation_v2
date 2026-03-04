import customtkinter as ctk
from src.gui.theme import COLORS, create_card_frame, create_label, create_accent_button, create_entry
from src.utils.constants import DEFAULT_SETTINGS


class SettingsTab(ctk.CTkFrame):
    def __init__(self, parent, bot_engine):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._engine = bot_engine
        self._config = bot_engine.config
        self._entries = {}
        self._switches = {}
        self._build_ui()

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(10, 4))
        create_label(top, "Settings", "heading").pack(side="left")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=10, pady=(0, 8))

        create_accent_button(
            actions, "Save All", self._on_save, color="accent_green", width=100
        ).pack(side="left", padx=4, pady=2)

        create_accent_button(
            actions, "Reset Defaults", self._on_reset, color="accent_orange", width=130
        ).pack(side="left", padx=4, pady=2)

        scroll = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_dark"])
        scroll.pack(fill="both", expand=True, padx=10, pady=8)

        sections = {
            "Game Connection": [
                ("game_process", "Game Process Name", "Process executable name"),
                ("game_window", "Game Window Title", "Window title to find"),
            ],
            "Controls": [
                ("interact_key", "Interact Key", "Key for NPC/object interaction"),
                ("loot_key", "Loot Pickup Key", "Key for picking up loot"),
            ],
            "Bot Behavior": [
                ("map_clear_timeout", "Map Clear Timeout (sec)", "Max time in a map before giving up"),
                ("loot_spam_interval_ms", "Loot Spam Interval (ms)", "How often to press E during navigation"),
                ("stuck_timeout_sec", "Stuck Timeout (sec)", "Seconds before trying escape angles"),
                ("waypoint_tolerance", "Waypoint Tolerance (units)", "Distance to consider waypoint reached"),
            ],
            "Hotkeys": [
                ("hotkey_start", "Start Hotkey", "Global hotkey to start bot"),
                ("hotkey_stop", "Stop Hotkey", "Global hotkey to stop bot"),
                ("hotkey_pause", "Pause Hotkey", "Global hotkey to pause/resume"),
            ],
            "Performance": [
                ("loop_delay_ms", "Main Loop Delay (ms)", "Delay between bot ticks"),
            ],
        }

        for section_name, fields in sections.items():
            card = create_card_frame(scroll)
            card.pack(fill="x", pady=8)

            create_label(card, section_name, "subheading").pack(
                anchor="w", padx=10, pady=(12, 4)
            )

            for key, label_text, tooltip in fields:
                row = ctk.CTkFrame(card, fg_color="transparent")
                row.pack(fill="x", padx=10, pady=3)

                label_frame = ctk.CTkFrame(row, fg_color="transparent")
                label_frame.pack(side="left", fill="x", expand=True)

                create_label(label_frame, label_text, "body").pack(anchor="w")
                create_label(label_frame, tooltip, "small", "text_muted").pack(anchor="w")

                current = self._config.get(key, DEFAULT_SETTINGS.get(key, ""))
                default_val = DEFAULT_SETTINGS.get(key, "")

                if isinstance(default_val, bool):
                    switch = ctk.CTkSwitch(
                        row,
                        text="",
                        fg_color=COLORS["border"],
                        progress_color=COLORS["accent_blue"],
                        button_color=COLORS["text_primary"],
                    )
                    switch.pack(side="right", padx=4)
                    if bool(current):
                        switch.select()
                    else:
                        switch.deselect()
                    self._switches[key] = switch
                else:
                    entry = create_entry(row, width=120)
                    entry.pack(side="right", padx=4)
                    entry.insert(0, str(current))
                    self._entries[key] = entry

            ctk.CTkFrame(card, fg_color="transparent", height=4).pack()

        self._status = create_label(scroll, "", "small", "text_muted")
        self._status.pack(pady=8)


    def _set_entry_value(self, key: str, value):
        widget = self._entries.get(key)
        if widget is None:
            return
        widget.delete(0, "end")
        widget.insert(0, str(value))

    def _on_save(self):
        for key, widget in self._entries.items():
            val_str = widget.get().strip()
            default = DEFAULT_SETTINGS.get(key, "")

            if isinstance(default, int):
                try:
                    self._config.set(key, int(val_str))
                except ValueError:
                    self._config.set(key, default)
            elif isinstance(default, float):
                try:
                    self._config.set(key, float(val_str))
                except ValueError:
                    self._config.set(key, default)
            elif isinstance(default, bool):
                self._config.set(key, val_str.lower() in {"1", "true", "yes", "on"})
            else:
                self._config.set(key, val_str)

        for key, switch in self._switches.items():
            self._config.set(key, bool(switch.get()))

        self._status.configure(
            text="Settings saved successfully",
            text_color=COLORS["accent_green"]
        )

    def _on_reset(self):
        self._config.reset()

        for key, widget in self._entries.items():
            default = DEFAULT_SETTINGS.get(key, "")
            widget.delete(0, "end")
            widget.insert(0, str(default))

        for key, switch in self._switches.items():
            default_val = bool(DEFAULT_SETTINGS.get(key, False))
            if default_val:
                switch.select()
            else:
                switch.deselect()

        self._status.configure(
            text="Settings reset to defaults",
            text_color=COLORS["accent_orange"]
        )

