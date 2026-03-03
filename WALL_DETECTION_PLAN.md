# Wall Detection + Auto-Navigation Plan

> **Status:** Research complete, awaiting implementation approval.  
> **Session date:** Feb 24–25, 2026  
> **Next step:** User opens new session to approve and implement.

---

## User Prompt (Feb 24 2026)

> "Check chat_log.md, check copilot-instructions.md and read the latest changes. Now that we have quite correctly working basics of the bot, I was thinking about something more advanced. Could you try to detect walls somehow? Torchlight has predefined layouts of 12 maps + hideout (it has more but we wont be using others), if we could detect walls with memory reading or somehow else reliably then we could start building navigation system that would auto explore map with given goals, like kill all monsters or rush towards boss or rush carjack and sandlord events and then boss and leave. We have still sdk dump files in 'moje' directory, try using that, maybe it can be valuable or whatever you think can work. Now this is a huge implementation so do not implement it without my specific request, let me first revise the plan if needed."

Follow-up:

> "Its because since your session started I merged another different PR and you're trying to adjust same files. I will open new session after that to consider implementation of this big system overhaul. Since your session there was multiple PRs merged already so to avoid file conflicts can you log all the findings and entire prompts conversation to different file instead of chat_log.md?"

---

## SDK Dump Research Findings

Thorough analysis of `moje/[torchlight_infinite] Objects Dump.txt` and `moje/[torchlight_infinite] Names Dump.txt` revealed the following game-specific systems directly relevant to wall detection and navigation.

---

### 1. EMapTaleCollisionPoint — The Wall Actor System

The game uses a **completely custom wall/collision system** separate from UE4's standard physics collision.

**Class:** `/Script/UE_game.EMapTaleCollisionPoint`
- Instance size: `0x728` (1832 bytes)
- Base: `EEntity` — same `AActor` subclass as `EGameplay` events — **findable in GObjects the same way**
- Default components (confirmed from Objects Dump):
  - `UserEntity` — `SceneComponent` (root; holds world position at `+0x124`)
  - `EMapTaleCollision` — `EMapTaleCollisionComponent` (wall shape/collision data)
  - `EMsg` — `EMsgComponent` (unrelated message component)

**Class:** `/Script/UE_game.EMapTaleCollisionComponent`
- Instance size: `0x128` (296 bytes)
- ⚠️ **NO reflected properties in SDK dump** — fields are pure C++, not Blueprint-exposed
- Contains: wall shape data (box/polygon extents, rotation, collision type flags)

**EActorType enum has `E_mapTaleCollisionActor` (value 5)** — wall actors can be found in GObjects by class name scan, exactly like `FightMgr` and `EGameplay`.

**Position read path** (same pattern as all EEntity subclasses):
```
actor_ptr → +0x130 (RootComponent ptr) → +0x124 (RelativeLocation: X, Y, Z as 3× float32)
```

---

### 2. ECollisionThrough — Wall Type Bitmask

Each wall has an `ECollisionThrough` byte that is a bitmask of which movement types can pass through it:

| Flag | Value | Meaning |
|---|---|---|
| `E_none` | 0x00 | Nothing passes (**solid wall**) |
| `E_walk` | 0x01 | Walking passes |
| `E_charge` | 0x02 | Charge/dash passes |
| `E_jump` | 0x04 | Jump passes |
| `E_blink` | 0x08 | Blink/teleport passes |
| `E_bullet_normal` | 0x10 | Normal bullets pass |
| `E_bullet_parabolic` | 0x20 | Parabolic bullets pass |
| `E_bullet_floor` | 0x40 | Floor bullets pass |
| `E_view` | 0x80 | Line of sight passes |
| **`E_high_wall`** | **0x00** | **Solid — nothing passes** |
| **`E_medium_wall`** | **0x20** | **Parabolic bullets only** |
| **`E_low_wall`** | **0xBC** | **Walk/blink/bullets pass** |
| **`E_edge_wall`** | **0xF0** | **Bullets only** |
| **`E_blink_wall`** | **0x8C** | **Blink + bullets only** |
| **`E_all`** | **0xFF** | **Completely passable (open floor marker?)** |

For navigation purposes: walls with `(value & E_walk) == 0` are **impassable on foot** → block cells in the A* grid.

---

### 3. EWayfindingResult + EAStarCheckCollisionType — The Game Has Its Own A*

```
EWayfindingResult:
  E_none, E_success_dda, E_success_astar, E_failed, E_to_pos_after_failed

EAStarCheckCollisionType:
  E_check_all, E_only_static, E_as_entity

EAStarOpenType:
  E_none, E_ByFrom, E_ByTo
```

