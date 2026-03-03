# Torchlight Infinite Auto-Player Bot V2

---

## ⚠️ CRITICAL: User Testing Artefacts Location

**All user-uploaded files go to the `moje/` directory.** This includes:
- **Bot log files** from manual in-game tests (e.g. `bot_20260225_170110.log`)
- **SDK dump files** from the SDK Dumper tool (e.g. `[torchlight_infinite] Objects Dump.txt`)

When the user says "I uploaded a new log" or "I pushed new SDK files", always check `moje/` first and sort by modification time.

##  ⚠️ MOST IMPORTANT: Overview
This project is a Python desktop bot for "Torchlight Infinite," designed to automate gameplay through direct memory reading for a fully automated map running experience. The primary goal is to achieve 45 map runs per hour, with each map cycle completing in approximately 80 seconds. To be more precise the primary goal of the project is to prove that AI driven automation can efficiently handle repetitive gameplay tasks and optimize in-game performance to be as efficient as an avarage player. Efficiency of the bot compared to avarage player will be examined by examinators, we don't know exactly what will be measured, but we expect it to be evaluated based on speed, accuracy and loot gathered per hour. Reliability is also key aspect, bot needs to be stable and resilient to in-game events for at least 4 hours without interruption, examinators may interfere the bot with unexpected in-game events such as disconnections of internet connection. It serves as a university project demonstrating reverse engineering, state machine architecture, and desktop GUI development, specifically targeting the auto-bomber build. The bot utilizes memory reading with pre-analyzed offsets from a UE4 SDK class dump for accuracy and stability.

## ⚠️ CRITICAL: How To Start Every New Agent Session

**Before doing anything else, every new agent session MUST:**
1. Read this file (`.github/copilot-instructions.md`) fully — it is the single technical reference containing architecture, offsets, constraints, design decisions, and maintenance procedures
2. Read `CHAT_LOG.md` fully — it contains the full conversation history between the user and every previous agent, including all findings, failed attempts, and next steps
3. Do NOT read `AGENT_PROMPT.md` for code direction — it is a static onboarding snapshot that is intentionally never updated

---

## ⚠️ CRITICAL: User Role Constraint (University Project Requirement)

**The user CANNOT make code or architecture decisions.** This is a hard constraint from the university project rules — the examiners must see that all technical decisions are made by the AI, not steered by the student.

**What the user CAN do:**
- Run the bot on their Windows machine and observe its behavior
- Test features in-game and report what they saw (e.g. "the bot did X when it should do Y")
- Produce SDK dump files using their dumper tool and upload them to `attached_assets/`
- Report bugs and inefficiencies discovered through gameplay testing
- Approve or request revision of a **plan description** (behavior/outcome level, not implementation level)

**What the user CANNOT do:**
- Choose between implementation approaches (e.g. "use method A vs method B")
- Answer questions about code architecture, data structures, or algorithms
- Read code and make decisions based on it — they may view code passively but must not reason from it

**Consequences for the AI agent:**
- Never ask the user code-level questions (e.g. "should I replace X with Y?", "which offset should I use?")
- When planning, describe behavior and outcomes only — not how they are implemented
- All ambiguous implementation decisions must be resolved by the AI using SDK dumps, CHAT_LOG.md context, and technical reasoning
- If genuinely blocked and need user input, ask only about **observable game behavior** or **request specific new dump files**

---

## ⚠️ CRITICAL: Session Memory System

There is **no persistent memory between agent sessions**. The full project memory lives in two files:

- **`.github/copilot-instructions.md`** — Technical reference: architecture, offsets, constraints, current known issues, and maintenance procedures.
- **`CHAT_LOG.md`** — Annotated session history organised **by version number**, not by raw conversation transcript. Each session entry should contain: version number, summary of what changed, key technical findings, confirmed/denied hypotheses, and any important user-provided data (measurements, in-game confirmations, test results). Do NOT append full verbatim conversation — write a compact annotated summary that preserves all technically useful information in as few words as possible.

**Both files must be updated at the end of every session** before committing. Future agents reconstruct all context from these two files alone.

**Mandatory end-of-session checklist before any commit:**
1. Add an annotated summary entry (by version) to `CHAT_LOG.md` — include: version tag, what changed, new findings, confirmed facts, any user measurements or in-game confirmations, and the next required test/action if any. Do NOT copy-paste full conversation.
2. Update `.github/copilot-instructions.md` with any new technical findings, changed offsets, or revised design decisions
3. Never commit without completing both steps above

**Repository policy (effective 2026-03-03):** `replit.md` is removed. Do not recreate it. Keep all technical memory in this file and `CHAT_LOG.md` only.

---

## Versioning
- Every commit must bump the version number (in main.py APP_VERSION and window title)
- Use semantic versioning: v2.X.0 for each change set
- Current version tracked in main.py as APP_VERSION constant

## User Preferences
- Dark themed UI (GitHub-dark inspired palette, Segoe UI font family)
- "F" key for ALL interactions (portals, map device, NPCs)
- "E" key for loot pickup (spam during map runs)
- Global hotkeys: F5 (Start/Stop Recording), F6 (Pause/Resume Recording), F9 (Start Bot), F10 (Stop Bot), F11 (Pause/Resume Bot), P (Mark Portal waypoint during recording)
- Minimap is in top-LEFT corner of screen
- Window title bar offset: (1, 31)
- User's pixel color tool provides: x,y coords + hue,sat,val,lit,gray for clicked pixels
- Player character center (feet): (952, 569) client area coordinates — used for stopping movement
- Right-click ONCE at map start to initiate movement (character then follows cursor automatically). Avoid additional right-clicks in-map; move cursor to character center to stop movement. **Exception:** during exit-portal icon click flow (Pirates shifted layout), a single right-click at character center is allowed immediately before left-clicking the portal icon to prevent drift while aiming.
- Hideout login spawn position: (1506, -2421)

## System Architecture
The bot is structured around a `main.py` entry point, with core logic in `src/core/`, a GUI in `src/gui/`, and utility functions in `src/utils/`.

