import customtkinter as ctk
from src.gui.theme import COLORS, FONTS, create_card_frame, create_label, create_accent_button, create_entry
from src.utils.logger import log


class AddressManagerTab(ctk.CTkFrame):
    def __init__(self, parent, bot_engine, on_debug_ui_changed=None):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._engine = bot_engine
        self._on_debug_ui_changed = on_debug_ui_changed
        self._active_scanner = None
        self._debug_ui_enabled = bool(self._engine.config.get("debug_ui_enabled", False))
        self._updating_debug_switch = False
        self._build_ui()

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(8, 4))
        create_label(top, "Address Setup", "heading").pack(anchor="w")

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=8, pady=(0, 4))

        self._attach_btn = create_accent_button(
            btn_frame, "Attach to Game", self._on_attach, color="accent_cyan", width=110
        )
        self._attach_btn.pack(side="left", padx=2)

        self._attach_status = create_label(btn_frame, "", "small", "text_muted")
        self._attach_status.pack(side="left", padx=(2, 4))

        self._debug_ui_switch = ctk.CTkSwitch(
            btn_frame,
            text="Debug UI",
            command=self._on_debug_ui_toggled,
            fg_color=COLORS["border"],
            progress_color=COLORS["accent_blue"],
            button_color=COLORS["text_primary"],
            text_color=COLORS["text_secondary"],
        )
        self._debug_ui_switch.pack(side="right", padx=2)
        if self._debug_ui_enabled:
            self._debug_ui_switch.select()
        else:
            self._debug_ui_switch.deselect()

        scanner_card = create_card_frame(self)
        scanner_card.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        scanner_header = ctk.CTkFrame(scanner_card, fg_color="transparent")
        scanner_header.pack(fill="x", padx=8, pady=(8, 4))

        create_label(scanner_header, "Auto-Scanner", "subheading").pack(side="left")
        self._chain_status = create_label(scanner_header, "Chain: Not resolved", "small", "text_muted")
        self._chain_status.pack(side="right", padx=8)

        scanner_btns = ctk.CTkFrame(scanner_card, fg_color="transparent")
        scanner_btns.pack(fill="x", padx=8, pady=(0, 4))

        self._rescan_btn = create_accent_button(
            scanner_btns, "Re-scan", self._on_rescan, color="accent_orange", width=100
        )
        self._rescan_btn.pack(side="left", padx=2, pady=2)

        self._probe_btn = create_accent_button(
            scanner_btns, "Probe Events", self._on_probe_events, color="accent_cyan", width=120
        )
        self._probe_btn.pack(side="left", padx=2, pady=2)
        if not self._debug_ui_enabled:
            self._probe_btn.pack_forget()

        fnamepool_frame = ctk.CTkFrame(scanner_card, fg_color="transparent")
        fnamepool_frame.pack(fill="x", padx=8, pady=(0, 4))

        create_label(fnamepool_frame, "FNamePool:", "small", "text_secondary").pack(side="left")
        self._fnamepool_entry = create_entry(
            fnamepool_frame,
            placeholder="paste from dump tool if scan fails",
            width=200,
        )
        self._fnamepool_entry.pack(side="left", padx=(4, 4))

        self._fnamepool_set_btn = create_accent_button(
            fnamepool_frame, "Set", self._on_set_fnamepool, color="accent_cyan", width=50
        )
        self._fnamepool_set_btn.pack(side="left", padx=2)

        self._fnamepool_status = create_label(fnamepool_frame, "", "small", "text_muted")
        self._fnamepool_status.pack(side="left", padx=4)

        self._scan_log = ctk.CTkTextbox(
            scanner_card,
            fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_secondary"],
            font=FONTS["mono_small"],
            corner_radius=6,
            height=180,
        )
        self._scan_log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._scan_log.configure(state="disabled")

        self._global_status = create_label(self, "", "small", "text_muted")
        self._global_status.pack(anchor="w", padx=10, pady=(0, 8))

    def _scan_progress(self, msg: str):
        try:
            self._scan_log.after(0, lambda: self._append_scan_log(msg))
        except Exception:
            pass

    def _append_scan_log(self, msg: str):
        log.info(f"[AddrMgr] {msg}")
        self._scan_log.configure(state="normal")
        self._scan_log.insert("end", msg + "\n")
        self._scan_log.see("end")
        self._scan_log.configure(state="disabled")

    def _on_debug_ui_toggled(self):
        if self._updating_debug_switch:
            return
        enabled = bool(self._debug_ui_switch.get())
        self.set_debug_ui_enabled(enabled)
        try:
            self._engine.config.set("debug_ui_enabled", enabled)
        except Exception:
            pass
        if callable(self._on_debug_ui_changed):
            try:
                self._on_debug_ui_changed(enabled)
            except Exception:
                pass

    def set_debug_ui_enabled(self, enabled: bool):
        enabled = bool(enabled)
        self._debug_ui_enabled = enabled
        if enabled:
            if not self._probe_btn.winfo_ismapped():
                self._probe_btn.pack(side="left", padx=2, pady=2)
        else:
            if self._probe_btn.winfo_ismapped():
                self._probe_btn.pack_forget()

        self._updating_debug_switch = True
        try:
            if enabled:
                self._debug_ui_switch.select()
            else:
                self._debug_ui_switch.deselect()
        finally:
            self._updating_debug_switch = False

    def _on_probe_events(self):
        """Run immediate event probe and dump roster diagnostics to the scan log."""
        import threading

        self._probe_btn.configure(state="disabled", text="Probing...")
        self._scan_log.configure(state="normal")
        self._scan_log.delete("1.0", "end")
        self._scan_log.configure(state="disabled")

        def _run():
            try:
                scanner = getattr(self._engine, "scanner", None)
                if scanner is None:
                    self._probe_btn.after(0, lambda: self._append_scan_log("No scanner - attach first"))
                    return

                self._probe_btn.after(0, lambda: self._append_scan_log("Running get_typed_events()..."))
                events = scanner.get_typed_events()
                carjack = [e for e in (events or []) if e.event_type == "Carjack"]
                sandlord = [e for e in (events or []) if e.event_type == "Sandlord"]
                unknown = [e for e in (events or []) if not e.is_target_event]

                self._probe_btn.after(
                    0,
                    lambda: self._append_scan_log(
                        f"Events: {len(events or [])} total - Carjack={len(carjack)} Sandlord={len(sandlord)} Unknown={len(unknown)}"
                    ),
                )

                for ev in carjack:
                    self._probe_btn.after(
                        0,
                        lambda e=ev: self._append_scan_log(
                            f"Carjack veh=0x{e.carjack_vehicle_addr:X} pos=({e.position[0]:.0f},{e.position[1]:.0f}) CW={e.carjack_work_count}/{e.carjack_max_work_count}"
                        ),
                    )
                    veh = ev.carjack_vehicle_addr
                    fnp = scanner._fnamepool_addr
                    if veh and fnp:
                        roster = scanner._read_truck_guard_roster(veh, fnp)
                        if roster:
                            self._probe_btn.after(0, lambda r=roster: self._append_scan_log(f"Guard roster: {len(r)} guard(s)"))
                            for g in roster:
                                self._probe_btn.after(
                                    0,
                                    lambda gg=g: self._append_scan_log(
                                        f"  guard addr=0x{gg['addr']:X} pos=({gg['x']:.0f},{gg['y']:.0f}) abp={gg['abp']!r}"
                                    ),
                                )
                        else:
                            self._probe_btn.after(
                                0,
                                lambda: self._append_scan_log("Guard roster EMPTY - check log for [TRAP-PROBE] TArray count"),
                            )
                    elif not veh:
                        self._probe_btn.after(0, lambda: self._append_scan_log("carjack_vehicle_addr=0 - truck not matched yet"))

                if not carjack:
                    self._probe_btn.after(
                        0,
                        lambda: self._append_scan_log("No Carjack event found - enter a Carjack map and try again"),
                    )
            except Exception as exc:
                self._probe_btn.after(0, lambda: self._append_scan_log(f"Error: {exc}"))
            finally:
                self._probe_btn.after(0, lambda: self._probe_btn.configure(state="normal", text="Probe Events"))

        threading.Thread(target=_run, daemon=True).start()

    def _on_rescan(self):
        if not self._engine.memory.is_attached:
            self._append_scan_log("Not attached to game - click 'Attach to Game' first")
            return

        self._rescan_btn.configure(state="disabled", text="Scanning...")
        self._scan_log.configure(state="normal")
        self._scan_log.delete("1.0", "end")
        self._scan_log.configure(state="disabled")

        from src.core.scanner import UE4Scanner

        scanner = UE4Scanner(self._engine.memory, self._engine.addresses, self._scan_progress)
        self._active_scanner = scanner

        import threading

        def _run():
            result = scanner.scan_dump_chain(use_cache=False)
            self._rescan_btn.after(0, lambda: self._scan_finished(result.success))

        threading.Thread(target=_run, daemon=True).start()

    def _scan_finished(self, success: bool):
        self._rescan_btn.configure(state="normal", text="Re-scan")
        self._active_scanner = None

        if success:
            self._engine.last_scan_failed = False
            self._chain_status.configure(text="Chain: OK", text_color=COLORS["accent_green"])
            self._global_status.configure(text="Scan complete", text_color=COLORS["accent_green"])
        else:
            self._chain_status.configure(text="Chain: Failed", text_color=COLORS["accent_red"])
            self._global_status.configure(text="Scan failed - see log above", text_color=COLORS["accent_red"])
            self._engine.last_scan_failed = True
            self._attach_btn.configure(text="Attach to Game", state="normal")
            self._attach_status.configure(text="Failed", text_color=COLORS["accent_red"])
            try:
                self._engine.memory.detach()
            except Exception:
                pass
            if hasattr(self._engine, "_scanner"):
                self._engine._scanner = None
            self._show_outdated_popup()

    def _show_outdated_popup(self):
        popup = ctk.CTkToplevel(self.winfo_toplevel())
        popup.title("Update Required")
        popup.geometry("400x220")
        popup.resizable(False, False)
        popup.grab_set()
        popup.focus_force()

        popup.configure(fg_color=COLORS["bg_dark"])

        header = ctk.CTkFrame(popup, fg_color=COLORS["bg_medium"], corner_radius=0)
        header.pack(fill="x")
        create_label(header, "Update Required", "subheading", "accent_orange").pack(padx=16, pady=12)

        body = ctk.CTkFrame(popup, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=16)

        msg = (
            "This version of the bot is not compatible with the\n"
            "current game version.\n\n"
            "A new game update may have been released.\n"
            "Please wait for a bot update."
        )
        create_label(body, msg, "body", "text_secondary").pack(anchor="center")

        ctk.CTkButton(
            popup,
            text="OK",
            width=100,
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["button_hover"],
            text_color=COLORS["text_primary"],
            font=FONTS["body"],
            command=popup.destroy,
        ).pack(pady=(0, 16))

    def _on_attach(self):
        self._engine.last_scan_failed = False
        self._attach_btn.configure(state="disabled", text="Attaching...")
        self._attach_status.configure(text="Connecting...", text_color=COLORS["accent_orange"])

        import threading

        def _do_attach():
            success, message = self._engine.attach_to_game()
            self._attach_btn.after(0, lambda: self._finish_attach(success, message))

        threading.Thread(target=_do_attach, daemon=True).start()

    def _finish_attach(self, success: bool, message: str):
        if success and getattr(self._engine, "last_scan_failed", False):
            success = False
            message = "Dump chain scan failed - address chain could not be resolved"

        if success:
            self._attach_btn.configure(text="Attached", state="disabled", fg_color=COLORS["accent_green"])
            self._attach_status.configure(text="Connected", text_color=COLORS["accent_green"])

            if self._engine.window.is_found:
                self._engine.input.set_target_window(self._engine.window.hwnd)

            if self._engine.scanner:
                self._append_scan_log("Attached - using scan results from attach")
                fnp = self._engine.scanner.fnamepool_addr
                if fnp:
                    self._fnamepool_entry.delete(0, "end")
                    self._fnamepool_entry.insert(0, f"0x{fnp:X}")
                    self._fnamepool_status.configure(text=f"0x{fnp:X} OK", text_color=COLORS["accent_green"])
                else:
                    def _on_deferred_done():
                        self._fnamepool_entry.after(0, self._update_fnamepool_from_scanner)

                    if not hasattr(self._engine, "_deferred_scan_callbacks"):
                        self._engine._deferred_scan_callbacks = []
                    self._engine._deferred_scan_callbacks.append(_on_deferred_done)

                self._chain_status.configure(text="Chain: OK", text_color=COLORS["accent_green"])
                self._global_status.configure(text="Connected", text_color=COLORS["accent_green"])
            else:
                self._append_scan_log("Attached - running dump chain scan...")
                self._on_rescan()
        else:
            self._attach_btn.configure(text="Attach to Game", state="normal")
            self._attach_status.configure(text="Failed", text_color=COLORS["accent_red"])
            self._append_scan_log(f"FAILED: {message}")

    def _update_fnamepool_from_scanner(self):
        if not self._engine.scanner:
            return
        fnp = self._engine.scanner.fnamepool_addr
        if fnp:
            self._fnamepool_entry.delete(0, "end")
            self._fnamepool_entry.insert(0, f"0x{fnp:X}")
            self._fnamepool_status.configure(text=f"0x{fnp:X} OK", text_color=COLORS["accent_green"])
            self._append_scan_log(f"FNamePool auto-populated: 0x{fnp:X}")

    def _on_set_fnamepool(self):
        if not self._engine.memory.is_attached:
            self._append_scan_log("Not attached to game")
            return

        scanner = self._engine.scanner
        if not scanner:
            self._append_scan_log("Scanner not initialized - run Re-scan first")
            return

        addr_str = self._fnamepool_entry.get().strip()
        if not addr_str:
            self._append_scan_log("Enter FNamePool address from dump tool (hex format, e.g. 0x7FF7D2481D40)")
            return

        try:
            addr = int(addr_str, 16) if addr_str.startswith(("0x", "0X")) else int(addr_str, 16)
        except ValueError:
            self._append_scan_log(f"Invalid address: {addr_str}")
            return

        candidates = [addr, addr - 0x10, addr + 0x10]
        for candidate in candidates:
            for test_idx in [1, 2, 3, 5, 10]:
                test_name = self._engine.memory.read_fname(candidate, test_idx)
                if test_name and len(test_name) > 1 and test_name.isprintable():
                    if hasattr(scanner, "set_fnamepool_addr"):
                        scanner.set_fnamepool_addr(candidate)
                    elif hasattr(scanner, "_scanner") and hasattr(scanner._scanner, "set_fnamepool_addr"):
                        scanner._scanner.set_fnamepool_addr(candidate)
                    else:
                        scanner._fnamepool_addr = candidate
                    if candidate != addr:
                        offset_diff = candidate - addr
                        self._append_scan_log(
                            f"FNamePool set to 0x{candidate:X} (adjusted {offset_diff:+d} from input, FName[{test_idx}] = '{test_name}')"
                        )
                    else:
                        self._append_scan_log(
                            f"FNamePool set to 0x{candidate:X} (validated: FName[{test_idx}] = '{test_name}')"
                        )
                    self._fnamepool_status.configure(text=f"0x{candidate:X} OK", text_color=COLORS["accent_green"])
                    return

        self._append_scan_log(f"All candidates failed. Running diagnostics on 0x{addr:X}...")
        for offset in [0, -0x10, 0x10]:
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
                        import struct

                        header = struct.unpack_from("<H", entry0, 0)[0]
                        is_wide = header & 1
                        name_len = (header >> 6) & 0x3FF
                        self._append_scan_log(f"  Entry[0] header: wide={is_wide}, len={name_len}")
                        if 0 < name_len < 100:
                            name_bytes = entry0[2:2 + name_len]
                            try:
                                name_str = name_bytes.decode("utf-8", errors="replace")
                                self._append_scan_log(f"  Entry[0] name: '{name_str}'")
                            except Exception:
                                pass
                else:
                    self._append_scan_log("  No valid Block[0] ptr at base+0x10")

        self._append_scan_log("Validation FAILED - see diagnostics above")
        self._fnamepool_status.configure(text="FAILED", text_color=COLORS["accent_red"])

    def destroy(self):
        super().destroy()