The game implements its **own A\* pathfinding** (not UE4 NavMesh) with:
- **DDA (Digital Differential Analyzer)** — ray-cast for fast line-of-sight checks (`E_success_dda`)
- **Full A\*** — for complex paths around obstacles (`E_success_astar`)

The grid it operates on is built from `EMapTaleCollisionPoint` actors. We can replicate this externally once we have the same wall data.

---

### 4. NineGridSceneDataTableRow — Map Grid Metadata (**CRITICAL**)

```
ScriptStruct /Script/NineGridWorld.NineGridSceneDataTableRow:
  +0x008  MapID        (int32)     — unique ID per map
  +0x010  MapName      (FString)   — internal map name
  +0x020  MinPosition  (Vector2D)  — map boundary minimum (world X, Y)
  +0x028  MaxPosition  (Vector2D)  — map boundary maximum (world X, Y)
  +0x030  GridSize     (int32)     — cell size in world units
  +0x038  SublevelPath (FString)   — path to sub-level asset
```

This table gives **exact map boundaries and cell size for every map** — the foundation of a grid-based navigation system. Lives in a `NineGridDataTable` object findable in GObjects.

With `MinPosition`, `MaxPosition`, and `GridSize` we can compute the exact 2D grid dimensions for any map without any manual measurement.

---

### 5. MinimapSaveObject — Live Explored-Area Record

Live singleton in GObjects: `/Engine/Transient.MinimapSaveObject_2147305245`

```
MinimapSaveObject:
  +0x028  Records  TMap<FString, MinMapRecord>

MinMapRecord:
  +0x000  Timestamp     (int64)
  +0x008  Pos           (TArray<Vector>)  — array of visited world positions
  +0x018  IconDataArray (TArray<MapIconData>)
```

Not useful for wall detection directly, but `Pos` array can confirm which cells have been visited (walkable area exploration tracking).

---

### 6. MiniMapSave — Raw Minimap Byte Data

```
Class MiniMapSave:
  +0x028  MinMapData  (TArray<uint8>)  — raw minimap image bytes
```

Likely the minimap rendered as a flat byte array encoding road/wall pixels. Combined with `EMinimapWidget.WallColor` (RGBA) and `EMinimapWidget.RoadColor` (RGBA) — both reflected fields — this could decode to a walkable pixel grid.

---

### 7. EGameMasterCommand: E_add_mapgrid / E_remove_mapgrid

Server can add/remove grid cells at runtime. The navigation grid is **not fully static** — destructible walls and opened passages can change it during a map run.

---

### 8. QAHelper.GetCurrentLevelWalkableArea

A leftover QA/debug function returning an integer for the current level's walkable area count. Confirms the game internally tracks walkable cell count.

---

## Proposed Implementation Plan (3 Phases)

---

### Phase 1 — One-Time Wall Data Collection per Map

**Goal:** For each of the 12 maps, scan all `EMapTaleCollisionPoint` instances from GObjects and save wall data to a JSON file (`data/wall_data.json`). Only needs to be done once per map since layouts are predefined.

**Implementation steps:**
1. Scan GObjects for objects whose class name == `"EMapTaleCollisionPoint"`
2. For each found actor:
   - Read position: `actor_ptr → +0x130 → +0x124` (X, Y, Z float32)
   - Attempt to read `EMapTaleCollisionComponent` sub-object for wall bounding box shape (runtime exploration needed since no SDK-reflected fields)
   - Fallback: use actor center position as a point obstacle (~200 units radius) if shape data unreadable
3. Read `NineGridSceneDataTableRow` from `NineGridDataTable` in GObjects for map boundary and cell size
4. Save as `data/wall_data.json` keyed by English map name:
   ```json
   {
     "Swirling Mines": {
       "grid_size": 100,
       "min_x": -15000, "min_y": -15000,
       "max_x": 15000, "max_y": 15000,
       "walls": [
         {"x": -5420, "y": 3100, "half_w": 200, "half_h": 50, "type": "high_wall"},
         ...
       ]
     }
   }
   ```
5. **Trigger:** Automatically in a background thread ~5s after zone load, only when no saved wall data exists for the current map
6. **Overlay:** Draw walls as orange/red line segments or boxes on the debug overlay for immediate visual confirmation

**What user needs to do:**
- Enter each of the 12 maps once with the bot running — wall data collects automatically
- Confirm overlay looks correct (walls drawn in expected positions)

---

### Phase 2 — Grid Construction + A* Pathfinding

**Goal:** Build a 2D boolean walkability grid from wall data and run A* over it to generate paths between any two world positions.

**Implementation:**
1. Load wall data for current map → compute grid dimensions from `NineGridSceneDataTableRow` bounds + `GridSize`
2. For each wall entry: mark all grid cells overlapping the wall bounding box as `blocked = True`
3. A* algorithm:
   - Open set (min-heap by f-score), closed set
   - Diagonal movement (8-directional), cost 1.0 ortho / 1.414 diagonal
   - Heuristic: octile distance (optimal for 8-dir grid)
   - Returns list of world-coordinate waypoints (cell centers)
