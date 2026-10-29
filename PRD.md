# PRD — Looper³ Loop Gate Integration (Draft v1.0, owner DragonFlyAI)

Looper³ has three autonomy modes: in_loop (strictest), on_loop, autonomous. Today the mode is fixed per run (harness cap + SME cap) and routeByMode exists only in the trading graph. This project makes the loop a PER-ACTION GATE shared by all six harnesses. Effective mode comes from five caps: wanted mode, harness cap, SME cap, action risk tier, runtime floor (floor = strictest(runFloor, globalFloor)). The gate may tighten autonomy automatically; it can NEVER loosen it automatically.

## Problems
P1 mode fixed per run not per action; P2 approval/veto logic only in trading graph; P3 on_loop veto window auto-approves on expiry (fails open); P4 in_loop has no timeout; P5 no system-wide tightening; P6 tool-name string-matching classifier misclassifies (e.g. read_and_delete as tier 1).

## Goals
G1 one gate subgraph for all six harness types for every side-effecting tool call of tier>=2. G2 effective mode = strictest of (wanted, harness cap, SME cap, tier cap, floor), in code. G3 replay-safe: crash/resume never re-asks a decided gate and never skips one. G4 fail closed everywhere (unknown tool, unreadable flag, missing on-call coverage, timeout => stricter or rejected). G5 automatic tightening per run and globally; loosening only via manual, version-pinned config. G6 full LangSmith visibility of which cap bound each decision.
Non-goals: changing risk limits/backtest gates/kill switch/reconciliation; governing learning loops (wiki compiler, Hindsight, hypothesis lab - they run in monitoring/analysis harnesses, can't touch money, write only to raw/outputs/); automatic promotion of autonomy (paper-to-live stays manual); a learned classifier that can LOWER a tier (may only raise).

## Current model
type Mode = "in_loop" | "on_loop" | "autonomous";
const order: Mode[] = ["in_loop","on_loop","autonomous"]; // index 0 strictest
const clamp = (wanted, cap) => order.indexOf(wanted) <= order.indexOf(cap) ? wanted : cap;
effectiveMode(wanted, harness, sme) = clamp(clamp(wanted, HARNESS[harness].cap), sme.maxAutonomy)
Harness caps: research, analysis, monitoring = autonomous. business process, build = on_loop. execution = in_loop.

## Functional requirements
FR-1 Static tool-tier registry. Every tool has a declared tier 1-4 in a versioned registry next to the tool definition. Tier 1 read-only -> cap autonomous; 2 internal mutable -> autonomous (rate-limited, logged); 3 external reversible -> on_loop; 4 critical/irreversible -> in_loop. Unregistered tool = tier 4. Build-time check fails CI if any registered tool lacks a tier. Optional learned classifier may only RAISE a tier at runtime.

FR-2 Five-way effective mode:
const TIER_CAP = {1:"autonomous",2:"autonomous",3:"on_loop",4:"in_loop"};
effectiveMode(wanted, harness, sme, tier, floor) = [HARNESS[harness].cap, sme.maxAutonomy, TIER_CAP[tier], floor].reduce((m,cap)=>clamp(m,cap), wanted)
floor = strictest(runFloor, globalFloor). The gate also records which cap was binding: harness | sme | tier | floor (or "unregistered").

FR-3 Gate subgraph (LangGraph node split; never one awaiting node):
pending tool call -> gate -> routeGate -> execute | vetoWindow | approval -> execute | reject
gateNode(s): tier = TOOL_TIER[s.pending.tool]; if undefined return {gate:{mode:"in_loop",tier:4,binding:"unregistered"}}; floor = strictest(s.runFloor, s.globalFloor); mode = effectiveMode(s.wantedMode, s.harness, s.sme, tier, floor); return {gate:{mode,tier,binding:bindingCap(s,tier,floor)}}
routeGate: autonomous->"execute", on_loop->"vetoWindow", else "approval".
approvalNode: d = interrupt({kind:"approval",action:s.pending,tier:s.gate.tier}); return d.approved ? {approved:true} : {approved:false, rejectReason:d.reason}
vetoWindowNode: d = interrupt({kind:"veto",action:s.pending,expiresAt:s.vetoExpiresAt}); return {approved:d.approved, approvedBy:d.by}
Requirements: gateNode is pure and deterministic. NO side effect before interrupt() inside an interrupting node (LangGraph re-runs the node from the top on resume). Notification sending and scheduler registration run in an EARLIER node safe to repeat, keyed by sha(thread_id + tool_call_id). The veto window does NOT block: the thread pauses and the scheduler resumes it with Command({resume:{approved:true,by:"timeout"}}) only if FR-5 allows. Gated tool calls of tier>=3 are non-replayable and use idempotency key thread_id + tool_call_id.

FR-4 in_loop timeout. Every approval has an expiry (default 30 min for tier-3 actions forced into in_loop, 15 min for tier 4). On expiry: REJECT, record rejectReason "timeout", escalate via the alert channel. touchesMoney:true escalates on every timeout.

FR-5 on_loop eligibility + on-call heartbeat. on_loop only for actions that are reversible or have a registered compensating action; otherwise the action becomes in_loop. The veto window may auto-approve on expiry ONLY if an on-call heartbeat is active (operator checks in; expires after N hours, default 8). No active heartbeat => on_loop becomes in_loop and FR-4 timeout applies (reject on expiry). Confirmed notification delivery is also required but not sufficient alone.

FR-6 Runtime floor (automatic tightening). Run floor (thread state, checkpointed) raised when: router confidence below threshold; run token/dollar/turn budget exceeded; data-freshness check fails; paper results drift outside the backtest's expected range. Global floor (SQLite agent_flags next to the kill switch) raised when: total spend/loss across agents exceeds daily limits; kill switch trips (floor becomes in_loop and kill switch halts execution); reconciliation leaves orders unresolved. If agent_flags can't be read the global floor counts as in_loop. Floors only move stricter automatically; LOWERING a floor is a manual, logged operator action.

FR-7 Router and intake. Router outputs (harness, sme, wantedMode) into state; it can only lower wantedMode and never re-routes on resume. Context intake adds an autonomy_mode slot with wikiFillable:false and NO allowDefault for trading and betting SMEs. Handoffs into a stricter harness take the target's caps (no separate handoff rule).

FR-8 Versioned policy config. HARNESS caps, SME maxAutonomy, TOOL_TIER, timeouts, heartbeat duration live in a versioned config file in git; config version stored on each thread (version pinning). The loop may never change: risk limits, autonomy caps, tier registry, judge gates, holdout boundary, execution harness, or this list.

FR-9 Telemetry and audit. Tag every gate decision in LangSmith with effectiveMode, tier, binding, harness, smeId, configVersion, decision, decidedBy (policy|human|timeout), latencyMs. Also write every decision to an append-only audit table. Weekly report: share of decisions bound by each cap by harness and SME.

## Non-functional
NFR-1 gate overhead < 20 ms p95 for autonomous decisions (no network calls in gateNode). NFR-2 zero duplicate side effects under crash/resume (chaos test: kill process at every node boundary). NFR-3 approvals reachable from mobile in <3 taps. NFR-4 gate decisions survive Supabase failover (PostgresSaver on direct or session pooler URL, never port 6543). NFR-5 every fail-closed path has a unit test.

## Data model
-- SQLite (QS.terminal PC), extends agent_flags
insert into agent_flags (key,value) values ('global_floor','autonomous') on conflict (key) do nothing;
insert into agent_flags (key,value) values ('oncall_heartbeat_until','0') on conflict (key) do nothing;
-- Postgres (Supabase) append-only audit
create table if not exists gate_decisions (
  idem_key text primary key, -- sha(thread_id + tool_call_id)
  thread_id text not null, tool text not null, tier smallint not null,
  effective_mode text not null check (effective_mode in ('in_loop','on_loop','autonomous')),
  binding text not null,
  decision text not null check (decision in ('approved','rejected','vetoed','timeout')),
  decided_by text not null, config_version text not null,
  created_at timestamptz not null default now());
revoke update, delete on gate_decisions from public;
Thread state additions: pending, gate, runFloor, wantedMode, approved, rejectReason, vetoExpiresAt, configVersion.

## Rollout phases (exit criteria)
M1 Static tool-tier registry + CI check — 100% of tools tiered, CI blocks untiered tools.
M2 Global floor in agent_flags + fail-closed read — unreadable flag means in_loop in tests.
M3 Gate node split; trading graph routed through it with NO change in behaviour — trading traces match pre-change behaviour on a replay set.
M4 on_loop reversibility rule + on-call heartbeat + in_loop timeouts — fail-closed paths tested; no auto-approve without heartbeat.
M5 Automatic floor-tightening triggers (run + global) — each trigger covered by a test and a LangSmith alert.
M6 Roll out to build, business-process and monitoring harnesses — all side-effecting tools in all harnesses gated.
M7 LangSmith binding-cap tags + weekly report + audit table — report produced; audit table rejects UPDATE and DELETE.
Everything starts in paper mode with QS_PAPER_ONLY=1. Execution harness stays in_loop throughout.

## Acceptance criteria
1 Tier-1 read in build harness executes with no pause; tier-4 merge in same run hits approval. 2 Killing the process during approval and resuming never sends a second notification and never runs the action twice. 3 Unregistered tool always resolves to in_loop with binding "unregistered". 4 With no on-call heartbeat, an on_loop action whose veto window expires is REJECTED. 5 Irreversible action under an on_loop harness resolves to in_loop. 6 Exceeding a run budget raises that run's floor; exceeding the daily portfolio limit raises the global floor for every run. 7 No code path lowers a floor or cap without a manual, logged operator action. 8 Every gate decision appears in LangSmith and gate_decisions with all FR-9 fields.

## Open questions (do NOT decide these yourself; leave as config/TODO and list them)
1 Approval notification channel (Telegram, Pushover, custom PWA)? 2 Default veto window per harness (120 s for business process?). 3 Which build/process tools have true compensating actions? 4 Heartbeat UX (manual, calendar, both)? 5 Batched approvals for repeated tier-3 actions? 6 Exact daily portfolio thresholds that raise the global floor.

## Design decisions
Per-action loop (not per-run) kept. Single awaiting gate function rejected (LangGraph re-runs interrupted nodes; state saves only when a node finishes) -> node split. String-matching classifier rejected -> static registry. Delivery confirmation alone rejected -> reversibility rule + on-call heartbeat. Global floor added. Separate handoff rule dropped. Supervision loops stay separate from learning loops.
