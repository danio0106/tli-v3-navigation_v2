from enum import Enum, auto


APP_NAME = "Torchlight Infinite Bot V2"
APP_VERSION = 'v5.14.0'
GAME_PROCESS_NAME = "torchlight_infinite.exe"
GAME_WINDOW_TITLE = "Torchlight Infinite"

GAME_RESOLUTION = (1920, 1080)
DESKTOP_RESOLUTION = (2560, 1440)
CHARACTER_CENTER = (952, 569)

# Exit portal interaction button — template matching.
# Template-matches the exit portal icon (blue swirl) anywhere in the
# interaction button strip. Works for both normal layout and Pirates event
# layout (where an extra button shifts the portal icon left).
# Template: assets/exit_portal_icon.png (captured from in-game screenshot).
# Wide search region covers the full button bar so position drift is handled.
EXIT_PORTAL_TEMPLATE_PATH       = "assets/exit_portal_icon.png"
EXIT_PORTAL_MATCH_THRESHOLD     = 0.70   # TM_CCOEFF_NORMED minimum score
EXIT_PORTAL_SEARCH_REGION       = (680, 760, 560, 100)  # (left, top, w, h) client area — full button bar width
STAND_TOLERANCE = 100

CONFIG_FILE = "config.json"
ADDRESSES_FILE = "addresses.json"
PATHS_DIR = "paths"
LOG_FILE = "bot.log"
BOSS_AREAS_FILE = "data/boss_areas.json"
MINIMAP_KEY_MAP_FILE = "data/minimap_key_map.json"  # auto-learned: numeric TMap key → zone FName


class NavMode(Enum):
    RECORD = auto()
    NAVIGATE = auto()


class BotState(Enum):
    IDLE = auto()
    STARTING = auto()
    IN_HIDEOUT = auto()
    OPENING_MAP = auto()
    SELECTING_MAP = auto()
    ENTERING_MAP = auto()
    IN_MAP = auto()
    NAVIGATING = auto()
    RETURNING = auto()
    MAP_COMPLETE = auto()
    PAUSED = auto()
    STOPPING = auto()
    ERROR = auto()


INTERACT_KEY = "f"
LOOT_KEY = "e"

MAP_NAMES = [
    "High Court Maze",
    "Shadow Outpost",
    "Rainforest of Divine Legacy",
    "Grimwind Woods",
    "Demiman Village",
    "Swirling Mines",
    "Blustery Canyon",
    "Defiled Side Chamber",
    "Deserted District",
    "Wall of the Last Breath",
    "Abandoned Mines",
    "Singing Sand",
]

UE4_OFFSETS = {
    "OwningGameInstance": 0x210,
    "LocalPlayers": 0x038,
    "PlayerController": 0x030,
    "Pawn": 0x250,
    "Character": 0x260,
    "RootComponent": 0x130,
    "RelativeLocation": 0x124,
    "RelativeRotation": 0x130,
    "ComponentVelocity": 0x148,
}

DUMP_VERIFIED_CHAIN = [0x210, 0x038, 0x0, 0x030, 0x250, 0x130, 0x124]

FNAMEPOOL_PATTERNS = [
    b"\x48\x8D\x35\x00\x00\x00\x00\xEB\x16",
    b"\x48\x8D\x0D\x00\x00\x00\x00\xE8\x00\x00\x00\x00\xC6\x05",
    b"\x74\x09\x48\x8D\x15\x00\x00\x00\x00\xEB\x16",
    b"\x48\x8D\x05\x00\x00\x00\x00\xEB\x16",
    b"\x48\x8D\x15\x00\x00\x00\x00\xEB\x16",
    b"\x48\x8D\x15\x00\x00\x00\x00\xEB",
]

FNAMEPOOL_MASKS = [
    "xxx????xx",
    "xxx????x????xx",
    "xxxxx????xx",
    "xxx????xx",
    "xxx????xx",
    "xxx????x",
]

FNAMEPOOL_LEA_OFFSETS = [0, 0, 2, 0, 0, 0]

GOBJECTS_PATTERNS = [
    b"\x48\x8B\x05\x00\x00\x00\x00\x48\x8B\x0C\xC8\x48\x85\xC9",
    b"\x48\x8B\x05\x00\x00\x00\x00\x48\x8B\x0C\xC8\x4C\x8D\x04\xD1",
    b"\x48\x8D\x0D\x00\x00\x00\x00\xE8\x00\x00\x00\x00\x48\x8B\xD6",
]

GOBJECTS_MASKS = [
    "xxx????xxxxxxx",
    "xxx????xxxxxxxx",
    "xxx????x????xxx",
]

UE4_FNAME_BLOCK_OFFSET_BITS = 16
UE4_FNAME_MAX_BLOCKS = 8192
UE4_FNAME_POOL_BLOCKS_OFFSET = 0x10
UE4_FNAME_ENTRY_HEADER_SIZE = 2

UE4_FUOBJECTITEM_SIZE = 24
UE4_FUOBJECTARRAY_ELEMENTS_PER_CHUNK = 65536

UE4_UOBJECT_CLASS_OFFSET = 0x10
UE4_UOBJECT_FNAME_OFFSET = 0x18
UE4_UOBJECT_OUTER_OFFSET = 0x20

ECFGCOMPONENT_CFGINFO_OFFSET = 0x120
CFGINFO_ID_OFFSET = 0x000
CFGINFO_TYPE_OFFSET = 0x004
CFGINFO_EXTENDID_OFFSET = 0x008

EGAMEPLAY_EVENT_TYPES = {
    0xd8: "Carjack",
    0xdb: "Sandlord_Alive",
    0xdd: "Sandlord_Protect",
    0xde: "Sandlord_Kill",
}

