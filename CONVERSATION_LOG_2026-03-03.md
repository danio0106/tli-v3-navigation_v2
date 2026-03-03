# Conversation Log — 2026-03-03

Type: detailed chronological log (not summary)
Scope: full discussion thread about navigation reliability, rejection of unsuitable options, and agreed minimap-atlas rollout constraints.

---

## Turn-by-turn log

### Turn 1 — User request (problem framing)
User reported current bot navigation is the main weakness:
- wall detection is unreliable,
- grid from visited positions causes jitter and false blocked cells,
- model needs months of tuning they do not have,
- project deadline is in ~2 weeks,
- asks for fast and reliable alternatives,
- asks specifically whether DLL injection could provide better wall data and how hard that is,
- asks for alternatives beyond DLL injection.

### Turn 2 — Assistant response (initial recommendation)
Assistant recommended:
- avoid DLL injection for this deadline,
- avoid chasing perfect wall reconstruction,
- use reliability-first navigation behavior,
- deterministic route/checkpoint strategy,
- local runtime avoidance,
- stronger stuck-recovery ladder,
- objective-first routing using memory-stable event/boss/portal signals.

### Turn 3 — User request
User asked to explain and restate recommendation under project constraints.

### Turn 4 — Assistant response (constraints restatement)
Assistant restated:
- no DLL injection,
- no perfect wall-map attempt,
- deterministic routes + local obstacle handling,
- strict event→boss→portal flow,
- disable risky persistence causing poisoning.

### Turn 5 — User rejection of route idea
User explicitly rejected predefined paths/recorded paths approach:
- already tried before,
- monster, Carjack, Sandlord positions are random,
- objective requires average-player-like speed and loot/hour,
- reliability must survive lag/disruptions (including examiner interference),
- route approach considered too weak and not viable.

### Turn 6 — Assistant adaptation
Assistant switched proposal to adaptive objective navigation:
- dynamic memory targets,
- ephemeral obstacles with short TTL,
- anti-jitter steering controller,
- lag/disconnect resilience mechanisms.

### Turn 7 — User rejection of TTL-only model
User rejected short-TTL-only ephemeral walls for Torchlight corridor maps:
- narrow tunnels and walls near player make probe-first behavior too costly,
- would cause wall bouncing and repeated probing,
- each new run/session would repeat mistakes,
- efficiency would remain far from average player.

### Turn 8 — Assistant revised architecture
Assistant proposed hybrid with static prior:
- static geometry prior from minimap imagery,
- dynamic target selection from memory unchanged,
- runtime failures only as temporary edge costs,
- anti-bounce controller and deterministic recovery.

### Turn 9 — User question
User asked how this differs from current approach and whether current approach can be tuned to become equivalent.

### Turn 10 — Assistant distinction
Assistant clarified core difference:
- current approach = online geometry inference from movement outcomes,
- proposed approach = geometry known at run start from offline minimap atlas,
- tuning current approach cannot remove probing tax and lag sensitivity in narrow corridors.

### Turn 11 — User planning request with rollback requirement
User requested concrete practical plan and rollback path:
- keep access to working current version,
- explain exactly how geometry is extracted quickly,
- clarify whether many runs are required,
- explain how to mitigate CV imperfections.

### Turn 12 — Assistant repository-grounded discovery step
Assistant performed read-only repo grounding before final plan:
- read .github/copilot-instructions.md and CHAT_LOG.md,
- inspected navigator, wall scanner, pathfinder, scanner/minimap, calibrator, overlay,
- identified low-risk integration seams and fallback behavior,
- identified map identity and minimap key mapping risks,
- asked one decision question for initial coverage scope.

### Turn 13 — User decision
User selected initial choice: top 6 most-run maps first.

### Turn 14 — Assistant first rollout plan
Assistant laid out rollout plan:
- branch isolation,
- passive minimap+position logger,
- offline atlas generator,
- confidence-based geometry guidance,
- optional runtime toggle/fallback,
- phased validation.

### Turn 15 — User decision updates (critical)
User changed/locked decisions:
- coverage must be all 12 maps (cannot control map rolls),
- collection loop: user runs each map twice, assistant checks sufficiency; if insufficient user runs more,
- accepts confidence/soft-unknown CV handling,
- rollback can be handled by creating a second Git project copy,
- requested conversation session be written to file for new agent.

### Turn 16 — Assistant updated plan for 12 maps
Assistant updated plan accordingly:
- all 12 maps mandatory,
- iterative 2-runs baseline then targeted top-up,
- isolate in second project copy,
- maintain runtime fallback toggle.

### Turn 17 — User direct request
User requested first action: create conversation log file now (not summary), then user will create project copy.

### Turn 18 — Assistant action
Assistant created this file initially, but it was too condensed.

### Turn 19 — User correction
User said file does not contain full log; asked to fix so next agent can understand plan and context without struggling.

### Turn 20 — Assistant action
Assistant replaced file with this more detailed transcript-style version.

---

## Rejected approaches (explicit in conversation)

1) Pure predefined route/path recording as primary navigation model
- Rejected by user due to random event/monster placement and exam robustness demands.

2) Pure TTL ephemeral obstacle inference without prior geometry
- Rejected by user for tunnel-heavy map topology; expected to re-probe walls repeatedly and lose efficiency.

3) DLL injection as near-term solution
- Not selected due to deadline/risk tradeoff and stabilization burden.

---

## Accepted direction (current agreement)

1) Build geometry prior from minimap data offline.
2) Keep dynamic objective targeting from memory (events/monsters/boss/portal remain runtime-live).
3) Use runtime movement feedback only as temporary adjustment (not persistent poisoning).
4) Cover all 12 maps for exam safety.
5) Use iterative collection protocol: user runs 2x/map, assistant evaluates and requests only targeted additional runs.
6) Keep rollback safety via isolated working copy and fallback to current behavior.

---

## Operational next actions requested by user

1) This full conversation log file must exist for handoff to next agent.
2) User will create separate project copy in Git after this file is fixed.
3) Next agent should continue from this agreed architecture and collection protocol.

---

## Notes for next agent (continuation constraints)

- Do not revert to predefined-route-only solution.
- Do not present TTL-only obstacle model as primary geometry strategy.
- Do not require user to choose code-level architecture options; make implementation decisions in-agent.
- Keep exam objective central: reliability + efficiency comparable to average player, including resilience under lag/interference.
