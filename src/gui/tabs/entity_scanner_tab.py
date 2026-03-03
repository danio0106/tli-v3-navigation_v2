import math
import customtkinter as ctk

from src.gui.theme import COLORS, FONTS, create_card_frame, create_label, create_accent_button, create_entry
from src.utils.logger import log

# Header line format — must match the body format string in _refresh_display exactly.
# Body:  f"{i:>3}  {cls_short:<18s}  {mx:>8.0f}  {my:>8.0f}  {dist_str}  {valid_str}  {abp_short:<14}  {cfg_str:>9}"
# where dist_str is always 6 chars (f"{dist:>6.0f}" or "     —"); cfg_str is decimal ID or "--"
_LIST_HEADER = f"{'#':>3}  {'Class':<18}  {'X':>8}  {'Y':>8}  {'Dist':>6}  V  {'ABP type':<14}  {'CfgID':>9}"
_LIST_SEP    = "─" * len(_LIST_HEADER)


def _abp_short(abp_class: str) -> str:
    """Return a compact display name from a full AnimBlueprintGeneratedClass name.

    Examples:
      ABP_JiaoDuJunQingJia_C       → QingJia
      ABP_JiaoDuJunZhongJia_Tower… → ZhongJia_Tower
      ABP_SomeOtherMonster_C       → SomeOtherMonst  (14-char truncate)
    """
    if not abp_class or not isinstance(abp_class, str):
        return ""
    s = abp_class
    for prefix in ("ABP_JiaoDuJun", "ABP_"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if s.endswith("_C"):
        s = s[:-2]
    return s[:14]


class EntityScannerTab(ctk.CTkFrame):
    """Live entity scanner tab.

    Polls FightMgr.MapRoleMonster via scanner.get_monster_entities() and displays
    every monster entity currently tracked by the server: position, distance from
    player, validity flag, and class name.

    This is the primary research tool for identifying:
      - Carjack security-guard positions so the bot can navigate toward them
        instead of waiting at a fixed point.
      - Sandlord-wave completion: watch alive-count drop to 0 between waves.
    """

    def __init__(self, parent, bot_engine):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._engine = bot_engine
        self._auto_refresh = False
        self._update_id = None
        self._last_monsters: list = []  # cached last scan result
        self._filter_var = ctk.StringVar()
        self._max_dist_var = ctk.StringVar()
        self._auto_var = ctk.BooleanVar(value=False)
        self._hide_dead_var = ctk.BooleanVar(value=True)
        self._build_ui()

    # ------------------------------------------------------------------ #
    #  UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        # ── header row ──────────────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(10, 6))

        create_label(top, "Entity Scanner", "heading").pack(side="left")

        btn_frame = ctk.CTkFrame(top, fg_color="transparent")
        btn_frame.pack(side="right")

        self._auto_cb = ctk.CTkCheckBox(
            btn_frame,
            text="Auto (1 s)",
            variable=self._auto_var,
            font=FONTS["small"],
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent_cyan"],
            hover_color=COLORS["accent_cyan"],
            command=self._toggle_auto,
            width=90,
        )
        self._auto_cb.grid(row=0, column=0, padx=(0, 6), pady=2)

        self._scan_btn = create_accent_button(
            btn_frame, "Scan Now", self._do_scan, color="accent_cyan", width=90
        )
        self._scan_btn.grid(row=0, column=1, padx=0, pady=2)

        # ── stats card ──────────────────────────────────────────────────
        stats_card = create_card_frame(self)
        stats_card.pack(fill="x", padx=10, pady=(0, 6))

        stats_row = ctk.CTkFrame(stats_card, fg_color="transparent")
        stats_row.pack(fill="x", padx=10, pady=8)
        stats_row.columnconfigure((0, 1, 2, 3), weight=1)

        self._stat_labels: dict = {}
        for col, (name, key) in enumerate([
            ("Total", "total"),
            ("Alive (bValid)", "alive"),
            ("Unique Classes", "classes"),
            ("Player Pos", "playerpos"),
        ]):
            frame = ctk.CTkFrame(stats_row, fg_color=COLORS["bg_light"], corner_radius=6)
            frame.grid(row=0, column=col, padx=4, pady=2, sticky="nsew")
            create_label(frame, name, "small", "text_muted").pack(pady=(6, 0))
            lbl = create_label(frame, "—", "mono_small", "text_primary")
            lbl.pack(pady=(0, 6))
            self._stat_labels[key] = lbl

        # ── filter row ──────────────────────────────────────────────────
        filter_card = create_card_frame(self)
        filter_card.pack(fill="x", padx=10, pady=(0, 6))

        filter_row = ctk.CTkFrame(filter_card, fg_color="transparent")
        filter_row.pack(fill="x", padx=10, pady=8)

        create_label(filter_row, "Filter class:", "small", "text_muted").pack(
            side="left", padx=(0, 4)
        )
        self._filter_entry = create_entry(filter_row, "e.g. EMonster", width=150)
        self._filter_entry.pack(side="left")
        self._filter_entry.configure(textvariable=self._filter_var)
        self._filter_var.trace_add("write", lambda *_: self._refresh_display())

        create_label(filter_row, "Max dist:", "small", "text_muted").pack(
            side="left", padx=(10, 4)
        )
        self._max_dist_entry = create_entry(filter_row, "e.g. 3000", width=80)
        self._max_dist_entry.pack(side="left")
        self._max_dist_entry.configure(textvariable=self._max_dist_var)
        self._max_dist_var.trace_add("write", lambda *_: self._refresh_display())

        self._hide_dead_cb = ctk.CTkCheckBox(
            filter_row,
            text="Hide dead",
            variable=self._hide_dead_var,
            font=FONTS["small"],
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent_cyan"],
            hover_color=COLORS["accent_cyan"],
            command=self._refresh_display,
            width=90,
        )
        self._hide_dead_cb.pack(side="left", padx=10)

        clear_btn = create_accent_button(
            filter_row, "Clear", self._clear_filter,
            color="accent_purple", width=54,
        )
        clear_btn.pack(side="left", padx=6)

        self._filter_status = create_label(filter_row, "", "small", "text_muted")
        self._filter_status.pack(side="right")

        # ── class summary card ──────────────────────────────────────────
        summary_card = create_card_frame(self)
        summary_card.pack(fill="x", padx=10, pady=(0, 6))

        sum_hdr = ctk.CTkFrame(summary_card, fg_color="transparent")
        sum_hdr.pack(fill="x", padx=10, pady=(10, 2))
        create_label(sum_hdr, "Class breakdown  (alive / total)", "subheading").pack(side="left")

        self._summary_text = ctk.CTkTextbox(
            summary_card,
            fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_secondary"],
            font=FONTS["mono_small"],
            corner_radius=4,
            height=58,
            activate_scrollbars=False,
        )
        self._summary_text.pack(fill="x", padx=10, pady=(0, 10))
        self._summary_text.configure(state="disabled")

        # ── entity list card ────────────────────────────────────────────
        list_card = create_card_frame(self)
        list_card.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        list_hdr = ctk.CTkFrame(list_card, fg_color="transparent")
        list_hdr.pack(fill="x", padx=10, pady=(10, 2))
        create_label(list_hdr, "Entity list  (sorted by distance)", "subheading").pack(side="left")
        self._list_count_label = create_label(list_hdr, "", "small", "text_muted")
        self._list_count_label.pack(side="left", padx=8)

        # Column header is embedded as the first line(s) of the textbox so
        # it uses the same monospace font and automatically aligns with the
        # data rows — unlike pixel-width CTkLabel widgets which do not align
        # with character-based text.
        self._entity_text = ctk.CTkTextbox(
            list_card,
            fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_secondary"],
            font=FONTS["mono_small"],
            corner_radius=4,
        )
        self._entity_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._entity_text.configure(state="disabled")

    # ------------------------------------------------------------------ #
    #  Scan logic                                                           #
    # ------------------------------------------------------------------ #

    def _get_scanner(self):
        """Return the live UE4Scanner instance, or None if not available."""
        return getattr(self._engine, 'scanner', None) or getattr(self._engine, '_scanner', None)

    def _get_player_pos(self):
        """Return (px, py) read live from memory via chain, or (None, None).

        Reads directly through the pointer chain instead of relying on the
        cached game_state.player snapshot, which is only updated while the
        bot's main loop is running.  This works whenever the bot is attached,
        even when no map cycle is active (the typical entity-scanner use case).
        """
        try:
            gs = self._engine.game_state
            if gs:
                x = gs.read_chain("player_x")
                y = gs.read_chain("player_y")
                if x is not None and y is not None:
                    return float(x), float(y)
        except Exception:
            pass
        return None, None

    def _do_scan(self):
        scanner = self._get_scanner()
        if not scanner:
            self._set_text(self._entity_text, f"{_LIST_HEADER}\n{_LIST_SEP}\n"
                           "(No scanner available — attach to game first.)")
            self._stat_labels["total"].configure(text="—")
            self._stat_labels["alive"].configure(text="—")
            self._stat_labels["classes"].configure(text="—")
            return

        try:
            monsters = scanner.get_monster_entities()
        except Exception as exc:
            self._set_text(self._entity_text, f"{_LIST_HEADER}\n{_LIST_SEP}\n"
                           f"Scan error: {exc}")
            log.warning(f"[EntityScanner] scan error: {exc}")
            return

        self._last_monsters = monsters or []
        self._refresh_display()

    def _refresh_display(self):
        monsters = self._last_monsters
        px, py = self._get_player_pos()

        # -- stats -------------------------------------------------------
        total = len(monsters)
        alive = sum(1 for m in monsters if m.bvalid != 0)  # -1 = unread (assume alive)
        unique_classes = len({(m.sub_object_class or "?") for m in monsters})

        self._stat_labels["total"].configure(text=str(total))
        self._stat_labels["alive"].configure(text=str(alive))
        self._stat_labels["classes"].configure(text=str(unique_classes))
        if px is not None:
            self._stat_labels["playerpos"].configure(text=f"{px:.0f},{py:.0f}")
        else:
            self._stat_labels["playerpos"].configure(text="—")

        # -- class summary -----------------------------------------------
        class_totals: dict = {}
        class_alive: dict = {}
        for m in monsters:
            cn = m.sub_object_class or "?"
            class_totals[cn] = class_totals.get(cn, 0) + 1
            if m.bvalid != 0:
                class_alive[cn] = class_alive.get(cn, 0) + 1

        summary_lines = []
        for cn, cnt in sorted(class_totals.items(), key=lambda kv: kv[1], reverse=True):
            al = class_alive.get(cn, 0)
            summary_lines.append(f"{cn:<32s}  {al:>3}/{cnt:<3}")
        self._set_text(self._summary_text, "\n".join(summary_lines) if summary_lines else "(no monsters)")

        # -- filters -----------------------------------------------------
        hide_dead = self._hide_dead_var.get()

        # max-distance filter
        max_dist: float = 0.0
        try:
            v = self._max_dist_var.get().strip()
            if v:
                max_dist = float(v)
        except ValueError:
            max_dist = 0.0

        ftext = self._filter_var.get().strip().lower()

        # Pre-compute distance for every monster (avoids recalculating during sort)
        def _dist(m) -> float:
            if px is None:
                return 0.0
            dx = m.position[0] - px
            dy = m.position[1] - py
            return math.sqrt(dx * dx + dy * dy)

        dists = {id(m): _dist(m) for m in monsters}

        filtered = []
        for m in monsters:
            if hide_dead and m.bvalid == 0:
                continue
            if ftext and ftext not in (m.sub_object_class or "?").lower():
                continue
            if max_dist > 0 and px is not None and dists[id(m)] > max_dist:
                continue
            filtered.append(m)

        # sort: dead last, then by distance ascending
        filtered.sort(key=lambda m: (1 if m.bvalid == 0 else 0, dists[id(m)]))

        # -- list status bar ---------------------------------------------
        shown = len(filtered)
        if ftext or hide_dead or max_dist > 0:
            self._filter_status.configure(text=f"{shown} of {total} shown")
        else:
            self._filter_status.configure(text="")
        self._list_count_label.configure(text=f"({shown} entities)")

        # -- entity rows -------------------------------------------------
        lines = [_LIST_HEADER, _LIST_SEP]
        for i, m in enumerate(filtered, start=1):
            cls_short = (m.sub_object_class or "?").split(".")[-1]
            mx, my = m.position[0], m.position[1]
            if px is not None:
                dist_str = f"{dists[id(m)]:>6.0f}"
            else:
                dist_str = "     —"   # 6 chars to match {:>6.0f}
            valid_str = "✓" if m.bvalid == 1 else ("✗" if m.bvalid == 0 else "?")
            abp_short = _abp_short(getattr(m, "abp_class", ""))
            _cid = getattr(m, "source_type", -1)
            cfg_str = str(_cid) if _cid != -1 else "--"
            line = (
                f"{i:>3}  "
                f"{cls_short:<18s}  "
                f"{mx:>8.0f}  "
                f"{my:>8.0f}  "
                f"{dist_str}  "
                f"{valid_str}  "
                f"{abp_short:<14}  "
                f"{cfg_str:>9}"
            )
            lines.append(line)

        if len(lines) == 2:   # only header + separator
            lines.append("(no entities match filter)")

        self._set_text(self._entity_text, "\n".join(lines))

    # ------------------------------------------------------------------ #
    #  Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _set_text(self, widget: ctk.CTkTextbox, content: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", content)
        widget.configure(state="disabled")

    def _clear_filter(self):
        self._filter_var.set("")
        self._max_dist_var.set("")
        self._refresh_display()

    def _toggle_auto(self):
        self._auto_refresh = self._auto_var.get()
        if self._auto_refresh:
            self._schedule_auto()

    def _schedule_auto(self):
        if not self._auto_refresh:
            return
        self._do_scan()
        self._update_id = self.after(1000, self._schedule_auto)

    def destroy(self):
        self._auto_refresh = False
        if self._update_id:
            self.after_cancel(self._update_id)
            self._update_id = None
        super().destroy()
