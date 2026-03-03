"""Card Priority Tab — GUI for managing Netherrealm card priority.

Performance design:
 - All 46 card row widgets are created ONCE at init and stored in a pool.
 - Reordering only calls pack_forget → pack in new order + updates rank labels.
   No widget creation or destruction on every move.
 - Inline rank editor: click a rank number → type target rank → Enter to move instantly.
"""

import threading
import customtkinter as ctk
from typing import List, Optional, Dict

from src.gui.theme import COLORS, FONTS, create_card_frame, create_label, create_accent_button
from src.core.card_database import (
    CardDatabase, CardEntry,
    RARITY_COLORS, RARITY_INDEX_MAP, CATEGORY_DISPLAY, DEFAULT_EMPTY_TEXTURE,
)
from src.utils.logger import log

_RARITY_WEIGHT = {"rainbow": 4, "orange": 3, "purple": 2, "blue": 1}
_ROW_HEIGHT = 36


class CardPriorityTab(ctk.CTkFrame):
    """Tab for managing card selection priority."""

    def __init__(self, parent, bot_engine):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._engine = bot_engine
        # Share the same CardDatabase instance as BotEngine (priority changes propagate immediately)
        self._db = getattr(bot_engine, '_card_database', None) or CardDatabase()

        # Widget pool: card_id → _CardRow  (created once, never destroyed)
        self._pool: Dict[int, "_CardRow"] = {}
        # Current visible ordering after filters
        self._visible_rows: List["_CardRow"] = []

        self._filter_rarity: Optional[str] = None
        self._filter_category: Optional[str] = None
        self._editing_row: Optional["_CardRow"] = None  # row with active rank editor

        self._build_ui()
        self._build_card_pool()
        self._repack()

    # ── UI skeleton ────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(10, 4))
        create_label(top, "Card Priority", "heading").pack(side="left")

        btn_frame = ctk.CTkFrame(top, fg_color="transparent")
        btn_frame.pack(side="right")

        create_accent_button(
            btn_frame, "Default Order", self._on_reset,
            color="accent_orange", width=110,
        ).pack(side="left", padx=3)

        self._scan_btn = create_accent_button(
            btn_frame, "Scan Cards", self._on_scan_cards,
            color="accent_green", width=100,
        )
        self._scan_btn.pack(side="left", padx=3)

        create_accent_button(
            btn_frame, "Save", self._on_save,
            color="accent_blue", width=70,
        ).pack(side="left", padx=3)

        # Info line
        info = ctk.CTkFrame(self, fg_color="transparent")
        info.pack(fill="x", padx=10, pady=(0, 3))
        self._info_label = create_label(
            info,
            "Click a rank number to move a card. Highest priority = picked first.",
            "small", "text_muted",
        )
        self._info_label.pack(side="left")
        self._count_label = create_label(info, "", "small", "text_secondary")
        self._count_label.pack(side="right")

        # Filter bar
        fbar = ctk.CTkFrame(self, fg_color=COLORS["bg_medium"], height=34, corner_radius=6)
        fbar.pack(fill="x", padx=10, pady=(0, 5))

        create_label(fbar, "Filter:", "small", "text_secondary").pack(side="left", padx=(8, 4))

        self._rarity_btns: Dict[str, ctk.CTkButton] = {}
        for rarity in ("rainbow", "orange", "purple", "blue"):
            btn = ctk.CTkButton(
                fbar, text=rarity.title(), width=66, height=22,
                fg_color=RARITY_COLORS[rarity],
                hover_color=COLORS["button_hover"],
                text_color="#0D1117", font=FONTS["small"], corner_radius=4,
                command=lambda r=rarity: self._toggle_rarity(r),
            )
            btn.pack(side="left", padx=2, pady=4)
            self._rarity_btns[rarity] = btn

        ctk.CTkFrame(fbar, fg_color=COLORS["border"], width=1).pack(
            side="left", fill="y", padx=6, pady=4,
        )

        self._cat_var = ctk.StringVar(value="All")
        ctk.CTkOptionMenu(
            fbar, variable=self._cat_var,
            values=["All"] + list(CATEGORY_DISPLAY.values()),
            width=125, height=22, font=FONTS["small"],
            fg_color=COLORS["bg_light"], button_color=COLORS["border"],
            command=self._on_cat_filter,
        ).pack(side="left", padx=4, pady=4)

        self._status_label = create_label(fbar, "", "small", "accent_green")
        self._status_label.pack(side="right", padx=8)

        # Scrollable card list
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color=COLORS["bg_dark"],
            scrollbar_button_color=COLORS["scrollbar"],
        )
        self._scroll.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        # Column header inside scroll area
        hdr = ctk.CTkFrame(self._scroll, fg_color=COLORS["bg_medium"], height=24, corner_radius=4)
        hdr.pack(fill="x", pady=(0, 3), padx=2)
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Rank", font=FONTS["small"], text_color=COLORS["text_muted"], width=42).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(hdr, text="Card Name", font=FONTS["small"], text_color=COLORS["text_muted"], width=120, anchor="w").pack(side="left", padx=(18, 0))
        ctk.CTkLabel(hdr, text="Category", font=FONTS["small"], text_color=COLORS["text_muted"], width=100).pack(side="left", padx=(30, 0))
        ctk.CTkLabel(hdr, text="Rarity", font=FONTS["small"], text_color=COLORS["text_muted"], width=50).pack(side="left", padx=(10, 0))

        # Texture mapping section (collapsed by default)
        self._mapping_visible = False
        toggle_frame = ctk.CTkFrame(self, fg_color="transparent")
        toggle_frame.pack(fill="x", padx=10, pady=(0, 2))
        self._mapping_toggle = ctk.CTkButton(
            toggle_frame, text="\u25b6 Texture Mappings", width=160, height=22,
            fg_color="transparent", hover_color=COLORS["bg_light"],
            text_color=COLORS["text_muted"], font=FONTS["small"],
            anchor="w", corner_radius=4,
            command=self._toggle_mappings,
        )
        self._mapping_toggle.pack(side="left")

        self._mapping_frame = create_card_frame(self)
        # starts hidden

        self._mapping_text = ctk.CTkTextbox(
            self._mapping_frame, fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_secondary"],
            font=FONTS["mono_small"], height=80, corner_radius=4,
            border_width=1, border_color=COLORS["border"],
        )
        self._mapping_text.pack(fill="x", padx=8, pady=6)
        self._mapping_text.configure(state="disabled")

    # ── Widget pool ────────────────────────────────────────────────────────

    def _build_card_pool(self):
        """Create one row widget per card. Done once at init."""
        for card in sorted(self._db.get_all_cards(), key=lambda c: c.id):
            row = self._make_row(card)
            self._pool[card.id] = row

    def _make_row(self, card: CardEntry) -> "_CardRow":
        rarity_color = RARITY_COLORS.get(card.rarity, COLORS["text_primary"])

        frame = ctk.CTkFrame(
            self._scroll,
            fg_color=COLORS["bg_card"], corner_radius=5,
            border_width=1, border_color=COLORS["border"],
            height=_ROW_HEIGHT,
        )
        frame.pack_propagate(False)

        # Rank button (clickable → inline editor)
        rank_btn = ctk.CTkButton(
            frame, text="#1", width=40, height=24,
            fg_color=COLORS["bg_light"], hover_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"],
            font=FONTS["mono_small"], corner_radius=4,
            command=lambda: self._start_rank_edit(card.id),
        )
        rank_btn.pack(side="left", padx=(4, 2))

        # Hidden entry for rank editing (overlays the button)
        rank_entry = ctk.CTkEntry(
            frame, width=40, height=24,
            fg_color=COLORS["entry_bg"], border_color=COLORS["accent_blue"],
            text_color=COLORS["text_primary"],
            font=FONTS["mono_small"], corner_radius=4,
            justify="center",
        )
        # Not packed yet — shown on click

        # Rarity dot
        ctk.CTkLabel(
            frame, text="\u25cf", font=("Segoe UI", 13),
            text_color=rarity_color, width=16,
        ).pack(side="left", padx=(2, 3))

        # Name
        name_lbl = ctk.CTkLabel(
            frame, text=card.name,
            font=FONTS["body"], text_color=COLORS["text_primary"],
            anchor="w",
        )
        name_lbl.pack(side="left", padx=(0, 4))

        # Category
        cat_display = CATEGORY_DISPLAY.get(card.category, card.category)
        ctk.CTkLabel(
            frame, text=cat_display,
            font=FONTS["small"], text_color=COLORS["text_muted"],
            fg_color=COLORS["bg_light"], corner_radius=3,
            width=95, height=18,
        ).pack(side="left", padx=2)

        # Rarity text
        ctk.CTkLabel(
            frame, text=card.rarity.title(),
            font=FONTS["small"], text_color=rarity_color,
            width=55,
        ).pack(side="left", padx=4)

        # Description (right-aligned, truncated)
        desc = card.description or ""
        if len(desc) > 45:
            desc = desc[:42] + "..."
        if desc:
            ctk.CTkLabel(
                frame, text=desc,
                font=FONTS["small"], text_color=COLORS["text_muted"],
                anchor="e",
            ).pack(side="right", padx=(0, 6))

        # Tooltip on name
        if card.description:
            name_lbl.configure(cursor="hand2")
            _ToolTip(name_lbl, card.description)

        return _CardRow(
            card_id=card.id, frame=frame,
            rank_btn=rank_btn, rank_entry=rank_entry,
        )

    # ── Repack (fast reorder — no widget creation) ─────────────────────────

    def _repack(self):
        """Re-display rows in current priority order with active filters.
        Only calls pack_forget/pack — no widget creation or destruction."""

        # Cancel any active editor
        self._cancel_rank_edit()

        # Unpack all rows from scroll area (the header stays because it's first)
        for row in self._pool.values():
            row.frame.pack_forget()

        # Determine visible cards
        ordered = self._db.get_priority_list()

        if self._filter_rarity:
            ordered = [c for c in ordered if c.rarity == self._filter_rarity]
        if self._filter_category:
            cat_key = None
            for k, v in CATEGORY_DISPLAY.items():
                if v == self._filter_category:
                    cat_key = k
                    break
            if cat_key:
                ordered = [c for c in ordered if c.category == cat_key]

        self._visible_rows = []
        for i, card in enumerate(ordered):
            row = self._pool.get(card.id)
            if not row:
                continue
            row.rank_btn.configure(text=f"#{i + 1}")
            row.frame.pack(fill="x", pady=1, padx=2)
            self._visible_rows.append(row)

        total = len(self._db.get_all_cards())
        shown = len(self._visible_rows)
        mapped = sum(
            1 for t, info in self._db._texture_to_card.items()
            if info.get("card_ids") and t != DEFAULT_EMPTY_TEXTURE
        )
        self._count_label.configure(text=f"{shown}/{total} shown \u2022 {mapped} textures mapped")

    # ── Inline rank editor ─────────────────────────────────────────────────

    def _start_rank_edit(self, card_id: int):
        """Show an entry widget over the rank button for the clicked card."""
        self._cancel_rank_edit()

        row = self._pool.get(card_id)
        if not row:
            return

        # Find current visual rank
        current_rank = 0
        for i, r in enumerate(self._visible_rows):
            if r.card_id == card_id:
                current_rank = i + 1
                break

        row.rank_btn.pack_forget()
        row.rank_entry.pack(side="left", padx=(4, 2), before=row.frame.winfo_children()[1])
        row.rank_entry.delete(0, "end")
        row.rank_entry.insert(0, str(current_rank))
        row.rank_entry.select_range(0, "end")
        row.rank_entry.focus_set()

        row.rank_entry.bind("<Return>", lambda e: self._commit_rank_edit(card_id))
        row.rank_entry.bind("<KP_Enter>", lambda e: self._commit_rank_edit(card_id))
        row.rank_entry.bind("<Escape>", lambda e: self._cancel_rank_edit())
        row.rank_entry.bind("<FocusOut>", lambda e: self._cancel_rank_edit())

        self._editing_row = row

    def _commit_rank_edit(self, card_id: int):
        """Move card to the typed rank position."""
        row = self._pool.get(card_id)
        if not row:
            return

        raw = row.rank_entry.get().strip()
        try:
            target = int(raw)
        except ValueError:
            self._cancel_rank_edit()
            return

        # Work within the full priority order (not filtered view)
        order = list(self._db._priority_order)
        if card_id not in order:
            self._cancel_rank_edit()
            return

        # If filters active, map visible rank to absolute position
        if self._filter_rarity or self._filter_category:
            # Clamp target within visible range
            target = max(1, min(target, len(self._visible_rows)))
            # Find the card_id currently at that visible rank
            target_row = self._visible_rows[target - 1] if target <= len(self._visible_rows) else self._visible_rows[-1]
            # Find absolute position of target card in full order
            try:
                abs_target = order.index(target_row.card_id)
            except ValueError:
                abs_target = len(order) - 1
            # Remove dragged card and insert at target position
            order.remove(card_id)
            order.insert(abs_target, card_id)
        else:
            # No filter — target is absolute 1-based rank
            target = max(1, min(target, len(order)))
            old_idx = order.index(card_id)
            order.pop(old_idx)
            order.insert(target - 1, card_id)

        self._editing_row = None  # prevent cancel from re-hiding
        self._db.set_priority_order(order)

        # Restore button (entry gets unpacked by repack anyway)
        row.rank_entry.pack_forget()
        row.rank_btn.pack(side="left", padx=(4, 2), before=row.frame.winfo_children()[1])

        self._repack()
        self._status_label.configure(
            text=f"Moved to #{target}", text_color=COLORS["accent_green"],
        )

    def _cancel_rank_edit(self):
        """Hide the active rank entry and restore the button."""
        row = self._editing_row
        if not row:
            return
        self._editing_row = None
        try:
            row.rank_entry.pack_forget()
            # Re-show button if not already visible
            if not row.rank_btn.winfo_ismapped():
                row.rank_btn.pack(
                    side="left", padx=(4, 2),
                    before=row.frame.winfo_children()[1],
                )
        except Exception:
            pass

    # ── Filters ────────────────────────────────────────────────────────────

    def _toggle_rarity(self, rarity: str):
        if self._filter_rarity == rarity:
            self._filter_rarity = None
            for r, btn in self._rarity_btns.items():
                btn.configure(fg_color=RARITY_COLORS[r])
        else:
            self._filter_rarity = rarity
            for r, btn in self._rarity_btns.items():
                btn.configure(fg_color=RARITY_COLORS[r] if r == rarity else COLORS["bg_light"])
        self._repack()

    def _on_cat_filter(self, choice: str):
        self._filter_category = None if choice == "All" else choice
        self._repack()

    # ── Actions ────────────────────────────────────────────────────────────

    def _on_save(self):
        self._db.save()
        self._status_label.configure(text="Saved!", text_color=COLORS["accent_green"])

    def _on_reset(self):
        cards = sorted(
            self._db.get_all_cards(),
            key=lambda c: (-_RARITY_WEIGHT.get(c.rarity, 0), c.id),
        )
        self._db.set_priority_order([c.id for c in cards])
        self._filter_rarity = None
        self._filter_category = None
        self._cat_var.set("All")
        for r, btn in self._rarity_btns.items():
            btn.configure(fg_color=RARITY_COLORS[r])
        self._repack()
        self._status_label.configure(text="Reset to default", text_color=COLORS["accent_orange"])

    def _on_scan_cards(self):
        if not self._engine.memory.is_attached:
            self._status_label.configure(text="Not attached", text_color=COLORS["accent_red"])
            return
        scanner = getattr(self._engine, "scanner", None)
        if not scanner:
            self._status_label.configure(text="No scanner", text_color=COLORS["accent_red"])
            return

        self._scan_btn.configure(state="disabled", text="Scanning...")
        self._status_label.configure(text="")

        def _run():
            try:
                from src.core.card_memory_scanner import CardMemoryScanner
                cms = CardMemoryScanner(self._engine.memory, scanner)
                probe = cms.deep_probe()

                if not probe.ui_open:
                    self.after(0, lambda: self._status_label.configure(
                        text="Card UI not open", text_color=COLORS["accent_red"],
                    ))
                    return

                found = []
                for i, w in enumerate(probe.widgets):
                    info = self._extract_card_info(w)
                    if info:
                        found.append((i, *info))

                if found:
                    parts = []
                    for idx, tex, ri, card in found:
                        if card:
                            parts.append(f"{card.name}({card.rarity})")
                        else:
                            parts.append(f"{tex}({RARITY_INDEX_MAP.get(ri, '?')})")
                    msg = f"{len(found)} cards: " + ", ".join(parts)
                    color = COLORS["accent_green"]
                else:
                    msg = f"{probe.widget_count} widgets, no cards"
                    color = COLORS["accent_orange"]

                self.after(0, lambda: self._status_label.configure(text=msg, text_color=color))
                self.after(0, self._refresh_mappings)
            except Exception as exc:
                log.error(f"[CardPriority] Scan error: {exc}")
                self.after(0, lambda e=str(exc)[:55]: self._status_label.configure(
                    text=f"Error: {e}", text_color=COLORS["accent_red"],
                ))
            finally:
                self.after(0, lambda: self._scan_btn.configure(state="normal", text="Scan Cards"))

        threading.Thread(target=_run, daemon=True).start()

    def _extract_card_info(self, widget_info):
        empty_bg = next((p for p in widget_info.card_item_probes if p.name == "EmptyBg"), None)
        if not empty_bg or empty_bg.visibility != 1:
            return None
        card_icon = next((p for p in widget_info.card_item_probes if p.name == "CardIconMask"), None)
        effect_sw = next((p for p in widget_info.card_item_probes if p.name == "EffectSwitcher"), None)
        if not card_icon:
            return None
        tex = card_icon.icon_texture_name or ""
        if not tex or tex == DEFAULT_EMPTY_TEXTURE:
            return None
        ri = effect_sw.switcher_index if effect_sw else -1
        card = self._db.identify_card(tex, ri)
        return (self._db._clean_texture(tex), ri, card)

    # ── Texture mappings (collapsible) ─────────────────────────────────────

    def _toggle_mappings(self):
        if self._mapping_visible:
            self._mapping_frame.pack_forget()
            self._mapping_toggle.configure(text="\u25b6 Texture Mappings")
            self._mapping_visible = False
        else:
            self._mapping_frame.pack(fill="x", padx=10, pady=(0, 8))
            self._mapping_toggle.configure(text="\u25bc Texture Mappings")
            self._mapping_visible = True
            self._refresh_mappings()

    def _refresh_mappings(self):
        if not self._mapping_visible:
            return
        self._mapping_text.configure(state="normal")
        self._mapping_text.delete("0.0", "end")
        lines = []
        for tex, info in sorted(self._db._texture_to_card.items()):
            ids = info.get("card_ids", [])
            if not ids:
                lines.append(f"  {tex:20s} \u2192 (unmapped)")
                continue
            names = []
            for cid in ids:
                c = self._db.get_card(cid)
                names.append(f"{c.name} ({c.rarity})" if c else f"ID#{cid}")
            lines.append(f"  {tex:20s} \u2192 {', '.join(names)}")
        self._mapping_text.insert("0.0", "\n".join(lines) if lines else "  No mappings yet.")
        self._mapping_text.configure(state="disabled")


# ── Helper classes ─────────────────────────────────────────────────────────────

class _CardRow:
    __slots__ = ("card_id", "frame", "rank_btn", "rank_entry")

    def __init__(self, card_id: int, frame, rank_btn, rank_entry):
        self.card_id = card_id
        self.frame = frame
        self.rank_btn = rank_btn
        self.rank_entry = rank_entry


class _ToolTip:
    def __init__(self, widget, text: str):
        self._w = widget
        self._text = text
        self._tw = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _=None):
        if self._tw:
            return
        x = self._w.winfo_rootx() + 20
        y = self._w.winfo_rooty() + 28
        self._tw = tw = ctk.CTkToplevel()
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        ctk.CTkLabel(
            tw, text=self._text, font=FONTS["small"],
            fg_color=COLORS["bg_light"], text_color=COLORS["text_primary"],
            corner_radius=6, wraplength=280, padx=8, pady=4,
        ).pack()

    def _hide(self, _=None):
        if self._tw:
            self._tw.destroy()
            self._tw = None
