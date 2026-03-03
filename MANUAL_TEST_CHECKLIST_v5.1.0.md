# Manual Test Checklist — v5.1.0

## Goal
Validate behavior parity after planner/provider split (Event/Boss/Portal planning moved to GoalProviders).

## Preconditions
- Bot version shown as `v5.1.0`.
- Auto behavior set to `rush_events`.
- Debug overlay optional (recommended ON).

## Test A — Full Map Cycle (`rush_events`)
1. Start bot and enter a map with at least one target event.
2. Verify event navigation still triggers in nearest-first order.
3. If Carjack is selected, verify pre-clear behavior still occurs near truck before event handling.
4. Verify event handler executes after arrival (no distant event handling).
5. Verify boss phase runs after events.
6. Verify boss linger still lasts ~3s.
7. Verify portal phase finds exit, navigates, and confirms entry.

### Expected log markers
- `[RTNav] Phase 1 — Events`
- `[RTNav] Navigating to <EventType> at (...)`
- `[RTNav] Pre-clear around event (...)` (Carjack only)
- `[RTNav] Phase 2 — Boss`
- `[RTNav] Boss arena reached — lingering 3 s for auto-bomb`
- `[RTNav] Phase 3 — Exit portal`
- `[RTNav] Portal entry confirmed (attempt N)`

## Test B — Sandlord Avoidance Integrity
1. Run map with both Sandlord and another target event if possible.
2. Verify pathing to non-Sandlord target does not accidentally step onto Sandlord platform.

### Expected log markers
- `[RTNav] Routing around <n> Sandlord zone(s)` when appropriate.
- No premature Sandlord activation while heading to other targets.

## Test C — Explorer Anti-Idle Regression Check
1. Start map explorer.
2. Observe for 2–3 minutes.
3. Verify explorer does not stall wall-bumping for long intervals.

### Expected behavior
- Frequent target churn.
- No prolonged stationary periods.

## Pass Criteria
- Full cycle completes: Events → Boss → Portal with unchanged behavior.
- No new navigation deadlocks/stalls.
- No regression in portal confirmation.

## If failure occurs
Provide latest bot log from `moje/` and include:
- map name,
- behavior mode,
- exact phase where it diverged,
- whether movement stopped, oscillated, or timed out.