**Core Components:**
- **Memory Reading:** Handles game memory access, translates raw memory into game objects, and manages address scanning using pre-verified UE4 offsets.
- **Bot Engine:** Implements a state machine to manage the entire map cycle (IDLE, STARTING, IN_HIDEOUT, OPENING_MAP, SELECTING_MAP, ENTERING_MAP, IN_MAP, NAVIGATING, RETURNING, MAP_COMPLETE).
- **Navigation:** Manages waypoint navigation, stuck detection, and path recording, continuously reading player position and steering the cursor.
- **Navigation Planning Split (v5.1.0):** planning and execution are separated. `GoalProvider` implementations decide next map-cycle targets (events, boss, portal) and `TaskNavigator`/`RTNavigator` execute movement.
- **Navigation Drift Stabilization (v5.7.0):** drift-triggered replans in `RTNavigator` are gated by movement speed and evaluated against a short multi-segment path window. When character speed is normal, drift no longer forces immediate replans (prevents open-area back-and-forth oscillation).
- **Navigation Cache Safety Rollback (v5.8.0):** persisted SLAM `blocked` points in `data/wall_data.json` are ignored during cache grid build, and stuck-detected hard walls are no longer persisted to JSON (runtime-only). Active monster-position SLAM injection into wall cache is disabled. This prevents start-of-map `A* found no path` loops and overlay path-loss regressions caused by cumulative cache poisoning.
- **Portal Semantic Overlay Markers (v5.9.0):** `PortalDetector` now exposes `get_portal_markers()` with explicit `is_exit` metadata (`{"x","y","portal_id","is_exit"}`). Exit classification is memory-backed by pointer equality with `_exit_portal` (not inferred in overlay). `App` overlay feed prefers marker API, and `DebugOverlay` renders exit portals distinctly (blue diamond + `Exit N`) while normal portals remain red triangles (`Portal N`).
- **Portal-Hop Fallback Routing (v5.10.0):** `RTNavigator` replanning now attempts a one-hop route via reachable in-map portals when direct A* to the current goal fails. Candidate portals are sourced from `PortalDetector` markers, filtered by reachability, and ranked by approach path length + remaining distance to goal. Near the hop portal, RTNav presses interact and immediately replans to the original goal, enabling traversal across disconnected map sections linked by portals.
- **Portal-Hop Exit Safety Filter (v5.11.0):** hop-candidate selection now excludes markers flagged `is_exit` unless the current navigation goal itself is the exit portal. This prevents exit portal misclassification from causing mid-map hop detours while preserving correct exit-phase behavior.
- **Kill-All Reachability Guard (v5.12.0):** in `RTNavigator` unified kill_all routing, cluster stops and live monster-detour overrides are now gated by a quick A* reachability check. Adjacent-but-disconnected monster lanes (common in Wall of the Last Breath tunnel topology) are skipped instead of causing repeated `A* found no path` loops.
- **Hybrid Wall Confidence Overlay (v5.13.0):** `GridData` now tracks decaying walkable/blocked confidence per cell. In `Pathfinder`, when `wall_model_mode="hybrid"`, A* adds a small soft penalty on confidence-conflict cells while preserving binary blocked/walkable passability. Rollback switch: set `wall_model_mode="legacy"`.
- **Explorer Portal-Hop Enablement (v5.13.0):** explorer RTNavigator now receives scanner + portal detector + config, and MapExplorer reachability pre-check no longer rejects direct-A*-unreachable targets when portal markers exist. This allows exploration to cross disconnected map sections via existing RTNav portal-hop flow.
- **Portal Transition Verification (v5.13.0):** hop flow now verifies successful transition by significant post-interact position jump (~900u) before clearing hop state and replanning; config toggle `portal_transition_verify`.
- **Steering Hysteresis + Helper Navigator Reuse (v5.14.0):** `RTNavigator._steer()` now keeps the previous lookahead waypoint briefly when same-goal backward flips occur, reducing rapid cursor oscillation; and `BotEngine._get_helper_rt_nav()` reuses active primary/explorer RTNavigator instances to prevent helper-induced second right-click toggles and concurrent steering-loop conflicts (notably during Carjack).
- **Input & Control:** Simulates Windows input and manages the game window.
- **Map Interaction:** Detects and selects map cards, identifies portals, and manages UI-open detection with retry logic.
- **Computer Vision:** Used for fast screen capturing, vertex-based active card detection, RGB glow rarity classification, and hexagon position calibration.

**Key Design Details:**
- **Scanner Architecture:** Uses `scan_dump_chain()`, caches GWorld, employs regex for pattern scanning, and defers FNamePool/GObjects scans to a background thread.
- **Map Selection:** Prioritizes card types (Rainbow > Orange > Purple > Blue). Card detection uses hardcoded vertex positions, and active/inactive status is determined by sampling gray patches. Rarity is classified by RGB glow chevron sampling. The bot will NEVER click a card unless it is confirmed active to avoid penalty exploration runs. **v5.3.0:** one-time Survival prompt handling is enforced directly in `MapSelector._open_portal_sequence()` with a single lightweight check immediately after the first Open Portal click (the only observed timing for this popup), then the normal second portal click proceeds. **v5.4.0:** popup detector threshold calibrated from 230 → 225 and comparison changed to inclusive (`>=`) after live log proved borderline dialog mean `(229,228,232)` failed detection. **v5.5.0:** popup click coordinates corrected by title-bar offset (`-1,-31`) so "Do not show again" / "Confirm" clicks land on controls instead of below them.
- **Event Detection:** ⚠️ CfgInfo-based detection is permanently broken (CfgInfo.ID is always zero at runtime — server never populates it). ⚠️ TMap key in FightMgr.MapGamePlay is runtime **spawn_index** (e.g. 9,10,11), not static event type ID. Correct typed-event path: classify Carjack/Sandlord by MapGamePlay (+0x800) + MapCustomTrap (+0x7B0) class-name/position rules. **v4.4.0 update:** Sandlord completion no longer trusts unstable wave display; use `FightMgr.MapRoleMonster` (+0x120) nearby entity activity plus actor-gone / `bValid==0`. Overlay should show stable `SANDLORD` (no `W:x` label). **v4.5.0:** Carjack marker includes nearby monster count (`G:n`) from `FightMgr.MapRoleMonster` as guard-scanner baseline. **v4.6.0:** Carjack marker also shows top nearby monster classes (`[ClassA:n,ClassB:n]`) for live guard identification. **v4.62.0:** `G:n` reverted to 0 — EServant/MapServant confirmed FALSE POSITIVE (player pet). Guard count = 0 until real class found. **v4.63.0:** EBuffComponent probe deployed — scanning for S11 buff IDs 0x82 (E_s11_daizibao = guard buff) and 0x92 (E_s11_reinforce = horde buff) in raw EBuffComponent bytes. Logs `[BuffProbe] NEAR/FAR`. **v4.64.0:** First-sight probe was INCONCLUSIVE (all entities at spawn time show identical EBuffComp bytes — flee-buff 0x82 applied only when guards enter flee state, not at spawn). Continuous 5s re-probe added (`[BuffProbe] RESCAN`) + extended scan to full 0x3D4 bytes + `[MapUnit]` one-shot probe of FightMgr.MapUnit (+0x760) for class enumeration. **v4.65.0:** EBuffComponent approach abandoned — all probes (0x3D4 bytes + RESCAN) inconclusive. All dead probe methods deleted. Entity scan upgraded to 120 Hz. Guard detection replaced with flee-speed physics: `get_fleeing_entities()` uses 16-sample position history deques; GuardSeed tags first 3 NEAR entities in first 4 s; post-seed uses speed ≥ 120 u/s threshold.
- **Active Event Rule (user-confirmed Feb 27 2026):** Only **one** map event can be active at a time. Carjack and Sandlord can both exist in memory on the same map, but only one is actually running gameplay at any moment. Treat dual near-event counts as overlap/noise unless active-state evidence confirms both (do not assume simultaneous activity).
- **Monster Type Discrimination:** ⚠️ `AActor::InstanceComponents TArray @ actor+0x1F0` is **always empty for EMonster** (confirmed: 314 monsters, all data=0x0 count=0). ✅ **Correct component path:** `EEntity::ueComponents TMap @ entity+0x288`. TMap stride = 0x18, key (UClass*) at +0x00, value (EComponent*) at +0x08. ABP chain: `EMonster+0x288 → EAnimeComponent → +0x128 → ESkeletalMeshComponent → +0x750 → AnimBPClass`. **⚠️ ALL EMonster-LEVEL GUARD DISCRIMINATORS CONFIRMED DEAD (v4.40.0):** (1) ABP — cosmetic pool, same ABP types on guards and attackers same map; (2) EQAInfoComponent source_type — 0 live EQAInfoComponent on any EMonster in GObjects; (3) CfgInfo.ID (ECfgComponent) — per monster-type config row, not per spawn role; (4) proximity — 10–30 attackers spawn on each guard corpse within truck radius → more false positives than true positives from first kill. **⚠️ EServant/MapServant CONFIRMED FALSE POSITIVE (v4.62.0):** EServant = player pet companion class; `FightMgr.MapServant` (+0x850) = player pet registry. Screenshot proof (Feb 28 2026): overlay G1 dot tracked the player's decorative umbrella-hat companion, not a guard. Dead approach #8. **⚠️ RoleRarity/bIsElite CONFIRMED DEAD (v4.63.0):** bot_20260227_202901.log `[RoleProbe]` shows `elite=0 boss=0 rarity=0` for ALL S11 EMonster entities. Dead approach #9. **ACTIVE (v4.63.0):** EBuffComponent buff ID scan — `E_s11_daizibao` (0x82) semantically exclusive to guards (carrying treasure bags), `E_s11_reinforce` (0x92) exclusive to horde. `_probe_entity_buff_component()` scans first 480 bytes of EBuffComponent for uint32 S11 buff IDs. Real guard class = unknown; guards appear to be `EMonster` in MapRoleMonster (discrimination method TBD pending [BuffProbe] log analysis). `is_carjack_guard()` kept as stub returning `False`. Bot falls back to truck-position navigation during Carjack. **v4.65.0:** EBuffComponent approach ABANDONED (all probes up to 0x3D4 bytes inconclusive). All dead probe infrastructure deleted. Guard detection replaced with flee-speed physics: entity scan at 120 Hz, 16-sample position history per entity, speed ≥ 120 u/s + survived ≥ 1.5 s = fleeing guard. GuardSeed: first 3 NEAR entities (≤ 2 500 u of truck) in first 4 s always returned. Bot now tracks real guard positions via `get_fleeing_entities()`.
- **Carjack Game Mechanics:** 3 security guards spawn near the truck initially. If not all killed within ~5 seconds, 3 more spawn alongside them — **maximum 6 guards alive at any time**. After any group is fully killed, the next 3 spawn. Need to kill 51 total guards to complete the event (faster = more rewards). **⚠️ 24-second hard deadline: if all 51 guards are not killed within 24 seconds, the event ends with NO rewards. The bot must complete the event BEFORE this timer expires.** Guards **run away** from the player. The ~100+ other Carjack-spawned monsters **attack** the player. **⚠️ v4.62.0: EServant/MapServant reverted — EServant = player pet class, NOT guard.** Guards are EMonster in MapRoleMonster (discrimination method TBD). **v4.65.0:** Bot uses flee-speed detection (`get_fleeing_entities()`) to track guards — guards flee from player while attackers charge toward player. **⚠️ CRITICAL SPAWN MECHANIC (confirmed Feb 27 2026):** On each guard death, 10–30 attacker monsters spawn on top of and immediately around the dead guard's position — prevents proximity detection. **⚠️ CW truck probe (`EMapCustomTrap.carjack_work_count`) CONFIRMED BROKEN in live test (Feb 28 2026): `CW:0/0, TS:-1, CS:-1` throughout entire 51-guard fight.** Carjack completion detection via truck bValid still unconfirmed.
- **Carjack HUD Confirmation Rule (user-confirmed Feb 27 2026):** Right-side blue star icon is Carjack. Outside active fight it shows fixed level `5`. During active Carjack it shows countdown `24 → 0`. Successful completion shows `51` above the icon (all guards killed) and rewards drop; timeout at `0` without 51 kills means failure and no loot.
- **Sandlord Game Mechanics:** Waves of monsters spawn sequentially — kill current wave, next wave appears. Between waves the arena is empty (alive count near Sandlord platform drops to 0). In-game observed: brief spike to ~19 monsters at wave start → drops to 0 on wave death. Wave completion signal = alive count within ~2000u of Sandlord platform == 0 while event is still active (bValid=1). Random side-events on the map can also spawn monsters near the player, which may pollute a radius-only scan — ABP class discrimination needed to be certain.
- **Portal Detection:** Enumerates FightMgr via GObjects.
- **Thread Safety:** Employs RLock, locks on critical states, and `threading.Event` for synchronization.
- **Memory Exploration Tools:** Utilities for runtime object and class property exploration.
- **Zone Name Recognition:** Reads UWorld's FName at UObject+0x18 offset, resolves through FNamePool, and uses an auto-learning mapping system to store internal to English map names.
- **Calibration System:** `src/core/scale_calibrator.py` provides an active 2x2 matrix calibration per map, stored in `data/map_calibrations.json`, handling arbitrary axis rotations/swaps for isometric camera.

