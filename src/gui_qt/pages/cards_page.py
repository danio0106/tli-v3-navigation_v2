import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.core.card_database import (
    CardDatabase,
    CATEGORY_DISPLAY,
    DEFAULT_EMPTY_TEXTURE,
    RARITY_COLORS,
    RARITY_INDEX_MAP,
)
from src.utils.logger import log
from src.gui_qt.theme import set_button_variant

_RARITY_WEIGHT = {"rainbow": 4, "orange": 3, "purple": 2, "blue": 1}
_RARITY_SURFACE = {
    "rainbow": "#3A2327",
    "orange": "#3A2A17",
    "purple": "#302142",
    "blue": "#1A2D45",
}
_RARITY_FORE = {
    "rainbow": "#FFC7C7",
    "orange": "#FFD49B",
    "purple": "#DCBEFF",
    "blue": "#B5DEFF",
}


class DragDropCardTable(QTableWidget):
    rows_reordered = Signal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.rows_reordered.emit()


class CardsPage(QFrame):
    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge
        self._engine = bridge.engine
        self._db = getattr(self._engine, "_card_database", None) or CardDatabase()
        self._debug_ui_enabled = bool(self._engine.config.get("debug_ui_enabled", False))

        self._filter_rarity = None
        self._filter_category = None
        self._visible_card_ids = []
        self._updating_table = False
        self._building_table = False

        self._build_ui()
        self._rebuild_table()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("Card Priority")
        title.setObjectName("PageTitle")
        top.addWidget(title)
        top.addStretch(1)

        self._default_btn = QPushButton("Default Order")
        self._default_btn.clicked.connect(self._on_reset)
        set_button_variant(self._default_btn, "warning")
        top.addWidget(self._default_btn)

        if self._debug_ui_enabled:
            self._scan_btn = QPushButton("Scan Cards (Diag)")
            self._scan_btn.clicked.connect(self._on_scan_cards)
            set_button_variant(self._scan_btn, "info")
            self._scan_btn.setToolTip("Diagnostic: reads live card widget textures and rarity effects")
            top.addWidget(self._scan_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        set_button_variant(self._save_btn, "success")
        top.addWidget(self._save_btn)
        root.addLayout(top)

        info = QHBoxLayout()
        self._info_label = QLabel("Drag and drop rows to reorder cards. You can still edit Rank directly.")
        self._info_label.setObjectName("PageBody")
        info.addWidget(self._info_label)
        info.addStretch(1)
        self._count_label = QLabel("")
        self._count_label.setObjectName("PageBody")
        info.addWidget(self._count_label)
        root.addLayout(info)

        fbar = QVBoxLayout()
        fbar.setSpacing(6)

        rarity_row = QHBoxLayout()
        rarity_row.setSpacing(6)
        rarity_row.addWidget(QLabel("Rarity filter:"))
        self._rarity_btns = {}
        for rarity in ("rainbow", "orange", "purple", "blue"):
            btn = QPushButton(rarity.title())
            btn.clicked.connect(lambda checked=False, r=rarity: self._toggle_rarity(r))
            btn.setToolTip(rarity.title())
            btn.setMinimumWidth(84)
            btn.setStyleSheet(f"background:{RARITY_COLORS[rarity]}; color:#F8FAFC; border:1px solid #4A5564;")
            self._rarity_btns[rarity] = btn
            rarity_row.addWidget(btn)
        rarity_row.addStretch(1)
        fbar.addLayout(rarity_row)

        category_row = QHBoxLayout()
        category_row.setSpacing(6)
        category_row.addWidget(QLabel("Category filter:"))
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("All")
        for v in CATEGORY_DISPLAY.values():
            self._cat_combo.addItem(v)
        self._cat_combo.currentTextChanged.connect(self._on_cat_filter)
        self._cat_combo.setMinimumWidth(220)
        category_row.addWidget(self._cat_combo)
        category_row.addStretch(1)
        fbar.addLayout(category_row)

        status_row = QHBoxLayout()
        status_row.addStretch(1)
        self._status_label = QLabel("")
        self._status_label.setObjectName("PageBody")
        status_row.addWidget(self._status_label)
        fbar.addLayout(status_row)
        root.addLayout(fbar)

        self._table = DragDropCardTable(0, 5)
        self._table.setObjectName("CardPriorityTable")
        self._table.setHorizontalHeaderLabels(["Rank", "Card Name", "Category", "Details", "ID"])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setDragEnabled(True)
        self._table.setAcceptDrops(True)
        self._table.viewport().setAcceptDrops(True)
        self._table.setDropIndicatorShown(True)
        self._table.setDragDropOverwriteMode(False)
        self._table.setDragDropMode(QAbstractItemView.InternalMove)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.rows_reordered.connect(self._on_rows_reordered)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        self._table.setColumnHidden(4, True)
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.setStyleSheet(
            "QTableWidget#CardPriorityTable {"
            "background:#0F141A;"
            "border:1px solid #30363D;"
            "border-radius:10px;"
            "}"
            "QTableWidget#CardPriorityTable::item { padding: 6px 8px; }"
            "QTableWidget#CardPriorityTable::item:selected { background:#2A3A4E; color:#E6EDF3; }"
        )
        root.addWidget(self._table, 1)

        if self._debug_ui_enabled:
            toggle = QHBoxLayout()
            self._mapping_visible = False
            self._mapping_btn = QPushButton("▶ Texture Mappings")
            self._mapping_btn.clicked.connect(self._toggle_mappings)
            set_button_variant(self._mapping_btn, "muted")
            toggle.addWidget(self._mapping_btn)
            toggle.addStretch(1)
            root.addLayout(toggle)

            self._mapping_text = QPlainTextEdit()
            self._mapping_text.setReadOnly(True)
            self._mapping_text.hide()
            root.addWidget(self._mapping_text)

    def _current_ordered_cards(self):
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
        return ordered

    def _rebuild_table(self):
        self._building_table = True
        try:
            ordered = self._current_ordered_cards()
            self._visible_card_ids = [c.id for c in ordered]

            self._table.setRowCount(len(ordered))
            drag_enabled = not (self._filter_rarity or self._filter_category)
            self._table.setDragDropMode(
                QAbstractItemView.InternalMove if drag_enabled else QAbstractItemView.NoDragDrop
            )
            self._table.setDragEnabled(drag_enabled)
            for r, card in enumerate(ordered):
                rank_item = QTableWidgetItem(str(r + 1))
                rank_item.setFlags(rank_item.flags() | Qt.ItemIsEditable)

                name_item = QTableWidgetItem(card.name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)

                category = CATEGORY_DISPLAY.get(card.category, card.category)
                cat_item = QTableWidgetItem(category)
                cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsEditable)

                desc_item = QTableWidgetItem("Hover")
                desc_item.setTextAlignment(Qt.AlignCenter)
                desc_item.setForeground(QBrush(QColor(_RARITY_FORE.get(card.rarity, "#E6EDF3"))))
                raw_desc = card.description or "No description available."
                tooltip = (
                    "<div style='max-width:360px; color:#E6EDF3; background:#0F141A; "
                    "border:1px solid #3D4757; border-radius:8px; padding:10px; line-height:1.35;'>"
                    f"<div style='font-weight:700; margin-bottom:6px;'>{card.name}</div>"
                    f"<div>{raw_desc}</div>"
                    "</div>"
                )
                desc_item.setToolTip(tooltip)
                desc_item.setFlags(desc_item.flags() & ~Qt.ItemIsEditable)

                id_item = QTableWidgetItem(str(card.id))
                id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)

                self._table.setItem(r, 0, rank_item)
                self._table.setItem(r, 1, name_item)
                self._table.setItem(r, 2, cat_item)
                self._table.setItem(r, 3, desc_item)
                self._table.setItem(r, 4, id_item)

                # Subtle rarity-tinted row backgrounds improve scanning speed.
                row_bg = QBrush(QColor(_RARITY_SURFACE.get(card.rarity, "#141A21")))
                for col in range(0, 4):
                    it = self._table.item(r, col)
                    if it is not None:
                        it.setBackground(row_bg)

            total = len(self._db.get_all_cards())
            shown = len(ordered)
            mapped = sum(
                1 for t, info in self._db._texture_to_card.items()
                if info.get("card_ids") and t != DEFAULT_EMPTY_TEXTURE
            )
            self._count_label.setText(f"{shown}/{total} shown • {mapped} textures mapped")
            if not drag_enabled:
                self._status_label.setText("Drag reorder disabled while filters are active")
                self._status_label.setStyleSheet("color:#8B949E;")
            else:
                self._status_label.setText("")
        finally:
            self._building_table = False

    def _apply_rank_edit(self, row: int, target_rank: int):
        if row < 0 or row >= len(self._visible_card_ids):
            return
        card_id = self._visible_card_ids[row]

        order = list(self._db._priority_order)
        if card_id not in order:
            return

        if self._filter_rarity or self._filter_category:
            target_rank = max(1, min(target_rank, len(self._visible_card_ids)))
            target_id = self._visible_card_ids[target_rank - 1]
            abs_target = order.index(target_id) if target_id in order else len(order) - 1
            order.remove(card_id)
            order.insert(abs_target, card_id)
        else:
            target_rank = max(1, min(target_rank, len(order)))
            old_idx = order.index(card_id)
            order.pop(old_idx)
            order.insert(target_rank - 1, card_id)

        self._db.set_priority_order(order)
        self._status_label.setText(f"Moved to #{target_rank}")
        self._status_label.setStyleSheet("color:#3FB950;")
        self._rebuild_table()

    def _on_item_changed(self, item):
        if self._building_table or self._updating_table:
            return
        if item.column() != 0:
            return
        try:
            target = int((item.text() or "").strip())
        except ValueError:
            self._rebuild_table()
            return

        self._updating_table = True
        try:
            self._apply_rank_edit(item.row(), target)
        finally:
            self._updating_table = False

    def _on_rows_reordered(self):
        if self._building_table or self._updating_table:
            return
        if self._filter_rarity or self._filter_category:
            self._rebuild_table()
            return

        row_count = self._table.rowCount()
        visible_ids = []
        for row in range(row_count):
            item = self._table.item(row, 4)
            if item is None:
                continue
            try:
                visible_ids.append(int(item.text()))
            except Exception:
                continue

        if not visible_ids:
            return

        old_order = list(self._db._priority_order)
        visible_set = set(visible_ids)
        untouched = [cid for cid in old_order if cid not in visible_set]
        new_order = list(visible_ids) + untouched
        self._db.set_priority_order(new_order)
        self._status_label.setText("Order updated via drag & drop")
        self._status_label.setStyleSheet("color:#3FB950;")
        self._rebuild_table()

    def _toggle_rarity(self, rarity: str):
        if self._filter_rarity == rarity:
            self._filter_rarity = None
            for r, btn in self._rarity_btns.items():
                btn.setStyleSheet(f"background:{RARITY_COLORS[r]}; color:#F8FAFC; border:1px solid #4A5564;")
        else:
            self._filter_rarity = rarity
            for r, btn in self._rarity_btns.items():
                if r == rarity:
                    btn.setStyleSheet(f"background:{RARITY_COLORS[r]}; color:#FFFFFF; border:2px solid #F8FAFC;")
                else:
                    btn.setStyleSheet("background:#21262D; color:#8B949E;")
        self._rebuild_table()

    def _on_cat_filter(self, choice: str):
        self._filter_category = None if choice == "All" else choice
        self._rebuild_table()

    def _on_save(self):
        self._db.save()
        self._status_label.setText("Saved!")
        self._status_label.setStyleSheet("color:#3FB950;")

    def _on_reset(self):
        cards = sorted(self._db.get_all_cards(), key=lambda c: (-_RARITY_WEIGHT.get(c.rarity, 0), c.id))
        self._db.set_priority_order([c.id for c in cards])
        self._filter_rarity = None
        self._filter_category = None
        self._cat_combo.setCurrentText("All")
        for r, btn in self._rarity_btns.items():
            btn.setStyleSheet(f"background:{RARITY_COLORS[r]}; color:#F8FAFC; border:1px solid #4A5564;")
        self._rebuild_table()
        self._status_label.setText("Reset to default")
        self._status_label.setStyleSheet("color:#D29922;")

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

    def _on_scan_cards(self):
        if not self._debug_ui_enabled:
            return
        if not self._engine.memory.is_attached:
            self._status_label.setText("Not attached")
            self._status_label.setStyleSheet("color:#F85149;")
            return
        scanner = getattr(self._engine, "scanner", None)
        if not scanner:
            self._status_label.setText("No scanner")
            self._status_label.setStyleSheet("color:#F85149;")
            return

        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("Scanning...")
        self._status_label.setText("")

        def _run():
            try:
                from src.core.card_memory_scanner import CardMemoryScanner

                cms = CardMemoryScanner(self._engine.memory, scanner)
                probe = cms.deep_probe()
                if not probe.ui_open:
                    self._set_status_async("Card UI not open", "#F85149")
                    return

                found = []
                for i, w in enumerate(probe.widgets):
                    info = self._extract_card_info(w)
                    if info:
                        found.append((i, *info))

                if found:
                    parts = []
                    for _, tex, ri, card in found:
                        if card:
                            parts.append(f"{card.name}({card.rarity})")
                        else:
                            parts.append(f"{tex}({RARITY_INDEX_MAP.get(ri, '?')})")
                    msg = f"{len(found)} cards: " + ", ".join(parts)
                    color = "#3FB950"
                else:
                    msg = f"{probe.widget_count} widgets, no cards"
                    color = "#D29922"

                self._set_status_async(msg, color)
                self._refresh_mappings_async()
            except Exception as exc:
                log.error(f"[CardPriorityQt] Scan error: {exc}")
                self._set_status_async(f"Error: {str(exc)[:55]}", "#F85149")
            finally:
                self._reset_scan_btn_async()

        threading.Thread(target=_run, daemon=True).start()

    def _set_status_async(self, text: str, color: str):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: (self._status_label.setText(text), self._status_label.setStyleSheet(f"color:{color};")))

    def _refresh_mappings_async(self):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._refresh_mappings)

    def _reset_scan_btn_async(self):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: (self._scan_btn.setEnabled(True), self._scan_btn.setText("Scan Cards (Diag)")))

    def _toggle_mappings(self):
        if not self._debug_ui_enabled:
            return
        self._mapping_visible = not self._mapping_visible
        if self._mapping_visible:
            self._mapping_btn.setText("▼ Texture Mappings")
            self._mapping_text.show()
            self._refresh_mappings()
        else:
            self._mapping_btn.setText("▶ Texture Mappings")
            self._mapping_text.hide()

    def _refresh_mappings(self):
        if not self._debug_ui_enabled:
            return
        if not self._mapping_visible:
            return
        lines = []
        for tex, info in sorted(self._db._texture_to_card.items()):
            ids = info.get("card_ids", [])
            if not ids:
                lines.append(f"  {tex:20s} -> (unmapped)")
                continue
            names = []
            for cid in ids:
                c = self._db.get_card(cid)
                names.append(f"{c.name} ({c.rarity})" if c else f"ID#{cid}")
            lines.append(f"  {tex:20s} -> {', '.join(names)}")
        self._mapping_text.setPlainText("\n".join(lines) if lines else "  No mappings yet.")
