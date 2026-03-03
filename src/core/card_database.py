"""Card Database — manages the Netherrealm card registry, auto-learn texture mapping, and priority system.

All 46 cards are pre-registered with name, category, rarity, and description.
At runtime, when the memory scanner detects a card texture (CardIconMask) + rarity
(EffectSwitcher), it matches them to a database entry and auto-learns the texture→card
mapping for future identification.

Usage:
    db = CardDatabase()                       # loads from data/card_database.json
    card = db.identify_card("Gear_02", 1)     # texture + rarity_index → card or None
    db.learn_texture("NewTex_04", 15)         # associate texture with card ID 15
    db.save()                                 # persist to JSON
    ordered = db.get_priority_list()          # cards in user-defined priority order
    db.set_priority_order([3, 1, 2, ...])     # user reorders → save
"""

import json
import os
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from src.utils.logger import log

# ── Constants ──────────────────────────────────────────────────────────────────
RARITY_INDEX_MAP = {0: "blue", 1: "purple", 2: "orange", 3: "rainbow"}
RARITY_TO_INDEX = {"blue": 0, "purple": 1, "orange": 2, "rainbow": 3}
RARITY_COLORS = {
    "blue": "#58A6FF",
    "purple": "#BC8CFF",
    "orange": "#D29922",
    "rainbow": "#FF6B6B",
}
CATEGORY_DISPLAY = {
    "sandlord": "Sandlord",
    "outlaw": "Outlaw",
    "legendary_gear": "Legendary Gear",
    "commodity": "Commodity",
    "memory_fragment": "Memory Fragment",
    "global_drops": "Global Drops",
    "netherrealm": "Netherrealm",
    "other": "Other",
}
DEFAULT_EMPTY_TEXTURE = "Aember_01"


@dataclass
class CardEntry:
    """One card in the database."""
    id: int
    name: str
    category: str
    rarity: str  # "blue", "purple", "orange", "rainbow"
    description: str = ""

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.rarity_symbol})"

    @property
    def rarity_symbol(self) -> str:
        return {"blue": "\u2b26", "purple": "\u2b25", "orange": "\u2b25", "rainbow": "\u2728"}.get(self.rarity, "?")

    @property
    def rarity_index(self) -> int:
        return RARITY_TO_INDEX.get(self.rarity, -1)

    @property
    def full_label(self) -> str:
        cat = CATEGORY_DISPLAY.get(self.category, self.category)
        return f"{self.name} — {cat} [{self.rarity.title()}]"