**UI/UX:**
- A dark-themed GUI built with `customtkinter` featuring tabs for Dashboard, Address Manager, Paths, and Settings.
- **Debug Overlay:** A transparent tkinter window drawn over the game showing waypoints (color-coded), tolerance circles, path lines, player position, navigation target, portal markers, event/entity markers, and stuck detection. It runs in a separate thread and feeds from game state at 200ms intervals.
- **Paths Tab:** Provides full waypoint editing capabilities including scrollable lists, multi-select, delete, reorder, type changes (node/stand), portal flag toggling, and coordinate/wait_time editing.

**Complete Map Cycle:**
The bot executes a precise sequence including: Hideout Spawn/Positioning, Open Map Device, Region Selection, Card Selection (highest rarity active), Adding Affixes (5 blind clicks), Opening Portal, Map Identification, Path Navigation, Event Detection & Completion (Sandlord and Carjack), Boss Kill, and Exit Portal.

## SDK Dump Offsets Reference

All offsets below were extracted from SDK Dumper tool output files. When a game update changes offsets, re-dump and search these files to update `src/utils/constants.py`.

### Player Position Chain (GWorld → Player XYZ)
**File to search:** `OffsetDump.txt` or class hierarchy dumps
**How found:** Traced the UE4 pointer chain from GWorld static to player coordinates
```
GWorld → +0x210 (OwningGameInstance) → +0x038 (LocalPlayers) → [0] → +0x030 (PlayerController)
  → +0x250 (Pawn) → +0x130 (RootComponent) → +0x124 (RelativeLocation: X,Y,Z as 3 floats)
```
Full chain in constants: `DUMP_VERIFIED_CHAIN = [0x210, 0x038, 0x0, 0x030, 0x250, 0x130, 0x124]`
- **To find after update:** Search dumps for `UGameInstance`, `APlayerController`, `APawn`, `USceneComponent`. The offsets are property offsets within each class.

### UObject Base Offsets (all UE4 objects)
**File to search:** `UObject` class definition in SDK headers
```
UObject+0x10 = ClassPrivate (UClass* pointer)
UObject+0x18 = NamePrivate (FName index — used for zone name reading)
UObject+0x20 = OuterPrivate (parent object pointer — used for ECfg→EGameplay matching)
```
- **To find after update:** These are UE4 engine offsets, rarely change between versions. Search for `class UObject` in SDK header dump.

### FNamePool
**Runtime address:** `0x7FF64F611D40` (current build, changes every game update)
**How found:** Sig-scan patterns in `FNAMEPOOL_PATTERNS` (LEA instructions referencing FNamePool)
- Pattern 1: `48 8D 35 ?? ?? ?? ?? EB 16` — resolves RIP-relative LEA to FNamePool struct
- FNamePool+0x10 = Blocks[0] pointer (array of FNameEntry blocks)
- Each FNameEntry: 2-byte header (length in lower 10 bits) + UTF-8 string
- **To find after update:** Usually auto-found by sig scan. If patterns break, use dump tool address directly (it reports Blocks[0], subtract 0x10 for struct base). Validate with FName[3] = 'ByteProperty'.

### GObjects (FUObjectArray)
**How found:** Sig-scan patterns in `GOBJECTS_PATTERNS` referencing FUObjectArray
- GObjects+0x0 = Objects (pointer to chunk array)
- Each chunk = array of FUObjectItem (24 bytes each: UObject* at +0x0)
- NumElements at GObjects+0x14, NumChunks at GObjects+0x10
- Elements per chunk: 65536
- **To find after update:** Usually auto-found by sig scan. Search for `48 8B 05 ?? ?? ?? ?? 48 8B 0C C8` pattern.

### Zone Name (Current Map Identification)
**How it works:** Reads UWorld's FName at UObject+0x18, resolves through FNamePool
- GWorld static → dereference → UWorld pointer → +0x18 = FName index
- FName index → FNamePool lookup → internal Chinese name (e.g., `SD_GeBuLinYingDi`)
- Mapped to English via `data/zone_name_mapping.json` (auto-learning system)

