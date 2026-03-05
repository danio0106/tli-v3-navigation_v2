import math

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from src.utils.logger import log
from src.gui_qt.theme import set_button_variant

_LIST_HEADER = f"{'#':>3}  {'Class':<18}  {'X':>8}  {'Y':>8}  {'Dist':>6}  V  {'ABP type':<14}  {'CfgID':>9}"
_LIST_SEP = "-" * len(_LIST_HEADER)


def _abp_short(abp_class: str) -> str:
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


class EntityScannerPage(QFrame):
    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge
        self._engine = bridge.engine
        self._last_monsters = []
        self._auto_refresh = False
        self._build_ui()

        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(1000)
        self._auto_timer.timeout.connect(self._do_scan)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("Entity Scanner")
        title.setObjectName("PageTitle")
        top.addWidget(title)
        top.addStretch(1)

        self._auto_cb = QCheckBox("Auto (1 s)")
        self._auto_cb.toggled.connect(self._toggle_auto)
        top.addWidget(self._auto_cb)

        self._scan_btn = QPushButton("Scan Now")
        self._scan_btn.clicked.connect(self._do_scan)
        set_button_variant(self._scan_btn, "info")
        top.addWidget(self._scan_btn)
        root.addLayout(top)

        stats_card = QFrame()
        stats_card.setObjectName("Card")
        stats_layout = QGridLayout(stats_card)
        stats_layout.setHorizontalSpacing(10)

        self._stat_labels = {}
        stats = [("Total", "total"), ("Alive (bValid)", "alive"), ("Unique Classes", "classes"), ("Player Pos", "playerpos")]
        for col, (name, key) in enumerate(stats):
            stats_layout.addWidget(QLabel(name), 0, col)
            lbl = QLabel("-")
            lbl.setObjectName("PageBody")
            stats_layout.addWidget(lbl, 1, col)
            self._stat_labels[key] = lbl
        root.addWidget(stats_card)

        filter_card = QFrame()
        filter_card.setObjectName("Card")
        filter_layout = QHBoxLayout(filter_card)

        filter_layout.addWidget(QLabel("Filter class:"))
        self._filter_entry = QLineEdit()
        self._filter_entry.setPlaceholderText("e.g. EMonster")
        self._filter_entry.setFixedWidth(160)
        self._filter_entry.textChanged.connect(self._refresh_display)
        filter_layout.addWidget(self._filter_entry)

        filter_layout.addWidget(QLabel("Max dist:"))
        self._max_dist_entry = QLineEdit()
        self._max_dist_entry.setPlaceholderText("e.g. 3000")
        self._max_dist_entry.setFixedWidth(100)
        self._max_dist_entry.textChanged.connect(self._refresh_display)
        filter_layout.addWidget(self._max_dist_entry)

        self._hide_dead_cb = QCheckBox("Hide dead")
        self._hide_dead_cb.setChecked(True)
        self._hide_dead_cb.toggled.connect(self._refresh_display)
        filter_layout.addWidget(self._hide_dead_cb)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_filter)
        set_button_variant(clear_btn, "muted")
        filter_layout.addWidget(clear_btn)

        filter_layout.addStretch(1)
        self._filter_status = QLabel("")
        self._filter_status.setObjectName("PageBody")
        filter_layout.addWidget(self._filter_status)
        root.addWidget(filter_card)

        summary_card = QFrame()
        summary_card.setObjectName("Card")
        summary_layout = QVBoxLayout(summary_card)
        summary_title = QLabel("Class breakdown (alive / total)")
        summary_title.setObjectName("PageTitle")
        summary_layout.addWidget(summary_title)

        self._summary_text = QPlainTextEdit()
        self._summary_text.setReadOnly(True)
        self._summary_text.setMaximumBlockCount(500)
        summary_layout.addWidget(self._summary_text)
        root.addWidget(summary_card)

        list_card = QFrame()
        list_card.setObjectName("Card")
        list_layout = QVBoxLayout(list_card)

        list_top = QHBoxLayout()
        list_title = QLabel("Entity list (sorted by distance)")
        list_title.setObjectName("PageTitle")
        list_top.addWidget(list_title)
        self._list_count_label = QLabel("")
        self._list_count_label.setObjectName("PageBody")
        list_top.addWidget(self._list_count_label)
        list_top.addStretch(1)
        list_layout.addLayout(list_top)

        self._entity_text = QPlainTextEdit()
        self._entity_text.setReadOnly(True)
        self._entity_text.setMaximumBlockCount(1500)
        list_layout.addWidget(self._entity_text)
        root.addWidget(list_card, 1)

    def _get_scanner(self):
        return getattr(self._engine, "scanner", None) or getattr(self._engine, "_scanner", None)

    def _get_player_pos(self):
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
            self._entity_text.setPlainText(f"{_LIST_HEADER}\n{_LIST_SEP}\n(No scanner available - attach to game first.)")
            self._stat_labels["total"].setText("-")
            self._stat_labels["alive"].setText("-")
            self._stat_labels["classes"].setText("-")
            return

        try:
            self._last_monsters = scanner.get_monster_entities() or []
        except Exception as exc:
            self._entity_text.setPlainText(f"{_LIST_HEADER}\n{_LIST_SEP}\nScan error: {exc}")
            log.warning(f"[EntityScannerQt] scan error: {exc}")
            return

        self._refresh_display()

    def _refresh_display(self):
        monsters = self._last_monsters
        px, py = self._get_player_pos()

        total = len(monsters)
        alive = sum(1 for m in monsters if m.bvalid != 0)
        unique_classes = len({(m.sub_object_class or "?") for m in monsters})

        self._stat_labels["total"].setText(str(total))
        self._stat_labels["alive"].setText(str(alive))
        self._stat_labels["classes"].setText(str(unique_classes))
        self._stat_labels["playerpos"].setText(f"{px:.0f},{py:.0f}" if px is not None else "-")

        class_totals = {}
        class_alive = {}
        for m in monsters:
            cn = m.sub_object_class or "?"
            class_totals[cn] = class_totals.get(cn, 0) + 1
            if m.bvalid != 0:
                class_alive[cn] = class_alive.get(cn, 0) + 1

        summary_lines = []
        for cn, cnt in sorted(class_totals.items(), key=lambda kv: kv[1], reverse=True):
            summary_lines.append(f"{cn:<32s}  {class_alive.get(cn, 0):>3}/{cnt:<3}")
        self._summary_text.setPlainText("\n".join(summary_lines) if summary_lines else "(no monsters)")

        hide_dead = self._hide_dead_cb.isChecked()
        max_dist = 0.0
        try:
            raw = self._max_dist_entry.text().strip()
            if raw:
                max_dist = float(raw)
        except ValueError:
            max_dist = 0.0

        ftext = self._filter_entry.text().strip().lower()

        def _dist(mon):
            if px is None:
                return 0.0
            dx = mon.position[0] - px
            dy = mon.position[1] - py
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

        filtered.sort(key=lambda m: (1 if m.bvalid == 0 else 0, dists[id(m)]))

        shown = len(filtered)
        self._filter_status.setText(f"{shown} of {total} shown" if (ftext or hide_dead or max_dist > 0) else "")
        self._list_count_label.setText(f"({shown} entities)")

        lines = [_LIST_HEADER, _LIST_SEP]
        for i, m in enumerate(filtered, start=1):
            cls_short = (m.sub_object_class or "?").split(".")[-1]
            mx, my = m.position[0], m.position[1]
            dist_str = f"{dists[id(m)]:>6.0f}" if px is not None else "     -"
            valid_str = "Y" if m.bvalid == 1 else ("N" if m.bvalid == 0 else "?")
            abp_short = _abp_short(getattr(m, "abp_class", ""))
            cid = getattr(m, "source_type", -1)
            cfg_str = str(cid) if cid != -1 else "--"
            lines.append(
                f"{i:>3}  {cls_short:<18s}  {mx:>8.0f}  {my:>8.0f}  {dist_str}  {valid_str}  {abp_short:<14}  {cfg_str:>9}"
            )

        if len(lines) == 2:
            lines.append("(no entities match filter)")
        self._entity_text.setPlainText("\n".join(lines))

    def _clear_filter(self):
        self._filter_entry.clear()
        self._max_dist_entry.clear()
        self._refresh_display()

    def _toggle_auto(self, enabled: bool):
        self._auto_refresh = bool(enabled)
        if self._auto_refresh:
            self._auto_timer.start()
            self._do_scan()
        else:
            self._auto_timer.stop()

    def closeEvent(self, event):  # noqa: N802
        try:
            self._auto_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)