# DEPRECATED — these are SDK-derived enum values, NOT runtime TMap keys.
# At runtime, FightMgr.MapGamePlay TMap keys are the spawn_index (sequential
# instance ID, e.g. 9, 10, 11). CfgInfo.ID is always 0 at runtime.
# Type identification uses MapCustomTrap class-name + position proximity.
# Only kept because scanner.py._read_event_info() (legacy GObjects path) still
# references EGAMEPLAY_TARGET_IDS. Do NOT use these for TMap-based detection.
EGAMEPLAY_CARJACK_IDS = {0xd8}   # DEPRECATED: SDK enum, not TMap key
EGAMEPLAY_SANDLORD_IDS = {0xdb, 0xdd, 0xde}  # DEPRECATED: SDK enum, not TMap key
EGAMEPLAY_TARGET_IDS = EGAMEPLAY_CARJACK_IDS | EGAMEPLAY_SANDLORD_IDS  # DEPRECATED

# ── Carjack guard detection — movement-prediction approach (v4.65.0) ──────────
# All memory-reading guard discriminators are confirmed dead (Feb 28 2026).
# Guard discrimination now uses movement-prediction: guards flee from the player
# while horde monsters attack/stay.  ABP is still cached silently for data
# collection during manual testing sessions.
#
# [GuardSeed] captures the first GUARD_SEED_MAX entities within 2500u of the
# truck in the first GUARD_SEED_WINDOW_SECS — these are reliably the initial
# guard batch and are used for comparison-only data logging.
GUARD_SEED_WINDOW_SECS: float = 4.0       # seconds after Carjack activation for seed capture
GUARD_SEED_MAX: int = 3                   # first 3 entities near truck = confirmed initial guards

# Movement-prediction flee detection thresholds.
# Entities whose velocity vector points away from the player at ≥ this speed
# (world-units/s) are classified as guards (flee behaviour).
GUARD_FLEE_MIN_SPEED: float = 120.0       # world-units/s minimum away-from-player speed
GUARD_MIN_SURVIVE_SECS: float = 1.5       # entity must have been alive this long before classification

FIGHTMGR_OFFSETS = {
    "FightPool": 0x028,
    "MapRole": 0x080,
    "MapRolePlayer": 0x0D0,
    "MapRoleMonster": 0x120,
    "MapBullet": 0x170,
    "MapLaser": 0x1C0,
    "MapAttack": 0x210,
    "MapLastedEffect": 0x260,
    "MapDirectHit": 0x2B0,
    "MapActionBall": 0x300,
    "MapObject": 0x350,
    "MapAffixCarrier": 0x3A0,
    "MapDestructible": 0x3F0,
    "MapPortal": 0x440,
    "MapFollower": 0x490,
    "MapElevator": 0x4E0,
    "MapCheckPoint": 0x530,
    "MapBuffArea": 0x580,
    "MapProximityShield": 0x5D0,
    "MapObstacle": 0x620,
    "MapGroundEffect": 0x670,
    "MapNPC": 0x6C0,
    "MapInteractiveItem": 0x710,
    "MapUnit": 0x760,
    "MapCustomTrap": 0x7B0,
    "MapGamePlay": 0x800,
    "MapServant": 0x850,
}

FIGHTMGR_MAP_PORTAL_OFFSET = FIGHTMGR_OFFSETS["MapPortal"]
FIGHTMGR_MAP_ROLE_OFFSET = FIGHTMGR_OFFSETS["MapRole"]
FIGHTMGR_MAP_MONSTER_OFFSET = FIGHTMGR_OFFSETS["MapRoleMonster"]
FIGHTMGR_MAP_GAMEPLAY_OFFSET = FIGHTMGR_OFFSETS["MapGamePlay"]
FIGHTMGR_MAP_CUSTOMTRAP_OFFSET = FIGHTMGR_OFFSETS["MapCustomTrap"]
FIGHTMGR_MAP_INTERACTIVE_OFFSET = FIGHTMGR_OFFSETS["MapInteractiveItem"]
# FIGHTMGR_MAP_SERVANT_OFFSET intentionally omitted — MapServant = player pet registry (EServant = pet)
# FIGHTMGR_MAP_UNIT_OFFSET intentionally omitted — not used after v4.65.0 cleanup

