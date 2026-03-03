"""Memory-based card selector for Netherrealm map selection.

Replaces the CV-based CardDetector with deterministic memory reading.
Uses CardMemoryScanner to probe widget state and CardDatabase for
card identification + user-defined priority ordering.

Signals:
  - EmptyBg.visibility == 1 (Collapsed) → card IS present in slot
  - CardIconMask.icon_texture_name → card identity (texture key)
  - EffectSwitcher.active_index → rarity (0=blue, 1=purple, 2=orange, 3=rainbow)
  - Aember_01 / empty texture → default empty, not a real card

Widget→Hex mapping (v4.89.0 Pure Memory Coordinate Match):
  Each UIMysticMapItem_C widget dynamically gets layout coordinates on the Canvas.
  X and Y spatial locations are obtained by reading the Floats at `UWidget->Slot(UCanvasPanelSlot)->LayoutData->Offsets`.
  Those coordinates perfectly map to the physical screen spacing defined in `HEX_POSITIONS`, 
  making the CV rarity correlation entirely obsolete.
  
Created: v4.85.0  |  Widget-slot mapping (CV-free X/Y): v4.89.0
"""

import json
import os
import struct
import time
from typing import Optional, List, Tuple, Dict
from dataclasses import dataclass, field

from src.core.memory_reader import MemoryReader
from src.core.card_database import CardDatabase, CardEntry, RARITY_INDEX_MAP, DEFAULT_EMPTY_TEXTURE
from src.utils.logger import log
from src.utils.constants import (
    UE4_UOBJECT_CLASS_OFFSET,
    UE4_UOBJECT_FNAME_OFFSET,
    UE4_UOBJECT_OUTER_OFFSET,
    UWIDGET_VISIBILITY_OFFSET,
    MYSTERY_MAP_ITEM_CLASS,
    UISHRINK_TEXT_CLASS,
    NORMAL_MAP_NAME_WIDGET,
    UISHRINK_TEXT_CURR_KEY_OFFSET,
    MYSTERY_CARD_ICON_MASK,
    MYSTERY_CARD_EFFECT_SWITCHER,
    MYSTERY_CARD_EMPTY_BG,
    MYSTERY_MAP_ITEM_CARD_VIEW,
    WIDGET_SWITCHER_ACTIVE_INDEX,
    MASKED_ICON_UNDERLYING_ICON,
    MAP_NODE_NAMES,
    HEX_POSITIONS,
    UWIDGET_SLOT_OFFSET,
    UCANVASPANELSLOT_LAYOUTDATA_OFFSET,
    FMARGIN_LEFT_OFFSET,
    FMARGIN_TOP_OFFSET,
)


@dataclass
class DetectedCard:
    """A card detected via memory reading in one hex slot."""
    widget_index: int           # index in the GObjects enumeration (0-based)
    widget_address: int         # UIMysticMapItem_C address
    card_view_address: int      # MysteryCardItem_C address
    texture_name: str           # raw texture key from CardIconMask
    clean_texture: str          # cleaned texture key (prefix stripped)
    rarity_index: int           # EffectSwitcher.active_index (0-3), -1 if unreadable
    rarity_name: str            # "blue"/"purple"/"orange"/"rainbow" or ""
    card_entry: Optional[CardEntry] = None  # resolved from CardDatabase
    priority_rank: int = 9999   # lower = higher priority (from CardDatabase)
    card_name: str = ""         # display name if resolved
    hex_slot_index: int = -1    # screen hex position 0-11; -1 = not yet known
    map_key: str = ""           # CurrTextKey from NormalMapName widget


