"""Card Memory Scanner — diagnostic module for reading Netherrealm card UI widgets from memory.

This module provides memory-based probing of the Mystery (秘境) card selection UI.
It is a DIAGNOSTIC / SUPPLEMENTARY tool — the primary card detection system remains
the image-recognition-based CardDetector.

SDK analysis (Feb–Mar 2026) revealed:
  - Game's card system uses Blueprint widgets under /Game/BluePrint/UI/Mystery/
  - UIMysticMapItem_C  (size 0x368): per-hexagon card widget, 14 live instances when UI open
  - MysteryCardItem_C  (size 0x368): card-face sub-widget embedded in each map item
  - Mystery_C          (size 0x5C0): root card selection UI widget
  - MysteryArea_C      (size 0x3F8): area / region selection widget
  - Widgets ONLY exist in GObjects when the card selection UI is open (0 when closed)
  - Widget state is managed by Lua via LuaMgr bridge — not from SDK-known C++ offsets
  - No C++ game-logic classes exist for Mystery — entire system is Blueprint + Lua.
  - EConfigItemType::E_destiny_card = internal type name for these cards.

Card identification approach (v4.82.0 — DEEP PROBE):
  We probe EVERY SDK-visible field on each widget to build a fingerprint database.
  The probe reads:

  UIMysticMapItem_C (outer hexagon card widget):
    - Visibility, RenderOpacity on the widget itself
    - All 30+ sub-widget pointers: BossBg, BossIcon, BossLine, BossLineBg, BossNameBg,
      BossProgressWatcherIcon, BossTalentPointSwitcher, ClickButton, CoreTalentPointIcon,
      GoldFrameBg, Highlight, hole2/hole2_2/hole6, Image_731, MysteryCardView,
      NormalMapNameBg, NormalTalentPointIcon, UIImage/UIImage_1/UIImage_2/UIImage_71/
      UIImage_74/UIImage_280/UIImage_644, etc.
    - For each UIImage sub-widget: Visibility, CurrStyleId, Brush.ResourceName FName,
      Brush.ResourceObject FName

  MysteryCardItem_C (inner card-face sub-widget, via MysteryCardView ptr):
    - BuffIcon, BuffIconBg, CardIconMask, ClickBtn, EffectSwitcher, EmptyBg, EmptyIcon,
      FrameImg, ProgressBgImage, ProgressBgImage_2, TalentPointIcon, UIImage/UIImage_1/
      UIImage_2/UIImage_41, UIMysteryProgressItem
    - EffectSwitcher ActiveWidgetIndex (rarity VFX tier: 4 slots, 0-3)
    - FrameImg CurrStyleId + Brush texture name
    - BuffIcon CurrStyleId + Brush texture name
    - CardIconMask -> UnderlyingIconTexture -> FName (card identity)
    - EmptyBg/EmptyIcon visibility (filled vs empty slot detection)
    - Raw bytes dump of Blueprint region (0x278-0x368) for both widgets

  All readings are DIAGNOSTIC-ONLY until validated against CV results.
"""

import struct
import time
import json
import os
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass, field

from src.core.memory_reader import MemoryReader
from src.utils.logger import log
from src.utils.constants import (
    UE4_UOBJECT_CLASS_OFFSET,
    UE4_UOBJECT_FNAME_OFFSET,
    UE4_UOBJECT_OUTER_OFFSET,
    UWIDGET_VISIBILITY_OFFSET,
    UWIDGET_RENDER_OPACITY,
    MYSTERY_MAP_ITEM_CLASS,
    MYSTERY_ROOT_CLASS,
    MYSTERY_AREA_CLASS,
    # UIMysticMapItem_C offsets
    MYSTERY_MAP_ITEM_HIGHLIGHT,
    MYSTERY_MAP_ITEM_GOLD_FRAME,
    MYSTERY_MAP_ITEM_CLICK_BUTTON,
    MYSTERY_MAP_ITEM_CARD_VIEW,
    MYSTERY_MAP_ITEM_BOSS_BG,
    MYSTERY_MAP_ITEM_BOSS_ICON,
    MYSTERY_MAP_ITEM_BOSS_TALENT_SWITCHER,
    # MysteryCardItem_C offsets
    MYSTERY_CARD_BUFF_ICON,
    MYSTERY_CARD_BUFF_ICON_BG,
    MYSTERY_CARD_ICON_MASK,
    MYSTERY_CARD_EFFECT_SWITCHER,
    MYSTERY_CARD_EMPTY_BG,
    MYSTERY_CARD_EMPTY_ICON,
    MYSTERY_CARD_FRAME_IMG,
    MYSTERY_CARD_PROGRESS_ITEM,
    # WidgetSwitcher
    WIDGET_SWITCHER_ACTIVE_INDEX,
    # UIImage
    UIMAGE_BRUSH_OFFSET,
    UIMAGE_CURR_STYLE_ID,
    # FSlateBrush sub-offsets
    BRUSH_RESOURCE_OBJECT,
    BRUSH_RESOURCE_NAME,
    # UIMaskedIcon
    MASKED_ICON_UNDERLYING_ICON,
)