class CardDatabase:
    """Manages the 46-card Netherrealm card registry with auto-learn texture mapping."""

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            data_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "data",
            )
        self._data_dir = data_dir
        self._filepath = os.path.join(data_dir, "card_database.json")

        self._cards: Dict[int, CardEntry] = {}          # id → CardEntry
        self._texture_to_card: Dict[str, Dict] = {}     # texture_name → {card_ids, note}
        self._priority_order: List[int] = []             # ordered card IDs

        self._load()

    # ── Public API ─────────────────────────────────────────────────────────

    def get_card(self, card_id: int) -> Optional[CardEntry]:
        return self._cards.get(card_id)

    def get_all_cards(self) -> List[CardEntry]:
        return list(self._cards.values())

    def get_priority_list(self) -> List[CardEntry]:
        """Return all cards ordered by user-defined priority (index 0 = highest)."""
        result = []
        seen = set()
        for cid in self._priority_order:
            if cid in self._cards and cid not in seen:
                result.append(self._cards[cid])
                seen.add(cid)
        # Append any cards not in priority order (new cards)
        for cid, card in self._cards.items():
            if cid not in seen:
                result.append(card)
        return result

    def set_priority_order(self, card_ids: List[int]):
        """Set new priority order from GUI drag/reorder."""
        self._priority_order = list(card_ids)
        self.save()
        log.info(f"[CardDB] Priority order updated ({len(card_ids)} cards)")

    def identify_card(self, texture_name: str, rarity_index: int) -> Optional[CardEntry]:
        """Identify a card from its CardIconMask texture name + EffectSwitcher index.

        Returns the matching CardEntry or None if unknown.
        """
        if not texture_name or texture_name == DEFAULT_EMPTY_TEXTURE:
            return None

        # Strip common prefix: "UI_SpCard_Main_" → just the key
        clean = self._clean_texture(texture_name)

        mapping = self._texture_to_card.get(clean)
        if not mapping or not mapping.get("card_ids"):
            return None

        rarity = RARITY_INDEX_MAP.get(rarity_index, "")
        # Find the card with matching rarity among the mapped IDs
        for cid in mapping["card_ids"]:
            card = self._cards.get(cid)
            if card and card.rarity == rarity:
                return card

        # If no rarity match, do NOT guess. Return None to trigger screenshot collection.
        # This forces the bot to rank it 9999 and run the screenshot fallback
        return None

    def learn_texture(self, texture_name: str, card_id: int) -> bool:
        """Associate a texture name with a card ID. Returns True if new mapping."""
        if not texture_name or texture_name == DEFAULT_EMPTY_TEXTURE:
            return False
        if card_id not in self._cards:
            return False

        clean = self._clean_texture(texture_name)
        if clean not in self._texture_to_card:
            self._texture_to_card[clean] = {"card_ids": [], "note": "auto-learned"}

        entry = self._texture_to_card[clean]
        if card_id not in entry["card_ids"]:
            entry["card_ids"].append(card_id)
            self.save()
            card = self._cards[card_id]
            log.info(f"[CardDB] AUTO-LEARN: {clean} → {card.name} ({card.rarity})")
            return True
        return False

    def get_texture_mapping(self) -> Dict[str, List[int]]:
        """Return texture → card_ids map for display."""
        return {tex: info["card_ids"] for tex, info in self._texture_to_card.items()}

    def get_known_textures(self) -> List[str]:
        """Return all known texture names (excluding default empty)."""
        return [t for t in self._texture_to_card if t != DEFAULT_EMPTY_TEXTURE]

    def get_card_priority_rank(self, card_id: int) -> int:
        """Return 0-based priority rank of a card (-1 if not in priority list)."""
        try:
            return self._priority_order.index(card_id)
        except ValueError:
            return -1

    def get_cards_by_rarity(self, rarity: str) -> List[CardEntry]:
        return [c for c in self._cards.values() if c.rarity == rarity]

    def get_cards_by_category(self, category: str) -> List[CardEntry]:
        return [c for c in self._cards.values() if c.category == category]

    # ── Persistence ────────────────────────────────────────────────────────

    def save(self):
        """Write current state to data/card_database.json."""
        try:
            os.makedirs(self._data_dir, exist_ok=True)

            cards_list = []
            for card in sorted(self._cards.values(), key=lambda c: c.id):
                cards_list.append({
                    "id": card.id,
                    "name": card.name,
                    "category": card.category,
                    "rarity": card.rarity,
                    "description": card.description,
                })

            data = {
                "_comment": "Netherrealm card database — all 46 cards with auto-learned icon texture mappings",
                "_version": "1.0.0",
                "_rarity_map": {"0": "blue", "1": "purple", "2": "orange", "3": "rainbow"},
                "cards": cards_list,
                "texture_to_card": self._texture_to_card,
                "priority_order": self._priority_order,
            }

            with open(self._filepath, "w") as f:
                json.dump(data, f, indent=2)

        except Exception as exc:
            log.error(f"[CardDB] Failed to save: {exc}")

    def _load(self):
        """Load from data/card_database.json."""
        if not os.path.exists(self._filepath):
            log.warning(f"[CardDB] No database file at {self._filepath}")
            return

        try:
            with open(self._filepath, "r") as f:
                data = json.load(f)

            for entry in data.get("cards", []):
                card = CardEntry(
                    id=entry["id"],
                    name=entry["name"],
                    category=entry["category"],
                    rarity=entry["rarity"],
                    description=entry.get("description", ""),
                )
                self._cards[card.id] = card

            self._texture_to_card = data.get("texture_to_card", {})
            self._priority_order = data.get("priority_order", list(self._cards.keys()))

            log.info(f"[CardDB] Loaded {len(self._cards)} cards, "
                     f"{len(self._texture_to_card)} texture mappings, "
                     f"priority list of {len(self._priority_order)}")

        except Exception as exc:
            log.error(f"[CardDB] Failed to load: {exc}")

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_texture(texture_name: str) -> str:
        """Strip common prefixes/suffixes from texture name to get the key."""
        t = texture_name
        if t.startswith("UI_SpCard_Main_"):
            t = t[len("UI_SpCard_Main_"):]
        return t
