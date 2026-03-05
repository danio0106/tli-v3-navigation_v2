import threading
import time
from typing import List, Set

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.core.waypoint import Waypoint
from src.utils.constants import MAP_NAMES
from src.gui_qt.theme import set_button_variant


class PathsPage(QFrame):
    _COV_MIN_RELIABLE_POINTS = 120
    _COV_GOOD_PCT = 70.0

    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge
        self._engine = bridge.engine

        self._last_auto_map = ""
        self._loaded_waypoints: List[Waypoint] = []
        self._selected_indices: Set[int] = set()
        self._is_paused = False
        self._nav_mode = "auto"
        self._updating_table = False
        self._overlay_callback = None
        self._grid_overlay_cb = None
        self._manual_stop_event = None
        self._manual_thread = None

        try:
            self._engine.config.set("nav_mode", "auto")
        except Exception:
            pass

        self._build_ui()
        self._sync_auto_behavior_ui_from_config()
        self._update_mode_ui()

        self._map_timer = QTimer(self)
        self._map_timer.setInterval(2000)
        self._map_timer.timeout.connect(self._map_poll_tick)
        self._map_timer.start()
        self._map_poll_tick()

        self._record_timer = QTimer(self)
        self._record_timer.timeout.connect(self._record_tick)

        self._explore_poll_timer = QTimer(self)
        self._explore_poll_timer.setInterval(2000)
        self._explore_poll_timer.timeout.connect(self._explore_poll)

        self._refresh_coverage_overview()
        self._refresh_wall_status()

    def set_overlay_callback(self, callback):
        self._overlay_callback = callback

    def set_grid_overlay_callback(self, callback):
        self._grid_overlay_cb = callback

    def _notify_overlay(self):
        self._sync_recorder()
        if callable(self._overlay_callback):
            try:
                self._overlay_callback(self._loaded_waypoints)
            except Exception:
                pass

    def _sync_recorder(self):
        recorder = self._engine.path_recorder
        if recorder and recorder.is_recording:
            recorder._waypoints = list(self._loaded_waypoints)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("Map Paths")
        title.setObjectName("PageTitle")
        top.addWidget(title)
        top.addStretch(1)
        root.addLayout(top)

        map_card = QFrame()
        map_card.setObjectName("Card")
        map_layout = QHBoxLayout(map_card)
        map_layout.addWidget(QLabel("Current Map:"))
        self._map_name_label = QLabel("Reading...")
        self._map_name_label.setObjectName("PageBody")
        map_layout.addWidget(self._map_name_label)
        map_layout.addStretch(1)
        root.addWidget(map_card)

        mode_card = QFrame()
        mode_card.setObjectName("Card")
        mode_layout = QHBoxLayout(mode_card)
        mode_layout.addWidget(QLabel("Navigation Mode:"))

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)

        self._mode_record_btn = QPushButton("Recording")
        self._mode_record_btn.setCheckable(True)
        self._mode_record_btn.setToolTip("Recording mode")
        self._mode_record_btn.clicked.connect(self._on_set_record_mode)
        mode_layout.addWidget(self._mode_record_btn)
        self._mode_group.addButton(self._mode_record_btn)

        self._mode_auto_btn = QPushButton("Auto Navigation")
        self._mode_auto_btn.setCheckable(True)
        self._mode_auto_btn.setToolTip("Auto navigation mode")
        self._mode_auto_btn.clicked.connect(self._on_set_auto_mode)
        mode_layout.addWidget(self._mode_auto_btn)
        self._mode_group.addButton(self._mode_auto_btn)

        self._mode_info = QLabel("")
        self._mode_info.setObjectName("PageBody")
        mode_layout.addWidget(self._mode_info)
        mode_layout.addStretch(1)
        root.addWidget(mode_card)

        self._rec_card = QFrame()
        self._rec_card.setObjectName("Card")
        rec_layout = QVBoxLayout(self._rec_card)
        rec_title = QLabel("Recording")
        rec_title.setObjectName("PageTitle")
        rec_layout.addWidget(rec_title)

        self._rec_status = QLabel("Status: Idle")
        self._rec_status.setObjectName("PageBody")
        rec_layout.addWidget(self._rec_status)

        self._rec_count = QLabel("Points: 0")
        self._rec_count.setObjectName("PageBody")
        rec_layout.addWidget(self._rec_count)

        rec_btns_1 = QHBoxLayout()
        self._record_btn = QPushButton("Start (F5)")
        self._record_btn.clicked.connect(self._on_record)
        set_button_variant(self._record_btn, "success")
        self._record_btn.setToolTip("Start recording waypoints (F5)")
        rec_btns_1.addWidget(self._record_btn)

        self._pause_btn = QPushButton("Pause (F6)")
        self._pause_btn.clicked.connect(self._on_pause_record)
        self._pause_btn.setEnabled(False)
        set_button_variant(self._pause_btn, "warning")
        rec_btns_1.addWidget(self._pause_btn)
        rec_layout.addLayout(rec_btns_1)

        rec_btns_2 = QHBoxLayout()
        self._portal_btn = QPushButton("Mark Portal")
        self._portal_btn.clicked.connect(self._on_mark_portal)
        set_button_variant(self._portal_btn, "warning")
        self._portal_btn.setToolTip("Mark portal waypoint (P)")
        rec_btns_2.addWidget(self._portal_btn)

        self._save_btn = QPushButton("Save Path")
        self._save_btn.clicked.connect(self._on_save)
        set_button_variant(self._save_btn, "primary")
        rec_btns_2.addWidget(self._save_btn)
        rec_layout.addLayout(rec_btns_2)
        root.addWidget(self._rec_card)

        self._auto_card = QFrame()
        self._auto_card.setObjectName("Card")
        auto_layout = QVBoxLayout(self._auto_card)
        auto_title = QLabel("Auto-Navigation (A*)")
        auto_title.setObjectName("PageTitle")
        auto_layout.addWidget(auto_title)

        behavior_row = QHBoxLayout()
        behavior_row.addWidget(QLabel("Auto behavior:"))
        self._auto_behavior_menu = QComboBox()
        self._auto_behavior_menu.addItems(["Rush Events", "Kill All", "Boss Rush"])
        self._auto_behavior_menu.currentTextChanged.connect(self._on_auto_behavior_change)
        behavior_row.addWidget(self._auto_behavior_menu)
        behavior_row.addStretch(1)
        auto_layout.addLayout(behavior_row)

        wall_row = QHBoxLayout()
        wall_row.addWidget(QLabel("Walkable data:"))
        self._wall_status_label = QLabel("-")
        self._wall_status_label.setObjectName("PageBody")
        wall_row.addWidget(self._wall_status_label)
        wall_row.addStretch(1)
        auto_layout.addLayout(wall_row)

        cov_head = QHBoxLayout()
        cov_title = QLabel("Coverage Overview")
        cov_title.setObjectName("PageTitle")
        cov_head.addWidget(cov_title)
        cov_head.addStretch(1)
        self._cov_refresh_btn = QPushButton("Refresh")
        self._cov_refresh_btn.clicked.connect(self._refresh_coverage_overview)
        set_button_variant(self._cov_refresh_btn, "muted")
        cov_head.addWidget(self._cov_refresh_btn)
        auto_layout.addLayout(cov_head)

        self._coverage_table = QTableWidget(0, 2)
        self._coverage_table.setObjectName("CoverageTable")
        self._coverage_table.setHorizontalHeaderLabels(["Map", "Coverage"])
        self._coverage_table.verticalHeader().setVisible(False)
        self._coverage_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._coverage_table.setSelectionMode(QTableWidget.NoSelection)
        self._coverage_table.setAlternatingRowColors(True)
        self._coverage_table.setShowGrid(False)
        self._coverage_table.verticalHeader().setDefaultSectionSize(30)
        self._coverage_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._coverage_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._coverage_table.setStyleSheet(
            "QTableWidget#CoverageTable {"
            "background:#10161D;"
            "border:1px solid #30363D;"
            "border-radius:8px;"
            "}"
            "QTableWidget#CoverageTable::item { padding: 4px 8px; }"
            "QTableWidget#CoverageTable::item:selected { background: transparent; }"
        )
        auto_layout.addWidget(self._coverage_table)

        explore_title = QLabel("Map Explorer")
        explore_title.setObjectName("PageTitle")
        auto_layout.addWidget(explore_title)

        explore_desc = QLabel("Fully automated exploration: bot navigates frontiers and builds walkable coverage.")
        explore_desc.setObjectName("PageBody")
        explore_desc.setWordWrap(True)
        auto_layout.addWidget(explore_desc)

        self._explore_progress_label = QLabel("Idle")
        self._explore_progress_label.setObjectName("PageBody")
        auto_layout.addWidget(self._explore_progress_label)

        explore_btns = QHBoxLayout()
        self._explore_start_btn = QPushButton("Explore Map")
        self._explore_start_btn.clicked.connect(self._on_explore_start)
        set_button_variant(self._explore_start_btn, "info")
        explore_btns.addWidget(self._explore_start_btn)
        self._explore_stop_btn = QPushButton("Stop")
        self._explore_stop_btn.setEnabled(False)
        self._explore_stop_btn.clicked.connect(self._on_explore_stop)
        set_button_variant(self._explore_stop_btn, "danger")
        explore_btns.addWidget(self._explore_stop_btn)
        auto_layout.addLayout(explore_btns)

        manual_title = QLabel("Manual Explore")
        manual_title.setObjectName("PageTitle")
        auto_layout.addWidget(manual_title)

        manual_desc = QLabel("Assisted exploration: you move the character, bot samples positions and updates coverage.")
        manual_desc.setObjectName("PageBody")
        manual_desc.setWordWrap(True)
        auto_layout.addWidget(manual_desc)

        self._manual_status_label = QLabel("Idle")
        self._manual_status_label.setObjectName("PageBody")
        auto_layout.addWidget(self._manual_status_label)

        manual_btns = QHBoxLayout()
        self._manual_start_btn = QPushButton("Start Manual Explore")
        self._manual_start_btn.clicked.connect(self._on_manual_explore_start)
        set_button_variant(self._manual_start_btn, "primary")
        manual_btns.addWidget(self._manual_start_btn)
        self._manual_stop_btn = QPushButton("Stop")
        self._manual_stop_btn.setEnabled(False)
        self._manual_stop_btn.clicked.connect(self._on_manual_explore_stop)
        set_button_variant(self._manual_stop_btn, "danger")
        manual_btns.addWidget(self._manual_stop_btn)
        auto_layout.addLayout(manual_btns)

        root.addWidget(self._auto_card)

        self._wp_card = QFrame()
        self._wp_card.setObjectName("Card")
        wp_layout = QVBoxLayout(self._wp_card)
        wp_head = QHBoxLayout()
        wp_title = QLabel("Waypoints")
        wp_title.setObjectName("PageTitle")
        wp_head.addWidget(wp_title)
        self._wp_count_label = QLabel("(0)")
        self._wp_count_label.setObjectName("PageBody")
        wp_head.addWidget(self._wp_count_label)
        wp_head.addStretch(1)
        wp_layout.addLayout(wp_head)

        self._wp_table = QTableWidget(0, 6)
        self._wp_table.setHorizontalHeaderLabels(["#", "X", "Y", "Type", "Portal", "Wait"])
        self._wp_table.verticalHeader().setVisible(False)
        self._wp_table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self._wp_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._wp_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self._wp_table.setEditTriggers(QTableWidget.NoEditTriggers)
        wp_layout.addWidget(self._wp_table)

        row1 = QHBoxLayout()
        self._btn_delete = QPushButton("Delete Selected")
        self._btn_delete.clicked.connect(self._on_delete_selected)
        set_button_variant(self._btn_delete, "danger")
        row1.addWidget(self._btn_delete)
        self._btn_delete_all = QPushButton("Delete All")
        self._btn_delete_all.clicked.connect(self._on_delete_all)
        set_button_variant(self._btn_delete_all, "danger")
        row1.addWidget(self._btn_delete_all)
        wp_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self._btn_move_up = QPushButton("Move Up")
        self._btn_move_up.clicked.connect(self._on_move_up)
        set_button_variant(self._btn_move_up, "muted")
        row2.addWidget(self._btn_move_up)
        self._btn_move_down = QPushButton("Move Down")
        self._btn_move_down.clicked.connect(self._on_move_down)
        set_button_variant(self._btn_move_down, "muted")
        row2.addWidget(self._btn_move_down)
        wp_layout.addLayout(row2)

        row3 = QHBoxLayout()
        self._btn_set_node = QPushButton("Set Node")
        self._btn_set_node.clicked.connect(self._on_set_node)
        set_button_variant(self._btn_set_node, "info")
        row3.addWidget(self._btn_set_node)
        self._btn_set_stand = QPushButton("Set Stand")
        self._btn_set_stand.clicked.connect(self._on_set_stand)
        set_button_variant(self._btn_set_stand, "warning")
        row3.addWidget(self._btn_set_stand)
        self._btn_toggle_portal = QPushButton("Toggle Portal")
        self._btn_toggle_portal.clicked.connect(self._on_toggle_portal)
        set_button_variant(self._btn_toggle_portal, "primary")
        row3.addWidget(self._btn_toggle_portal)
        wp_layout.addLayout(row3)

        edit_row = QHBoxLayout()
        edit_row.addWidget(QLabel("X:"))
        self._edit_x = QLineEdit()
        self._edit_x.setFixedWidth(80)
        edit_row.addWidget(self._edit_x)
        edit_row.addWidget(QLabel("Y:"))
        self._edit_y = QLineEdit()
        self._edit_y.setFixedWidth(80)
        edit_row.addWidget(self._edit_y)
        edit_row.addWidget(QLabel("Wait:"))
        self._edit_wait = QLineEdit()
        self._edit_wait.setFixedWidth(70)
        edit_row.addWidget(self._edit_wait)
        self._btn_apply = QPushButton("Apply")
        self._btn_apply.clicked.connect(self._on_apply_edit)
        set_button_variant(self._btn_apply, "success")
        edit_row.addWidget(self._btn_apply)
        edit_row.addStretch(1)
        wp_layout.addLayout(edit_row)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("Add WP:"))
        self._add_type = QComboBox()
        self._add_type.addItems(["node", "stand"])
        add_row.addWidget(self._add_type)
        self._btn_add_wp = QPushButton("Add at Player Pos")
        self._btn_add_wp.clicked.connect(self._on_add_waypoint)
        set_button_variant(self._btn_add_wp, "primary")
        add_row.addWidget(self._btn_add_wp)
        add_row.addStretch(1)
        wp_layout.addLayout(add_row)

        root.addWidget(self._wp_card)

        self._actions_card = QFrame()
        self._actions_card.setObjectName("Card")
        actions_layout = QVBoxLayout(self._actions_card)
        actions_title = QLabel("Actions")
        actions_title.setObjectName("PageTitle")
        actions_layout.addWidget(actions_title)
        self._delete_path_btn = QPushButton("Delete Path")
        self._delete_path_btn.clicked.connect(self._on_delete_path)
        set_button_variant(self._delete_path_btn, "danger")
        actions_layout.addWidget(self._delete_path_btn)
        root.addWidget(self._actions_card)

    def _get_active_map(self) -> str:
        try:
            return self._engine._resolve_current_map() or ""
        except Exception:
            return ""

    def _map_poll_tick(self):
        name = self._get_active_map()
        self._on_map_poll_result(name)

    def _on_map_poll_result(self, name: str):
        if name:
            self._map_name_label.setText(name)
            if name != self._last_auto_map:
                self._last_auto_map = name
                self._load_waypoints_for_map(name)
        else:
            self._map_name_label.setText("Not detected")
        self._refresh_wall_status()

    def _on_set_record_mode(self):
        self._set_nav_mode("record")

    def _on_set_auto_mode(self):
        self._set_nav_mode("auto")

    def _set_nav_mode(self, mode: str):
        self._nav_mode = "auto" if mode == "auto" else "record"
        self._engine.config.set("nav_mode", "auto" if self._nav_mode == "auto" else "manual")
        self._update_mode_ui()

    def _refresh_mode_toggle_styles(self, in_record: bool):
        if in_record:
            self._mode_record_btn.setStyleSheet(
                "background:#238636; color:#F3FFF6; border:1px solid #2EA043; border-radius:8px; padding:6px 12px;"
            )
            self._mode_auto_btn.setStyleSheet(
                "background:#1A212C; color:#8B949E; border:1px solid #30363D; border-radius:8px; padding:6px 12px;"
            )
        else:
            self._mode_record_btn.setStyleSheet(
                "background:#1A212C; color:#8B949E; border:1px solid #30363D; border-radius:8px; padding:6px 12px;"
            )
            self._mode_auto_btn.setStyleSheet(
                "background:#6E40C9; color:#F4EEFF; border:1px solid #8B63D6; border-radius:8px; padding:6px 12px;"
            )

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
        self._auto_behavior_menu.setCurrentText(value_to_label.get(raw, "Rush Events"))

    def _update_mode_ui(self):
        in_record = self._nav_mode == "record"
        recorder = self._engine.path_recorder
        if recorder:
            recorder.set_logging_enabled(in_record)

        self._mode_record_btn.setChecked(in_record)
        self._mode_auto_btn.setChecked(not in_record)
        self._refresh_mode_toggle_styles(in_record)

        self._rec_card.setVisible(in_record)
        self._auto_card.setVisible(not in_record)
        if in_record:
            self._mode_info.setText("Walk to record waypoints")
        else:
            self._mode_info.setText("Bot navigates autonomously via A*")
        self._update_recording_sections_visibility()

    def _update_recording_sections_visibility(self):
        recorder = self._engine.path_recorder
        is_recording = bool(recorder and recorder.is_recording)
        show_legacy = (self._nav_mode == "record") or is_recording
        self._wp_card.setVisible(show_legacy)
        self._actions_card.setVisible(show_legacy)

    def _refresh_wall_status(self):
        map_name = self._get_active_map()
        if not map_name:
            self._wall_status_label.setText("-")
            return
        self._wall_status_label.setText(self._engine.get_wall_data_status(map_name))

    def _push_grid_overlay(self):
        if not callable(self._grid_overlay_cb):
            return
        try:
            from src.core.wall_scanner import WallScanner, WallPoint
            from src.utils.constants import WALL_GRID_CELL_SIZE, WALL_GRID_HALF_SIZE

            map_name = self._get_active_map()
            if not map_name:
                return

            gs = self._engine.game_state
            px = gs.read_chain("player_x") or 0.0
            py = gs.read_chain("player_y") or 0.0

            raw_points = WallScanner._load_json().get(map_name, [])
            points = [WallPoint.from_dict(p) for p in raw_points if isinstance(p, dict)]
            if not points:
                self._grid_overlay_cb([], [], WALL_GRID_CELL_SIZE)
                return

            ws = WallScanner.__new__(WallScanner)
            grid = ws.build_walkable_grid(points, px, py, half_size=WALL_GRID_HALF_SIZE, cell_size=WALL_GRID_CELL_SIZE)

            walkable_xy = []
            for row in range(grid.rows):
                for col in range(grid.cols):
                    if not grid.is_blocked(row, col):
                        walkable_xy.append(grid.grid_to_world(row, col))
            frontier_xy = grid.get_frontier_world_positions(max_samples=500)
            self._grid_overlay_cb(walkable_xy, frontier_xy, grid.cell_size)
        except Exception:
            pass

    def _refresh_coverage_overview(self):
        metrics = self._engine.get_all_coverage_metrics() or {}
        self._coverage_table.setRowCount(len(MAP_NAMES))
        for i, name in enumerate(MAP_NAMES):
            row = metrics.get(name, {}) if isinstance(metrics, dict) else {}
            count = int(row.get("covered", 0) or 0)
            if count < self._COV_MIN_RELIABLE_POINTS:
                text = "NONE   no data"
                fg_color = "#8B949E"
                bg_color = "#151B23"
            else:
                pct = float(row.get("pct", 0.0) or 0.0)
                if pct >= self._COV_GOOD_PCT:
                    label = "GOOD"
                elif pct >= 35.0:
                    label = "MID"
                else:
                    label = "LOW"
                text = f"{label:<6} {pct:5.1f}%   {count:,} pts"
                fg_color = self._coverage_percent_color(pct)
                bg_color = self._coverage_percent_bg_color(pct)

            name_item = QTableWidgetItem(name)
            cov_item = QTableWidgetItem(text)
            row_bg = QBrush(QColor(bg_color))
            name_item.setBackground(row_bg)
            cov_item.setBackground(row_bg)
            name_item.setForeground(QBrush(QColor("#E6EDF3")))
            cov_item.setForeground(QBrush(QColor(fg_color)))
            cov_item.setTextAlignment(Qt.AlignCenter)

            self._coverage_table.setItem(i, 0, name_item)
            self._coverage_table.setItem(i, 1, cov_item)

    def _coverage_percent_color(self, pct: float) -> str:
        pct = max(0.0, min(100.0, float(pct)))
        # 0..50: red -> yellow, 50..100: yellow -> green.
        if pct <= 50.0:
            t = pct / 50.0
            r = int(248 + (210 - 248) * t)
            g = int(81 + (153 - 81) * t)
            b = int(73 + (34 - 73) * t)
        else:
            t = (pct - 50.0) / 50.0
            r = int(210 + (63 - 210) * t)
            g = int(153 + (185 - 153) * t)
            b = int(34 + (80 - 34) * t)
        return f"#{r:02X}{g:02X}{b:02X}"

    def _coverage_percent_bg_color(self, pct: float) -> str:
        # Dark tint derived from the foreground color for readable row shading.
        fg = self._coverage_percent_color(pct)
        r = int(fg[1:3], 16)
        g = int(fg[3:5], 16)
        b = int(fg[5:7], 16)
        r = int(r * 0.17)
        g = int(g * 0.17)
        b = int(b * 0.17)
        return f"#{r:02X}{g:02X}{b:02X}"

    def _on_explore_start(self):
        def _switch_to_dashboard_for_activity():
            try:
                app = self.window()
                if app and hasattr(app, "_set_page"):
                    app._set_page("dashboard")
            except Exception:
                pass

        def _push_dashboard_explore_status(text: str, active: bool):
            def _apply():
                try:
                    app = self.window()
                    dashboard = getattr(app, "_pages", {}).get("dashboard") if app else None
                    if dashboard and hasattr(dashboard, "set_explorer_progress"):
                        dashboard.set_explorer_progress(text, active)
                except Exception:
                    pass

            QTimer.singleShot(0, _apply)

        def _progress(elapsed, total, targets, positions, cov_pct=0.0, covered=0, estimated=0, frontier=0, force=False):
            trend = "estimating"
            if estimated > 0:
                trend = f"{cov_pct:.1f}%"
            txt = (
                f"Running until complete | Coverage: {trend} ({covered}/{estimated}) | "
                f"Frontier: {frontier} | Targets: {targets} | Pos: {positions}"
            )
            QTimer.singleShot(0, lambda t=txt: self._explore_progress_label.setText(t))
            _push_dashboard_explore_status(txt, True)
            if force or not self._engine.explorer_running:
                QTimer.singleShot(0, self._on_explore_done)

        ok = self._engine.start_map_explorer(duration_s=None, progress_cb=_progress)
        if ok:
            _switch_to_dashboard_for_activity()
            self._explore_start_btn.setEnabled(False)
            self._explore_stop_btn.setEnabled(True)
            self._explore_progress_label.setText("Running until complete")
            _push_dashboard_explore_status("Running until complete", True)
            self._explore_poll_timer.start()
        elif self._engine.is_running:
            self._explore_progress_label.setText("Stop the bot first - Explorer can't run alongside bot loop")
        else:
            self._explore_progress_label.setText("Error - not attached or already running")

    def _on_explore_stop(self):
        self._engine.stop_map_explorer()
        self._on_explore_done()

    def _on_explore_done(self):
        self._explore_start_btn.setEnabled(True)
        self._explore_stop_btn.setEnabled(False)
        self._explore_poll_timer.stop()
        try:
            app = self.window()
            dashboard = getattr(app, "_pages", {}).get("dashboard") if app else None
            if dashboard and hasattr(dashboard, "set_explorer_progress"):
                dashboard.set_explorer_progress("", False)
        except Exception:
            pass
        self._refresh_wall_status()
        self._refresh_coverage_overview()

    def _explore_poll(self):
        if not self._engine.explorer_running:
            self._on_explore_done()
            return
        self._refresh_wall_status()

    def _on_manual_explore_start(self):
        map_name = self._get_active_map()
        if not map_name:
            self._manual_status_label.setText("Map not detected - attach first")
            return
        if self._manual_thread and self._manual_thread.is_alive():
            return

        stop_ev = threading.Event()
        self._manual_stop_event = stop_ev
        self._manual_thread = threading.Thread(
            target=self._manual_explore_thread_fn,
            args=(map_name, stop_ev),
            daemon=True,
            name="QtManualExplore",
        )
        self._manual_start_btn.setEnabled(False)
        self._manual_stop_btn.setEnabled(True)
        self._manual_status_label.setText("Running - walk the map")
        self._manual_thread.start()

    def _on_manual_explore_stop(self):
        if self._manual_stop_event:
            self._manual_stop_event.set()
        self._manual_start_btn.setEnabled(True)
        self._manual_stop_btn.setEnabled(False)
        self._manual_status_label.setText("Stopped")

    def _manual_explore_thread_fn(self, map_name: str, stop_ev: threading.Event):
        from src.core.map_explorer import MapExplorer
        from src.utils.constants import (
            MAP_EXPLORER_POSITION_FLUSH_EVERY,
            MAP_EXPLORER_POSITION_FLUSH_S,
            MAP_EXPLORER_POSITION_POLL_S,
            MAP_EXPLORER_POSITION_SAMPLE_DIST,
        )

        sample_dist_sq = MAP_EXPLORER_POSITION_SAMPLE_DIST ** 2
        sampler = MapExplorer.__new__(MapExplorer)
        sampler._map_name = map_name
        sampler._cancelled = False
        sampler._sampler_last_pos = None
        existing_keys = sampler._load_existing_keys()
        pending = []
        last_flush = time.time()
        last_pushed_key_count = -1

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

            if len(existing_keys) != last_pushed_key_count:
                self._manual_refresh_grid(map_name, x, y)
                last_pushed_key_count = len(existing_keys)

            time.sleep(MAP_EXPLORER_POSITION_POLL_S)

        if pending:
            sampler._flush_positions(pending)
        try:
            lx = self._engine.game_state.read_chain("player_x") or 0.0
            ly = self._engine.game_state.read_chain("player_y") or 0.0
        except Exception:
            lx, ly = 0.0, 0.0
        self._manual_refresh_grid(map_name, lx, ly)

        QTimer.singleShot(0, self._on_manual_explore_stopped_from_thread)

    def _on_manual_explore_stopped_from_thread(self):
        self._manual_start_btn.setEnabled(True)
        self._manual_stop_btn.setEnabled(False)
        self._manual_status_label.setText("Done - grid saved")
        self._refresh_wall_status()
        self._refresh_coverage_overview()

    def _manual_refresh_grid(self, map_name: str, px: float = 0.0, py: float = 0.0):
        if not callable(self._grid_overlay_cb):
            return
        try:
            from src.core.wall_scanner import WallPoint, WallScanner
            from src.utils.constants import WALL_GRID_CELL_SIZE, WALL_GRID_HALF_SIZE

            raw_points = WallScanner._load_json().get(map_name, [])
            points = [WallPoint.from_dict(p) for p in raw_points if isinstance(p, dict)]
            if not points:
                return

            ws = WallScanner.__new__(WallScanner)
            grid = ws.build_walkable_grid(
                points,
                px,
                py,
                half_size=WALL_GRID_HALF_SIZE,
                cell_size=WALL_GRID_CELL_SIZE,
            )
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
            return
        waypoints = recorder.load_path(map_name)
        self._loaded_waypoints = waypoints if waypoints else []
        self._selected_indices.clear()
        self._refresh_waypoint_list()

    def _refresh_waypoint_list(self):
        self._updating_table = True
        try:
            self._wp_table.setRowCount(len(self._loaded_waypoints))
            self._wp_count_label.setText(f"({len(self._loaded_waypoints)})")
            for i, wp in enumerate(self._loaded_waypoints):
                self._wp_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
                self._wp_table.setItem(i, 1, QTableWidgetItem(f"{wp.x:.1f}"))
                self._wp_table.setItem(i, 2, QTableWidgetItem(f"{wp.y:.1f}"))
                self._wp_table.setItem(i, 3, QTableWidgetItem(wp.wp_type))
                self._wp_table.setItem(i, 4, QTableWidgetItem("Yes" if wp.is_portal else "No"))
                self._wp_table.setItem(i, 5, QTableWidgetItem(f"{wp.wait_time:.1f}"))

            if len(self._selected_indices) == 1:
                idx = next(iter(self._selected_indices))
                if 0 <= idx < len(self._loaded_waypoints):
                    wp = self._loaded_waypoints[idx]
                    self._edit_x.setText(f"{wp.x:.1f}")
                    self._edit_y.setText(f"{wp.y:.1f}")
                    self._edit_wait.setText(f"{wp.wait_time:.1f}")
        finally:
            self._updating_table = False
        self._notify_overlay()

    def _on_table_selection_changed(self):
        if self._updating_table:
            return
        self._selected_indices = {idx.row() for idx in self._wp_table.selectionModel().selectedRows()}
        if len(self._selected_indices) == 1:
            idx = next(iter(self._selected_indices))
            wp = self._loaded_waypoints[idx]
            self._edit_x.setText(f"{wp.x:.1f}")
            self._edit_y.setText(f"{wp.y:.1f}")
            self._edit_wait.setText(f"{wp.wait_time:.1f}")

    def _on_delete_selected(self):
        if not self._selected_indices:
            return
        for idx in sorted(self._selected_indices, reverse=True):
            if 0 <= idx < len(self._loaded_waypoints):
                self._loaded_waypoints.pop(idx)
        self._selected_indices.clear()
        self._refresh_waypoint_list()

    def _on_delete_all(self):
        self._loaded_waypoints.clear()
        self._selected_indices.clear()
        self._refresh_waypoint_list()

    def _on_move_up(self):
        if len(self._selected_indices) != 1:
            return
        idx = next(iter(self._selected_indices))
        if idx <= 0 or idx >= len(self._loaded_waypoints):
            return
        self._loaded_waypoints[idx], self._loaded_waypoints[idx - 1] = self._loaded_waypoints[idx - 1], self._loaded_waypoints[idx]
        self._selected_indices = {idx - 1}
        self._refresh_waypoint_list()

    def _on_move_down(self):
        if len(self._selected_indices) != 1:
            return
        idx = next(iter(self._selected_indices))
        if idx < 0 or idx >= len(self._loaded_waypoints) - 1:
            return
        self._loaded_waypoints[idx], self._loaded_waypoints[idx + 1] = self._loaded_waypoints[idx + 1], self._loaded_waypoints[idx]
        self._selected_indices = {idx + 1}
        self._refresh_waypoint_list()

    def _on_set_node(self):
        for idx in self._selected_indices:
            if 0 <= idx < len(self._loaded_waypoints):
                self._loaded_waypoints[idx].wp_type = "node"
                self._loaded_waypoints[idx].wait_time = 0.0
        self._refresh_waypoint_list()

    def _on_set_stand(self):
        for idx in self._selected_indices:
            if 0 <= idx < len(self._loaded_waypoints):
                self._loaded_waypoints[idx].wp_type = "stand"
        self._refresh_waypoint_list()

    def _on_toggle_portal(self):
        for idx in self._selected_indices:
            if 0 <= idx < len(self._loaded_waypoints):
                self._loaded_waypoints[idx].is_portal = not self._loaded_waypoints[idx].is_portal
        self._refresh_waypoint_list()

    def _on_apply_edit(self):
        if len(self._selected_indices) != 1:
            return
        idx = next(iter(self._selected_indices))
        if idx < 0 or idx >= len(self._loaded_waypoints):
            return
        wp = self._loaded_waypoints[idx]
        try:
            x_val = self._edit_x.text().strip()
            y_val = self._edit_y.text().strip()
            w_val = self._edit_wait.text().strip()
            if x_val:
                wp.x = float(x_val)
            if y_val:
                wp.y = float(y_val)
            if w_val:
                wp.wait_time = float(w_val)
        except ValueError:
            pass
        self._refresh_waypoint_list()

    def _on_add_waypoint(self):
        gs = self._engine.game_state
        if not gs:
            return
        try:
            gs.update()
        except Exception:
            pass
        if not hasattr(gs, "player") or not gs.player:
            return
        pos = gs.player.position
        if not pos or (pos.x == 0 and pos.y == 0):
            return
        wp = Waypoint(x=pos.x, y=pos.y, wp_type=self._add_type.currentText())
        if self._selected_indices:
            insert_idx = max(self._selected_indices) + 1
            self._loaded_waypoints.insert(insert_idx, wp)
        else:
            self._loaded_waypoints.append(wp)
        self._refresh_waypoint_list()

    def _on_record(self):
        map_name = self._get_active_map()
        if not map_name:
            return
        recorder = self._engine.path_recorder
        if not recorder:
            return

        if recorder.is_recording:
            recorder.stop_recording()
            self._record_timer.stop()
            self._loaded_waypoints = list(recorder.waypoints)
            self._record_btn.setText("Start Recording (F5)")
            self._pause_btn.setText("Pause (F6)")
            self._pause_btn.setEnabled(False)
            self._rec_status.setText("Status: Stopped")
            self._rec_count.setText(f"Points: {recorder.waypoint_count}")
            self._is_paused = False
            self._refresh_waypoint_list()
            self._update_recording_sections_visibility()
        else:
            recorder.start_recording(map_name)
            self._record_btn.setText("Stop Recording (F5)")
            self._pause_btn.setEnabled(True)
            self._pause_btn.setText("Pause (F6)")
            self._rec_status.setText("Status: Recording...")
            self._is_paused = False
            interval = int(max(0.05, float(getattr(recorder, "_record_interval", 0.2))) * 1000)
            self._record_timer.start(interval)
            self._update_recording_sections_visibility()

    def _on_pause_record(self):
        recorder = self._engine.path_recorder
        if not recorder or not recorder.is_recording:
            return
        self._is_paused = not self._is_paused
        if self._is_paused:
            self._record_timer.stop()
            self._pause_btn.setText("Resume (F6)")
            self._rec_status.setText("Status: Paused")
        else:
            self._pause_btn.setText("Pause (F6)")
            self._rec_status.setText("Status: Recording...")
            interval = int(max(0.05, float(getattr(recorder, "_record_interval", 0.2))) * 1000)
            self._record_timer.start(interval)

    def _record_tick(self):
        recorder = self._engine.path_recorder
        if not recorder or not recorder.is_recording or self._is_paused:
            return
        recorder.record_tick()
        self._rec_count.setText(f"Points: {recorder.waypoint_count}")
        self._loaded_waypoints = list(recorder.waypoints)
        self._refresh_waypoint_list()

    def _on_mark_portal(self):
        recorder = self._engine.path_recorder
        if recorder and recorder.is_recording:
            recorder.add_portal_waypoint()
            self._rec_count.setText(f"Points: {recorder.waypoint_count}")
            self._loaded_waypoints = list(recorder.waypoints)
            self._refresh_waypoint_list()

    def _on_save(self):
        map_name = self._get_active_map()
        if not map_name:
            return
        recorder = self._engine.path_recorder
        if not recorder:
            return

        if recorder.is_recording:
            recorder.stop_recording()
            self._record_timer.stop()
            self._record_btn.setText("Start Recording (F5)")
            self._pause_btn.setEnabled(False)
            self._pause_btn.setText("Pause (F6)")
            self._rec_status.setText("Status: Stopped")
            self._is_paused = False
            self._update_recording_sections_visibility()

        if self._loaded_waypoints:
            recorder._waypoints = list(self._loaded_waypoints)

        if recorder.save_path(map_name):
            self._rec_status.setText("Status: Saved!")

    def _on_delete_path(self):
        map_name = self._get_active_map()
        if not map_name:
            return
        recorder = self._engine.path_recorder
        if recorder and recorder.delete_path(map_name):
            self._loaded_waypoints = []
            self._selected_indices.clear()
            self._refresh_waypoint_list()

    def closeEvent(self, event):  # noqa: N802
        try:
            self._map_timer.stop()
            self._record_timer.stop()
            self._explore_poll_timer.stop()
            if self._manual_stop_event:
                self._manual_stop_event.set()
        except Exception:
            pass
        super().closeEvent(event)