### Event Detection (EGameplay + ECfgComponent)
**File to search:** Search SDK headers for `class EGameplay`, `class ECfgComponent`, `class EEntity`
```
EGameplay class (inherits EEntity, size 0x728):
  - Inherits AActor → UObject base
  - RootComponent at +0x130 (RelativeLocation for event world position)
  - EEntity::bValid at +0x720 (bool)
  - EEntity::ueComponents TMap at +0x288

ECfgComponent class (size 0x240):
  - CfgInfo struct at +0x120 (ID: int32 +0x0, Type: byte +0x4, ExtendId: int32 +0x8)
  - ArtResInfo at +0x130
  - ⚠️ CONFIRMED: CfgInfo is ALWAYS ZERO at runtime. Server never populates it. Do not use.
```
- **Event type IDs from SDK (0xD8=Carjack, 0xDB/0xDD/0xDE=Sandlord):** ⚠️ These are SDK enum values — they are NOT the runtime TMap keys. Confirmed Feb 24 2026: TMap key = runtime spawn_index (9, 10, 11), matched via EGameplay+0x714 SpawnIndex field.
- **⚠️ BROKEN (do not use):** GObjects class scan + CfgInfo.ID matching. CfgInfo is always zero — permanently broken.
- **⚠️ BROKEN (do not use):** Hardcoded type IDs (0xD8 etc.) as TMap key matcher — spawn_index values vary per map session.
- **✅ CORRECT method:** Read `FightMgr.MapGamePlay` TMap at +0x800 — all entries are target events (is_target_event=True). Identify Carjack by cross-referencing spawn_index with `FightMgr.MapCustomTrap` TMap at +0x7B0: if spawn_index appears in MapCustomTrap → Carjack; else → Sandlord.
- **EGameplay children:** Every instance has exactly 3 GObjects children (ECfgComponent, EMsgComponent, SceneComponent). All identical across event types — cannot be used for type discrimination.
- **ueComponents TMap at EEntity+0x288:** ✅ CONFIRMED WORKING (v3.1.7 log). Each EEntity instance stores its game-layer components in this TMap (UClass* → EComponent*). EGameplay instances have 2 entries: ECfgComponent and EMsgComponent. EMonster instances will have EAnimeComponent here (not yet confirmed for EMonster, but this is the only known viable path since InstanceComponents is always empty). TMap element layout (stride=0x18=24 bytes): key at +0x00 (UClass*, 8 bytes), value at +0x08 (EComponent*, 8 bytes), hash at +0x10/+0x14. Implementation: read data_ptr + array_num from entity+0x288 (same as FightMgr TMap reads), iterate elements, match key FName == "EAnimeComponent", grab value ptr.
- **To find after update:** Search for `EGameplay`, `ECfgComponent`, `EEntity` in class dump. See FightMgr section for the correct detection path.

### FightMgr (Portal & Entity Detection)
**File to search:** Search SDK headers for `class FightMgr`
**Live instance:** Found in GObjects at `/Engine/Transient` path, class name `FightMgr`. Already implemented in `portal_detector.py`.
**⚠️ IMPORTANT:** GObjects always contains TWO objects named "FightMgr": (1) the UFightMgr class definition (non-transient outer, lower InternalIndex), and (2) the live singleton instance (Outer = `/Engine/Transient`). When calling `find_object_by_name("FightMgr")`, you MUST filter the results by checking `UObject+0x20` (OuterPrivate) and resolving its FName — use the result whose outer name contains "transient". Using the first result without filtering returns the class definition and will read garbage from TMap offsets. `portal_detector.py` and `scanner.py` both implement this filtering (fixed in v3.1.6).

**Complete verified TMap offset table (SDK dump Feb 2026):**
```
FightMgr TMap fields (each TMap = 0x50 bytes; key=int32 logic_id, value=EEntity* ptr):
  +0x028 = FightPool          (struct, not TMap)
  +0x080 = MapRole
  +0x0D0 = MapRolePlayer
  +0x120 = MapRoleMonster
  +0x170 = MapBullet
  +0x1C0 = MapLaser
  +0x210 = MapAttack
  +0x260 = MapLastedEffect
  +0x2B0 = MapDirectHit
  +0x300 = MapActionBall
  +0x350 = MapObject
  +0x3A0 = MapAffixCarrier
  +0x3F0 = MapDestructible
  +0x440 = MapPortal          ← used by portal_detector.py ✓
  +0x490 = MapFollower        ← last entry in constants.py as of v3.0.0
  +0x4E0 = MapElevator        ← missing from constants.py
  +0x530 = MapCheckPoint      ← missing from constants.py
  +0x580 = MapBuffArea        ← missing from constants.py
  +0x5D0 = MapProximityShield ← missing from constants.py
  +0x620 = MapObstacle        ← missing from constants.py
  +0x670 = MapGroundEffect    ← missing from constants.py
  +0x6C0 = MapNPC             ← missing from constants.py
  +0x710 = MapInteractiveItem ← missing from constants.py
  +0x760 = MapUnit            ← missing from constants.py
  +0x7B0 = MapCustomTrap      ← Carjack vehicle (EMapCustomTrap); in constants.py ✓
  +0x800 = MapGamePlay        ← ✅ Event type detection; in constants.py ✓
  +0x850 = MapServant         ← ⚠️ PLAYER PET registry (EServant = pet, NOT guard) — false positive confirmed v4.62.0; offset in constants.py
  +0x8A0 = LevelInfo          (single ObjectProperty, not TMap)
```

**TMap element layout (stride=24, theoretically derived from UE4 TSetElement; portal_detector.py NOT yet validated in-game):**
```
Each element = 24 bytes:
  +0x00 = int32  Key       (runtime spawn_index — NOT a static type ID)
  +0x04 = int32  padding
  +0x08 = ptr64  Value     (EGameplay* or EMapCustomTrap*)
  +0x10 = int32  HashNext
  +0x14 = int32  HashIndex
```
- Key confirmed = spawn_index (values 9,10,11 matched EGameplay+0x714 SpawnIndex field in Feb 24 log)
- Carjack detection: cross-reference MapGamePlay key with MapCustomTrap key — matching spawn_index = Carjack
- **To find after update:** Search for `FightMgr` class in SDK dump. Verify each MapXxx field offset and TMap key/value types.

### Quick Update Checklist After Game Patch
1. Run SDK Dumper tool on updated game
2. FNamePool/GObjects: Usually auto-found by sig scan. If broken, check dump tool output for new addresses
3. Player chain: Search for `UGameInstance`, `APlayerController`, `APawn` — verify offset values
4. Event offsets: Search for `EGameplay`, `ECfgComponent`, `EEntity` — check CfgInfo offset
5. FightMgr offsets: Search `UFightMgr:MapGamePlay`, `UFightMgr:MapCustomTrap`, `UFightMgr:MapPortal`, **`UFightMgr:MapServant`** — verify all offsets in `FIGHTMGR_OFFSETS` in `constants.py`
6. EServant was CONFIRMED to be player pet class (NOT guard) — MapServant = pet registry. Do NOT use for guard detection. Guards are EMonster in MapRoleMonster (class/discrimination method TBD). Keep `_read_servant_entities()` as diagnostic stub only.
5. FightMgr: Search for `UFightMgr` — verify pool offsets
6. Update `src/utils/constants.py` with any changed values
7. Test with "Re-scan" + "Scan Events" in Address Manager tab

---

## POST-UPDATE MAINTENANCE GUIDE

> ⚠️ **AGENT RULE:** Whenever you implement any new feature that reads game memory, uses an SDK-derived offset, or relies on a class/property name from a dump file — you MUST add a corresponding numbered entry to the "What Breaks After a Game Update" list below. Include: what the feature does, which file/constant holds the offset, how the offset was originally found (dump search term + what to look for), and how to update it. Failure to do this means future agents cannot maintain the feature after a game update.

**When to use this section:** The user reports that the bot shows an "outdated" popup after a game update, or that memory reading is broken (player position shows 0,0 or Re-scan fails). A new SDK dump has been uploaded to `attached_assets/` or `moje/`.

The bot itself detects this situation: when the player attaches to the game and the memory chain scan fails (position chain returns 0,0 or scan_dump_chain() returns success=False), a popup appears saying the bot is likely outdated. This guide tells the agent exactly what to fix.

---

### What Breaks After a Game Update — Full Inventory

Every game binary update recompiles the executable. This shifts addresses of all static variables and changes the machine code. Here is every component that depends on the binary, what breaks, and exactly how to fix it:

---

#### 1. GWorld Static Pointer (MOST CRITICAL — breaks player position)

**What breaks:** Player X/Y/Z reads return 0. Bot cannot navigate. Re-scan fails.