# ── UMG Widget Offsets (Mystery / Netherrealm card selection UI) ───────────────
# Offsets confirmed from SDK dump (Feb 2026, in-game card UI open).
#
# Inheritance chain:
#   UWidget (base)  →  UPanelWidget  →  UContentWidget  →  UUserWidget
#     →  Mystery_C / MysteryArea_C / UIMysticMapItem_C / MysteryCardItem_C
#
# ESlateVisibility enum values (UE4):
#   0 = Visible, 1 = Collapsed, 2 = Hidden,
#   3 = HitTestInvisible, 4 = SelfHitTestInvisible
#
# UWidget base offsets (apply to ALL widget subclasses):
UWIDGET_SLOT_OFFSET        = 0x028   # PanelSlot*  (parent layout slot)
UCANVASPANELSLOT_LAYOUTDATA_OFFSET = 0x038 # FAnchorData (Size: 0x28) within UCanvasPanelSlot
LAYOUTDATA_OFFSETS_MARGIN  = 0x000   # FMargin offsets start within FAnchorData
FMARGIN_LEFT_OFFSET        = 0x000   # float Left (X coordinate)
FMARGIN_TOP_OFFSET         = 0x004   # float Top (Y coordinate)
UWIDGET_RENDER_TRANSFORM   = 0x090   # FWidgetTransform (0x1C bytes: Angle, Shear, Scale, Translation)
UWIDGET_RENDER_PIVOT       = 0x0AC   # FVector2D   (render transform pivot)
UWIDGET_VISIBILITY_OFFSET  = 0x0C3   # ESlateVisibility (1 byte enum)
UWIDGET_RENDER_OPACITY     = 0x0C8   # float        (0.0–1.0)
#
# UUserWidget offsets (apply to all Blueprint widgets):
UUSERWIDGET_COLOR_OPACITY  = 0x118   # FLinearColor (4 floats = 16 bytes: R,G,B,A)
UUSERWIDGET_WIDGET_TREE    = 0x1E8   # UWidgetTree* (root of sub-widget hierarchy)
#
# UIMysticMapItem_C offsets (Size: 0x370, per-card hex widget):
#   Each of the 12+2 live instances represents one map hexagon on the
#   card selection UI.  Only present in GObjects when the card UI is open
#   (0 instances when closed — confirmed by non-UI dump differential).
MYSTERY_MAP_ITEM_BOSS_BG       = 0x278  # UIImage* BossBg
MYSTERY_MAP_ITEM_BOSS_ICON     = 0x288  # UIImage* BossIcon
MYSTERY_MAP_ITEM_BOSS_TALENT_SWITCHER = 0x2B0  # WidgetSwitcher* BossTalentPointSwitcher
MYSTERY_MAP_ITEM_CLICK_BUTTON  = 0x2B8  # UIButton* ClickButton
MYSTERY_MAP_ITEM_GOLD_FRAME    = 0x2C8  # UIImage* GoldFrameBg
MYSTERY_MAP_ITEM_HIGHLIGHT     = 0x2D0  # UIImage* Highlight
MYSTERY_MAP_ITEM_CARD_VIEW     = 0x300  # MysteryCardItem_C* MysteryCardView
#
# MysteryCardItem_C offsets (Size: 0x368, card-face sub-widget):
#   Embedded inside each UIMysticMapItem_C via MysteryCardView pointer (+0x300).
#   Contains the actual card visuals: icon, frame, effect animation, buff icon.
MYSTERY_CARD_BUFF_ICON         = 0x280  # UIImage* BuffIcon (buff/card icon image)
MYSTERY_CARD_BUFF_ICON_BG      = 0x288  # UIImage* BuffIconBg
MYSTERY_CARD_ICON_MASK         = 0x290  # UIMaskedIcon* CardIconMask (card main icon)
MYSTERY_CARD_CLICK_BTN         = 0x298  # UIButton* ClickBtn
MYSTERY_CARD_EFFECT_SWITCHER   = 0x2A0  # WidgetSwitcher* EffectSwitcher (4 slots = rarity VFX)
MYSTERY_CARD_EMPTY_BG          = 0x2A8  # UIImage* EmptyBg
MYSTERY_CARD_EMPTY_ICON        = 0x2B0  # UIImage* EmptyIcon
MYSTERY_CARD_FRAME_IMG         = 0x2B8  # UIImage* FrameImg (frame changes per rarity)
MYSTERY_CARD_PROGRESS_ITEM     = 0x308  # UIMysteryProgressItem* progress sub-widget
#
# WidgetSwitcher offset (standard UE4/UMG):
WIDGET_SWITCHER_ACTIVE_INDEX   = 0x128  # int32 ActiveWidgetIndex (which child is displayed)
#
# UIImage offsets (class /Script/UE_game.UIImage, inherits UMG.Image):
#   UIImage adds game-specific style system on top of standard UMG Image.
UIMAGE_BRUSH_OFFSET            = 0x110  # FSlateBrush struct (0x90 bytes, from UMG.Image)
UIMAGE_CURR_STYLE_ID           = 0x224  # int32 CurrStyleId (game's runtime style identifier)
UIMAGE_SOFT_OBJECT_PATH        = 0x230  # FSoftObjectPath (0x18 bytes: FName + SubPathString)
#
# FSlateBrush sub-offsets (relative to Brush start = UIImage + 0x110):
BRUSH_IMAGE_SIZE               = 0x008  # FVector2D (8 bytes)
BRUSH_RESOURCE_OBJECT          = 0x050  # UObject* (Texture2D pointer)
BRUSH_RESOURCE_NAME            = 0x058  # FName (texture asset name)
#
# UIMaskedIcon offsets (class /Script/UE_game.UIMaskedIcon):
#   Used for card icon with mask effect (CardIconMask on MysteryCardItem_C).
MASKED_ICON_MASK_TEXTURE       = 0x220  # FSoftObjectProperty (0x28 bytes)
MASKED_ICON_ICON_TEXTURE       = 0x248  # FSoftObjectProperty (0x28 bytes)
MASKED_ICON_UNDERLYING_MASK    = 0x278  # Texture2D* resolved mask
MASKED_ICON_UNDERLYING_ICON    = 0x280  # Texture2D* resolved icon texture
#
# UIShrinkTextBlock offsets (class /Script/UE_game.UIShrinkTextBlock, Size:0x2C8):
#   Text property (FText)   at +0x138 via parent TextBlock:Text  (complex to read)
#   CurrTextKey (FString)   at +0x170 via UIShrinkTextBlock:CurrTextKey  <- USE THIS
#   TextContent (FText)     at +0x188 via UIShrinkTextBlock:TextContent
UISHRINK_TEXT_CURR_KEY_OFFSET = 0x170  # FString (TArray<TCHAR> = ptr8+count4+max4 = 0x10 bytes)
#
# GObjects class name used to find live Mystery widget instances.
MYSTERY_MAP_ITEM_CLASS    = "UIMysticMapItem_C"
MYSTERY_ROOT_CLASS        = "Mystery_C"
MYSTERY_AREA_CLASS        = "MysteryArea_C"
UISHRINK_TEXT_CLASS       = "UIShrinkTextBlock"
NORMAL_MAP_NAME_WIDGET    = "NormalMapName"  # FName of the map-name text block inside UIMysticMapItem_C
#
# Reference resolution for coordinate scaling.
# All hardcoded pixel coordinates (HEX_POSITIONS, CARD_SLOTS, button positions)
# were measured at this resolution.  scale_factor = actual_size / reference_size.
REFERENCE_RESOLUTION = (1920, 1080)