class MemoryCardSelector:
    """Fast memory-based card identification for bot map selection.

    This is a PRODUCTION module — not diagnostic. It reads the minimum
    fields needed to identify all 12 card slots and select the best one
    based on user-defined priority from CardDatabase.

    Designed to be called from MapSelector._select_card() as a replacement
    for the CV-based CardDetector pipeline.
    """

    def __init__(self, memory: MemoryReader, scanner, card_db: CardDatabase):
        """
        Args:
            memory: Live MemoryReader with game attached
            scanner: UE4Scanner with fnamepool_addr and gobjects_addr resolved
            card_db: CardDatabase with priority ordering + texture mappings
        """
        self._memory = memory
        self._scanner = scanner
        self._db = card_db
        # Session cache: widget_addr → (map_key, hex_slot_index)
        # Built once per game session on first detect_cards() call.
        self._session_widget_map: Dict[int, Tuple[str, int]] = {}
        self._session_widgets_loaded: bool = False

    # -- Public API ---------------------------------------------------------

    def is_card_ui_open(self) -> bool:
        """Check if the card selection UI is open by looking for live widget instances.

        Much faster than CV template matching — single GObjects enumeration.
        Returns True if any non-Default UIMysticMapItem_C instances exist.
        """
        fnamepool = getattr(self._scanner, '_fnamepool_addr', 0)
        gobjects = getattr(self._scanner, '_gobjects_addr', 0)
        if not fnamepool or not gobjects:
            return False

        results = self._memory.find_gobjects_by_class_name(
            gobjects, fnamepool, MYSTERY_MAP_ITEM_CLASS, max_objects=500000
        )
        return any(not name.startswith("Default__") for _, name in results)

    def detect_cards(self) -> Tuple[List[DetectedCard], float]:
        """Scan all card widgets and identify present cards.

        Returns:
            (list of DetectedCard for slots that have a real card, elapsed_ms)

        Cards are returned UNSORTED — caller decides ordering.
        Empty slots (EmptyBg.vis != 1, or Aember_01 texture) are excluded.
        """
        t0 = time.perf_counter()
        cards: List[DetectedCard] = []

        fnamepool = getattr(self._scanner, '_fnamepool_addr', 0)
        gobjects = getattr(self._scanner, '_gobjects_addr', 0)
        if not fnamepool or not gobjects:
            log.warning("[MemCardSel] Cannot scan — FNamePool or GObjects not resolved")
            return cards, 0.0

        # Find all live UIMysticMapItem_C instances
        all_instances = self._memory.find_gobjects_by_class_name(
            gobjects, fnamepool, MYSTERY_MAP_ITEM_CLASS, max_objects=500000
        )
        live = [(addr, name) for addr, name in all_instances
                if not name.startswith("Default__")]

        if not live:
            elapsed = (time.perf_counter() - t0) * 1000
            log.info(f"[MemCardSel] No card widgets found (UI closed?) — {elapsed:.0f}ms")
            return cards, elapsed

        # Build widget→slot map once per game session (or if widgets reallocated)
        needs_rebuild = False
        if not self._session_widgets_loaded:
            needs_rebuild = True
        else:
            # Check if current live widgets are in our cache. If not, UI was rebuilt.
            cache_keys = set(self._session_widget_map.keys())
            live_keys = {addr for addr, _ in live}
            if not live_keys.issubset(cache_keys):
                needs_rebuild = True

        if needs_rebuild:
            self._build_session_widget_map({addr for addr, _ in live})

        for idx, (addr, inst_name) in enumerate(live):
            card = self._read_card_slot(idx, addr)
            if card is not None:
                cards.append(card)

        elapsed = (time.perf_counter() - t0) * 1000
        log.info(f"[MemCardSel] Detected {len(cards)}/{len(live)} filled slots in {elapsed:.0f}ms")
        for c in cards:
            rank_str = f"rank={c.priority_rank}" if c.priority_rank < 9999 else "unranked"
            name_str = f"{c.clean_texture} -> {c.card_name}" if c.card_name else c.clean_texture
            slot_str = f" hex={c.hex_slot_index}" if c.hex_slot_index >= 0 else ""
            log.info(f"  [{c.widget_index:2d}] {name_str} ({c.rarity_name}) {rank_str}{slot_str}")

        return cards, elapsed

    def select_best_card(self) -> Tuple[Optional[DetectedCard], List[DetectedCard], float]:
        """Detect all cards and return the highest-priority one.

        Priority is determined by CardDatabase.get_card_priority_rank().
        Cards with known priority rank are preferred over unknown ones.
        Among unknown cards, higher rarity wins (rainbow > orange > purple > blue).

        Returns:
            (best_card or None, all_detected_cards, elapsed_ms)
        """
        cards, elapsed = self.detect_cards()
        if not cards:
            return None, cards, elapsed

        # Sort: lower priority_rank = higher priority; ties broken by rarity_index desc
        def sort_key(c: DetectedCard):
            # Known cards (rank < 9999) come first, sorted by rank ascending
            # Unknown cards (rank == 9999) come after, sorted by rarity descending
            is_known = 0 if c.priority_rank < 9999 else 1
            return (is_known, c.priority_rank, -c.rarity_index)

        cards_sorted = sorted(cards, key=sort_key)
        best = cards_sorted[0]

        name_str = f"{best.clean_texture} -> {best.card_name}" if best.card_name else best.clean_texture
        log.info(f"[MemCardSel] Best card: [{best.widget_index}] {name_str} "
                 f"({best.rarity_name}, rank={best.priority_rank})")

        return best, cards_sorted, elapsed

    # -- Internal helpers ---------------------------------------------------

    def _read_card_slot(self, widget_idx: int, map_item_addr: int) -> Optional[DetectedCard]:
        """Read one UIMysticMapItem_C and determine if it holds a real card.

        Returns DetectedCard if slot has a card, None if empty/unreadable.
        """
        # Step 1: Read MysteryCardView pointer (MysteryCardItem_C)
        card_view_ptr = self._read_ptr(map_item_addr + MYSTERY_MAP_ITEM_CARD_VIEW)
        if not card_view_ptr:
            return None

        # Step 2: Check EmptyBg visibility — must be 1 (Collapsed) = card present
        empty_bg_ptr = self._read_ptr(card_view_ptr + MYSTERY_CARD_EMPTY_BG)
        if not empty_bg_ptr:
            return None

        empty_bg_vis = self._read_visibility(empty_bg_ptr)
        if empty_bg_vis != 1:  # 1 = Collapsed = card is present
            return None

        # Step 3: Read CardIconMask texture name
        texture_name = self._read_card_icon_texture(card_view_ptr)
        if not texture_name or texture_name == DEFAULT_EMPTY_TEXTURE:
            return None

        # Step 4: Read EffectSwitcher rarity index
        rarity_index = self._read_effect_switcher_index(card_view_ptr)
        rarity_name = RARITY_INDEX_MAP.get(rarity_index, "")

        # Step 5: Identify card via CardDatabase
        card_entry = self._db.identify_card(texture_name, rarity_index)
        clean_tex = self._db._clean_texture(texture_name)

        # Step 6: Get priority rank
        priority_rank = 9999
        card_name = ""
        if card_entry:
            priority_rank = self._db.get_card_priority_rank(card_entry.id)
            if priority_rank < 0:
                priority_rank = 9999  # not in priority list
            card_name = card_entry.name

        # Enrich with widget→slot info from session cache
        map_key, hex_slot = self._session_widget_map.get(map_item_addr, ("", -1))

        return DetectedCard(
            widget_index=widget_idx,
            widget_address=map_item_addr,
            card_view_address=card_view_ptr,
            texture_name=texture_name,
            clean_texture=clean_tex,
            rarity_index=rarity_index,
            rarity_name=rarity_name,
            card_entry=card_entry,
            priority_rank=priority_rank,
            card_name=card_name,
            hex_slot_index=hex_slot,
            map_key=map_key,
        )

    # -- Widget→Slot mapping (CV-free pure memory hex coordinate detection) ----------------------

    def _build_session_widget_map(self, live_addrs: set) -> None:
        """Build self._session_widget_map: {widget_addr: (map_key, hex_slot_index)}.

        runs once per game session (or when UI widgets are recreated).
        Evaluates the actual layout coordinates of each UIMysticMapItem_C instance
        in UI memory, matching their Float X/Y Canvas offsets to the known
        HEX_POSITIONS grid. Calculates a dynamic UI anchor translation to 
        perfectly align them.
        """
        self._session_widgets_loaded = True  # prevent re-entry even on failure
        self._session_widget_map.clear()
        fnamepool = getattr(self._scanner, '_fnamepool_addr', 0)
        gobjects = getattr(self._scanner, '_gobjects_addr', 0)
        if not fnamepool or not gobjects:
            return

        from src.utils.constants import HEX_POSITIONS

        # 1. Read all spatial coordinates from memory
        memory_points = []  # list of (addr, x, y)
        for map_item_addr in live_addrs:
            slot_ptr = self._read_ptr(map_item_addr + UWIDGET_SLOT_OFFSET)
            if not slot_ptr:
                continue

            x_bytes = self._memory.read_bytes(slot_ptr + UCANVASPANELSLOT_LAYOUTDATA_OFFSET + FMARGIN_LEFT_OFFSET, 4)
            y_bytes = self._memory.read_bytes(slot_ptr + UCANVASPANELSLOT_LAYOUTDATA_OFFSET + FMARGIN_TOP_OFFSET, 4)
            
            if not x_bytes or not y_bytes or len(x_bytes) < 4 or len(y_bytes) < 4:
                continue
                
            x_layout = struct.unpack_from("<f", x_bytes, 0)[0]
            y_layout = struct.unpack_from("<f", y_bytes, 0)[0]

            if abs(x_layout) < 1.0 and abs(y_layout) < 1.0:
                continue
                
            memory_points.append((map_item_addr, x_layout, y_layout))
            log.info(f"[MemCardSel] Raw widget addr {map_item_addr:#x} layout = ({x_layout:.1f}, {y_layout:.1f})")

        if not memory_points:
            log.warning("[MemCardSel] Failed to read any Layout coords from widgets.")
            return

        # 2. Advanced Affine RANSAC to find Scale and Translation Vector
        # The game's Canvas UI coordinates don't map 1:1 to Absolute Screen Pixels.
        # It typically has a ~0.525x scale and a large anchor offset. We dynamically 
        # discover this to survive arbitrary resolution changes.
        import itertools
        best_match_count = 0
        best_params = (1.0, 0.0, 1.0, 0.0) # sx, tx, sy, ty
        best_mapping = {}  # idx in memory_points -> hex_slot_idx

        # Generate a small set of spread-out hex pairs to serve as anchors.
        # Hex 0 (Left), Hex 6 (Right), Hex 2 (Top), Hex 11 (Bottom)
        anchor_pairs = [ (0, 6), (2, 11), (7, 5), (6, 0), (11, 2) ]

        for (h_idx_i, h_idx_j) in anchor_pairs:
            hx_i, hy_i = HEX_POSITIONS[h_idx_i]
            hx_j, hy_j = HEX_POSITIONS[h_idx_j]
            
            for mi, mj in itertools.permutations(range(len(memory_points)), 2):
                _, mx_i, my_i = memory_points[mi]
                _, mx_j, my_j = memory_points[mj]
                
                dx_m = mx_i - mx_j
                dy_m = my_i - my_j
                if abs(dx_m) < 10 or abs(dy_m) < 10:
                    continue
                
                # Derive scale
                scale_x = (hx_i - hx_j) / dx_m
                scale_y = (hy_i - hy_j) / dy_m
                
                # Sanity check: Scale is typically ~0.52. Reject crazy outliers.
                if not (0.2 < abs(scale_x) < 3.0): continue
                if not (0.2 < abs(scale_y) < 3.0): continue
                
                # Derive offset translation
                offset_x = hx_i - mx_i * scale_x
                offset_y = hy_i - my_i * scale_y
                
                match_count = 0
                current_mapping = {}
                
                # Given this affine transform (scale + offset), map all points
                for test_idx, (_, tmx, tmy) in enumerate(memory_points):
                    sx = tmx * scale_x + offset_x
                    sy = tmy * scale_y + offset_y
                    
                    best_dist = float('inf')
                    best_h = -1
                    for tx, (thx, thy) in HEX_POSITIONS.items():
                        dist = (thx - sx)**2 + (thy - sy)**2
                        if dist < best_dist:
                            best_dist = dist
                            best_h = tx
                            
                    # 60 pixel Euclidean tolerance (3600 squared)
                    if best_dist < 3600:
                        match_count += 1
                        current_mapping[test_idx] = best_h
                        
                if match_count > best_match_count:
                    best_match_count = match_count
                    best_params = (scale_x, offset_x, scale_y, offset_y)
                    best_mapping = current_mapping

        # 3. Apply the winning alignment mapping
        sx, tx, sy, ty = best_params
        found_coords = 0
        for test_idx, hex_slot in best_mapping.items():
            addr, mx, my = memory_points[test_idx]
            self._session_widget_map[addr] = ("", hex_slot)
            slot_label = f"hex={hex_slot} ({MAP_NODE_NAMES.get(hex_slot, '?')})"
            log.info(f"[MemCardSel] Mapped Widget {addr:#x} -> {slot_label} (Layout X:{mx:.1f} Y:{my:.1f})")
            found_coords += 1

        if found_coords > 0:
            log.info(f"[MemCardSel] Affine Mapping completed: {found_coords}/{len(live_addrs)} slots resolved.")
            log.info(f"   Transforms -> Scale X:{sx:.3f}, Y:{sy:.3f} | Offset X:{tx:.1f}, Y:{ty:.1f}")
        else:
            log.warning(f"[MemCardSel] Failed to map widget coordinates spatially.")
            




    # -- Low-level card helpers ---------------------------------------------

    def _read_card_icon_texture(self, card_view_addr: int) -> str:
        """Read CardIconMask -> UnderlyingIconTexture -> FName."""
        icon_mask_ptr = self._read_ptr(card_view_addr + MYSTERY_CARD_ICON_MASK)
        if not icon_mask_ptr:
            return ""

        # UIMaskedIcon -> UnderlyingIconTexture pointer at MASKED_ICON_UNDERLYING_ICON
        icon_tex_ptr = self._read_ptr(icon_mask_ptr + MASKED_ICON_UNDERLYING_ICON)
        if not icon_tex_ptr:
            # Fallback: try soft object path at +0x248
            soft_fname = self._read_fname_at(icon_mask_ptr + 0x248)
            return soft_fname or ""

        return self._read_fname(icon_tex_ptr)

    def _read_effect_switcher_index(self, card_view_addr: int) -> int:
        """Read EffectSwitcher.ActiveWidgetIndex for rarity."""
        sw_ptr = self._read_ptr(card_view_addr + MYSTERY_CARD_EFFECT_SWITCHER)
        if not sw_ptr:
            return -1

        data = self._memory.read_bytes(sw_ptr + WIDGET_SWITCHER_ACTIVE_INDEX, 4)
        if data and len(data) >= 4:
            val = struct.unpack_from("<i", data, 0)[0]
            if 0 <= val <= 20:
                return val
        return -1

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

    def _read_fname(self, obj_addr: int) -> str:
        """Read FName string of a UObject."""
        fnamepool = getattr(self._scanner, '_fnamepool_addr', 0)
        if not fnamepool:
            return ""
        fname_data = self._memory.read_bytes(obj_addr + UE4_UOBJECT_FNAME_OFFSET, 4)
        if not fname_data:
            return ""
        ci = struct.unpack("<i", fname_data)[0]
        if ci <= 0:
            return ""
        return self._memory.read_fname(fnamepool, ci) or ""

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