**Where in code:** `src/core/scanner.py` top section — `UE4_GWORLD_PATTERNS` list (8 byte patterns). These sig-scan the game binary for LEA/MOV instructions that load the GWorld static pointer.

**How originally found (Feb 2026):** SDK Dumper tool reported GWorld at a specific address. Then several MOV/LEA instruction patterns were extracted from the binary that load that address. Multiple patterns are tried in order for robustness.

**How to fix after update:**
1. Open new `[torchlight_infinite] Objects Dump.txt` (from SDK Dumper tool) uploaded to `attached_assets/` or `moje/`
2. Search for a line starting with `World /Game/` or look for the World object — the GWorld static should appear near it
3. Alternatively, open the game in x64dbg/Cheat Engine, search for the GWorld address in the .text section to find the referencing instructions
4. If at least one existing pattern still matches, no change needed — verify by testing Re-scan
5. If all patterns fail: add the new MOV/LEA pattern bytes to `UE4_GWORLD_PATTERNS` and corresponding mask string to `UE4_GWORLD_MASKS` in `src/core/scanner.py`
6. **Validation:** After fix, Re-scan should succeed and player position should show real coordinates (non-zero)

---

#### 2. FNamePool Address (breaks zone name detection, event classification)

**What breaks:** Zone name shows as "Unknown" or raw FName string. Carjack vs Sandlord classification may fail (needs class name resolution). "Set" button in Address Manager tab needs manual entry.

**Where in code:** `src/utils/constants.py` — `FNAMEPOOL_PATTERNS` (6 patterns) and `FNAMEPOOL_MASKS` and `FNAMEPOOL_LEA_OFFSETS`. Used in `src/core/scanner.py` `scan_fnamepool()`.

**How originally found (Feb 2026):**
- SDK Dumper tool directly reports `FNamePool: 0xXXXXXXXX` in its output
- Additionally, LEA instructions in the game binary that load FNamePool were identified and byte patterns extracted
- FNamePool structure: `FNamePool + 0x10` = pointer to Blocks[0], each block = array of FNameEntry (2-byte header + UTF-8 string)
- Validated by checking FName at index 3 = "ByteProperty" (standard UE4 string)
- Note: SDK Dumper reports the Blocks[0] pointer address; our code needs the FNamePool struct header which is 0x10 bytes before that

**How to fix after update:**
1. Run SDK Dumper on updated game. Output will contain a line like: `FNamePool: 00007FF7D2481D40`
2. That address is the Blocks[0] pointer. The actual FNamePool struct is at `address - 0x10`
3. In Address Manager tab → FNamePool field → enter `0x<address>` from dump tool output and click "Set"
4. The bot will auto-try `addr`, `addr-0x10`, `addr+0x10` and validate by checking if FName[3] resolves to "ByteProperty"
5. If validation succeeds, zone names will start resolving again
6. If the sig-scan patterns no longer work (FNamePool field auto-populates with wrong address): extract new LEA patterns from updated binary and add to `FNAMEPOOL_PATTERNS`/`FNAMEPOOL_MASKS` in `src/utils/constants.py`

---

#### 3. GObjects Address (breaks event detection, portal detection, zone detection)

**What breaks:** `find_object_by_name()` returns nothing. FightMgr cannot be found. Event/portal detection fails.

**Where in code:** `src/utils/constants.py` — `GOBJECTS_PATTERNS` (3 patterns) and `GOBJECTS_MASKS`. Used in `src/core/scanner.py` `scan_gobjects()`.

**How originally found (Feb 2026):**
- SDK Dumper tool directly reports `GObjects: 0xXXXXXXXX`
- Pattern `48 8B 05 ?? ?? ?? ?? 48 8B 0C C8` was extracted — this is a MOV RAX + indexed memory access typical of GObjects chunk array traversal
- GObjects structure: `GObjects + 0x10` = NumChunks int32, `GObjects + 0x14` = NumElements int32, `GObjects + 0x0` = pointer to chunk pointer array
- Each chunk = 65536 UObject pointers (FUObjectItem at 24 bytes each, UObject* at offset 0)
- Note: SDK Dumper's reported address may be +0x10 from the FUObjectArray struct base — handled in scanner code

**How to fix after update:**
1. Run SDK Dumper. Output will contain: `GObjects: 00007FF7D249ADE8`
2. If Re-scan succeeds (test by clicking Re-scan and checking if player position works), no change needed
3. If Re-scan fails: compare the new GObjects address from dump tool with the old pattern search results to identify which instruction references changed
4. Add new pattern to `GOBJECTS_PATTERNS` in `src/utils/constants.py`

---

#### 4. Player Position Chain (pointer offsets within UE4 classes)

**What breaks:** Player X/Y/Z reads return garbage or 0 even after GWorld scan succeeds.

**Where in code:** `src/utils/constants.py` — `DUMP_VERIFIED_CHAIN = [0x210, 0x038, 0x0, 0x030, 0x250, 0x130, 0x124]`. Also `UE4_OFFSETS` dict.

**How originally found (Feb 2026):**
- Traced the standard UE4 pointer chain from GWorld static to player coordinates:
  - GWorld → `+0x210` = `OwningGameInstance` (UGameInstance*)
  - UGameInstance → `+0x038` = `LocalPlayers` (TArray of ULocalPlayer*)
  - LocalPlayers → `[0]` = first player (ULocalPlayer*, array element 0 = `+0x0`)
  - ULocalPlayer → `+0x030` = `PlayerController` (APlayerController*)
  - APlayerController → `+0x250` = `Pawn` (APawn*)
  - APawn → `+0x130` = `RootComponent` (USceneComponent*)
  - USceneComponent → `+0x124` = `RelativeLocation` (FVector: 3 floats = X, Y, Z)
- These offsets were verified against class property dumps from SDK Dumper:
  - Search `[torchlight_infinite] Objects Dump.txt` for `UGameInstance` class, find `OwningGameInstance` property with `[Offset:0x210]`
  - Search for `APlayerController`, find `Pawn` at `[Offset:0x250]`
  - Search for `APawn`, find `RootComponent` at `[Offset:0x130]`
  - Search for `USceneComponent`, find `RelativeLocation` at `[Offset:0x124]`

**How to fix after update:**
1. Open `[torchlight_infinite] Objects Dump.txt` from SDK Dumper
2. Search for `class UGameInstance` — find property `OwningGameInstance`. Note the `[Offset:0xXXX]` value. Update `DUMP_VERIFIED_CHAIN[0]` and `UE4_OFFSETS["OwningGameInstance"]`
3. Search for `class APlayerController` — find `Pawn`. Note offset. Update `DUMP_VERIFIED_CHAIN[3]` and `UE4_OFFSETS["Pawn"]`
4. Search for `class APawn` — find `RootComponent`. Note offset. Update `DUMP_VERIFIED_CHAIN[4]` and `UE4_OFFSETS["RootComponent"]`
5. Search for `class USceneComponent` — find `RelativeLocation`. Note offset. Update `DUMP_VERIFIED_CHAIN[6]` and `UE4_OFFSETS["RelativeLocation"]`
6. `LocalPlayers` array offset at `+0x038` and `PlayerController` at `+0x030` are in ULocalPlayer — verify these as well

---

#### 5. UObject Base Offsets (class/name/outer pointers)

**What breaks:** Class name resolution, FName reading, parent-object matching — all GObjects-based features break.

**Where in code:** `src/utils/constants.py`:
- `UE4_UOBJECT_CLASS_OFFSET = 0x10` — ClassPrivate pointer (UClass*)
- `UE4_UOBJECT_FNAME_OFFSET = 0x18` — NamePrivate FName index (int32)
- `UE4_UOBJECT_OUTER_OFFSET = 0x20` — OuterPrivate parent object pointer

**How originally found:** These are standard UE4 engine-level UObject struct layout offsets that appear in every UE4 game. Verified against the SDK dump's `class UObject` definition. Very rarely change between game updates.

**How to fix after update:** Search `[torchlight_infinite] Objects Dump.txt` for `class UObject`. Check the offsets for `ClassPrivate`, `NamePrivate`, `OuterPrivate`. These should still be 0x10/0x18/0x20. If changed, update in `src/utils/constants.py`.