# ── Carjack reward strongbox (BaoXianXiang) ──────────────────────────────────
# After a SUCCESSFUL Carjack event (51 guards killed before 24-second timer)
# the game spawns reward safes near the truck that the bot must interact with
# using the F key.  These entities live in FightMgr.MapInteractiveItem (0x710),
# NOT in MapRoleMonster or MapObject.
#
# Three tiers identified from S11 Names/minimap sprites (Feb 27 2026):
#   SK_THTJ_BaoXianXiang_01 = small safe   (UI_MiniMap_S11xiaoxingbaoxianxiang)
#   SK_THTJ_BaoXianXiang_02 = large safe   (UI_MiniMap_S11daxingbaoxianxiang)
#   SK_THTJ_BaoXianXiang_03 = special safe (UI_MiniMap_S11tejibaoxianxiang)
#   SK_ZhiXieBaoXiang       = armored safe (ZhiXie = armored cash, rarer variant)
#
# The game entity class name is expected to be EMapInteractiveItem or a subclass.
# To implement interaction:
#   1. After carjack_work_count reaches 51 (bValid→0 on truck), scan MapInteractiveItem
#      TMap for any entity within 3000u of the truck position.
#   2. Navigate to each entity's position and press F.
#   3. The entity's bValid should drop to 0 after successful pickup.
#
# TODO (future): implement Carjack strongbox pickup loop in bot_engine.py
# after the Carjack event completion transition (CARJACK_DONE → NAVIGATING).
CARJACK_STRONGBOX_SEARCH_RADIUS_SQ: float = 3000.0 ** 2

# Optional bounty-order UI detection (Carjack branch).
# If the template file does not exist, bounty handling is skipped safely.
CARJACK_BOUNTY_UI_TEMPLATE_PATH = "assets/carjack_bounty_ui_template.png"
CARJACK_BOUNTY_UI_MATCH_THRESHOLD = 0.78
CARJACK_BOUNTY_UI_SEARCH_REGION = (560, 360, 1320, 860)  # x1,y1,x2,y2 (client)
CARJACK_BOUNTY_UI_CLICK_POSITIONS = [
    (1765, 995),
    (1765, 995),
]

# Distance in game-world units at which a nearby event interrupts waypoint navigation.
EVENT_PROXIMITY_TRIGGER_UNITS = 1000.0

HIDEOUT_POSITION = (5650, 4500)

NEXT_BUTTON = (1765, 995)
ADD_AFFIX_BUTTON = (180, 809)
RESET_AFFIX_BUTTON = (368, 809)
OPEN_PORTAL_BUTTON_POS = (1765, 995)

TIP_POPUP_DIALOG_REGION = (519, 479, 849, 168)
TIP_POPUP_CONFIRM_BUTTON = (1047, 664)
TIP_POPUP_DONT_SHOW_CHECKBOX = (767, 723)
TIP_POPUP_WHITE_THRESHOLD = 225

ATTEMPTS_REGION = (1258, 627, 1330, 642)
ATTEMPTS_VERIFY_MAX_R = 80
ATTEMPTS_VERIFY_MIN_BR_DIFF = 40

HEX_POSITIONS = {
    0: (497, 301),
    1: (933, 321),
    2: (1127, 259),
    3: (1351, 321),
    4: (911, 532),
    5: (1151, 489),
    6: (1519, 491),
    7: (449, 499),
    8: (622, 627),
    9: (1385, 613),
    10: (979, 727),
    11: (723, 789),
}

MAP_NODE_NAMES = {
    0: "High Court Maze",
    1: "Shadow Outpost",
    2: "Rainforest of Divine Legacy",
    3: "Grimwind Woods",
    4: "Demiman Village",
    5: "Swirling Mines",
    6: "Blustery Canyon",
    7: "Defiled Side Chamber",
    8: "Deserted District",
    9: "Wall of the Last Breath",
    10: "Abandoned Mines",
    11: "Singing Sand",
}