# ---------------------------------------------------------------------------
# Complete sub-widget maps (from SDK dump per UE4 Blueprint class)
# Each entry: (offset, name, widget_type)
# widget_type controls what fields we probe on the resolved pointer.
# ---------------------------------------------------------------------------

MAP_ITEM_SUBWIDGETS = [
    (0x278, "BossBg",                       "UIImage"),
    (0x280, "BossBg2",                      "UIImage"),
    (0x288, "BossIcon",                     "UIImage"),
    (0x290, "BossLine",                     "UIImage"),
    (0x298, "BossLineBg",                   "UIImage"),
    (0x2A0, "BossNameBg",                   "UIImage"),
    (0x2A8, "BossProgressWatcherIcon",      "UIImage"),
    (0x2B0, "BossTalentPointSwitcher",      "WidgetSwitcher"),
    (0x2B8, "ClickButton",                  "UIButton"),
    (0x2C0, "CoreTalentPointIcon",          "UIImage"),
    (0x2C8, "GoldFrameBg",                  "UIImage"),
    (0x2D0, "Highlight",                    "UIImage"),
    (0x2D8, "hole2",                        "UIImage"),
    (0x2E0, "hole2_2",                      "UIImage"),
    (0x2E8, "hole6",                        "UIImage"),
    (0x2F0, "Image_731",                    "UIImage"),
    (0x2F8, "Ind",                          "UIGamepadIcon"),
    (0x300, "MysteryCardView",              "MysteryCardItem"),
    (0x308, "NormalMapNameBg",              "UIImage"),
    (0x310, "NormalTalentPointIcon",        "UIImage"),
    (0x318, "UIImage",                      "UIImage"),
    (0x320, "UIImage_1",                    "UIImage"),
    (0x328, "UIImage_2",                    "UIImage"),
    (0x330, "UIImage_71",                   "UIImage"),
    (0x338, "UIImage_74",                   "UIImage"),
    (0x340, "UIImage_280",                  "UIImage"),
    (0x348, "UIImage_644",                  "UIImage"),
    (0x350, "UIParticleEmitter_hole04",     "UIParticleEmitter"),
    (0x358, "UIParticleEmitter_hole04_1",   "UIParticleEmitter"),
    (0x360, "UIParticleEmitter_hole04_2",   "UIParticleEmitter"),
    (0x368, "UIParticleEmitter_hole04_3",   "UIParticleEmitter"),
]

