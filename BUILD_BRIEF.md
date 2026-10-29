# Build brief for Hermes — Looper³ Loop Gate (phased)

Read PRD.md in this folder first. You are building the loop gate in a NEW standalone TypeScript project in this directory (Node 20+, vitest, strict TS, better-sqlite3 for the agent_flags store, @langchain/langgraph for the gate subgraph later). Do not touch any other repo or folder. Do not connect to any broker, exchange, Supabase, LangSmith or the network; use fakes/in-memory stand-ins and clearly marked adapters. Paper mode only (QS_PAPER_ONLY=1 default). Never create or print secrets.

Work ONE phase at a time. After each phase: run tests + typecheck, commit on branch `looper3/m<N>-<slug>` (git init if needed, author GustheTrader <jeffgus@gmail.com>), write `PHASE_N_REPORT.md` (what was built, test output summary, open questions you did not decide, anything the PRD left ambiguous), then STOP and wait. Never start the next phase on your own. Never merge to main.

Rules from the PRD that every phase must keep: fail closed everywhere; the gate can only tighten, never loosen; unregistered tool = tier 4 / in_loop; gateNode is pure and deterministic with no I/O; floors only move stricter automatically; do not decide the PRD's open questions (put them in config as clearly named TODO values and list them in the report).

## Phase 1 = M1 + M2 (do these two now)
M1 — core types and tool-tier registry:
- src/policy/modes.ts: Mode, order, clamp, strictest(), plus effectiveMode(wanted, harness, sme, tier, floor) exactly per FR-2, and bindingCap() returning "harness"|"sme"|"tier"|"floor"|"unregistered" (define a deterministic tie-break order and document it).
- src/policy/config.ts + policy/looper3.policy.json (versioned): HARNESS caps for the six harness types (research, analysis, monitoring = autonomous; business_process, build = on_loop; execution = in_loop), SME maxAutonomy placeholders (crypto, nfl, research, coding), TIER_CAP, in_loop timeouts (30 min tier-3-forced, 15 min tier 4), heartbeat hours 8, configVersion. Validate with a schema; loading an invalid or missing config must fail closed (throw), never default to looser.
- src/policy/toolTiers.ts: TOOL_TIER registry (versioned, tool -> tier 1-4, plus `reversible`, `compensatingAction?`, `touchesMoney?`), lookupTier() returning tier 4 for unknown tools, and a `raiseTier()` that can only raise.
- scripts/check-tool-tiers.ts: CI check that fails if any tool definition lacks a tier (scan a tools/ directory of tool definitions; create a few example tools incl. a trap `read_and_delete` that must be tier 4). Wire it as `npm run check:tiers`.
- Tests: exhaustive table test of effectiveMode over all (wanted x harness cap x sme cap x tier x floor) combos asserting result == strictest of all inputs; property test that no input combination yields a looser mode than any single cap; unregistered tool -> tier 4; raiseTier can't lower; invalid config throws.
M2 — global floor store:
- src/flags/agentFlags.ts: SQLite-backed agent_flags (key,value) with global_floor default 'autonomous' and oncall_heartbeat_until default '0' (seed with insert-on-conflict-do-nothing as in the PRD). readGlobalFloor() returns in_loop on ANY failure (db missing, locked, corrupt, unparsable or unknown value). raiseGlobalFloor() may only move stricter; lowerGlobalFloor() requires an explicit operator identity + reason and writes to an append-only floor_changes log table. No other code path may lower it.
- Tests: unreadable/corrupt/missing db -> in_loop; garbage value -> in_loop; automatic raise only stricter (attempting to lower via raise is a no-op); manual lower is logged; heartbeat read fails closed (treated as inactive).
Exit criteria: M1 100% of registered tools tiered and CI check blocks an untiered tool (prove with a test fixture); M2 unreadable flag means in_loop in tests.

## Later phases (do not start): M3 gate node split on a LangGraph subgraph with the existing trading graph routed through it unchanged; M4 on_loop reversibility + heartbeat + in_loop timeouts; M5 automatic floor-tightening triggers; M6 roll-out to build, business-process, monitoring harnesses; M7 telemetry + weekly report + append-only audit table.