CARD_SLOTS = {
    0: {
        "name": "High Court Maze",
        "active_top": (497, 277),
        "active_tl": (462, 306),
        "active_tr": (533, 306),
        "inactive_top": (497, 299),
        "inactive_tl": (463, 327),
        "inactive_tr": (532, 327),
        "interior_sample": (497, 344),
        "measured": "active",
    },
    1: {
        "name": "Shadow Outpost",
        "active_top": (933, 278),
        "active_tl": (898, 306),
        "active_tr": (969, 306),
        "inactive_top": (933, 300),
        "inactive_tl": (899, 327),
        "inactive_tr": (968, 327),
        "interior_sample": (933, 345),
        "measured": "inactive",
    },
    2: {
        "name": "Rainforest of Divine Legacy",
        "active_top": (1128, 216),
        "active_tl": (1093, 244),
        "active_tr": (1164, 244),
        "inactive_top": (1128, 238),
        "inactive_tl": (1094, 265),
        "inactive_tr": (1163, 265),
        "interior_sample": (1128, 283),
        "measured": "both",
    },
    3: {
        "name": "Grimwind Woods",
        "active_top": (1352, 278),
        "active_tl": (1317, 306),
        "active_tr": (1388, 306),
        "inactive_top": (1352, 300),
        "inactive_tl": (1318, 327),
        "inactive_tr": (1387, 327),
        "interior_sample": (1352, 345),
        "measured": "inactive",
    },
    4: {
        "name": "Demiman Village",
        "active_top": (911, 490),
        "active_tl": (876, 518),
        "active_tr": (947, 518),
        "inactive_top": (911, 512),
        "inactive_tl": (877, 539),
        "inactive_tr": (946, 539),
        "interior_sample": (911, 557),
        "measured": "inactive",
    },
    5: {
        "name": "Swirling Mines",
        "active_top": (1152, 447),
        "active_tl": (1117, 475),
        "active_tr": (1188, 475),
        "inactive_top": (1152, 469),
        "inactive_tl": (1118, 496),
        "inactive_tr": (1187, 496),
        "interior_sample": (1152, 514),
        "measured": "inactive",
    },
    6: {
        "name": "Blustery Canyon",
        "active_top": (1520, 449),
        "active_tl": (1485, 477),
        "active_tr": (1556, 477),
        "inactive_top": (1520, 471),
        "inactive_tl": (1486, 498),
        "inactive_tr": (1555, 498),
        "interior_sample": (1520, 516),
        "measured": "inactive",
    },
    7: {
        "name": "Defiled Side Chamber",
        "active_top": (449, 476),
        "active_tl": (414, 504),
        "active_tr": (485, 504),
        "inactive_top": (449, 498),
        "inactive_tl": (415, 525),
        "inactive_tr": (484, 525),
        "interior_sample": (449, 543),
        "measured": "active",
    },
    8: {
        "name": "Deserted District",
        "active_top": (623, 584),
        "active_tl": (588, 613),
        "active_tr": (658, 612),
        "inactive_top": (622, 606),
        "inactive_tl": (588, 633),
        "inactive_tr": (658, 633),
        "interior_sample": (623, 651),
        "measured": "both",
    },
    9: {
        "name": "Wall of the Last Breath",
        "active_top": (1386, 570),
        "active_tl": (1351, 598),
        "active_tr": (1422, 598),
        "inactive_top": (1386, 592),
        "inactive_tl": (1352, 619),
        "inactive_tr": (1421, 619),
        "interior_sample": (1386, 637),
        "measured": "inactive",
    },
    10: {
        "name": "Abandoned Mines",
        "active_top": (980, 704),
        "active_tl": (945, 732),
        "active_tr": (1016, 732),
        "inactive_top": (980, 726),
        "inactive_tl": (946, 753),
        "inactive_tr": (1015, 753),
        "interior_sample": (980, 771),
        "measured": "active",
    },
    11: {
        "name": "Singing Sand",
        "active_top": (724, 747),
        "active_tl": (689, 775),
        "active_tr": (760, 775),
        "inactive_top": (724, 769),
        "inactive_tl": (690, 796),
        "inactive_tr": (759, 796),
        "interior_sample": (724, 814),
        "measured": "inactive",
    },
}

GLOW_CHEVRON_POLYGON = [(-10, -18), (-4, -21), (7, -21), (11, -18), (16, -13), (16, -4), (15, -3), (-17, -2), (-18, -2), (-18, -10)]

BORDER_ACTIVE_GRAY_MIN = 55
BORDER_ACTIVE_GRAY_MAX = 110
BORDER_INACTIVE_GRAY_MAX = 52
BACKGROUND_GRAY_MIN = 130

RARITY_PRIORITY = {"RAINBOW": 4, "ORANGE": 3, "PURPLE": 2, "BLUE": 1, "UNKNOWN": 0}

KEYWORD_SCAN_DEFAULTS = [
    "portal", "fight", "pickcard", "hunluan", "card", "affix",
    "gameplay", "mapobject", "netherrealm", "stage", "S11",
    "mysterious", "fightmgr", "confusion", "deck", "slot",
]

# ── Scanner timing ────────────────────────────────────────────────────────────
# Entity scan thread interval (seconds).  8 ms ≈ 120 Hz — viable after v4.65.0
# probe cleanup since each tick is only a TMap position read + cached ABP lookup
# with no new memory-intensive probes.  Tune down if CPU usage increases on
# slower machines.  The position-history window covers ~130 ms at this rate.
ENTITY_SCAN_INTERVAL_S: float = 0.008   # 8 ms → ~120 Hz entity scanning

DEFAULT_SETTINGS = {
    "game_process": GAME_PROCESS_NAME,
    "game_window": GAME_WINDOW_TITLE,
    "interact_key": INTERACT_KEY,
    "loot_key": LOOT_KEY,
    "map_clear_timeout": 300,
    "hotkey_start": "F9",
    "hotkey_stop": "F10",
    "hotkey_pause": "F11",
    "loop_delay_ms": 50,
    "loot_spam_interval_ms": 150,
    "stuck_timeout_sec": 5,
    "waypoint_tolerance": 200,
    "nav_mode": "manual",  # "manual" (recorded path) or "auto" (A* autonomous)
    "auto_behavior": "rush_events",  # rush_events | kill_all | boss_rush
    # Wall model mode: "legacy" = current binary grid only, "hybrid" =
    # binary grid + confidence/decay overlay costs in Pathfinder.
    "wall_model_mode": "hybrid",
    # Portal-hop transition verification in RTNavigator.
    "portal_transition_verify": True,
}

