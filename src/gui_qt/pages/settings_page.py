from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from src.gui_qt.theme import set_button_variant

from src.utils.constants import DEFAULT_SETTINGS


class SettingsPage(QFrame):
    def __init__(self, bridge, on_calibrate=None):
        super().__init__()
        self._bridge = bridge
        self._engine = bridge.engine
        self._config = self._engine.config
        self._on_calibrate = on_calibrate
        self._entries = {}
        self._switches = {}
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        top.addWidget(title)
        top.addStretch(1)

        self._save_btn = QPushButton("Save All")
        self._save_btn.clicked.connect(self._on_save)
        set_button_variant(self._save_btn, "success")
        top.addWidget(self._save_btn)

        self._reset_btn = QPushButton("Reset Defaults")
        self._reset_btn.clicked.connect(self._on_reset)
        set_button_variant(self._reset_btn, "warning")
        top.addWidget(self._reset_btn)
        root.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(8)

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
            ],
            "Hotkeys": [
                ("hotkey_start", "Start Hotkey", "Global hotkey to start bot"),
                ("hotkey_stop", "Stop Hotkey", "Global hotkey to stop bot"),
                ("hotkey_pause", "Pause Hotkey", "Global hotkey to pause/resume"),
            ],
        }

        for section_name, fields in sections.items():
            card = QFrame()
            card.setObjectName("Card")
            card_layout = QVBoxLayout(card)
            card_layout.setSpacing(6)

            h = QLabel(section_name)
            h.setObjectName("PageTitle")
            card_layout.addWidget(h)

            for key, label_text, tooltip in fields:
                row = QGridLayout()
                row.setColumnStretch(0, 1)
                label = QLabel(label_text)
                label.setObjectName("PageBody")
                tip = QLabel(tooltip)
                tip.setObjectName("PageBody")
                tip.setStyleSheet("color: #6E7681;")

                current = self._config.get(key, DEFAULT_SETTINGS.get(key, ""))
                default_val = DEFAULT_SETTINGS.get(key, "")
                if isinstance(default_val, bool):
                    control = QCheckBox()
                    control.setChecked(bool(current))
                    self._switches[key] = control
                else:
                    control = QLineEdit(str(current))
                    control.setFixedWidth(140)
                    self._entries[key] = control

                row.addWidget(label, 0, 0)
                row.addWidget(control, 0, 1, alignment=Qt.AlignRight)
                row.addWidget(tip, 1, 0, 1, 2)
                card_layout.addLayout(row)

            layout.addWidget(card)

        cal_card = QFrame()
        cal_card.setObjectName("Card")
        cal_layout = QVBoxLayout(cal_card)
        cal_layout.setSpacing(6)

        cal_title = QLabel("Calibration")
        cal_title.setObjectName("PageTitle")
        cal_layout.addWidget(cal_title)

        cal_hint = QLabel("Calibrates world axis and scale in current map")
        cal_hint.setObjectName("PageBody")
        cal_hint.setWordWrap(True)
        cal_layout.addWidget(cal_hint)

        self._calibrate_btn = QPushButton("Calibrate")
        self._calibrate_btn.clicked.connect(self._on_calibrate_clicked)
        set_button_variant(self._calibrate_btn, "warning")
        cal_layout.addWidget(self._calibrate_btn, alignment=Qt.AlignLeft)
        layout.addWidget(cal_card)

        danger_card = QFrame()
        danger_card.setObjectName("Card")
        danger_layout = QVBoxLayout(danger_card)
        danger_layout.setSpacing(6)

        danger_title = QLabel("Data Management")
        danger_title.setObjectName("PageTitle")
        danger_layout.addWidget(danger_title)

        danger_hint = QLabel(
            "Deletes persisted walkable map coverage for all maps. This cannot be undone."
        )
        danger_hint.setObjectName("PageBody")
        danger_hint.setWordWrap(True)
        danger_layout.addWidget(danger_hint)

        self._delete_cov_btn = QPushButton("Delete Map Coverage Data")
        self._delete_cov_btn.clicked.connect(self._on_delete_map_coverage_data)
        set_button_variant(self._delete_cov_btn, "danger")
        danger_layout.addWidget(self._delete_cov_btn, alignment=Qt.AlignLeft)
        layout.addWidget(danger_card)

        self._status = QLabel("")
        self._status.setObjectName("PageBody")
        layout.addWidget(self._status)
        layout.addStretch(1)

        scroll.setWidget(container)
        root.addWidget(scroll, 1)

    def _on_save(self):
        for key, widget in self._entries.items():
            val_str = widget.text().strip()
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
            self._config.set(key, bool(switch.isChecked()))

        self._status.setText("Settings saved successfully")
        self._status.setStyleSheet("color: #3FB950;")

    def _on_reset(self):
        self._config.reset()
        for key, widget in self._entries.items():
            widget.setText(str(DEFAULT_SETTINGS.get(key, "")))
        for key, switch in self._switches.items():
            switch.setChecked(bool(DEFAULT_SETTINGS.get(key, False)))
        self._status.setText("Settings reset to defaults")
        self._status.setStyleSheet("color: #D29922;")

    def _on_delete_map_coverage_data(self):
        first = QMessageBox.question(
            self,
            "Delete Map Coverage Data",
            "This will delete persisted map coverage data for all maps. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if first != QMessageBox.Yes:
            return

        second = QMessageBox.warning(
            self,
            "Final Confirmation",
            "Are you absolutely sure? This operation cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if second != QMessageBox.Yes:
            return

        removed = int(self._engine.delete_all_map_coverage_data() or 0)
        self._status.setText(f"Deleted map coverage data for {removed} maps")
        self._status.setStyleSheet("color: #D29922;")

    def _on_calibrate_clicked(self):
        proceed = QMessageBox.question(
            self,
            "Start Calibration",
            "Calibration will move and click your character in the current map.\n"
            "Use only when the area is safe. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if proceed != QMessageBox.Yes:
            return

        if callable(self._on_calibrate):
            started, msg = self._on_calibrate()
            color = "#3FB950" if started else "#D29922"
            self._status.setText(msg)
            self._status.setStyleSheet(f"color: {color};")
            return

        self._status.setText("Calibration callback unavailable")
        self._status.setStyleSheet("color: #D29922;")
