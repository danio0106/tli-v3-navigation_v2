import customtkinter as ctk
from src.gui.theme import COLORS, FONTS, create_card_frame, create_label, create_accent_button, create_entry
from src.utils.logger import log


PRESET_SLOTS = [
    ("player_x", "Player X", "float", "Player X world coordinate"),
    ("player_y", "Player Y", "float", "Player Y world coordinate"),
    ("player_z", "Player Z (optional)", "float", "Player Z coordinate (height)"),
    ("player_health", "Player Health", "int", "Current HP value"),
    ("player_max_health", "Player Max Health", "int", "Maximum HP value"),
    ("map_id", "Zone ID (optional)", "int", "Current zone/map identifier"),
    ("is_hideout", "Is Hideout (optional)", "byte", "1 when in hideout, 0 otherwise"),
    ("is_in_map", "Is In Map (optional)", "byte", "1 when inside a map instance"),
]

TYPE_OPTIONS = ["float", "int", "byte", "short", "double", "uint", "ulong"]


class AddressManagerTab(ctk.CTkFrame):
    def __init__(self, parent, bot_engine):
        super().__init__(parent, fg_color=COLORS["bg_dark"])
        self._engine = bot_engine
        self._slots = {}
        self._update_id = None
        self._active_scanner = None
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

        create_accent_button(
            btn_frame, "Save All", self._on_save_all, color="accent_green", width=80
        ).pack(side="left", padx=2)

        create_accent_button(
            btn_frame, "Reset Defaults", self._on_reset, color="accent_orange", width=100
        ).pack(side="left", padx=2)

        hint = create_label(
            self,
            "Paste memory addresses found by the scanner. Use Test to verify each reads a valid value.",
            "small", "text_muted"
        )
        hint.pack(anchor="w", padx=8, pady=(0, 4))

        scanner_card = create_card_frame(self)
        scanner_card.pack(fill="x", padx=8, pady=(0, 8))

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

        fnamepool_frame = ctk.CTkFrame(scanner_card, fg_color="transparent")
        fnamepool_frame.pack(fill="x", padx=8, pady=(0, 4))

        create_label(fnamepool_frame, "FNamePool:", "small", "text_secondary").pack(side="left")
        self._fnamepool_entry = create_entry(fnamepool_frame, placeholder="paste from dump tool if scan fails", width=200)
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
            height=100,
        )
        self._scan_log.pack(fill="x", padx=8, pady=(0, 8))
        self._scan_log.configure(state="disabled")

        calib_card = create_card_frame(self)
        calib_card.pack(fill="x", padx=8, pady=(0, 8))

        calib_header = ctk.CTkFrame(calib_card, fg_color="transparent")
        calib_header.pack(fill="x", padx=8, pady=(8, 4))

        create_label(calib_header, "Card Detection", "subheading").pack(side="left")

        self._calib_status = create_label(calib_header, "Not calibrated", "small", "text_muted")
        self._calib_status.pack(side="right", padx=8)

        calib_btns = ctk.CTkFrame(calib_card, fg_color="transparent")
        calib_btns.pack(fill="x", padx=8, pady=(0, 4))

        self._calib_btn = create_accent_button(
            calib_btns, "Calibrate Hexagons", self._on_calibrate, color="accent_purple", width=140
        )
        self._calib_btn.pack(side="left", padx=2, pady=2)

        self._detect_btn = create_accent_button(
            calib_btns, "Detect Cards", self._on_detect_cards, color="accent_cyan", width=110
        )
        self._detect_btn.pack(side="left", padx=2, pady=2)

        self._probe_card_btn = create_accent_button(
            calib_btns, "Probe Card Memory", self._on_probe_card_memory, color="accent_green", width=150
        )
        self._probe_card_btn.pack(side="left", padx=2, pady=2)

        self._calib_log = ctk.CTkTextbox(
            calib_card,
            fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_secondary"],
            font=FONTS["mono_small"],
            corner_radius=6,
            height=120,
        )
        self._calib_log.pack(fill="x", padx=8, pady=(0, 8))
        self._calib_log.configure(state="disabled")

        scroll = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_dark"])
        scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        for addr_name, display_name, default_type, description in PRESET_SLOTS:
            card = create_card_frame(scroll)
            card.pack(fill="x", pady=4)

            header = ctk.CTkFrame(card, fg_color="transparent")
            header.pack(fill="x", padx=8, pady=(8, 2))

            create_label(header, display_name, "subheading").pack(side="left")

            status_lbl = create_label(header, "", "small", "text_muted")
            status_lbl.pack(side="right")

            create_label(card, description, "small", "text_muted").pack(
                anchor="w", padx=8, pady=(0, 4)
            )

            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=(0, 4))

            create_label(row, "Module:", "small", "text_secondary").pack(side="left")
            module_entry = create_entry(row, placeholder="torchlight_infinite.exe", width=100)
            module_entry.pack(side="left", padx=(4, 8))

            create_label(row, "Base Offset:", "small", "text_secondary").pack(side="left")
            offset_entry = create_entry(row, placeholder="0x0", width=80)
            offset_entry.pack(side="left", padx=(4, 0))

            row2 = ctk.CTkFrame(card, fg_color="transparent")
            row2.pack(fill="x", padx=8, pady=(0, 4))

            create_label(row2, "Type:", "small", "text_secondary").pack(side="left")
            type_dropdown = ctk.CTkOptionMenu(
                row2,
                values=TYPE_OPTIONS,
                width=70,
                fg_color=COLORS["entry_bg"],
                button_color=COLORS["bg_light"],
                button_hover_color=COLORS["button_hover"],
                text_color=COLORS["text_primary"],
                font=FONTS["mono_small"],
            )
            type_dropdown.pack(side="left", padx=(4, 8))

            create_label(row2, "Pointer Offsets:", "small", "text_secondary").pack(side="left")
            offsets_entry = create_entry(row2, placeholder="comma-sep hex, e.g. 0x30, 0x120, 0x1A0")
            offsets_entry.pack(side="left", fill="x", expand=True, padx=(4, 8))

            test_btn = create_accent_button(
                row2, "Test", lambda n=addr_name: self._on_test(n), width=60
            )
            test_btn.pack(side="right")

            live_lbl = create_label(card, "", "mono_small", "accent_cyan")
            live_lbl.pack(anchor="w", padx=8, pady=(0, 8))

            saved = self._engine.addresses.get_address(addr_name)
            if saved:
                module_entry.insert(0, saved.get("base_module", "torchlight_infinite.exe"))
                offset = saved.get("base_offset", 0)
                if offset:
                    offset_entry.insert(0, f"0x{offset:X}")
                offsets_list = saved.get("offsets", [])
                if offsets_list:
                    offsets_entry.insert(0, ", ".join(f"0x{o:X}" for o in offsets_list))
                type_dropdown.set(saved.get("value_type", default_type))
                if saved.get("verified"):
                    status_lbl.configure(text="Verified", text_color=COLORS["accent_green"])
            else:
                module_entry.insert(0, "torchlight_infinite.exe")
                type_dropdown.set(default_type)

            self._slots[addr_name] = {
                "module": module_entry,
                "offset": offset_entry,
                "offsets": offsets_entry,
                "type": type_dropdown,
                "status": status_lbl,
                "live": live_lbl,
            }

        self._global_status = create_label(scroll, "", "small", "text_muted")
        self._global_status.pack(pady=8)

        self._start_live_update()

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

    def _on_probe_events(self):
        """Call get_typed_events() immediately and display TRAP-PROBE results.

        Fires the truck guard-roster probe (EMapCustomTrapS11Component TArray at
        +0x128) and work-count candidate read, then shows a summary in the scan
        log.  Use while standing in a Carjack map to validate the TArray offset
        hypothesis without needing the bot chase loop to be active.
        """
        import threading

        self._probe_btn.configure(state="disabled", text="Probing...")
        self._scan_log.configure(state="normal")
        self._scan_log.delete("1.0", "end")
        self._scan_log.configure(state="disabled")

        def _run():
            try:
                scanner = getattr(self._engine, "scanner", None)
                if scanner is None:
                    self._probe_btn.after(0, lambda: self._append_scan_log("No scanner — attach first"))
                    return
                self._probe_btn.after(0, lambda: self._append_scan_log("Running get_typed_events()..."))
                events = scanner.get_typed_events()
                carjack = [e for e in (events or []) if e.event_type == "Carjack"]
                sandlord = [e for e in (events or []) if e.event_type == "Sandlord"]
                unknown  = [e for e in (events or []) if not e.is_target_event]
                self._probe_btn.after(0, lambda: self._append_scan_log(
                    f"Events: {len(events or [])} total — "
                    f"Carjack={len(carjack)} Sandlord={len(sandlord)} Unknown={len(unknown)}"
                ))
                for ev in carjack:
                    self._probe_btn.after(0, lambda e=ev: self._append_scan_log(
                        f"Carjack veh=0x{e.carjack_vehicle_addr:X} "
                        f"pos=({e.position[0]:.0f},{e.position[1]:.0f}) "
                        f"CW={e.carjack_work_count}/{e.carjack_max_work_count}"
                    ))
                    # Fire roster probe explicitly so results appear in scan log
                    veh = ev.carjack_vehicle_addr
                    fnp = scanner._fnamepool_addr
                    if veh and fnp:
                        roster = scanner._read_truck_guard_roster(veh, fnp)
                        if roster:
                            self._probe_btn.after(0, lambda r=roster: self._append_scan_log(
                                f"Guard roster: {len(r)} guard(s)"
                            ))
                            for g in roster:
                                self._probe_btn.after(0, lambda gg=g: self._append_scan_log(
                                    f"  guard addr=0x{gg['addr']:X} "
                                    f"pos=({gg['x']:.0f},{gg['y']:.0f}) "
                                    f"abp={gg['abp']!r}"
                                ))
                        else:
                            self._probe_btn.after(0, lambda: self._append_scan_log(
                                "Guard roster EMPTY — check log for [TRAP-PROBE] TArray count"
                            ))
                    elif not veh:
                        self._probe_btn.after(0, lambda: self._append_scan_log(
                            "carjack_vehicle_addr=0 — truck not matched yet"
                        ))
                if not carjack:
                    self._probe_btn.after(0, lambda: self._append_scan_log(
                        "No Carjack event found — enter a Carjack map and try again"
                    ))
            except Exception as exc:
                self._probe_btn.after(0, lambda: self._append_scan_log(f"Error: {exc}"))
            finally:
                self._probe_btn.after(0, lambda: self._probe_btn.configure(
                    state="normal", text="Probe Events"
                ))

        threading.Thread(target=_run, daemon=True).start()

    def _append_calib_log(self, msg: str):
        log.debug(f"[Calib] {msg}")
        self._calib_log.configure(state="normal")
        self._calib_log.insert("end", msg + "\n")
        self._calib_log.see("end")
        self._calib_log.configure(state="disabled")

    def _on_calibrate(self):
        self._calib_btn.configure(state="disabled", text="Calibrating...")
        self._calib_log.configure(state="normal")
        self._calib_log.delete("1.0", "end")
        self._calib_log.configure(state="disabled")

        import threading

        def _run():
            hex_calibrator = self._engine.hex_calibrator
            if hex_calibrator is None:
                self._calib_btn.after(0, lambda: self._append_calib_log("No calibrator available"))
                self._calib_btn.after(0, lambda: self._calib_btn.configure(state="normal", text="Calibrate Hexagons"))
                return

            result = hex_calibrator.calibrate(debug=True)

            if result is not None:
                for idx in sorted(result["hexagons"].keys()):
                    h = result["hexagons"][idx]
                    self._calib_btn.after(0, lambda i=idx, hx=h: self._append_calib_log(
                        f"Hex {i}: center={hx['center']}  glow={hx['glow_region']}"
                    ))
                self._calib_btn.after(0, lambda: self._append_calib_log(f"Source: {result['source']}"))
                if result["debug_image_path"]:
                    self._calib_btn.after(0, lambda: self._append_calib_log(f"Debug image: {result['debug_image_path']}"))
                self._calib_btn.after(0, lambda: self._calib_status.configure(
                    text="Calibrated", text_color=COLORS["accent_green"]
                ))
            else:
                self._calib_btn.after(0, lambda: self._append_calib_log("Calibration failed"))
                self._calib_btn.after(0, lambda: self._calib_status.configure(
                    text="Failed", text_color=COLORS["accent_red"]
                ))

            self._calib_btn.after(0, lambda: self._calib_btn.configure(state="normal", text="Calibrate Hexagons"))

        threading.Thread(target=_run, daemon=True).start()

    def _on_detect_cards(self):
        self._detect_btn.configure(state="disabled", text="Detecting...")

        import threading

        def _run():
            map_selector = self._engine.map_selector
            if map_selector._card_detector is None:
                self._detect_btn.after(0, lambda: self._append_calib_log("No card detector"))
                self._detect_btn.after(0, lambda: self._detect_btn.configure(state="normal", text="Detect Cards"))
                return

            active, unknown = map_selector._card_detector.detect_active_cards(debug=True)

            last_result = map_selector._card_detector.get_last_result()
            if active is not None:
                self._detect_btn.after(0, lambda a=active: self._append_calib_log(f"Active cards: {a}"))
                if last_result:
                    details = last_result.get("details", {})
                    rarities = last_result.get("rarities", {})
                    for idx in range(12):
                        d = details.get(idx, {})
                        state = d.get("state", "?")
                        vg = d.get("vertex_grays", {})
                        i_gray = d.get("inactive_top_gray", 0)
                        if isinstance(i_gray, dict):
                            i_gray = i_gray.get("mean", 0)
                        top_g = vg.get("top", 0) if isinstance(vg, dict) else 0
                        ice_n = sum(1 for g in vg.values() if g >= 150) if isinstance(vg, dict) else 0
                        card_n = sum(1 for g in vg.values() if g < 130) if isinstance(vg, dict) else 0
                        r_info = rarities.get(idx, {})
                        r_name = r_info.get("rarity", "")
                        b_r = r_info.get("b_minus_r", 0)
                        gs = d.get("glow_sat", 0)
                        self._detect_btn.after(0, lambda i=idx, s=state, ic=ice_n, cn=card_n, ig=i_gray, g=gs, r=r_name, br=b_r: self._append_calib_log(
                            f"Hex {i}: {s} gs={g:.0f} ice={ic}/6 card={cn}/6 it={ig:.0f} {r} B-R={br:.0f}" if r else f"Hex {i}: {s} gs={g:.0f} ice={ic}/6 card={cn}/6 it={ig:.0f}"
                        ))
                    self._detect_btn.after(0, lambda a=active: self._append_calib_log(
                        f"Active count: {len(a)}"
                    ))
            else:
                self._detect_btn.after(0, lambda: self._append_calib_log("Detection failed"))

            self._detect_btn.after(0, lambda: self._detect_btn.configure(state="normal", text="Detect Cards"))

        threading.Thread(target=_run, daemon=True).start()

    def _on_probe_card_memory(self):
        """Run CardMemoryScanner deep probe and display results in calib log."""
        self._probe_card_btn.configure(state="disabled", text="Probing...")
        self._calib_log.configure(state="normal")
        self._calib_log.delete("1.0", "end")
        self._calib_log.configure(state="disabled")

        import threading

        def _vn(vis):
            return {0: "V", 1: "C", 2: "H", 3: "HTI", 4: "SHTI"}.get(vis, f"?{vis}")

        def _run():
            try:
                scanner = self._engine.scanner
                if not scanner:
                    self._probe_card_btn.after(0, lambda: self._append_calib_log(
                        "Scanner not available - attach to game first"))
                    return

                fnamepool = getattr(scanner, '_fnamepool_addr', 0)
                gobjects = getattr(scanner, '_gobjects_addr', 0)
                if not fnamepool or not gobjects:
                    self._probe_card_btn.after(0, lambda: self._append_calib_log(
                        "FNamePool/GObjects not resolved - run Re-scan first"))
                    return

                from src.core.card_memory_scanner import CardMemoryScanner
                cms = CardMemoryScanner(self._engine.memory, scanner)
                result = cms.deep_probe()

                # Display summary in the GUI textbox
                self._probe_card_btn.after(0, lambda: self._append_calib_log(
                    f"=== Card Memory Probe ==="))
                self._probe_card_btn.after(0, lambda: self._append_calib_log(
                    f"UI open: {result.ui_open} | Widgets: {result.widget_count} | "
                    f"{result.elapsed_ms:.0f}ms"))
                self._probe_card_btn.after(0, lambda: self._append_calib_log(
                    f"Mystery_C: {result.mystery_root_exists} | "
                    f"MysteryArea_C: {result.mystery_area_exists}"))

                for i, w in enumerate(result.widgets):
                    vis = {0: "V", 1: "C", 2: "H", 3: "HTI", 4: "SHTI"}.get(
                        w.visibility, f"?{w.visibility}")

                    # Find key card-item probes
                    def find_card(name):
                        return next((p for p in w.card_item_probes
                                     if p.name == name), None)
                    def find_map(name):
                        return next((p for p in w.map_item_probes
                                     if p.name == name), None)

                    es = find_card("EffectSwitcher")
                    frame = find_card("FrameImg")
                    buf = find_card("BuffIcon")
                    eb = find_card("EmptyBg")
                    hl = find_map("Highlight")
                    bts = find_map("BossTalentPointSwitcher")

                    parts = [f"vis={vis}"]
                    if hl and hl.ptr:
                        parts.append(f"HL={_vn(hl.visibility)}")
                    if bts and bts.ptr:
                        parts.append(f"BTSw={bts.switcher_index}")
                    if es and es.ptr:
                        parts.append(f"EfxIdx={es.switcher_index}")
                    if frame and frame.ptr:
                        parts.append(f"FrSty={frame.style_id}")
                        if frame.brush_resource_fname:
                            parts.append(f"FrTex={frame.brush_resource_fname}")
                    if buf and buf.ptr:
                        parts.append(f"BufSty={buf.style_id}")
                        if buf.brush_resource_fname:
                            parts.append(f"BufTex={buf.brush_resource_fname}")
                    if eb and eb.ptr:
                        parts.append(f"EmBg={_vn(eb.visibility)}")

                    summary = " | ".join(parts)
                    idx = i
                    text = f"[{idx:2d}] {w.instance_name}: {summary}"
                    self._probe_card_btn.after(0, lambda t=text: self._append_calib_log(t))

                self._probe_card_btn.after(0, lambda: self._append_calib_log(
                    f"Full details logged + saved to data/card_probe_*.json"))

            except Exception as exc:
                import traceback
                tb = traceback.format_exc()
                self._probe_card_btn.after(0, lambda e=str(exc): self._append_calib_log(
                    f"ERROR: {e}"))
                self._probe_card_btn.after(0, lambda t=tb: self._append_calib_log(t))
            finally:
                self._probe_card_btn.after(0, lambda: self._probe_card_btn.configure(
                    state="normal", text="Probe Card Memory"))

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
        scanner = UE4Scanner(
            self._engine.memory, self._engine.addresses, self._scan_progress
        )
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
            self._refresh_slots_from_saved()
            self._chain_status.configure(text="Chain: OK", text_color=COLORS["accent_green"])
            self._global_status.configure(
                text="Scan complete - addresses auto-filled", text_color=COLORS["accent_green"]
            )
        else:
            self._chain_status.configure(text="Chain: Failed", text_color=COLORS["accent_red"])
            self._global_status.configure(
                text="Scan failed - see log above", text_color=COLORS["accent_red"]
            )
            self._engine.last_scan_failed = True
            self._show_outdated_popup()

    def _show_outdated_popup(self):
        import customtkinter as ctk

        popup = ctk.CTkToplevel(self.winfo_toplevel())
        popup.title("Update Required")
        popup.geometry("400x220")
        popup.resizable(False, False)
        popup.grab_set()
        popup.focus_force()

        popup.configure(fg_color=COLORS["bg_dark"])

        header = ctk.CTkFrame(popup, fg_color=COLORS["bg_medium"], corner_radius=0)
        header.pack(fill="x")
        create_label(
            header,
            "⚠  Update Required",
            "subheading",
            "accent_orange",
        ).pack(padx=16, pady=12)

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

    def _refresh_slots_from_saved(self):
        for addr_name, display_name, default_type, description in PRESET_SLOTS:
            saved = self._engine.addresses.get_address(addr_name)
            if saved and (saved.get("base_offset", 0) != 0 or saved.get("offsets")):
                slot = self._slots[addr_name]
                slot["module"].delete(0, "end")
                slot["module"].insert(0, saved.get("base_module", "torchlight_infinite.exe"))
                slot["offset"].delete(0, "end")
                offset = saved.get("base_offset", 0)
                if offset:
                    slot["offset"].insert(0, f"0x{offset:X}")
                slot["offsets"].delete(0, "end")
                offsets_list = saved.get("offsets", [])
                if offsets_list:
                    slot["offsets"].insert(0, ", ".join(f"0x{o:X}" for o in offsets_list))
                slot["type"].set(saved.get("value_type", default_type))
                if saved.get("verified"):
                    slot["status"].configure(text="Verified", text_color=COLORS["accent_green"])
                else:
                    slot["status"].configure(text="Unverified", text_color=COLORS["accent_orange"])

    def _on_attach(self):
        self._attach_btn.configure(state="disabled", text="Attaching...")
        self._attach_status.configure(text="Connecting...", text_color=COLORS["accent_orange"])
        import threading
        def _do_attach():
            success, message = self._engine.attach_to_game()
            self._attach_btn.after(0, lambda: self._finish_attach(success, message))
        threading.Thread(target=_do_attach, daemon=True).start()

    def _finish_attach(self, success: bool, message: str):
        if success:
            self._attach_btn.configure(text="Attached", state="disabled",
                                       fg_color=COLORS["accent_green"])
            self._attach_status.configure(
                text="Connected", text_color=COLORS["accent_green"]
            )
            if self._engine.window.is_found:
                self._engine.input.set_target_window(self._engine.window.hwnd)
            if self._engine.scanner:
                self._append_scan_log("Attached - using scan results from attach")
                fnp = self._engine.scanner.fnamepool_addr
                if fnp:
                    self._fnamepool_entry.delete(0, "end")
                    self._fnamepool_entry.insert(0, f"0x{fnp:X}")
                    self._fnamepool_status.configure(
                        text=f"0x{fnp:X} OK", text_color=COLORS["accent_green"])
                else:
                    def _on_deferred_done():
                        self._fnamepool_entry.after(0, self._update_fnamepool_from_scanner)
                    if not hasattr(self._engine, '_deferred_scan_callbacks'):
                        self._engine._deferred_scan_callbacks = []
                    self._engine._deferred_scan_callbacks.append(_on_deferred_done)
                self._refresh_slots_from_saved()
                self._chain_status.configure(text="Chain: OK", text_color=COLORS["accent_green"])
                self._global_status.configure(
                    text="Scan complete - addresses auto-filled", text_color=COLORS["accent_green"]
                )
            else:
                self._append_scan_log("Attached - running dump chain scan...")
                self._on_rescan()
        else:
            self._attach_btn.configure(text="Attach to Game", state="normal")
            self._attach_status.configure(
                text="Failed", text_color=COLORS["accent_red"]
            )
            self._append_scan_log(f"FAILED: {message}")

    def _update_fnamepool_from_scanner(self):
        if not self._engine.scanner:
            return
        fnp = self._engine.scanner.fnamepool_addr
        if fnp:
            self._fnamepool_entry.delete(0, "end")
            self._fnamepool_entry.insert(0, f"0x{fnp:X}")
            self._fnamepool_status.configure(
                text=f"0x{fnp:X} OK", text_color=COLORS["accent_green"])
            self._append_scan_log(f"FNamePool auto-populated: 0x{fnp:X}")

    def _parse_slot(self, addr_name: str) -> dict:
        slot = self._slots[addr_name]
        module = slot["module"].get().strip() or "torchlight_infinite.exe"

        offset_str = slot["offset"].get().strip()
        try:
            if offset_str.startswith("0x") or offset_str.startswith("0X"):
                base_offset = int(offset_str, 16)
            elif offset_str:
                base_offset = int(offset_str)
            else:
                base_offset = 0
        except ValueError:
            base_offset = 0

        offsets_str = slot["offsets"].get().strip()
        offsets = []
        if offsets_str:
            for part in offsets_str.split(","):
                part = part.strip()
                try:
                    if part.startswith("0x") or part.startswith("0X"):
                        offsets.append(int(part, 16))
                    elif part:
                        offsets.append(int(part))
                except ValueError:
                    pass

        value_type = slot["type"].get()

        return {
            "base_module": module,
            "base_offset": base_offset,
            "offsets": offsets,
            "value_type": value_type,
        }

    def _on_save_all(self):
        for addr_name, display_name, default_type, description in PRESET_SLOTS:
            parsed = self._parse_slot(addr_name)
            self._engine.addresses.set_address(
                name=addr_name,
                base_module=parsed["base_module"],
                base_offset=parsed["base_offset"],
                offsets=parsed["offsets"],
                value_type=parsed["value_type"],
                description=description,
            )

        self._global_status.configure(
            text="All addresses saved", text_color=COLORS["accent_green"]
        )

    def _on_test(self, addr_name: str):
        slot = self._slots[addr_name]

        if not self._engine.memory.is_attached:
            slot["status"].configure(text="Not attached", text_color=COLORS["accent_red"])
            slot["live"].configure(text="Attach to game process first")
            return

        parsed = self._parse_slot(addr_name)
        if parsed["base_offset"] == 0 and not parsed["offsets"]:
            slot["status"].configure(text="No address set", text_color=COLORS["accent_orange"])
            slot["live"].configure(text="Enter a base offset or pointer chain")
            return

        from src.core.memory_reader import PointerChain
        chain = PointerChain(
            base_module=parsed["base_module"],
            base_offset=parsed["base_offset"],
            offsets=parsed["offsets"],
            value_type=parsed["value_type"],
        )

        value = self._engine.memory.read_pointer_chain(chain)
        if value is not None:
            slot["live"].configure(text=f"Value: {value}")
            slot["status"].configure(text="Readable", text_color=COLORS["accent_green"])
        else:
            slot["live"].configure(text="Failed to read - check address")
            slot["status"].configure(text="Unreadable", text_color=COLORS["accent_red"])

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
                    scanner._fnamepool_addr = candidate
                    if candidate != addr:
                        offset_diff = candidate - addr
                        self._append_scan_log(
                            f"FNamePool set to 0x{candidate:X} (adjusted {offset_diff:+d} from input, "
                            f"FName[{test_idx}] = '{test_name}')")
                    else:
                        self._append_scan_log(
                            f"FNamePool set to 0x{candidate:X} (validated: FName[{test_idx}] = '{test_name}')")
                    self._fnamepool_status.configure(
                        text=f"0x{candidate:X} OK", text_color=COLORS["accent_green"])
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
                        if name_len > 0 and name_len < 100:
                            name_bytes = entry0[2:2+name_len]
                            try:
                                name_str = name_bytes.decode("utf-8", errors="replace")
                                self._append_scan_log(f"  Entry[0] name: '{name_str}'")
                            except:
                                pass
                else:
                    self._append_scan_log(f"  No valid Block[0] ptr at base+0x10")

        self._append_scan_log("Validation FAILED - see diagnostics above")
        self._fnamepool_status.configure(text="FAILED", text_color=COLORS["accent_red"])

    def _on_reset(self):
        self._engine.addresses.reset_to_defaults()

        for addr_name, display_name, default_type, description in PRESET_SLOTS:
            slot = self._slots[addr_name]
            slot["module"].delete(0, "end")
            slot["module"].insert(0, "torchlight_infinite.exe")
            slot["offset"].delete(0, "end")
            slot["offsets"].delete(0, "end")
            slot["type"].set(default_type)
            slot["status"].configure(text="", text_color=COLORS["text_muted"])
            slot["live"].configure(text="")

        self._global_status.configure(
            text="Reset to defaults", text_color=COLORS["accent_orange"]
        )

    def _start_live_update(self):
        self._update_live_values()

    def _update_live_values(self):
        if self._engine.memory.is_attached:
            chain_x = self._engine.addresses.get_chain("player_x")
            if chain_x:
                val = self._engine.memory.read_pointer_chain(chain_x)
                if val is not None:
                    self._chain_status.configure(text="Chain: OK", text_color=COLORS["accent_green"])
                else:
                    self._chain_status.configure(text="Chain: Broken", text_color=COLORS["accent_red"])

            for addr_name in self._slots:
                chain = self._engine.addresses.get_chain(addr_name)
                if chain:
                    value = self._engine.memory.read_pointer_chain(chain)
                    if value is not None:
                        self._slots[addr_name]["live"].configure(text=f"Live: {value}")

        self._update_id = self.after(500, self._update_live_values)

    def destroy(self):
        if self._update_id:
            self.after_cancel(self._update_id)
        super().destroy()