# ── Wall detection & auto-navigation ──────────────────────────────────────────

# GObjects class name for wall/collision actors.
# NOTE (Feb 25 2026, confirmed by in-map SDK dump): EMapTaleCollisionPoint instances
# are NOT registered in GObjects even when inside a live map — only the CDO exists
# at world origin.  The NineGrid C++ subsystem manages these outside UObject/GObjects.
# The scan_wall_actors() method is kept for completeness but will always return 0
# results.  The primary wall-detection path is MinimapSaveObject (see below).
WALL_ACTOR_CLASS = "EMapTaleCollisionPoint"

# Path to per-map walkable-area cache (also used for old wall-actor cache).
# Since the 12 map layouts are predefined and never change, this JSON is permanent
# once collected — no re-scan is ever needed unless the file is deleted.
WALL_DATA_FILE = "data/wall_data.json"

# ECollisionThrough bitmask — bit 0 (0x01) = walking allowed.
COLLISION_THROUGH_WALK = 0x01

# Radius (world units) around each visited position to mark walkable in the A* grid.
#
# Connectivity requirement: 2 × radius > max_sample_spacing, so adjacent circles
# overlap and A* can pass through without hitting a blocked gap.
#
# MinimapSaveObject sampling is distance-based at ~600 world units per sample
# (confirmed from live test: 5 positions, avg consecutive spacing = 591u).
# At full character run speed (≈2000 u/s): spacing ≈ 800u (game still ~2–3 Hz).
# Worst observed single-step spacing in the test: 1096u (large open area).
#
#   radius 280u → 600u gap = 40u (0.3 cells) → barely connected at avg speed
#                 800u gap = 240u (1.6 cells) → BREAKS at top speed
#   radius 450u → 600u overlap = 300u        → solid at avg speed
#                 800u gap = -100u (overlap)  → solid at top speed
#                 900u gap = 0u              → just-touches at extreme spacing
#
# 450 is chosen so connectivity holds up to 900u sample spacing with margin.
WALL_POINT_DEFAULT_RADIUS    = 220.0
VISITED_CELL_WALKABLE_RADIUS = 150.0

# A* grid cell size in world units.
# 75 u ≈ one character width.  Resolves corridors the old 150 u grid collapsed
# into single ambiguous cells.  4× cell count → ~2× A* time, offset by the
# higher node limit and async worker thread (never blocks the tick loop).
WALL_GRID_CELL_SIZE = 75

# Half-extent fallback used when NO walkable data is available (no-cache case).
# The primary path uses data-driven bounds computed from the actual walkable
# points — see build_walkable_grid() in wall_scanner.py.
WALL_GRID_HALF_SIZE = 15000

# Extra margin (world units) added around the bounding box of walkable points
# when computing data-driven grid bounds.  One walkable radius ensures the
# circles at the edge of the data are fully contained in the grid.
WALL_GRID_MARGIN = 1500

# ── Hybrid wall-confidence overlay (v5.13.0) ───────────────────────────────
# Read by GridData/Pathfinder only when config wall_model_mode == "hybrid".
WALL_CONF_DECAY_HALFLIFE_S = 240.0
WALL_CONF_DECAY_MIN_STEP_S = 0.5
WALL_CONF_WALKABLE_STRENGTH = 1.0
WALL_CONF_BLOCKED_STRENGTH = 0.8
WALL_CONF_PENALTY_MAX = 2.0

# How many cached walkable points are considered "sufficient" data for a map.
# When the cache exceeds this threshold on map entry, the ZoneWatcher skips
# the legacy MinimapSaveObject retries (which always return 0 now) so no
# wasted scanning occurs.  The direct PosSampler thread handles new points.
MINIMAP_SCAN_SKIP_THRESHOLD = 200

# ── MinimapSaveObject (primary walkable-area source) ──────────────────────────
# MinimapSaveObject is a live GObjects singleton at /Engine/Transient that stores
# ALL world positions the player has ever visited, keyed by a numeric config ID.
# Offsets confirmed from SDK dump (Feb 25 2026, in-map dump of YJ_XieDuYuZuo200):
#   MinimapSaveObject + 0x028  = Records  TMap<FString, MinMapRecord>
#   MinMapRecord + 0x000       = Timestamp  int64
#   MinMapRecord + 0x008       = Pos        TArray<FVector>   ← visited positions
#   MinMapRecord + 0x018       = IconDataArray TArray<MapIconData>
#
# ⚠️ TMap KEY IS A NUMERIC CONFIG ID, NOT THE ZONE FNAME (confirmed Feb 25 2026):
#   In-game test showed key='5311_0' for zone 'SD_GeBuLinYingDi' (Blustery Canyon).
#   The key format is '<map_config_id>_<instance_index>' (e.g. '5311_0').
#   Zone FName matching NEVER works. The bot uses an auto-learned JSON mapping:
#   MINIMAP_KEY_MAP_FILE ('data/minimap_key_map.json') stores key → zone_fname.
#   When a new map is encountered (single entry, no cached mapping), the bot
#   auto-learns the mapping so future scans find the entry immediately.
#
# TSetElement<TPair<FString, MinMapRecord>> stride = 0xD8 bytes:
#   SDK dump (Feb 25 2026) shows MinMapRecord Size:0x0C0 (192 bytes).
#   Stride = FString(0x10) + MinMapRecord(0xC0) + HashNextId+HashIndex(0x08) = 0xD8.
#   +0x00 FString key data ptr (ulong)
#   +0x08 FString ArrayNum    (int32, includes null terminator)
#   +0x0C FString ArrayMax    (int32)
#   +0x10 MinMapRecord.Timestamp (int64)
#   +0x18 MinMapRecord.Pos data ptr  (ulong)   ← MINIMAP_RECORD_POS_PTR
#   +0x20 MinMapRecord.Pos ArrayNum  (int32)   ← MINIMAP_RECORD_POS_NUM
#   +0x24 MinMapRecord.Pos ArrayMax  (int32)
#   +0x28 MinMapRecord.IconDataArray ptr   (ulong)
#   ... (remaining MinMapRecord fields fill to 0x10+0xC0=0xD0)
#   +0xD0 HashNextId          (int32)
#   +0xD4 HashIndex           (int32)
MINIMAP_SAVE_OBJECT_CLASS  = "MinimapSaveObject"
MINIMAP_RECORDS_OFFSET     = 0x028   # TMap<FString, MinMapRecord> within MinimapSaveObject
TMAP_FSTRING_ELEM_STRIDE   = 0xD8    # bytes per TSetElement: FString(16)+MinMapRecord(192)+hash(8)
MINIMAP_FSTRING_KEY_PTR    = 0x00    # FString TCHAR* data ptr within element
MINIMAP_FSTRING_KEY_LEN    = 0x08    # FString ArrayNum (chars incl. null) within element
MINIMAP_RECORD_POS_PTR     = 0x18    # Pos TArray data ptr within element
MINIMAP_RECORD_POS_NUM     = 0x20    # Pos TArray ArrayNum within element
FVECTOR_SIZE               = 12      # 3 × float32 = 12 bytes

