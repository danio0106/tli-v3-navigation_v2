# Torchlight Infinite Bot V2 — New Agent Catchup Prompt

## What This Project Is

A fully autonomous Python desktop bot for "Torchlight Infinite" (an ARPG built on Unreal Engine 4). The bot automates map farming — repeatedly entering maps, killing monsters, completing in-map events, and exiting. Target: 45 maps/hour (80-second cycle). It's a university bachelor's project where every line of code must be AI-written. The bot targets the "auto-bomber" build where skills auto-cast and the character follows the cursor automatically.

**This is a Windows desktop application** — it reads game memory via `pymem`, captures the screen via `mss`, and simulates input via `ctypes/win32api`. It runs alongside the game on the user's Windows PC. The Replit environment is used only for code editing; the bot runs on Windows.

## Current Version: v2.9.0

## Critical Files to Read First

1. **`replit.md`** — Full architecture, all UE4 memory offsets, SDK dump reference, TODO list
2. **`CHAT_LOG.md`** — Complete development history with every finding, measurement, and decision (1350+ lines)
3. **`src/utils/constants.py`** — All hardcoded offsets, positions, thresholds, hotkeys
4. **`src/core/scanner.py`** — Memory scanning (GWorld, FNamePool, GObjects, event detection)
5. **`src/core/memory_reader.py`** — Low-level memory read helpers, FName resolution, GObjects search
6. **`src/core/bot_engine.py`** — State machine (the brain of the bot)

## Project Structure

```
main.py                          # Entry point, APP_VERSION, console positioning
src/
  core/
    scanner.py                   # GWorld/FNamePool/GObjects sig scanning, event scanning
    memory_reader.py             # pymem wrapper, pointer chains, FName resolution, GObjects search
    bot_engine.py                # State machine: IDLE→IN_HIDEOUT→OPENING_MAP→SELECTING_MAP→ENTERING_MAP→IN_MAP→NAVIGATING→RETURNING→MAP_COMPLETE
    navigator.py                 # Waypoint following, stuck detection, cursor steering
    path_recorder.py             # Record/save/load waypoint paths per map
    map_selector.py              # Card detection, rarity classification, map device interaction
    card_detector.py             # Vertex-based active card detection, glow chevron rarity
    portal_detector.py           # FightMgr MapPortal enumeration
    scale_calibrator.py          # Per-map 2x2 matrix calibration (world↔screen)
    game_state.py                # Reads player position, zone info from memory
    screen_capture.py            # mss-based fast screen capture
    input_controller.py          # Windows input simulation (mouse, keyboard)
    window_manager.py            # Game window detection, focus, rect
    hex_calibrator.py            # Template-matching card position finder
    address_manager.py           # Manages resolved memory addresses
  gui/
    app.py                       # Main GUI window (customtkinter), tab management, overlay feed
    overlay.py                   # Transparent debug overlay drawn over game window
    theme.py                     # Dark theme colors
    tabs/
      dashboard_tab.py           # Status display, zone info, bot controls
      address_manager_tab.py     # Memory address display, scan buttons, event explorer
      paths_tab.py               # Waypoint list editor, recording controls
      settings_tab.py            # Configuration options
  utils/
    constants.py                 # ALL offsets, positions, thresholds, hotkeys
    config_manager.py            # JSON config persistence
    logger.py                    # Logging setup
data/
  map_starting_positions.json    # Spawn positions for all 12 maps
  zone_name_mapping.json         # Chinese FName → English map name mappings
  map_calibrations.json          # Per-map 2x2 transformation matrices
assets/
  glacial_abyss_text.png         # Template for region selection detection
  card_ui_text.png               # Template for card UI detection
```

## How Memory Reading Works

The bot reads the game's memory directly using `pymem`. The game is UE4, so we follow standard UE4 pointer chains:

1. **GWorld** — Found via sig scan of the game's .exe. Cached. Starting point for everything.
2. **Player Position** — `GWorld → +0x210 → +0x038 → [0] → +0x030 → +0x250 → +0x130 → +0x124` (3 floats: X, Y, Z)
3. **FNamePool** — Sig scanned or manually set. Resolves FName indices to strings. Used for zone names, class names, object identification.
4. **GObjects** — Sig scanned. Global object array. Used to find specific UE4 objects by class name (EGameplay, ECfgComponent, UFightMgr, etc.)
5. **Zone Name** — `GWorld → deref → UWorld → +0x18` (FName index) → FNamePool lookup → Chinese internal name → mapped to English via `zone_name_mapping.json`
6. **Portals** — Find UFightMgr via GObjects → MapPortal TMap at +0x440 → enumerate portal entities
7. **Events** — Find EGameplay instances via GObjects class search → match ECfgComponent children by Outer pointer → read positions via RootComponent+0x124

## The Big Unsolved Problem: Event Type Identification

The bot needs to detect and complete two in-map events: **Sandlord** and **Carjack**. The user forces both events to spawn at 100% rate via map points.

