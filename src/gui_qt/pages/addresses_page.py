import struct
import threading

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)
from src.gui_qt.theme import set_button_variant


class AddressesPage(QFrame):
    def __init__(self, bridge, on_debug_ui_changed=None):
        super().__init__()
        self._bridge = bridge
        self._engine = bridge.engine
        self._on_debug_ui_changed = on_debug_ui_changed
        self._active_scanner = None
        self._attach_in_progress = False
        self._debug_ui_enabled = bool(self._engine.config.get("debug_ui_enabled", False))
        self._updating_debug_switch = False
        self._build_ui()

    def _post_ui(self, fn):
        """Run callback on the page's UI thread.

        Using a QObject context avoids worker-thread singleShot delivery issues
        where callbacks may never execute.
        """
        try:
            QTimer.singleShot(0, self, fn)
        except Exception:
            pass

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        top = QVBoxLayout()
        top.setSpacing(6)
        title_row = QHBoxLayout()
        title = QLabel("Address Setup")
        title.setObjectName("PageTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        top.addLayout(title_row)

        control_row = QHBoxLayout()
        control_row.setSpacing(6)

        self._attach_btn = QPushButton("Attach to Game")
        self._attach_btn.clicked.connect(self._on_attach)
        set_button_variant(self._attach_btn, "primary")
        self._attach_btn.setToolTip("Attach bot to game process")
        control_row.addWidget(self._attach_btn)

        self._attach_status = QLabel("")
        self._attach_status.setObjectName("PageBody")
        control_row.addWidget(self._attach_status)

        self._debug_switch = QCheckBox("Debug UI")
        self._debug_switch.setChecked(self._debug_ui_enabled)
        self._debug_switch.toggled.connect(self._on_debug_ui_toggled)
        control_row.addWidget(self._debug_switch)
        control_row.addStretch(1)
        top.addLayout(control_row)
        root.addLayout(top)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)

        header = QHBoxLayout()
        h = QLabel("Auto-Scanner")
        h.setObjectName("PageTitle")
        header.addWidget(h)
        header.addStretch(1)
        self._chain_status = QLabel("Chain: Not resolved")
        self._chain_status.setObjectName("PageBody")
        header.addWidget(self._chain_status)
        card_layout.addLayout(header)

        btn_row = QHBoxLayout()
        self._rescan_btn = QPushButton("Re-scan")
        self._rescan_btn.clicked.connect(self._on_rescan)
        set_button_variant(self._rescan_btn, "warning")
        btn_row.addWidget(self._rescan_btn)

        self._probe_btn = QPushButton("Probe Events")
        self._probe_btn.clicked.connect(self._on_probe_events)
        set_button_variant(self._probe_btn, "info")
        self._probe_btn.setVisible(self._debug_ui_enabled)
        btn_row.addWidget(self._probe_btn)
        btn_row.addStretch(1)
        card_layout.addLayout(btn_row)

        fp_row = QHBoxLayout()
        fp_row.setSpacing(8)
        fp_row.addWidget(QLabel("FNamePool:"))
        self._fnamepool_entry = QLineEdit()
        self._fnamepool_entry.setPlaceholderText("paste from dump tool if scan fails")
        self._fnamepool_entry.setMinimumWidth(240)
        self._fnamepool_entry.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")
        fp_row.addWidget(self._fnamepool_entry)

        self._set_fp_btn = QPushButton("Set")
        self._set_fp_btn.clicked.connect(self._on_set_fnamepool)
        set_button_variant(self._set_fp_btn, "primary")
        self._set_fp_btn.setFixedWidth(56)
        fp_row.addWidget(self._set_fp_btn)

        self._fnamepool_status = QLabel("")
        self._fnamepool_status.setObjectName("PageBody")
        self._fnamepool_status.setMinimumWidth(150)
        self._fnamepool_status.setAlignment(Qt.AlignCenter)
        fp_row.addWidget(self._fnamepool_status)
        fp_row.addStretch(1)
        card_layout.addLayout(fp_row)

        self._scan_log = QPlainTextEdit()
        self._scan_log.setReadOnly(True)
        self._scan_log.setMaximumBlockCount(800)
        card_layout.addWidget(self._scan_log, 1)
        root.addWidget(card, 1)

        self._global_status = QLabel("")
        self._global_status.setObjectName("PageBody")
        root.addWidget(self._global_status)

    def _append_scan_log(self, msg: str):
        self._scan_log.appendPlainText(msg)

    def _scan_progress(self, msg: str):
        self._post_ui(lambda m=msg: self._append_scan_log(m))

    def _set_body_color(self, label: QLabel, color: str):
        if label is self._fnamepool_status:
            label.setStyleSheet(
                f"color: {color};"
                "background: #10161D;"
                "border: 1px solid #30363D;"
                "border-radius: 8px;"
                "padding: 2px 8px;"
            )
            return
        label.setStyleSheet(f"color: {color};")

    def _on_debug_ui_toggled(self, enabled: bool):
        if self._updating_debug_switch:
            return
        enabled = bool(enabled)
        self._debug_ui_enabled = enabled
        self._probe_btn.setVisible(enabled)
        try:
            self._engine.config.set("debug_ui_enabled", enabled)
        except Exception:
            pass
        if callable(self._on_debug_ui_changed):
            try:
                self._on_debug_ui_changed(enabled)
            except Exception:
                pass

    def _on_attach(self):
        if self._attach_in_progress:
            return
        self._engine.last_scan_failed = False
        self._attach_in_progress = True
        self._attach_btn.setEnabled(False)
        self._attach_btn.setText("Attaching...")
        self._attach_status.setText("Connecting...")
        self._set_body_color(self._attach_status, "#D29922")

        def _run():
            try:
                success, message = self._engine.attach_to_game()
            except Exception as exc:
                success, message = False, f"Attach error: {exc}"
            self._post_ui(lambda: self._finish_attach(success, message))

        threading.Thread(target=_run, daemon=True).start()

    def _finish_attach(self, success: bool, message: str):
        self._attach_in_progress = False
        if success and getattr(self._engine, "last_scan_failed", False):
            success = False
            message = "Dump chain scan failed - address chain could not be resolved"

        if success:
            self._attach_btn.setText("Attached")
            self._attach_btn.setEnabled(False)
            self._attach_status.setText("Connected")
            self._set_body_color(self._attach_status, "#3FB950")

            try:
                if self._engine.window.is_found:
                    self._engine.input.set_target_window(self._engine.window.hwnd)
            except Exception:
                pass

            scanner = getattr(self._engine, "scanner", None)
            if scanner:
                self._append_scan_log("Attached - using scan results from attach")
                fnp = getattr(scanner, "fnamepool_addr", 0) or getattr(scanner, "_fnamepool_addr", 0)
                if fnp:
                    self._fnamepool_entry.setText(f"0x{fnp:X}")
                    self._fnamepool_status.setText(f"0x{fnp:X} OK")
                    self._set_body_color(self._fnamepool_status, "#3FB950")
                else:
                    def _on_deferred_done():
                        self._post_ui(self._update_fnamepool_from_scanner)

                    if not hasattr(self._engine, "_deferred_scan_callbacks"):
                        self._engine._deferred_scan_callbacks = []
                    self._engine._deferred_scan_callbacks.append(_on_deferred_done)
                self._chain_status.setText("Chain: OK")
                self._set_body_color(self._chain_status, "#3FB950")
                self._global_status.setText("Connected")
                self._set_body_color(self._global_status, "#3FB950")
            else:
                self._append_scan_log("Attached - running dump chain scan...")
                self._on_rescan()
        else:
            self._attach_btn.setText("Attach to Game")
            self._attach_btn.setEnabled(True)
            self._attach_status.setText("Failed")
            self._set_body_color(self._attach_status, "#F85149")
            self._append_scan_log(f"FAILED: {message}")

    def _update_fnamepool_from_scanner(self):
        scanner = getattr(self._engine, "scanner", None)
        if not scanner:
            return
        fnp = getattr(scanner, "fnamepool_addr", 0) or getattr(scanner, "_fnamepool_addr", 0)
        if fnp:
            self._fnamepool_entry.setText(f"0x{fnp:X}")
            self._fnamepool_status.setText(f"0x{fnp:X} OK")
            self._set_body_color(self._fnamepool_status, "#3FB950")
            self._append_scan_log(f"FNamePool auto-populated: 0x{fnp:X}")

    def _on_rescan(self):
        if not self._engine.memory.is_attached:
            self._append_scan_log("Not attached to game - click 'Attach to Game' first")
            return

        self._rescan_btn.setEnabled(False)
        self._rescan_btn.setText("Scanning...")
        self._scan_log.clear()

        from src.core.scanner import UE4Scanner

        scanner = UE4Scanner(self._engine.memory, self._engine.addresses, self._scan_progress)
        self._active_scanner = scanner

        def _run():
            result = scanner.scan_dump_chain(use_cache=False)
            self._post_ui(lambda: self._scan_finished(bool(result.success)))

        threading.Thread(target=_run, daemon=True).start()

    def _scan_finished(self, success: bool):
        self._rescan_btn.setEnabled(True)
        self._rescan_btn.setText("Re-scan")
        self._active_scanner = None

        if success:
            self._engine.last_scan_failed = False
            self._chain_status.setText("Chain: OK")
            self._set_body_color(self._chain_status, "#3FB950")
            self._global_status.setText("Scan complete")
            self._set_body_color(self._global_status, "#3FB950")
        else:
            self._chain_status.setText("Chain: Failed")
            self._set_body_color(self._chain_status, "#F85149")
            self._global_status.setText("Scan failed - see log above")
            self._set_body_color(self._global_status, "#F85149")
            self._engine.last_scan_failed = True
            self._attach_btn.setText("Attach to Game")
            self._attach_btn.setEnabled(True)
            self._attach_status.setText("Failed")
            self._set_body_color(self._attach_status, "#F85149")
            try:
                self._engine.memory.detach()
            except Exception:
                pass
            if hasattr(self._engine, "_scanner"):
                self._engine._scanner = None
            self._show_outdated_popup()

    def _show_outdated_popup(self):
        QMessageBox.warning(
            self,
            "Update Required",
            "This version of the bot is not compatible with the current game version.\n\n"
            "A new game update may have been released.\n"
            "Please wait for a bot update.",
        )

    def _on_probe_events(self):
        self._probe_btn.setEnabled(False)
        self._probe_btn.setText("Probing...")
        self._scan_log.clear()

        def _run():
            try:
                scanner = getattr(self._engine, "scanner", None)
                if scanner is None:
                    self._post_ui(lambda: self._append_scan_log("No scanner - attach first"))
                    return

                self._post_ui(lambda: self._append_scan_log("Running get_typed_events()..."))
                events = scanner.get_typed_events() or []
                carjack = [e for e in events if e.event_type == "Carjack"]
                sandlord = [e for e in events if e.event_type == "Sandlord"]
                unknown = [e for e in events if not e.is_target_event]

                self._post_ui(
                    lambda: self._append_scan_log(
                        f"Events: {len(events)} total - Carjack={len(carjack)} Sandlord={len(sandlord)} Unknown={len(unknown)}"
                    )
                )

                for ev in carjack:
                    self._post_ui(
                        lambda e=ev: self._append_scan_log(
                            f"Carjack veh=0x{e.carjack_vehicle_addr:X} pos=({e.position[0]:.0f},{e.position[1]:.0f}) CW={e.carjack_work_count}/{e.carjack_max_work_count}"
                        ),
                    )
                    veh = ev.carjack_vehicle_addr
                    fnp = getattr(scanner, "_fnamepool_addr", 0)
                    if veh and fnp:
                        roster = scanner._read_truck_guard_roster(veh, fnp)
                        if roster:
                            self._post_ui(lambda r=roster: self._append_scan_log(f"Guard roster: {len(r)} guard(s)"))
                            for g in roster:
                                self._post_ui(
                                    lambda gg=g: self._append_scan_log(
                                        f"  guard addr=0x{gg['addr']:X} pos=({gg['x']:.0f},{gg['y']:.0f}) abp={gg['abp']!r}"
                                    ),
                                )
                        else:
                            self._post_ui(
                                lambda: self._append_scan_log("Guard roster EMPTY - check log for [TRAP-PROBE] TArray count"),
                            )
                    elif not veh:
                        self._post_ui(lambda: self._append_scan_log("carjack_vehicle_addr=0 - truck not matched yet"))

                if not carjack:
                    self._post_ui(
                        lambda: self._append_scan_log("No Carjack event found - enter a Carjack map and try again"),
                    )
            except Exception as exc:
                self._post_ui(lambda e=exc: self._append_scan_log(f"Error: {e}"))
            finally:
                self._post_ui(lambda: (self._probe_btn.setEnabled(True), self._probe_btn.setText("Probe Events")))

        threading.Thread(target=_run, daemon=True).start()

    def _on_set_fnamepool(self):
        if not self._engine.memory.is_attached:
            self._append_scan_log("Not attached to game")
            return

        scanner = getattr(self._engine, "scanner", None)
        if not scanner:
            self._append_scan_log("Scanner not initialized - run Re-scan first")
            return

        addr_str = self._fnamepool_entry.text().strip()
        if not addr_str:
            self._append_scan_log("Enter FNamePool address from dump tool (hex format, e.g. 0x7FF7D2481D40)")
            return

        try:
            addr = int(addr_str, 16)
        except ValueError:
            self._append_scan_log(f"Invalid address: {addr_str}")
            return

        candidates = [addr, addr - 0x10, addr + 0x10]
        for candidate in candidates:
            for test_idx in (1, 2, 3, 5, 10):
                test_name = self._engine.memory.read_fname(candidate, test_idx)
                if test_name and len(test_name) > 1 and test_name.isprintable():
                    if hasattr(scanner, "set_fnamepool_addr"):
                        scanner.set_fnamepool_addr(candidate)
                    elif hasattr(scanner, "_scanner") and hasattr(scanner._scanner, "set_fnamepool_addr"):
                        scanner._scanner.set_fnamepool_addr(candidate)
                    else:
                        scanner._fnamepool_addr = candidate
                    if candidate != addr:
                        diff = candidate - addr
                        self._append_scan_log(
                            f"FNamePool set to 0x{candidate:X} (adjusted {diff:+d} from input, FName[{test_idx}] = '{test_name}')"
                        )
                    else:
                        self._append_scan_log(
                            f"FNamePool set to 0x{candidate:X} (validated: FName[{test_idx}] = '{test_name}')"
                        )
                    self._fnamepool_status.setText(f"0x{candidate:X} OK")
                    self._set_body_color(self._fnamepool_status, "#3FB950")
                    return

        self._append_scan_log(f"All candidates failed. Running diagnostics on 0x{addr:X}...")
        for offset in (0, -0x10, 0x10):
            base = addr + offset
            raw = self._engine.memory.read_bytes(base, 0x30)
            if raw:
                hex_str = " ".join(f"{b:02X}" for b in raw)
                self._append_scan_log(f"  0x{base:X} raw: {hex_str}")
                block0_ptr = self._engine.memory.read_value(base + 0x10, "ulong")
                if block0_ptr and 0x10000 < block0_ptr < 0x7FFFFFFFFFFF:
                    self._append_scan_log(f"  Block[0] at base+0x10: 0x{block0_ptr:X}")
                    entry0 = self._engine.memory.read_bytes(block0_ptr, 16)
                    if entry0:
                        hex_e = " ".join(f"{b:02X}" for b in entry0)
                        self._append_scan_log(f"  First 16 bytes at Block[0]: {hex_e}")
                        header = struct.unpack_from("<H", entry0, 0)[0]
                        is_wide = header & 1
                        name_len = (header >> 6) & 0x3FF
                        self._append_scan_log(f"  Entry[0] header: wide={is_wide}, len={name_len}")
                        if 0 < name_len < 100:
                            try:
                                name_str = entry0[2 : 2 + name_len].decode("utf-8", errors="replace")
                                self._append_scan_log(f"  Entry[0] name: '{name_str}'")
                            except Exception:
                                pass
                else:
                    self._append_scan_log("  No valid Block[0] ptr at base+0x10")

        self._append_scan_log("Validation FAILED - see diagnostics above")
        self._fnamepool_status.setText("FAILED")
        self._set_body_color(self._fnamepool_status, "#F85149")