# Tolerance (world units) for considering a goal position "reached".
AUTO_NAV_GOAL_TOLERANCE = 300.0

# Seconds between full event re-scans during autonomous navigation.
# Covers lazy-loaded events that only appear after the player moves near them.
AUTO_NAV_EVENT_RESCAN_INTERVAL = 15.0

# Seconds between A* re-plan attempts when the current path is exhausted
# or the navigator gets stuck.
AUTO_NAV_REPLAN_INTERVAL = 8.0

# Maximum A* nodes to expand before giving up (prevents multi-second freezes
# on degenerate inputs).  At 75 u/cell a ±15 000 u map gives a 400×400 grid
# (160 000 cells).  200 000 node cap gives plenty of headroom for complex paths.
AUTO_NAV_ASTAR_MAX_NODES = 200000

# ── RTNavigator — real-time 60 Hz autonomous navigation ──────────────────────
# RTNavigator is the sole navigation system for all modes (auto + manual).
# A 60 Hz background loop reads position, steers, detects stuck, and replans
# A* on demand.  navigate_waypoints() handles recorded-path (manual) navigation.

# Loop frequency (Hz).  Position is read and cursor is moved every tick.
# 120 Hz: sub-pixel-precision steering, 2× faster stuck detection, smoother arcs.
RT_NAV_TICK_HZ = 120

# Stuck detection: if position moves < RT_NAV_STUCK_DIST world units for this
# many consecutive 120 Hz frames, trigger escape + A* replan.
# 60 frames @ 120 Hz = 500 ms — 167 ms faster reaction than the old 667 ms,
# while still absorbing brief hitches and inter-frame jitter.
RT_NAV_STUCK_FRAMES = 60
RT_NAV_STUCK_DIST   = 8.0      # world units minimum displacement per frame (halved for 120 Hz)

# Path lookahead: aim at the furthest path waypoint within this distance that
# passes the DDA line-of-sight check.  Produces smooth arcs through corners.
RT_NAV_LOOKAHEAD_DIST = 800.0  # world units

# Advance past a path waypoint when within this radius (prevents stuttering
# at tight corners caused by over-precision waypoint arrival checks).
RT_NAV_WAYPOINT_RADIUS = 200.0  # world units

# Phase goal reached when distance drops below this value.
RT_NAV_GOAL_RADIUS = 280.0  # world units

# Periodic safety-net A* replan interval (seconds).  A replan is also
# triggered immediately on goal change, on stuck detection, and on path
# drift > RT_NAV_DRIFT_THRESHOLD.  Bumped to 8 s since deviation detection
# now handles the fast-response case.
RT_NAV_REPLAN_INTERVAL = 8.0

# Goal-progress stuck detection: fires when the distance to the active goal
# has NOT decreased by RT_NAV_PROGRESS_MIN world units within
# RT_NAV_PROGRESS_TIMEOUT seconds — even if the character is physically moving
# (e.g. wall-sliding at full speed, which raw per-frame displacement misses).
RT_NAV_PROGRESS_TIMEOUT = 4.0    # seconds without meaningful goal progress
RT_NAV_PROGRESS_MIN     = 300.0  # world units improvement needed to reset timer

# Monster detour: if ≥ RT_NAV_MONSTER_MIN_COUNT alive monsters are within
# RT_NAV_MONSTER_RADIUS world units AND more than 45° off the heading to the
# phase goal, temporarily detour to walk through the cluster (auto-bomber).
RT_NAV_MONSTER_RADIUS    = 2500.0   # world units
RT_NAV_MONSTER_MIN_COUNT = 5        # minimum alive monsters to trigger detour

# Screen-pixel radius for escape cursor placement (same as legacy StuckDetector).
RT_NAV_ESCAPE_DIST = 340  # pixels

# Heading buffer: last N per-frame displacement vectors averaged to determine
# the character's actual heading when a stuck event fires.  Used by wall-aware
# escape (perpendicular escape) and by real-time wall learning (mark cell ahead).
RT_NAV_HEADING_BUF_SIZE = 24   # frames (~200 ms at 120 Hz)