---

#### 6. FightMgr TMap Offsets (event/portal detection)

**What breaks:** FightMgr TMap reads return empty. Portal detection, Carjack/Sandlord detection, event positions all fail.

**Where in code:** `src/utils/constants.py` — `FIGHTMGR_OFFSETS` dict (all `Map*` entries). Key ones:
- `MapPortal = 0x440` — used by `portal_detector.py`
- `MapCustomTrap = 0x7B0` — Sandlord platforms + Carjack vehicles
- `MapGamePlay = 0x800` — all map events (Carjack, Sandlord, Trial, etc.)
- `MapRoleMonster = 0x120` — nearby monster/entity scanner (Sandlord completion + Carjack guard count)

**How originally found (Feb 2026):**
- SDK Dumper produced `[torchlight_infinite] Objects Dump.txt` containing `class UFightMgr` definition
- Every TMap field had a property line: `[Offset:0x800] (Size:0x50) [ FField:...] ObjectProperty  /Script/UE_game.UFightMgr:MapGamePlay`
- The complete offset table was extracted by searching for `UFightMgr:Map` in the Objects Dump
- Confirmed by live memory test: reading FightMgr at `+0x800` with `array_num=3` matched the 3 expected events

**How to fix after update:**
1. Open `[torchlight_infinite] Objects Dump.txt` from new SDK dump
2. Search for `UFightMgr:MapPortal` — note the `[Offset:0xXXX]` value. Update `FIGHTMGR_OFFSETS["MapPortal"]`
3. Search for `UFightMgr:MapGamePlay` — note offset. Update `FIGHTMGR_OFFSETS["MapGamePlay"]`  
4. Search for `UFightMgr:MapCustomTrap` — note offset. Update `FIGHTMGR_OFFSETS["MapCustomTrap"]`
5. Update the convenience aliases: `FIGHTMGR_MAP_PORTAL_OFFSET`, `FIGHTMGR_MAP_GAMEPLAY_OFFSET`, `FIGHTMGR_MAP_CUSTOMTRAP_OFFSET`
6. If all field offsets need updating, search `UFightMgr:Map` to find all and update the full `FIGHTMGR_OFFSETS` dict

---

#### 7. EGameplay / EEntity Offsets (event position, wave counter, bValid)

**What breaks:** Event world positions read as 0. Wave counter doesn't change. bValid always 1. Event completion detection fails.

**Where in code:** `src/core/scanner.py` hardcoded offsets in `_read_tmap_events()` and `get_typed_events()`:
- `+0x130` = RootComponent pointer (AActor::RootComponent — standard UE4)
- `+0x124` = RelativeLocation in RootComponent (same as player chain)
- `+0x618` = wave counter (EEntity field) — ⚠️ CONFIRMED UNRELIABLE (2026-03-02): value fluctuates 1–4 randomly every second even while idle. Read and stored for raw overlay display only. DO NOT use in any game logic.
- `+0x714` = SpawnIndex (EGameplay field)
- `+0x720` = bValid (EEntity::bValid bool)

**How originally found (Feb 2026):**
- `+0x130` and `+0x124` are standard UE4 AActor/USceneComponent offsets, same as player chain
- `+0x618` wave counter: discovered by comparing EGameplay memory at 0x600-0x720 range across 4 dumps taken during Sandlord waves. Initial observation: value appeared to go 3→2→0 as waves completed. **⚠️ CONFIRMED UNRELIABLE (2026-03-02): subsequent live testing showed value fluctuates 1–4 every second with no consistent wave correlation. Do not use in any Sandlord or event logic.**
- `+0x714` SpawnIndex: discovered during TMap key investigation (Feb 24). Byte values matched TMap keys exactly (9,10,11). SDK dump confirmed EGameplay has a SpawnIndex property.
- `+0x720` bValid: SDK dump shows `EEntity::bValid` at offset 0x720. Used as completion signal (unvalidated in-game as of last session).

**How to fix after update:**
1. Search `[torchlight_infinite] Objects Dump.txt` for `class EGameplay` and `class EEntity`
2. Find `bValid` property offset in EEntity — update `+0x720` constant in `_read_tmap_events()`
3. Find any property near 0x714 that looks like a sequential ID — update SpawnIndex read offset
4. The wave counter at 0x618 has no SDK property name. ⚠️ CONFIRMED UNRELIABLE (2026-03-02) — even if you find a new candidate offset after a game update, do NOT use it for logic without extensive live validation across multiple wave transitions.

---

#### 8. EMapCustomTrap Class Name Filter (Carjack vs Sandlord discrimination)

**What breaks:** Carjack events misidentified as Sandlord (or vice versa). Bot navigates to wrong position for Carjack.

**Where in code:** `src/core/scanner.py` `get_typed_events()` — filter: `"TrapS" in class_name` to detect Carjack vehicle vs Sandlord platform.

**How originally found (Feb 2026 — confirmed):**
- `EMapCustomTrap` (base class, no suffix) = Sandlord trigger platform (`class EMapCustomTrap` in dump)
- `EMapCustomTrap2` and `EMapCustomTrap3` = wave-spawn mechanics that appear DURING Sandlord fight (not navigation targets)
- `EMapCustomTrapS5`, `EMapCustomTrapS7`, `EMapCustomTrapS10`, `EMapCustomTrapS11` = Carjack vehicle variants (season-specific). The "S" suffix followed by a number = seasonal Carjack vehicle class
- This was confirmed by user manually verifying positions in-game: the EMapCustomTrapS11 at (-2500,-950) was physically the Carjack vehicle; EMapCustomTrap at (-3200, 5900) was the Sandlord arena
- The filter `"TrapS" in class_name` correctly catches all seasonal variants (S5/S7/S10/S11) and excludes base + numeric variants (2/3/Attach)

**How to fix after update:**
1. Open `[torchlight_infinite] Objects Dump.txt` — search for `EMapCustomTrap`
2. List all class names starting with `EMapCustomTrap` in the dump
3. Classes WITHOUT a seasonal suffix (base class + EMapCustomTrap2/3/Attach) = Sandlord platforms → should NOT match `"TrapS"`
4. Classes WITH "S" + number suffix (EMapCustomTrapS11, S12, etc.) = Carjack vehicle → MUST match `"TrapS"`
5. If the game adds a new seasonal variant (e.g. EMapCustomTrapS12), the existing `"TrapS"` filter already handles it — no code change needed
6. If the naming convention changes (e.g. they rename to ECarjackTrap), update the filter in `get_typed_events()`

---

#### 9. ECfgComponent CfgInfo Offset (permanently broken, kept for reference)

**Status: Permanently broken — do NOT try to fix this, it will never work.**

CfgInfo struct at `ECfgComponent + 0x120` has ID/Type/ExtendId that are always zero at runtime. The game server never calls `InitCfg()` for EGameplay instances. This was confirmed in 5 separate dump sessions across multiple maps. The SDK-derived event type IDs (0xD8=Carjack, 0xDB/0xDD/0xDE=Sandlord) are enum values that do NOT match runtime TMap keys.

**If a new agent is tempted to "fix" CfgInfo-based detection:** Don't. It wastes 2-3 hours. The correct detection path is via `FightMgr.MapCustomTrap` class names (above).

---

#### 10. Screen UI Coordinates / Card Slot Positions (breaks map selection)

**What breaks:** Bot clicks wrong positions on screen. Card detection fails. Map device button not found.

**Where in code:** `src/utils/constants.py`:
- `CARD_SLOTS` dict — 12 card positions with `active_top`, `active_tl`, etc. in client area pixels
- `HEX_POSITIONS` dict — center of each card hexagon
- `NEXT_BUTTON = (1765, 995)`, `ADD_AFFIX_BUTTON = (180, 809)`, `OPEN_PORTAL_BUTTON_POS = (1765, 995)`
- `ATTEMPTS_REGION`, `TIP_POPUP_*` — UI region constants
- `GLOW_CHEVRON_POLYGON` — relative pixel polygon for rarity glow sampling