CARD_ITEM_SUBWIDGETS = [
    (0x278, "FlyToBuffAnim",        "WidgetAnimation"),
    (0x280, "BuffIcon",             "UIImage"),
    (0x288, "BuffIconBg",           "UIImage"),
    (0x290, "CardIconMask",         "UIMaskedIcon"),
    (0x298, "ClickBtn",             "UIButton"),
    (0x2A0, "EffectSwitcher",       "WidgetSwitcher"),
    (0x2A8, "EmptyBg",             "UIImage"),
    (0x2B0, "EmptyIcon",           "UIImage"),
    (0x2B8, "FrameImg",            "UIImage"),
    (0x2C0, "LTIcon",              "UIGamepadIcon"),
    (0x2C8, "ProgressBgImage",     "UIImage"),
    (0x2D0, "ProgressBgImage_2",   "UIImage"),
    (0x2D8, "RTIcon",              "UIGamepadIcon"),
    (0x2E0, "TalentPointIcon",     "UIImage"),
    (0x2E8, "UIImage",             "UIImage"),
    (0x2F0, "UIImage_1",           "UIImage"),
    (0x2F8, "UIImage_2",           "UIImage"),
    (0x300, "UIImage_41",          "UIImage"),
    (0x308, "UIMysteryProgressItem", "Other"),
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SubWidgetProbe:
    """Data read from a single resolved sub-widget pointer."""
    name: str = ""
    widget_type: str = ""
    offset: int = 0
    ptr: int = 0                        # resolved pointer (0 = null)
    visibility: int = -1                # ESlateVisibility (0-4), -1 = not read
    render_opacity: float = -1.0
    # UIImage specifics
    style_id: int = -1                  # UIImage::CurrStyleId, -1 = N/A
    brush_texture_name: str = ""        # FSlateBrush.ResourceName FName
    brush_resource_ptr: int = 0         # FSlateBrush.ResourceObject (Texture2D*)
    brush_resource_fname: str = ""      # FName of ResourceObject (texture identity)
    # WidgetSwitcher specifics
    switcher_index: int = -1            # WidgetSwitcher::ActiveWidgetIndex, -1 = N/A
    # UIMaskedIcon specifics
    icon_texture_ptr: int = 0
    icon_texture_name: str = ""


@dataclass
class CardWidgetInfo:
    """All diagnostic data read from one UIMysticMapItem_C + its MysteryCardItem_C."""
    address: int = 0
    instance_name: str = ""
    # UWidget base
    visibility: int = -1
    render_opacity: float = -1.0
    # All UIMysticMapItem_C sub-widget probes
    map_item_probes: List[SubWidgetProbe] = field(default_factory=list)
    # MysteryCardItem_C sub-widget probes (via MysteryCardView pointer)
    card_view_ptr: int = 0
    card_item_probes: List[SubWidgetProbe] = field(default_factory=list)
    # Raw bytes from UIMysticMapItem_C Blueprint region (0x278-0x370)
    raw_map_item_bytes: bytes = b""
    # Raw bytes from MysteryCardItem_C Blueprint region (0x278-0x310)
    raw_card_item_bytes: bytes = b""


@dataclass
class CardProbeResult:
    """Result of a full card UI deep probe."""
    timestamp: float = 0.0
    ui_open: bool = False
    widget_count: int = 0
    widgets: List[CardWidgetInfo] = field(default_factory=list)
    mystery_root_exists: bool = False
    mystery_area_exists: bool = False
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# Main scanner class
# ---------------------------------------------------------------------------

class CardMemoryScanner:
    """Deep diagnostic scanner for Mystery card selection UI widgets.

    Probes every SDK-visible field on each card widget to build a
    comprehensive fingerprint database for card identification.
    """

    def __init__(self, memory: MemoryReader, scanner):
        self._memory = memory
        self._scanner = scanner

    # -- Logging helpers ----------------------------------------------------

    def _log(self, msg: str):
        log.info(f"[CardMemScan] {msg}")

    def _log_debug(self, msg: str):
        log.debug(f"[CardMemScan] {msg}")

    # -- Low-level memory helpers -------------------------------------------

    def _is_probable_ptr(self, ptr: int) -> bool:
        return isinstance(ptr, int) and 0x10000000000 <= ptr <= 0x7FFFFFFFFFFF and (ptr & 0x7) == 0

    def _read_ptr(self, addr: int) -> Optional[int]:
        ptr = self._memory.read_value(addr, "ulong")
        if ptr and self._is_probable_ptr(ptr):
            return ptr
        return None

    def _read_visibility(self, widget_addr: int) -> int:
        data = self._memory.read_bytes(widget_addr + UWIDGET_VISIBILITY_OFFSET, 1)
        if data and len(data) >= 1:
            return data[0]
        return -1

    def _read_render_opacity(self, widget_addr: int) -> float:
        data = self._memory.read_bytes(widget_addr + UWIDGET_RENDER_OPACITY, 4)
        if data and len(data) >= 4:
            return struct.unpack_from("<f", data, 0)[0]
        return -1.0

    def _read_fname(self, obj_addr: int) -> str:
        """Read the FName string of a UObject at obj_addr."""
        fnamepool = getattr(self._scanner, '_fnamepool_addr', 0)
        if not fnamepool:
            return ""
        fname_data = self._memory.read_bytes(obj_addr + UE4_UOBJECT_FNAME_OFFSET, 4)
        if not fname_data:
            return ""
        comparison_index = struct.unpack("<i", fname_data)[0]
        if comparison_index <= 0:
            return ""
        return self._memory.read_fname(fnamepool, comparison_index) or ""

    def _read_fname_at(self, addr: int) -> str:
        """Read an FName (int32 comparison_index) at an arbitrary address."""
        fnamepool = getattr(self._scanner, '_fnamepool_addr', 0)
        if not fnamepool:
            return ""
        data = self._memory.read_bytes(addr, 4)
        if not data:
            return ""
        ci = struct.unpack("<i", data)[0]
        if ci <= 0:
            return ""
        return self._memory.read_fname(fnamepool, ci) or ""

    def _read_switcher_index(self, switcher_addr: int) -> int:
        data = self._memory.read_bytes(switcher_addr + WIDGET_SWITCHER_ACTIVE_INDEX, 4)
        if data and len(data) >= 4:
            val = struct.unpack_from("<i", data, 0)[0]
            if 0 <= val <= 20:
                return val
        return -1

    def _read_style_id(self, uiimage_addr: int) -> int:
        data = self._memory.read_bytes(uiimage_addr + UIMAGE_CURR_STYLE_ID, 4)
        if data and len(data) >= 4:
            return struct.unpack_from("<i", data, 0)[0]
        return -1

    def _read_brush_texture_name(self, uiimage_addr: int) -> str:
        return self._read_fname_at(uiimage_addr + UIMAGE_BRUSH_OFFSET + BRUSH_RESOURCE_NAME)

    def _read_brush_resource_object(self, uiimage_addr: int) -> Tuple[int, str]:
        """Read Brush.ResourceObject pointer and resolve its FName.

        Returns (ptr, fname_str).
        """
        ptr = self._read_ptr(uiimage_addr + UIMAGE_BRUSH_OFFSET + BRUSH_RESOURCE_OBJECT)
        if ptr:
            fname = self._read_fname(ptr)
            return (ptr, fname)
        return (0, "")

    # -- Sub-widget probe ---------------------------------------------------

    def _probe_subwidget(self, parent_addr: int, offset: int, name: str, wtype: str) -> SubWidgetProbe:
        """Read all diagnostic fields from one sub-widget pointer."""
        probe = SubWidgetProbe(name=name, widget_type=wtype, offset=offset)

        ptr = self._read_ptr(parent_addr + offset)
        if not ptr:
            return probe
        probe.ptr = ptr

        # All UWidget-derived: visibility + opacity
        probe.visibility = self._read_visibility(ptr)
        probe.render_opacity = self._read_render_opacity(ptr)

        if wtype == "UIImage":
            probe.style_id = self._read_style_id(ptr)
            probe.brush_texture_name = self._read_brush_texture_name(ptr)
            res_ptr, res_name = self._read_brush_resource_object(ptr)
            probe.brush_resource_ptr = res_ptr
            probe.brush_resource_fname = res_name

        elif wtype == "WidgetSwitcher":
            probe.switcher_index = self._read_switcher_index(ptr)

        elif wtype == "UIMaskedIcon":
            icon_ptr = self._read_ptr(ptr + MASKED_ICON_UNDERLYING_ICON)
            if icon_ptr:
                probe.icon_texture_ptr = icon_ptr
                probe.icon_texture_name = self._read_fname(icon_ptr)
            # Also probe via the soft object path (FSoftObjectProperty -> AssetPathName FName)
            # UIMaskedIcon::IconTexture at +0x248 is FSoftObjectPath (+0x000 = FName)
            soft_fname = self._read_fname_at(ptr + 0x248)
            if soft_fname and not probe.icon_texture_name:
                probe.icon_texture_name = f"(soft){soft_fname}"

        elif wtype == "UIButton":
            # UIButton is a UWidget subclass; vis/opacity already read.
            # Also read its own brush if it has one (UIButton inherits UMG.Button)
            pass

        elif wtype == "UIGamepadIcon":
            # Also Widget-derived; vis/opacity sufficient for now
            pass

        return probe

    # -- Public API ---------------------------------------------------------

    def is_card_ui_open(self) -> bool:
        """Quick check: are UIMysticMapItem_C instances in GObjects?"""
        fnamepool = getattr(self._scanner, '_fnamepool_addr', 0)
        gobjects = getattr(self._scanner, '_gobjects_addr', 0)
        if not fnamepool or not gobjects:
            return False
        results = self._memory.find_gobjects_by_class_name(
            gobjects, fnamepool, MYSTERY_MAP_ITEM_CLASS, max_objects=500000
        )
        return any(not name.startswith("Default__") for _, name in results)

    def get_card_widget_count(self) -> int:
        fnamepool = getattr(self._scanner, '_fnamepool_addr', 0)
        gobjects = getattr(self._scanner, '_gobjects_addr', 0)
        if not fnamepool or not gobjects:
            return 0
        results = self._memory.find_gobjects_by_class_name(
            gobjects, fnamepool, MYSTERY_MAP_ITEM_CLASS, max_objects=500000
        )
        return sum(1 for _, name in results if not name.startswith("Default__"))

    def deep_probe(self) -> CardProbeResult:
        """Full deep diagnostic probe -- reads EVERY sub-widget on every card instance.

        This is the PRIMARY diagnostic entry point.  Logs comprehensively and
        returns the full structured result for GUI display and correlation with
        known card state (e.g. user-confirmed active cards).
        """
        result = CardProbeResult(timestamp=time.time())

        fnamepool = getattr(self._scanner, '_fnamepool_addr', 0)
        gobjects = getattr(self._scanner, '_gobjects_addr', 0)
        if not fnamepool or not gobjects:
            self._log("Cannot probe -- FNamePool or GObjects not resolved")
            return result

        t0 = time.perf_counter()

        # -- Check root widget existence --
        mystery_roots = self._memory.find_gobjects_by_class_name(
            gobjects, fnamepool, MYSTERY_ROOT_CLASS, max_objects=500000
        )
        result.mystery_root_exists = any(
            not name.startswith("Default__") for _, name in mystery_roots
        )
        area_instances = self._memory.find_gobjects_by_class_name(
            gobjects, fnamepool, MYSTERY_AREA_CLASS, max_objects=500000
        )
        result.mystery_area_exists = any(
            not name.startswith("Default__") for _, name in area_instances
        )

        # -- Find all live UIMysticMapItem_C instances --
        all_instances = self._memory.find_gobjects_by_class_name(
            gobjects, fnamepool, MYSTERY_MAP_ITEM_CLASS, max_objects=500000
        )
        live_instances = [(addr, name) for addr, name in all_instances
                         if not name.startswith("Default__")]

        result.widget_count = len(live_instances)
        result.ui_open = len(live_instances) > 0

        if not live_instances:
            self._log("=== Card Probe: UI NOT OPEN (0 live instances) ===")
            return result

        # -- Probe each widget --
        for addr, inst_name in live_instances:
            info = self._read_full_widget(addr, inst_name)
            result.widgets.append(info)

        elapsed = (time.perf_counter() - t0) * 1000
        result.elapsed_ms = elapsed

        # -- Comprehensive logging --
        self._log_full_probe(result)

        # -- JSON export --
        self._save_probe_json(result)

        return result

    # Legacy aliases
    def probe_card_widgets(self) -> CardProbeResult:
        return self.deep_probe()

    def scan_card_widgets(self) -> List[CardWidgetInfo]:
        """Scan all live instances and return widget info list."""
        result = self.deep_probe()
        return result.widgets

    # -- Widget reading -----------------------------------------------------

    def _read_full_widget(self, addr: int, inst_name: str) -> CardWidgetInfo:
        """Deep-read ONE UIMysticMapItem_C and its MysteryCardItem_C."""
        info = CardWidgetInfo()
        info.address = addr
        info.instance_name = inst_name

        # UWidget base
        info.visibility = self._read_visibility(addr)
        info.render_opacity = self._read_render_opacity(addr)

        # -- Probe ALL UIMysticMapItem_C sub-widgets --
        for offset, name, wtype in MAP_ITEM_SUBWIDGETS:
            probe = self._probe_subwidget(addr, offset, name, wtype)
            info.map_item_probes.append(probe)

        # Raw UIMysticMapItem_C Blueprint bytes (0x278-0x370 = 0xF8 bytes)
        raw = self._memory.read_bytes(addr + 0x278, 0x370 - 0x278)
        if raw:
            info.raw_map_item_bytes = bytes(raw)

        # -- Drill into MysteryCardItem_C via MysteryCardView --
        cv_probe = next((p for p in info.map_item_probes
                         if p.name == "MysteryCardView"), None)
        if cv_probe and cv_probe.ptr:
            info.card_view_ptr = cv_probe.ptr
            for offset, name, wtype in CARD_ITEM_SUBWIDGETS:
                probe = self._probe_subwidget(cv_probe.ptr, offset, name, wtype)
                info.card_item_probes.append(probe)

            # Raw MysteryCardItem_C BP bytes (0x278-0x310 = 0x98 bytes)
            raw2 = self._memory.read_bytes(cv_probe.ptr + 0x278, 0x310 - 0x278)
            if raw2:
                info.raw_card_item_bytes = bytes(raw2)

        return info

    # -- Logging ------------------------------------------------------------

    def _log_full_probe(self, result: CardProbeResult):
        """Log the complete probe result — designed for maximum grep-ability."""
        sep = "=" * 80
        self._log(sep)
        self._log(f"DEEP CARD PROBE  |  {result.widget_count} widgets  |  "
                  f"{result.elapsed_ms:.0f} ms")
        self._log(f"  Mystery_C root exists: {result.mystery_root_exists}")
        self._log(f"  MysteryArea_C exists:  {result.mystery_area_exists}")
        self._log(f"  UI open:               {result.ui_open}")
        self._log(sep)

        for i, w in enumerate(result.widgets):
            vis = self._vis_name(w.visibility)
            self._log("")
            self._log(f"+-- WIDGET [{i:2d}] {w.instance_name} @ 0x{w.address:X}")
            self._log(f"|   vis={vis}  opacity={w.render_opacity:.2f}")

            # UIMysticMapItem_C sub-widgets
            self._log(f"|")
            self._log(f"|   -- UIMysticMapItem_C sub-widgets "
                      f"({len(w.map_item_probes)}) --")
            for p in w.map_item_probes:
                self._log_subwidget(p, indent="|   ")

            # Raw hex dump of MapItem BP region
            if w.raw_map_item_bytes:
                self._log(f"|")
                self._log(f"|   -- MapItem raw BP bytes "
                          f"(0x278..0x370, {len(w.raw_map_item_bytes)} B) --")
                self._log_hex_dump(w.raw_map_item_bytes, base_offset=0x278,
                                   indent="|   ")

            # MysteryCardItem_C sub-widgets
            if w.card_view_ptr:
                self._log(f"|")
                self._log(f"|   -- MysteryCardItem_C @ 0x{w.card_view_ptr:X} "
                          f"({len(w.card_item_probes)} sub-widgets) --")
                for p in w.card_item_probes:
                    self._log_subwidget(p, indent="|   ")

                # Raw hex dump of CardItem BP region
                if w.raw_card_item_bytes:
                    self._log(f"|")
                    self._log(f"|   -- CardItem raw BP bytes "
                              f"(0x278..0x310, {len(w.raw_card_item_bytes)} B) --")
                    self._log_hex_dump(w.raw_card_item_bytes, base_offset=0x278,
                                       indent="|   ")

            # Summary line
            summary = self._build_summary(w)
            self._log(f"|")
            self._log(f"|   >>> SUMMARY: {summary}")
            self._log(f"+{'=' * 78}")

        self._log("")
        self._log(f"=== END DEEP PROBE ({result.widget_count} widgets, "
                  f"{result.elapsed_ms:.0f} ms) ===")

    def _log_subwidget(self, p: SubWidgetProbe, indent: str = ""):
        """Log one sub-widget probe result on one line."""
        if not p.ptr:
            self._log(f"{indent}  +0x{p.offset:03X} {p.name:30s} "
                      f"[{p.widget_type:16s}] = NULL")
            return

        vis = self._vis_name(p.visibility)
        base = (f"{indent}  +0x{p.offset:03X} {p.name:30s} "
                f"[{p.widget_type:16s}] "
                f"vis={vis} op={p.render_opacity:.2f}")

        extras: List[str] = []
        if p.widget_type == "UIImage":
            extras.append(f"sty={p.style_id}")
            if p.brush_texture_name:
                extras.append(f"brushName={p.brush_texture_name}")
            if p.brush_resource_fname:
                extras.append(f"resObj={p.brush_resource_fname}")
            elif p.brush_resource_ptr:
                extras.append(f"resPtr=0x{p.brush_resource_ptr:X}")

        elif p.widget_type == "WidgetSwitcher":
            extras.append(f"activeIdx={p.switcher_index}")

        elif p.widget_type == "UIMaskedIcon":
            if p.icon_texture_name:
                extras.append(f"iconTex={p.icon_texture_name}")
            elif p.icon_texture_ptr:
                extras.append(f"iconPtr=0x{p.icon_texture_ptr:X}")

        if extras:
            self._log(f"{base}  | {' | '.join(extras)}")
        else:
            self._log(f"{base}")

    def _log_hex_dump(self, raw: bytes, base_offset: int = 0, indent: str = ""):
        """Log non-zero 8-byte aligned values from a raw bytes region."""
        interesting: List[str] = []
        for off in range(0, len(raw) - 7, 8):
            val = struct.unpack_from("<Q", raw, off)[0]
            if val == 0:
                continue
            abs_off = base_offset + off
            if self._is_probable_ptr(val):
                # Try to resolve FName for context
                fname = self._read_fname(val)
                if fname:
                    interesting.append(f"+0x{abs_off:03X}=ptr({fname})")
                else:
                    interesting.append(f"+0x{abs_off:03X}=ptr(0x{val:X})")
            else:
                lo = struct.unpack_from("<I", raw, off)[0]
                hi = struct.unpack_from("<I", raw, off + 4)[0]
                interesting.append(f"+0x{abs_off:03X}=0x{lo:08X}_{hi:08X}")

        for chunk_start in range(0, len(interesting), 3):
            chunk = interesting[chunk_start:chunk_start + 3]
            self._log(f"{indent}  {' '.join(chunk)}")

    def _build_summary(self, w: CardWidgetInfo) -> str:
        """One-line summary of the most interesting fields for quick grep."""
        parts: List[str] = []

        parts.append(f"vis={self._vis_name(w.visibility)}")

        def find_map(name: str) -> Optional[SubWidgetProbe]:
            return next((p for p in w.map_item_probes if p.name == name), None)

        def find_card(name: str) -> Optional[SubWidgetProbe]:
            return next((p for p in w.card_item_probes if p.name == name), None)

        h = find_map("Highlight")
        if h and h.ptr:
            parts.append(f"HL={self._vis_name(h.visibility)}")

        g = find_map("GoldFrameBg")
        if g and g.ptr:
            parts.append(f"Gold={self._vis_name(g.visibility)}")

        bts = find_map("BossTalentPointSwitcher")
        if bts and bts.ptr:
            parts.append(f"BTSw={bts.switcher_index}")

        es = find_card("EffectSwitcher")
        if es and es.ptr:
            parts.append(f"EfxIdx={es.switcher_index}")

        frame = find_card("FrameImg")
        if frame and frame.ptr:
            parts.append(f"FrSty={frame.style_id}")
            if frame.brush_resource_fname:
                parts.append(f"FrTex={frame.brush_resource_fname}")
            elif frame.brush_texture_name:
                parts.append(f"FrBrush={frame.brush_texture_name}")

        buf = find_card("BuffIcon")
        if buf and buf.ptr:
            parts.append(f"BufSty={buf.style_id}")
            if buf.brush_resource_fname:
                parts.append(f"BufTex={buf.brush_resource_fname}")

        cim = find_card("CardIconMask")
        if cim and cim.ptr:
            if cim.icon_texture_name:
                parts.append(f"CardIcon={cim.icon_texture_name}")

        eb = find_card("EmptyBg")
        if eb and eb.ptr:
            parts.append(f"EmBg={self._vis_name(eb.visibility)}")

        ei = find_card("EmptyIcon")
        if ei and ei.ptr:
            parts.append(f"EmIc={self._vis_name(ei.visibility)}")

        bi = find_map("BossIcon")
        if bi and bi.ptr and bi.brush_resource_fname:
            parts.append(f"BossRes={bi.brush_resource_fname}")

        return " | ".join(parts)

    @staticmethod
    def _vis_name(vis: int) -> str:
        return {0: "V", 1: "C", 2: "H", 3: "HTI", 4: "SHTI"}.get(vis, f"?{vis}")

    # -- JSON export --------------------------------------------------------

    def _save_probe_json(self, result: CardProbeResult):
        """Save probe data to data/card_probe_<timestamp>.json."""
        try:
            data_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "data",
            )
            os.makedirs(data_dir, exist_ok=True)

            ts = time.strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(data_dir, f"card_probe_{ts}.json")

            export: Dict[str, Any] = {
                "timestamp": result.timestamp,
                "elapsed_ms": result.elapsed_ms,
                "ui_open": result.ui_open,
                "widget_count": result.widget_count,
                "mystery_root": result.mystery_root_exists,
                "mystery_area": result.mystery_area_exists,
                "widgets": [],
            }

            for w in result.widgets:
                wdata: Dict[str, Any] = {
                    "address": hex(w.address),
                    "name": w.instance_name,
                    "visibility": w.visibility,
                    "opacity": w.render_opacity,
                    "map_item_subs": {},
                    "card_view_ptr": hex(w.card_view_ptr) if w.card_view_ptr else "0",
                    "card_item_subs": {},
                }
                for p in w.map_item_probes:
                    wdata["map_item_subs"][p.name] = self._probe_to_dict(p)
                for p in w.card_item_probes:
                    wdata["card_item_subs"][p.name] = self._probe_to_dict(p)

                if w.raw_map_item_bytes:
                    wdata["raw_map_item_hex"] = w.raw_map_item_bytes.hex()
                if w.raw_card_item_bytes:
                    wdata["raw_card_item_hex"] = w.raw_card_item_bytes.hex()

                export["widgets"].append(wdata)

            with open(filepath, "w") as f:
                json.dump(export, f, indent=2)

            self._log(f"Probe data saved to {filepath}")
        except Exception as exc:
            self._log(f"WARNING: Failed to save probe JSON: {exc}")

    @staticmethod
    def _probe_to_dict(p: SubWidgetProbe) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "offset": hex(p.offset),
            "type": p.widget_type,
            "ptr": hex(p.ptr) if p.ptr else "0",
            "visibility": p.visibility,
            "opacity": round(p.render_opacity, 3),
        }
        if p.widget_type == "UIImage":
            d["style_id"] = p.style_id
            d["brush_tex_name"] = p.brush_texture_name
            d["brush_res_ptr"] = hex(p.brush_resource_ptr) if p.brush_resource_ptr else "0"
            d["brush_res_fname"] = p.brush_resource_fname
        elif p.widget_type == "WidgetSwitcher":
            d["active_index"] = p.switcher_index
        elif p.widget_type == "UIMaskedIcon":
            d["icon_tex_ptr"] = hex(p.icon_texture_ptr) if p.icon_texture_ptr else "0"
            d["icon_tex_name"] = p.icon_texture_name
        return d
