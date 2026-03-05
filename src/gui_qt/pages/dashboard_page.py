import json
import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from src.utils.logger import log
from src.gui_qt.theme import set_button_variant


class DashboardPage(QFrame):
    ZONE_MAP_FILE = os.path.join("data", "zone_name_mapping.json")

    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge
        self._engine = bridge.engine
        self._zone_mapping_cache = {}
        self._zone_mapping_mtime = 0.0
        self._log_callback = None

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._update_stats)
        self._timer.start()

        self._setup_log_callback()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(6)
        title_row = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setObjectName("PageTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        top.addLayout(title_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)

        self._start_btn = QPushButton("Start Bot")
        self._start_btn.clicked.connect(self._on_start)
        set_button_variant(self._start_btn, "success")
        self._start_btn.setToolTip("Start bot")
        action_row.addWidget(self._start_btn)

        self._demo_btn = QPushButton("Demo")
        self._demo_btn.clicked.connect(self._on_demo)
        set_button_variant(self._demo_btn, "info")
        self._demo_btn.setToolTip("Start demo mode")
        action_row.addWidget(self._demo_btn)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._on_pause)
        self._pause_btn.setEnabled(False)
        set_button_variant(self._pause_btn, "warning")
        action_row.addWidget(self._pause_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)
        set_button_variant(self._stop_btn, "danger")
        action_row.addWidget(self._stop_btn)
        action_row.addStretch(1)
        top.addLayout(action_row)
        root.addLayout(top)

        status_card = QFrame()
        status_card.setObjectName("Card")
        status_layout = QVBoxLayout(status_card)

        row = QHBoxLayout()
        self._status_dot = QLabel("●")
        row.addWidget(self._status_dot)
        self._status_label = QLabel("IDLE")
        self._status_label.setObjectName("PageTitle")
        row.addWidget(self._status_label)
        row.addStretch(1)
        self._attached_label = QLabel("Not Attached")
        self._attached_label.setObjectName("PageBody")
        row.addWidget(self._attached_label)
        status_layout.addLayout(row)

        stats_grid = QGridLayout()
        self._maps_completed = QLabel("0")
        self._runtime = QLabel("0:00:00")
        stats_grid.addWidget(QLabel("Maps Completed"), 0, 0)
        stats_grid.addWidget(QLabel("Runtime"), 0, 1)
        stats_grid.addWidget(self._maps_completed, 1, 0)
        stats_grid.addWidget(self._runtime, 1, 1)
        status_layout.addLayout(stats_grid)
        root.addWidget(status_card)

        player_card = QFrame()
        player_card.setObjectName("Card")
        player_layout = QVBoxLayout(player_card)
        player_title = QLabel("Player State")
        player_title.setObjectName("PageTitle")
        player_layout.addWidget(player_title)

        grid = QGridLayout()
        self._player_labels = {}
        fields = [
            ("position", "Position", 0, 0),
            ("health", "Health", 0, 1),
            ("map_info", "Zone", 0, 2),
            ("native_info", "Native", 1, 0),
        ]
        for key, label, r, c in fields:
            name = QLabel(label)
            name.setObjectName("PageBody")
            val = QLabel("---")
            val.setObjectName("PageBody")
            grid.addWidget(name, r * 2, c)
            grid.addWidget(val, r * 2 + 1, c)
            self._player_labels[key] = val
        player_layout.addLayout(grid)
        root.addWidget(player_card)

        self._explore_card = QFrame()
        self._explore_card.setObjectName("Card")
        explore_layout = QVBoxLayout(self._explore_card)
        explore_title = QLabel("Explorer Coverage")
        explore_title.setObjectName("PageTitle")
        explore_layout.addWidget(explore_title)
        self._explore_progress_label = QLabel("Idle")
        self._explore_progress_label.setObjectName("PageBody")
        self._explore_progress_label.setWordWrap(True)
        explore_layout.addWidget(self._explore_progress_label)
        self._explore_card.hide()
        root.addWidget(self._explore_card)

        log_card = QFrame()
        log_card.setObjectName("Card")
        log_layout = QVBoxLayout(log_card)
        log_top = QHBoxLayout()
        log_title = QLabel("Activity Log")
        log_title.setObjectName("PageTitle")
        log_top.addWidget(log_title)
        self._log_path = QLabel(os.path.basename(log.log_filepath))
        self._log_path.setObjectName("PageBody")
        log_top.addWidget(self._log_path)
        log_top.addStretch(1)
        self._save_log_btn = QPushButton("Save Log")
        self._save_log_btn.clicked.connect(self._on_save_log)
        set_button_variant(self._save_log_btn, "info")
        log_top.addWidget(self._save_log_btn)
        log_layout.addLayout(log_top)

        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        self._log_default_max_h = 16777215
        self._log_compact_max_h = 140
        log_layout.addWidget(self._log_text)
        root.addWidget(log_card, 1)

    def set_log_compact(self, compact: bool):
        self._log_text.setMaximumHeight(self._log_compact_max_h if compact else self._log_default_max_h)

    def set_explorer_progress(self, text: str, active: bool):
        if active:
            self._explore_progress_label.setText(text or "Running...")
            self._explore_card.show()
            self.set_log_compact(True)
            return
        self._explore_card.hide()
        self.set_log_compact(False)

    def _setup_log_callback(self):
        def _on_log(level, message):
            QTimer.singleShot(0, lambda l=level, m=message: self.add_log(l, m))

        self._log_callback = _on_log
        log.add_callback(self._log_callback)

    def _translate_zone_name(self, internal_name: str) -> str:
        try:
            if os.path.exists(self.ZONE_MAP_FILE):
                mtime = os.path.getmtime(self.ZONE_MAP_FILE)
                if mtime != self._zone_mapping_mtime:
                    with open(self.ZONE_MAP_FILE, "r", encoding="utf-8") as f:
                        self._zone_mapping_cache = json.load(f)
                    self._zone_mapping_mtime = mtime
                english = self._zone_mapping_cache.get(internal_name, "")
                if english:
                    return english
        except Exception:
            pass
        return internal_name

    def _on_start(self):
        if self._engine.start():
            self._start_btn.setEnabled(False)
            self._demo_btn.setEnabled(False)
            self._pause_btn.setEnabled(True)
            self._stop_btn.setEnabled(True)

    def _on_demo(self):
        if self._engine.start_demo():
            self._start_btn.setEnabled(False)
            self._demo_btn.setEnabled(False)
            self._pause_btn.setEnabled(True)
            self._stop_btn.setEnabled(True)

    def _on_pause(self):
        self._engine.pause()
        self._pause_btn.setText("Resume" if self._engine.is_paused else "Pause")

    def _on_stop(self):
        self._engine.stop()
        self.set_explorer_progress("", active=False)
        self._start_btn.setEnabled(True)
        self._demo_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("Pause")
        self._stop_btn.setEnabled(False)

    def _on_save_log(self):
        log.flush()
        fp = log.log_filepath
        try:
            size = os.path.getsize(fp)
            size_str = f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"
            self._save_log_btn.setText(f"Saved ({size_str})")
            self.add_log("INFO", f"Log saved: {fp} ({size_str})")
            QTimer.singleShot(3000, lambda: self._save_log_btn.setText("Save Log"))
        except Exception as e:
            self.add_log("ERROR", f"Save log failed: {e}")

    def add_log(self, level: str, message: str):
        self._log_text.appendPlainText(f"[{level}] {message}")
        doc = self._log_text.document()
        while doc.blockCount() > 500:
            cursor = self._log_text.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.select(cursor.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def _update_stats(self):
        try:
            stats = self._engine.stats
            state = stats.get("state", "IDLE")
            color = {
                "IDLE": "#6E7681",
                "PAUSED": "#D29922",
                "ERROR": "#F85149",
                "STOPPING": "#F85149",
            }.get(state, "#3FB950")
            self._status_dot.setStyleSheet(f"color: {color};")
            self._status_label.setText(state)

            attached = bool(stats.get("attached", False))
            self._attached_label.setText("Attached" if attached else "Not Attached")
            self._attached_label.setStyleSheet("color: #3FB950;" if attached else "color: #6E7681;")

            self._maps_completed.setText(str(stats.get("maps_completed", 0)))
            runtime = int(stats.get("runtime", 0) or 0)
            mins, secs = divmod(runtime, 60)
            hours, mins = divmod(mins, 60)
            self._runtime.setText(f"{hours}:{mins:02d}:{secs:02d}")

            gs = self._engine.game_state
            if self._engine.memory.is_attached:
                gs.update()
            if self._engine.memory.is_attached and hasattr(self._engine, "_scanner") and self._engine._scanner:
                scanner = self._engine._scanner
                if hasattr(scanner, "_fnamepool_addr") and scanner._fnamepool_addr:
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
                self._player_labels["map_info"].setText(map_text)

            native_text = stats.get("native_status_label", "python")
            native_error = stats.get("native_error", "")
            if native_error:
                native_text = f"{native_text} | {native_error}"
            self._player_labels["native_info"].setText(native_text)

            if gs.is_valid:
                p = gs.player
                self._player_labels["position"].setText(f"({p.position.x:.1f}, {p.position.y:.1f})")

                hp_text = "---"
                scanner = getattr(self._engine, "_scanner", None)
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
                self._player_labels["health"].setText(hp_text)

                if not m.zone_name:
                    if m.is_in_hideout:
                        map_text = "Hideout"
                    elif m.is_in_map:
                        map_text = f"Map (ID: {m.map_id})"
                    else:
                        map_text = "Unknown"
                    self._player_labels["map_info"].setText(map_text)

        except Exception as e:
            self.add_log("WARNING", f"Dashboard stats update failed: {e}")

        if not self._engine.explorer_running and self._explore_card.isVisible():
            self.set_explorer_progress("", active=False)

    def closeEvent(self, event):  # noqa: N802
        try:
            self._timer.stop()
        except Exception:
            pass
        if self._log_callback:
            try:
                log.remove_callback(self._log_callback)
            except Exception:
                pass
        super().closeEvent(event)