# Non-blocking escape: the escape state machine steers toward the escape target
# for this many seconds instead of blocking the 60 Hz loop with time.sleep().
RT_NAV_ESCAPE_DURATION_S = 0.55  # seconds

# Path-deviation detection: if the player drifts more than this many world units
# perpendicular to the current path segment, trigger an immediate A* replan.
# Replaces the old 3 s periodic replan as the primary staleness detector.
RT_NAV_DRIFT_THRESHOLD = 500.0   # world units

# Learned walls persistence file.  Blocked cells inferred from stuck events are
# saved per-map so the A* grid improves across runs even without new PosSampler
# data.  Separate from wall_data.json (which stores walkable circles).
LEARNED_WALLS_FILE = "data/learned_walls.json"

# ── MapExplorer (automatic walkable-area data collection) ─────────────────────
# MapExplorer drives the character through random locations in the current map
# for a fixed duration.  The zone watcher runs in parallel and passively saves
# MinimapSaveObject positions — so exploration is entirely automatic.
#
# Strategy: pick a random world position within ±EXPLORE_RADIUS units of the
# spawn point, navigate toward it with a short per-target timeout.  On timeout
# or arrival, immediately pick a new random target.  This produces maximum
# coverage because:
#   • Short timeout (15 s) → ~20 different sectors attempted per 5-min session
#   • Random spread → different areas per target
#   • Navigator's existing stuck detection + escape tries 8 angles when blocked

# World-unit radius around player spawn used to pick random exploration targets.
# Set to 20 000 so the explorer can reach far corners of any 30 000 × 30 000
# map, even when the spawn point is near one edge.
MAP_EXPLORER_RADIUS = 20000.0

# Per-target navigation timeout (seconds).  Short enough to visit many sectors;
# long enough to traverse a significant portion of the map at run speed.
MAP_EXPLORER_TARGET_TIMEOUT_S = 25.0

# Default total exploration duration (seconds).
MAP_EXPLORER_DURATION_S = 300.0

# If the player has not moved more than this many world units in this many
# seconds, the explorer forces an escape (tries a far-away cardinal target).
MAP_EXPLORER_GLOBAL_STUCK_DIST  = 400.0   # world units
MAP_EXPLORER_GLOBAL_STUCK_TIME  = 30.0    # seconds

# Number of random candidate positions generated when picking the next target.
# The candidate with the greatest minimum distance to all previous targets is
# chosen (Maximin / farthest-point strategy) so repeated picks naturally
# spread across the map instead of clustering in one area.
MAP_EXPLORER_CANDIDATES = 8

# When an existing walkable grid is available the explorer uses frontier-guided
# targeting instead of purely random picks.  A "frontier" position is a blocked
# cell (unexplored) that is adjacent to at least one walkable cell — i.e. the
# boundary of the known map.  The explorer navigates to these boundary positions,
# the character overshoots slightly into the unknown, and the PosSampler records
# the new territory.
#
# FRONTIER_CANDIDATES controls how many frontier positions are sampled per pick
# to find the Maximin winner (maximally far from all previous targets).
# Higher = better spread; diminishing returns beyond 64.
MAP_EXPLORER_FRONTIER_CANDIDATES = 32

# Recompute frontier/grid periodically during exploration so newly sampled
# positions influence target picking in the same session (live updates).
MAP_EXPLORER_FRONTIER_REFRESH_S = 0.5   # frontier-only rescan from in-memory grid
MAP_EXPLORER_GRID_REBUILD_S     = 3.0   # full disk-read + grid rebuild (≥ flush interval)

# Completion-driven explorer termination (used when duration_s is None):
# stop only after frontier remains empty and coverage gain stays below threshold
# for a sustained window.
MAP_EXPLORER_COMPLETE_STABLE_S = 18.0
MAP_EXPLORER_COMPLETE_MIN_GAIN = 25

# Dynamic coverage-percentage estimate uses frontier count to infer remaining
# unexplored area. Larger multiplier makes % more conservative early on.
MAP_EXPLORER_FRONTIER_ESTIMATE_MULTIPLIER = 6.0

# Direct position sampling for walkable-area data collection.
# The explorer samples player X/Y every SAMPLE_DIST world units moved and saves
# the positions directly to wall_data.json — no MinimapSaveObject dependency.
# SAMPLE_DIST must be < 2 × VISITED_CELL_WALKABLE_RADIUS to guarantee that
# adjacent circles overlap and A* can path between them.
MAP_EXPLORER_POSITION_SAMPLE_DIST  = 25.0   # world units between saved samples (denser for 75 u grid)
MAP_EXPLORER_POSITION_POLL_S       = 0.016  # position check interval (~60 Hz)
MAP_EXPLORER_POSITION_FLUSH_EVERY  = 100    # flush to disk after this many new points
MAP_EXPLORER_POSITION_FLUSH_S      = 3.0    # or after this many seconds, whichever first

# Explorer fail-fast anti-idle guard.
# During exploration, any prolonged no-progress movement is pure time loss
# (no new sampled positions → no new grid coverage).  Abort the current target
# quickly so the explorer can retarget instead of bumping walls.
MAP_EXPLORER_NO_PROGRESS_TIMEOUT_S = 0.70   # seconds without meaningful movement
MAP_EXPLORER_NO_PROGRESS_DIST      = 90.0   # world units considered meaningful progress

# How many consecutive non-map ZoneWatcher readings before declaring map exit.
# Prevents inventory / pause / brief UI flicker from triggering a false exit.
ZONE_WATCHER_EXIT_THRESHOLD = 2