4. Path smoothing: DDA ray-cast to remove redundant intermediate waypoints (same technique the game itself uses)
5. Hook into `navigator.py`: when a dynamic A* path is available for the current target, use it instead of hand-recorded waypoints

---

### Phase 3 — Goal-Directed Navigation (the "brain")

**Goal:** Bot autonomously picks navigation targets based on current map objective.

**Available goals:**

| Goal | How | Data source |
|---|---|---|
| **Rush Events** | Navigate to nearest Carjack/Sandlord position | `FightMgr.MapGamePlay` (already read) |
| **Rush Boss** | Navigate to boss spawn area | Per-map predefined coordinates (to be recorded) |
| **Explore All** | Systematic flood-fill of entire walkable area | A* grid + visited-cell tracking |
| **Rush Exit** | Navigate to portal | `FightMgr.MapPortal` (already read) |

**Priority order:** Events (sorted by distance) → Boss → Exit portal

**Re-planning:** Re-run A* every 5s or when a new event appears in `MapGamePlay` (lazy-loaded events on approach).

---

## Recommended Approach for Wall Shape Data

Since `EMapTaleCollisionComponent` has no SDK-reflected fields, three approaches are ranked by precision:

### Option A — Runtime Memory Exploration (Best, most precise)
- Find a live `EMapTaleCollisionPoint` instance in GObjects
- Dump the 0x128 bytes of its `EMapTaleCollision` sub-object
- Look for float patterns consistent with box extents: positive values in range 50–5000 (plausible half-widths in world units)
- Find the `ECollisionThrough` byte — will be exactly one of: `0x00`, `0x20`, `0xBC`, `0xF0`, `0x8C`, `0x8F`, `0xFC`, `0xFF`
- Result: center position + (half_w, half_h, half_h_z) + wall type

### Option B — Point Obstacles Only (Simple fallback)
- Treat each `EMapTaleCollisionPoint` center as a ~200-unit radius circle obstacle
- Mark all grid cells within that radius as blocked
- Less precise (may block some narrow passages) but sufficient for A* at 100-unit grid resolution
- Zero runtime exploration needed — just position reads

### Option C — Minimap Pixel Color Decode (Easiest, lowest precision)
- Read `EMinimapWidget.WallColor` (RGBA) and `EMinimapWidget.RoadColor` (RGBA) from memory
- Screen-capture the in-game minimap region or read `MiniMapSave.MinMapData` byte array
- Threshold by color: wall pixels → blocked, road pixels → walkable
- Map pixel coordinates to world coordinates using existing `MapCalibration` scale matrix
- Resolution: ~5–10 meters per pixel (sufficient for approximate navigation, rough for tight corridors)

---

## Files That Would Change (when implemented)

| File | Change type | Description |
|---|---|---|
| `src/core/scanner.py` | Add method | `scan_wall_actors()` — enumerate GObjects for `EMapTaleCollisionPoint` |
| `src/core/wall_scanner.py` | **New module** | Wall data collection, `NineGridDataTable` read, grid construction, A* algorithm |
| `data/wall_data.json` | **New file** | Saved wall positions/shapes per English map name |
| `src/core/bot_engine.py` | Extend | Add goal system + dynamic A* path planning |
| `src/gui/overlay.py` | Extend | Draw wall segments/boxes on debug overlay |
| `src/utils/constants.py` | Add constants | `EMapTaleCollisionPoint` class name, `NINE_GRID_DATA_TABLE` GObjects path |

**Estimated implementation scope:** ~600–800 lines across 4–5 files.

---

## Key Constants (for reference when implementing)

```python
# GObjects class name to scan for wall actors
WALL_ACTOR_CLASS = "EMapTaleCollisionPoint"

# ECollisionThrough bitmask values
COLLISION_THROUGH_WALK   = 0x01
COLLISION_THROUGH_BLINK  = 0x08
COLLISION_HIGH_WALL      = 0x00   # impassable (nothing passes)
COLLISION_LOW_WALL       = 0xBC   # walk/blink/bullets pass
COLLISION_PASSABLE       = 0xFF   # open floor

# NineGridSceneDataTableRow offsets (from ScriptStruct dump)
NINE_GRID_ROW_MAP_ID        = 0x008  # int32
NINE_GRID_ROW_MAP_NAME      = 0x010  # FString
NINE_GRID_ROW_MIN_POSITION  = 0x020  # Vector2D (float32 x2)
NINE_GRID_ROW_MAX_POSITION  = 0x028  # Vector2D (float32 x2)
NINE_GRID_ROW_GRID_SIZE     = 0x030  # int32
```