**How originally found (Feb 2026):**
- User measured all 12 card hexagon positions using a pixel coordinate tool (clicking on screen)
- Title bar offset (1, 31) was subtracted from screen coordinates to get client area coordinates
- Card dimensions: 71×113px active, 69×109px inactive; active card "pops up" 22px when selected
- All measurements stored in `debug/all_12_cards_border_vertices.txt` for reference
- Glow polygon derived from 424 manually traced glow outline points on 4 cards (2 blue, 1 orange, 1 purple)

**How to fix after update:**
- These coordinates only change if the game changes its UI layout (rare, usually with major UI redesigns)
- If card selection breaks: user needs to take a screenshot of the map device UI and measure card positions again
- Use the pixel tool to click each card's top vertex (the pointy top) and compute `active_top = screen_pos - (1, 31)`
- For active cards: `active_top` is 22px higher than `inactive_top`
- The `GLOW_CHEVRON_POLYGON` relative offsets should still work unless glow shape changes significantly

---

#### 10b. UI Widget Memory Coordinates (CanvasPanelSlot offsets)

**What breaks:** Bot cannot resolve which card widget maps to which on-screen hex slot. The click array fails to match positions. Pure memory card mapping fails entirely.

**Where in code:** `src/utils/constants.py`:
- `UWIDGET_SLOT_OFFSET = 0x28` (Slot pointer inside UWidget)
- `UCANVASPANELSLOT_LAYOUTDATA_OFFSET = 0x38` (LayoutData struct inside UCanvasPanelSlot)
- `FMARGIN_LEFT_OFFSET = 0x0`, `FMARGIN_TOP_OFFSET = 0x4`

**How originally found (March 2026):**
- Traced `UIMysticMapItem_C` UI hierarchy through parent `UWidget`.
- Dump showed `UWidget` has `Slot` ObjectProperty at offset `0x028`.
- Because maps are drawn on a canvas, the slot is a `UCanvasPanelSlot`.
- `UCanvasPanelSlot` dump shows `LayoutData` at `0x038`.
- The base struct of LayoutData contains `Offsets` (`FMargin`) storing `Left` and `Top` float coordinates.

**How to fix after update:**
- Search Objects Dump for `class UWidget` to verify `Slot` offset.
- Search Objects Dump for `class UCanvasPanelSlot` to verify `LayoutData` offset.

---

#### 11. Template Images (breaks region/zone detection)

**What breaks:** "Glacial Abyss" region detection fails. Bot cannot click the correct map region.

**Where in code:** `assets/glacial_abyss_text.png` — used in `src/core/map_selector.py` for template matching. Also `assets/` directory for other UI templates if added.

**How originally found:** Screenshot of the game at the map device region selection screen. The text "Glacial Abyss" was cropped and saved as a 123×26px template image. Mean RGB (69, 68, 75).

**How to fix after update:** If the game changes the region name text or font, take a new screenshot, crop the text label, and replace `assets/glacial_abyss_text.png`. Size should match the original (±a few pixels). Threshold in map_selector.py is 0.7 (TM_CCOEFF_NORMED).

---

#### 12. Zone Name Mapping (breaks English zone display)

**What breaks:** Dashboard shows raw internal Chinese zone names instead of English (e.g. `SD_GeBuLinYingDi` instead of `Blustery Canyon`).

**Where in code:** `data/zone_name_mapping.json` — dict mapping internal FName strings to English names.

**How originally found:** Combination of:
1. Reading live GWorld FName from memory during bot sessions
2. Cross-referencing with Names Dump from SDK Dumper (`[torchlight_infinite] Names Dump.txt`)
3. Pinyin translation + user confirmation for each map

All 12 card-slot maps are confirmed. The hideout (Embers Rest) is mapped to `XZ_YuJinZhiXiBiNanSuo200`. The auto-learning system (`scanner.py`) updates this file automatically when the bot identifies a map via position.

**How to fix after update:** If game renames map FNames (unusual), run the bot in a known map. Zone FName appears in bot log: `Zone FName[XXXXX] = 'new_fname'`. Add the new FName → English mapping to `data/zone_name_mapping.json`.

---

#### 13. EMapTaleCollisionPoint Wall Actors (breaks A* autonomous navigation)

**What breaks:** Autonomous A* navigation (`nav_mode = "auto"`) falls back to direct navigation without wall avoidance. Bot may get stuck in corners or behind walls. The overlay A* path layer shows no path.

**Where in code:**
- `src/core/wall_scanner.py` — `WALL_ACTOR_CLASS = "EMapTaleCollisionPoint"` (in `src/utils/constants.py`)
- `WallScanner.scan_wall_actors()`: enumerates GObjects by class name, reads actor position via `actor → +0x130 (RootComponent) → +0x124 (RelativeLocation)` — same chain as EGameplay/MapBossRoom
- `data/wall_data.json`: per-map cache of wall actor positions (auto-created on first map entry in auto mode)

**How originally found (Feb 25 2026):**
- SDK Objects Dump: searched for `EMapTaleCollision` and found `class EMapTaleCollisionPoint` — custom wall actor class (EEntity subclass). The companion `class EMapTaleCollisionComponent` (size 0x128) holds the actual box shape but has no SDK-reflected fields.
- Position reading follows the same standard UE4 pattern as all other actors: `RootComponent at actor+0x130`, `RelativeLocation at RootComponent+0x124`.
- WALL_DETECTION_PLAN.md: complete research notes including ECollisionThrough bitmask, NineGridSceneDataTableRow grid bounds, and the game's own A* (EWayfindingResult).

**EMapTaleCollisionComponent shape data (deferred):**
The component (size 0x128 bytes, no SDK-reflected properties) presumably holds box half-extents and the `ECollisionThrough` bitmask (bit 0 = walk-passable). Offset layout not yet determined — requires a live memory session to compare byte values with known extents. Current implementation uses point-obstacle approximation (radius 220 world units).