### What Works
- We CAN find all EGameplay instances in memory
- We CAN read their world positions via RootComponent+0x124
- **Carjack position IS reliably readable** — confirmed across multiple maps by manual overlay verification
- Completed events get destroyed from GObjects (count drops)
- Zone name recognition works via FName reading

### What Doesn't Work — Event Type Identification
We CANNOT tell which EGameplay instance is Sandlord vs Carjack vs phantom. Every approach tried has failed:

| Approach | Result |
|----------|--------|
| CfgInfo.ID at ECfgComponent+0x120 | Always zero at runtime |
| Sub-object at EGameplay+0x4E0 | Always EMsgComponent (identical) |
| Pointer at EGameplay+0x320 | Points back to own ECfgComponent |
| Wave counter at +0x618 | Global/shared across all instances |
| Spawn index at +0x714 | Sequential numbering, not type-based |
| FName of EGameplay | Just "EGameplay" for all |
| Class name | Just "EGameplay" for all |

### What We Know About Events
- Always 3 EGameplay instances per map (in tested configs with 1-2 events forced)
- **Phantom events exist** — one instance at (650, 750) in Wall of Last Breath had a real position but was invisible and non-interactive
- Sandlord position stays at (0,0,0) — may not populate until player physically steps on it (step-on activation, no F key prompt)
- Carjack requires F key interaction and has a readable position
- Stale events can persist across maps

### Unexplored Avenues (prioritized)
1. **ueComponents TMap at EGameplay+0x288** — could contain typed sub-components beyond ECfg and EMsg
2. **ECfgComponent index at UObject+0x0C** — different per instance, unknown significance
3. **EGameplay FName number at UObject+0x1C** — UE4 instance numbering might correlate with type
4. **Alternative approaches** — screen-based detection, minimap markers, proximity/reactive detection

## Game Mechanics You Must Know

- **Auto-bomber build**: Skills auto-cast. Character follows cursor automatically after one right-click at map start. NEVER right-click again — move cursor to character center (952, 569) to stop.
- **"F" key** for ALL interactions (portals, NPCs, map device, Carjack event)
- **"E" key** for loot pickup (spam during map runs)
- **Card selection**: 3 active cards appear → pick one → it becomes the only active card for 1-4 remaining attempts, shuffling position each time. Bot must NEVER click inactive cards (triggers penalty exploration run = 2 wasted cycles).
- **Card rarity priority**: Rainbow > Orange > Purple > Blue
- **Sandlord**: Activates by stepping on it (no F key). Waves of enemies spawn. Counter at +0x618 tracks waves globally.
- **Carjack**: Requires F key interaction. Position is readable from memory.
- **Map device flow**: Open with F → select Glacial Abyss region → detect active cards → pick highest rarity → add affixes (5 blind clicks) → Open Portal → enter map
- **Isometric camera**: World axes are rotated/swapped differently per map. Requires per-map 2x2 matrix calibration.

## Calibration System

Each map needs a 2x2 transformation matrix to convert between world coordinates and screen pixels. The calibrator:
1. Moves cursor to character center (stops movement)
2. Left-clicks 300px to the right, waits for character to walk there
3. Records world delta = "screen_right" vector in world space
4. Repeats for 300px downward = "screen_down" vector
5. These two vectors form the transformation matrix
6. Saved per map in `data/map_calibrations.json`

**Status**: Calibrator built (v2.5.2) but only a few maps have been calibrated. User needs to run through remaining maps.

## Version Rules
- Every commit bumps version in `main.py` APP_VERSION
- Use semantic versioning: v2.X.0
- Log version at startup

## User Communication Style
- User provides precise pixel measurements using a color picker tool (gives x, y, hue, sat, val, lit, gray)
- User runs the bot on their Windows machine and reports results via chat
- User can provide SDK dumps (Guided Hacking UE Dumper v5.0.0, latin-1 encoding)
- User tests by entering maps manually and clicking "Scan Events" / "Calibrate Scale" buttons
- Save ALL findings, measurements, and decisions to CHAT_LOG.md
- Log everything to replit.md for cross-session persistence

## TODO for Next Session (Feb 24, 2026)

1. **Event detection — new approach**: Try alternative strategies for identifying which EGameplay is Sandlord vs Carjack (or abandon type identification and use reactive detection)
2. **Carjack navigation**: Since position IS readable, integrate navigation-to-Carjack into bot engine
3. **Sandlord strategy**: Design detection that doesn't rely on position (0,0,0 problem)
4. **Matrix calibration for all 12 maps**: Need fresh calibrations on v2.5.2
5. **Zone name mapping**: Complete auto-learning mappings for remaining maps

## LSP Note
`src/gui/app.py` has 29 LSP diagnostics — these are pre-existing and not from recent changes. The file works correctly at runtime on Windows despite the warnings (many are from Windows-specific imports that don't resolve in the Linux dev environment).