**How to fix after update:**
1. Search new Objects Dump for `class EMapTaleCollisionPoint` — if class still exists, no code change needed (GObjects scan by class name).
2. If class was renamed: update `WALL_ACTOR_CLASS` in `src/utils/constants.py`.
3. If actor position chain changed: update `UE4_OFFSETS["RootComponent"]` and `UE4_OFFSETS["RelativeLocation"]` (same as fix #4 and #7 — shared across EGameplay, MapBossRoom, and EMapTaleCollisionPoint).
4. Delete stale `data/wall_data.json` entries for affected maps (or delete the whole file) — they will be re-scanned on next map entry.

---

#### 14. EMonster ABP Component Chain (breaks guard-type discrimination)

**What breaks:** Entity Scanner cannot distinguish Carjack security guards from regular Carjack monsters. `abp_class` field stays empty on all monsters. Guard-count logic for Carjack completion falls back to raw proximity count (which includes non-guard monsters).

**Where in code:**
- `src/core/scanner.py` `_log_emonster_components()` — reads component chain and returns ABP class name string.
- `UE4Scanner._abp_cache: dict` — `{address → abp_name}` session cache; empty string means "tried, not found".
- `EventInfo.abp_class` — stores resolved ABP name for overlay and Entity Scanner tab.

**How originally found (Feb 26 2026):**
- v4.9.0–v4.10.0 added `_log_emonster_components()` using `AActor::InstanceComponents TArray @ actor+0x1F0`.
- **CONFIRMED BROKEN in bot_20260226_104945.log:** ALL 314 EMonster instances return `data=0x0 count=0`. Not a single monster uses InstanceComponents. OwnedComponents fallback (offsets 0x100/0xF0/0xF8/0x108/0x110) also finds nothing.
- **Root cause:** EMonster uses the game's own `EEntity::ueComponents TMap @ +0x288` (UClass* → EComponent*), NOT UE4's standard `AActor::InstanceComponents` or `AActor::OwnedComponents`.
- **Evidence for correct path:** v3.1.7 log (bot_20260224_182941.log) already showed `ueComponents TMap@0x288: data_ptr=0x2CFDC48BB00 num=2` working for EGameplay entities. SDK dump confirms `EEntity:ueComponents [Offset:0x288]` (MapProperty, UClass* → EComponent*). `EAnimeComponent:SkeletalMesh [Offset:0x128]` and `SkeletalMeshComponent:AnimBlueprintGeneratedClass [Offset:0x750]` confirmed in SDK dump.

**Correct ABP chain:**
```
EMonster + 0x288            → ueComponents TMap (data_ptr at +0, array_num at +8)
  Each element (stride 0x18): key at +0 (UClass*), value at +8 (EComponent*)
  Find entry where key FName == "EAnimeComponent"
EAnimeComponent + 0x128     → ESkeletalMeshComponent* (EAnimeComponent:SkeletalMesh, SDK offset confirmed)
ESkeletalMeshComponent + 0x750  → UClass* AnimBlueprintGeneratedClass (FName = "ABP_xxx_C")
ESkeletalMeshComponent + 0x758  → UClass* AnimClass (fallback)
```

**Known guard ABP class names (confirmed live + SDK):**
- `ABP_JiaoDuJunQingJia_C` — 56 live instances (bot_20260226_135800.log); also Sandlord monster (dual-role)
- `ABP_JiaoDuJunQingJia_Bow_C` — 11 live instances; dual-role
- `ABP_JiaoDuJunZhongJia_TowerShield_*_C` — 9 live instances; dual-role
- `ABP_HeiBangWuRenJi_C` — 73 live instances (Wall of the Last Breath)
- `ABP_ShaGu_C` — confirmed guard (carjack_guards_abp_screenshot2.png); dual-role with Sandlord
- Other confirmed: GaoYuanGuYing, HuiJinQiu, YouLingSX, ShiYanDiJi, GaoYuanHaoZhu
- ⚠️ **DUAL-ROLE:** JiaoDuJun* and ShaGu appear as BOTH Sandlord monsters AND guards on same map — only spatial proximity (3000u from truck) disambiguates
- Filter: `is_carjack_guard(abp_class)` using `CARJACK_GUARD_ABP_PREFIXES` in constants.py

**How to fix after update:**
1. If ABP names no longer resolve: verify `EAnimeComponent:SkeletalMesh` offset in new Objects Dump (search for `EAnimeComponent:SkeletalMesh`). Update `EAnimeComponent + 0x128` if changed.
2. If `AnimBlueprintGeneratedClass` offset changed: search `USkeletalMeshComponent:AnimBlueprintGeneratedClass` in dump. Update `ESkeletalMeshComponent + 0x750`.
3. If guard ABP class names changed with a season update: check new dump for `ABP_JiaoDuJun` — pattern `"ABP_JiaoDuJun" in abp_class` should still catch all variants unless class naming convention changed entirely.
4. If `ueComponents TMap` offset changed: search `EEntity:ueComponents` in dump. Update the `0x288` constant in `_log_emonster_components()` in `scanner.py`.

---

#### 15. EServant Guard Discrimination via FightMgr.MapServant (v4.60.0 — CONFIRMED FALSE POSITIVE)

**⚠️ This approach is DEAD — do NOT attempt to revive it.**

**What was believed:** EServant = Carjack escort guard class; MapServant = guard registry.

**What it actually is:** EServant = player pet/companion class (the decorative character following the player, e.g. umbrella-hat). MapServant (+0x850) = pet registry. Confirmed by user screenshot (Feb 28 2026): overlay G1 dot appeared exactly on the player's decorative pet companion, NOT on any guard.

**Why it seemed to work:** One player pet → always exactly G:1. "Position jumps" were pet following the player, not guard kills/replacements.

**Current state (v4.65.0):**
- All dead probe infrastructure removed: `_read_servant_entities()`, `_probe_entity_buff_component()`, `_probe_mapunit_once()` all deleted.
- `get_carjack_guard_positions()` now uses `get_fleeing_entities()` (flee-speed detection at 120 Hz), with truck-position fallback.
- `get_carjack_guard_debug_snapshot()` delegates to `get_fleeing_entities()` (real markers on overlay).
- `FIGHTMGR_MAP_SERVANT_OFFSET` kept in constants.py with comment "= player pet registry, NOT guard registry."
- Entity scan: **8 ms / 120 Hz**. Per-entity `_entity_pos_history` deques (16 samples) used for speed computation. GuardSeed phase: first 3 NEAR entities within 4 s of activation always returned as guards. Post-seed: entities with speed ≥ 120 u/s surviving ≥ 1.5 s treated as fleeing guards.

**Real guard class:** Unknown. Guards (押运保镖) appear to be `EMonster` in `FightMgr.MapRoleMonster` (same pool as horde) — discrimination method still TBD.

**How to maintain after game update:**
- No action needed for guard detection (this approach is abandoned).
- If `UFightMgr:MapServant` offset changes in new dump: update `FIGHTMGR_OFFSETS["MapServant"]` in `constants.py` for correctness (the offset is genuine, just not for guards).

---

**When the user reports:** "Bot is showing the outdated warning" or "Re-scan fails" or "player position is 0,0" — and they have uploaded new SDK dump files.

**Step 1 — Read the dump files**
- New dumps are in `attached_assets/` or `moje/` directory
- Files: `[torchlight_infinite] Objects Dump.txt` and `[torchlight_infinite] Names Dump.txt`
- Objects Dump: `[ Index:XXXXX] (Size:0xYYYYY) [UObject:ADDR] ClassName  /Path/To/Object` + property offset lines
- Names Dump: `[Number] [InternalIndex] FNameString`
- Note: Files may use Windows encoding (CRLF + cp1252). Read as latin-1 or use `tr -d '\r'` if needed.

**Step 2 — Check SDK Dumper output header**
- Look for lines like: `GObjects: 00007FF7...`, `FNamePool: 00007FF7...`, `GEngine: 00007FF7...`
- These are the new runtime addresses (change every game update)
- For FNamePool: the reported address is Blocks[0], actual struct is at `address - 0x10`

**Step 3 — Verify GWorld patterns still work**
- If the user ran Re-scan and it succeeded → GWorld patterns are still valid, skip to Step 5
- If Re-scan failed → check `UE4_GWORLD_PATTERNS` in `src/core/scanner.py` against the new binary

**Step 4 — Verify FNamePool**
- Use the address from Step 2. In Address Manager tab, enter the FNamePool address and click "Set"
- Bot will auto-try ±0x10 offsets and validate with FName index 3 = "ByteProperty"
- If auto-detected by sig scan, no change needed

**Step 5 — Verify player chain offsets**
- In Objects Dump, search for `class UGameInstance` → find `OwningGameInstance` at offset
- Search for `class APlayerController` → find `Pawn` at offset
- Search for `class APawn` → find `RootComponent` at offset
- Search for `class USceneComponent` → find `RelativeLocation` at offset
- Compare found offsets with `DUMP_VERIFIED_CHAIN` in `src/utils/constants.py`
- If any changed, update the chain

**Step 6 — Verify FightMgr offsets**
- In Objects Dump, search for `UFightMgr:MapGamePlay`, `UFightMgr:MapCustomTrap`, `UFightMgr:MapPortal`
- Compare offsets with `FIGHTMGR_OFFSETS` in `src/utils/constants.py`
- Update any that changed

**Step 7 — Verify EMapCustomTrap class names**
- In Objects Dump, search for `EMapCustomTrap`
- Verify that seasonal variants still follow the `*TrapS*` naming pattern
- If naming changed, update filter in `scanner.py` `get_typed_events()`

**Step 8 — Test**
- Bump APP_VERSION in `src/utils/constants.py`
- Bot: Attach → Re-scan → confirm player position shows real coordinates
- Run a test map with Sandlord+Carjack → Scan Events → verify correct classification
- The "outdated" popup should NOT appear after this fix

---

## External Dependencies
- **customtkinter**: For GUI development.
- **pymem**: For direct memory access.
- **psutil**: For process enumeration.
- **tkinter**: Standard Python GUI toolkit.
- **opencv-python-headless**: For computer vision tasks.
- **numpy**: For numerical operations.
- **mss**: For fast screen capturing.
- **ctypes/win32**: For Windows-specific input simulation and window management.
