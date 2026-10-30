# PHASE 2 REPORT — M3 (gate node split)

Status: **done, committed, stopped.** Phase 3 (M4) not started.
Branch: `looper3/m3-gate-subgraph` (branched from `looper3/m1-m2-core-and-floor-store`).
Project root: `C:\Users\jeffg\Projects\looper3` — nothing outside this folder was touched.

---

## 0. Compliance with the brief, and the one deviation

Brief items 1–6 all implemented; the build-harness question was **not** resolved (mode math untouched, current result pinned by a test, still listed as open). Deviations, both documented in §1:

1. The gate node is registered as `gateNode`, not `gate` — LangGraph forbids a node name equal to a state channel and the PRD requires a `gate` state field.
2. `prepare` sends its notification before the mode is known, because the brief orders `prepare → gate`; the payload is therefore a neutral "gate pending", carrying the veto deadline once known (set on the interrupt payload instead).

---

## 1. What was built

### File manifest (new)

| Path | Purpose |
|---|---|
| `src/gate/state.ts` | LangGraph state annotation: `pending, gate, runFloor, globalFloor, wantedMode, harness, sme, approved, rejectReason, vetoExpiresAt, configVersion` plus internal bookkeeping (`routerWantedMode`, `routed`, `preparedAtMs`, `prepareKey`, `executeKey`, `executed`, `decidedBy`). |
| `src/gate/graph.ts` | The subgraph wiring: `intake → readFloor → prepare → gateNode → routeGate → {execute \| vetoWindow \| approval \| reject}`, then `routeDecision → execute \| reject`. Compiled with a checkpointer (MemorySaver default). |
| `src/gate/nodes.ts` | `gateDecisionFor()` (the pure decision), `routeForMode`, `routeAfterGate`, `routeAfterDecision`, `parseResumePayload`, `onLoopEligible`, and the eight node implementations. |
| `src/gate/keys.ts` | `idempotencyKey(thread_id, tool_call_id)` = sha256, NUL-separated. |
| `src/gate/outbox.ts` | `NotificationOutbox` + `SchedulerStub`: in-memory, keyed, idempotent stand-ins for the two repeatable side effects. No network. |
| `src/gate/executor.ts` | `SideEffectLedger`: fake executor with the tier>=3 once-only rule and a `failNextApply` chaos knob. No broker/exchange. |
| `src/gate/router.ts` | FR-7 partial: `applyRouterDecision` (tighten-only, never re-routes), `autonomySlotFor` / `assertAutonomySlot` (never wiki-fillable, no default for crypto/nfl). |
| `src/gate/postgresSaver.ts` | Clearly marked `PostgresCheckpointSaverStub` + status string. No `pg` import, no URL, no connection; the constructor throws. |
| `src/trading/fakeTradingGraph.ts` | The fake trading graph: PRD "current model" `legacyEffectiveMode`, `routeByMode`, replay comparison helpers. |
| `scripts/bench-gate.ts` | `npm run bench:gate` — p50/p95/p99/max for three gate cases. |
| `test/fixtures/replay-set.json` | 30 recorded pending-action fixtures with locked legacy and gate outcomes. |
| `tests/helpers/gateHarness.ts` | Harness: real temporary agent_flags store, in-memory outbox/scheduler/ledger, controllable clock, node instrumentation, one-shot crash injection at a node boundary. |
| `tests/gateGraph.test.ts`, `gateReplay.test.ts`, `gateChaos.test.ts`, `gateIdempotency.test.ts`, `gateRouter.test.ts`, `gateBench.test.ts` | M3 test suites (counts in §6). |

### Phase-1 files touched (small, deliberate)

- `src/policy/types.ts`: `BINDINGS` gains `"wanted"`; new `FLOOR_SOURCES` (`run | global | both`).
- `src/policy/modes.ts`: `bindingCap` returns `"wanted"` when no cap sits at the effective rank (mode math untouched); new pure `floorSource(runFloor, globalFloor)`.
- `src/policy/config.ts` + `looper3.policy.json`: new `vetoWindowSeconds: 120` **TODO placeholder** (PRD OQ2), `configVersion` bumped `1.0.0 → 1.1.0`.
- `src/index.ts` barrel, `package.json` (`@langchain/langgraph ^1.5.3`, `bench:gate`), and the two Phase-1 tests whose assertions referenced the binding union / config version.

### Graph

```
START → intake → readFloor → prepare → gateNode → routeGate
                                                     ├─ autonomous → execute
                                                     ├─ on_loop    → vetoWindow
                                                     ├─ in_loop    → approval
                                                     └─ no decision→ reject
approval   → routeDecision ─┬→ execute
vetoWindow → routeDecision ─┴→ reject
execute → END        reject → END
```

- `gateNode` is pure: no clock, no db, no network, no mutation. It closes over the **frozen** policy config and tier registry only.
- `readFloor` is a separate node *before* gateNode and is the only db read: `readGlobalFloor()` from the Phase 1 `agent_flags` store, wrapped in try/catch, failure ⇒ `in_loop`.
- `approval` and `vetoWindow` call `interrupt()` as their **first** statement. Nothing is written, sent or executed before it.
- `prepare` is the only node with repeatable side effects, keyed by `sha256(thread_id + tool_call_id)`: one notification, one scheduler registration. It runs **before** the gate, so its notification is an unconditional "gate pending" intent (the PRD orders it that way; the mode is not known yet).
- `execute` refuses to run unless `gate.mode === "autonomous"` or `approved === true`, and executes tier>=3 actions at most once per idempotency key.
- Node name is `gateNode`, not `gate` (the one naming deviation from the brief). LangGraph refuses a node whose name collides with a state channel, and the PRD requires a state field called `gate`:
  `Error: gate is already being used as a state attribute (a.k.a. a channel), cannot also be used as a node name.`
  Verified in isolation on a bare `StateGraph`. `gateNode` is the PRD's own name for the function, and the node is still the pure `gate` step in the FR-3 chain `prepare → gate → routeGate`.

## 2. Binding-label gap (FR-3 union was incomplete)

FR-3's union is `harness | sme | tier | floor | unregistered`, which cannot express two real cases. Resolved **without touching the mode math**:

1. **`"wanted"`** — when the caller's own wanted mode is stricter than every cap, no cap bound the decision. Phase 1 reported the strictest cap, which mis-attributes the decision in telemetry. `bindingCap` now returns `"wanted"`; precedence for genuine ties is unchanged (`floor > tier > sme > harness`).
2. **`floorSource`** — `"floor"` cannot say whether the run floor or the global floor bound it. New pure `floorSource()` returns `run | global | both`, carried in `gate.floorSource` next to `gate.floor`.

`gate` therefore carries `{ mode, tier, binding, floor, floorSource, configVersion }`. Mode values are bit-identical to Phase 1: `effectiveMode` was not modified, and the 1080-combination table test plus the fast-check property test still pass.

## 3. Replay evidence (fake trading graph)

The real trading graph is **not in this repository** (it lives in the desk's trading repo), so `src/trading/fakeTradingGraph.ts` is an explicit stand-in: it reproduces the PRD "current model" (`effectiveMode(wanted, harness, sme)` — no tier, no floor) and `routeByMode`.

30 recorded pending-action fixtures, locked outcomes:

- **gate outcome matches the lock: 30/30.** Legacy outcome reproduces: 30/30.
- **execution harness (6 fixtures): identical to legacy — `approval` for all 6.**
- **never looser: 30/30** (`approval` ≥ `vetoWindow` ≥ `execute`).
- **tightened: exactly 10**, none loosened:

| fixture | action | harness | legacy | gate | why |
|---|---|---|---|---|---|
| r09 | merge_pull_request (t4) | build | vetoWindow | **approval** | tier cap |
| r10 | read_and_delete (t4) | build | vetoWindow | **approval** | tier cap |
| r11 | mystery_tool (unregistered) | build | vetoWindow | **approval** | unregistered ⇒ t4 |
| r14 | read_wiki (t1) | build | vetoWindow | **approval** | global floor `in_loop` |
| r17 | send_telegram_alert (t3) | research | execute | **vetoWindow** | tier cap |
| r18 | mystery_tool | research | execute | **approval** | unregistered ⇒ t4 |
| r19 | read_and_delete (t4) | research | execute | **approval** | tier cap |
| r23 | read_and_delete (t4) | business_process | vetoWindow | **approval** | tier cap |
| r24 | mystery_tool | business_process | vetoWindow | **approval** | unregistered ⇒ t4 |
| r25 | send_telegram_alert (t3) | business_process | vetoWindow | **approval** | run floor `in_loop` |

**Documented gap, deliberately unchanged:** r07 — a tier-1 read in the **build** harness resolves `on_loop → vetoWindow`, identical to legacy. PRD acceptance criterion 1 ("tier-1 read in build harness executes with no pause") still contradicts `build = on_loop`. Per instruction, the mode math was **not** touched; there is an explicit test (`gateReplay.test.ts > documented gap`) pinning the current behaviour, and it stays an open decision (§7).

## 4. Fail-closed behaviour (all tested)

| Situation | Result |
|---|---|
| tool not in registry | tier 4, `in_loop`, binding `unregistered` ⇒ approval |
| no gate decision reached / unknown mode | route to **reject**, nothing executed |
| `approved` not literally `true` | reject |
| malformed approval / veto resume payload (`"yes"`, `true`, `{}`, `{approved:"true"}`, `{approved:1}`, array) | reject with `invalid … response (fail closed)` |
| in_loop timeout resume (`by:"timeout"`) | **always reject** with `timeout` — never auto-approve (FR-4) |
| human approval after the in_loop deadline | reject `timeout` |
| on_loop timeout auto-approve **without** an active heartbeat | reject (`no active on-call heartbeat`) |
| on_loop timeout auto-approve for an irreversible action with no compensating action | reject |
| human approval after the veto window closed | reject (`veto window expired`) |
| `agent_flags` unreadable/missing/corrupt | `globalFloor = in_loop` ⇒ approval |
| intake sees missing/invalid `threadId`, `pending`, `wantedMode`, `runFloor`, `harness`, `sme` | `GateInputError`, the run does not start |
| Postgres saver stub used | throws, no connection attempted |
| `execute` reached without clearance | refuses and records why |

## 5. Crash/resume (NFR-2) and idempotency

Chaos method: the harness hook throws **before** the Nth node body runs, so the run aborts exactly at that node boundary with the checkpoint at the last completed node; a **new compiled graph on the same checkpointer and the same external stores** then resumes — a real restart, not a stream that keeps running (an early version of this test used stream abandonment and that was wrong: the abandoned run continued in the background and double-executed. Replaced with fault injection).

Results, for **every boundary** (visits 1–6 on both flows, plus a reject variant):

- one notification delivered (`deliveries === 1`) even though `prepare` is re-entered;
- one scheduler registration;
- one execution (`effectsFor(key) === 1`);
- **no re-ask after a decision** (`promptsAfterDecision === 0`, ≤1 decision delivered);
- **no skipped gate** (`gateNode` visited ≥ once);
- final state still `approved: true, executed: true` (or `approved: false` on the reject variant);
- crash **during** the approval resume: the human decision survives — verified `approved:true, executed:true, effects:1` after restart, with no second prompt (LangGraph persists the resume value in the checkpoint).

Idempotency: `sha256(thread_id + "\0" + tool_call_id)`, 64 hex chars, NUL separator so `("ab","c")` ≠ `("a","bc")`. The ledger applies a **tier>=3** key once (second attempt ⇒ `deduplicated`), while tier 1–2 keys replay by design (reads and internal writes are repeatable). Mid-execute crash (effect applied, checkpoint not written) resumes to exactly one effect with two attempts.

## 6. Verification (real output)

```
$ npm run verify            # typecheck + check:tiers + tests
OK - every tool definition declares a tier and matches the registry.
 Test Files  12 passed (12)
      Tests  173 passed (173)          (Phase 1 had 106; +67)

  tests/agentFlags.test.ts         35      tests/gateGraph.test.ts        29
  tests/config.test.ts             22      tests/toolTiers.test.ts        18
  tests/modes.test.ts              16      tests/checkToolTiers.test.ts   12
  tests/gateReplay.test.ts         11      tests/gateIdempotency.test.ts   9
  tests/gateRouter.test.ts          8      tests/gateChaos.test.ts         7
  tests/integration.test.ts         4      tests/gateBench.test.ts         2

$ npx tsc -p tsconfig.json --noEmit     → 0 errors
$ npm run build                          → dist/ ESM + policy JSON copied
$ node -e "<import dist/index.js>"       → gate: in_loop / tier 4 / binding tier / both → approval
```

### NFR-1 benchmark (real numbers, this machine: Node v26.7.0, Windows, tsx/vitest)

`npm run bench:gate` (50 000 iterations each, after 10 000 warm-up):

| case | p50 | p95 | p99 |
|---|---|---|---|
| autonomous tier-1 read (research) | 0.0056 ms | **0.0075 ms** | 0.0177 ms |
| tier-4 merge (build) | 0.0048 ms | 0.0065 ms | 0.0103 ms |
| unregistered tool | 0.0021 ms | 0.0028 ms | 0.0031 ms |

In the test suite (20 000 iterations): p50 0.0072 ms, p95 0.0132 ms, p99 0.0238 ms.

**Verdict: the 20 ms budget is met by a factor of ~2 600 for the gate decision.** But a full autonomous pass through the *compiled graph* is **p50 ≈ 40 ms / p95 ≈ 68 ms** on this box. I measured a bare 3-node LangGraph with MemorySaver (p50 ≈ 118 ms, p95 ≈ 336 ms in a tsx probe) and a 2-node graph without a checkpointer (p50 ≈ 54 ms): the cost is the LangGraph runtime + checkpoint serialisation in this environment, not the gate logic. If NFR-1 is intended end-to-end per action, it is **not** met and needs an owner decision (see §7). The test asserts only the gate-decision budget and reports the graph number.

## 7. Open decisions carried forward (not decided here)

1. **Build-harness tier-1 read** (Phase 1 gap #1): still `on_loop`; pinned by a test; unchanged by instruction.
2. **SME `maxAutonomy` values** remain conservative placeholders (crypto/nfl/coding `on_loop`, research `autonomous`).
3. **Heartbeat format**: stored as epoch-ms string with `0` = never set; needs confirmation before M4 writes it.
4. **on_loop "does not block" vs "thread pauses"** (FR-3/FR-5 wording): the veto window pauses the thread via `interrupt()`, which is what the implementation does.
5. **Veto window per harness** (PRD OQ2): single `vetoWindowSeconds: 120` placeholder; per-harness is undecided.
6. **Approval notification channel** (PRD OQ1): outbox intent only; no channel.
7. **Batched approvals** (OQ5) and **daily portfolio thresholds** (OQ6): untouched.
8. **NFR-1 scope**: gate decision (met, µs) vs end-to-end graph pass (~40–70 ms here, not met).
9. **Duplicate submission of the same `tool_call_id` after completion**: a fresh `invoke` with the same input re-enters the gate and **asks again** (verified: second prompt returned; the side effect stayed at exactly 1 because of the idempotency key). FR-3's crash/resume guarantee holds; this is a separate duplicate-submit case. Fix would be a persisted decision record consulted in `intake` — needs an owner decision (M4/M5).

## 8. What I could not verify / deliberately did not do

- **The real trading graph is not in this repo.** The replay comparison runs against a faithful stand-in of the current `routeByMode` model, not recorded production traces. M3's real integration (routing the actual graph through the subgraph unchanged) cannot be done here.
- **NFR-4 (Postgres checkpointer)**: stub only; no connection, no URL, no failover test.
- **No notifications and no scheduler**: in-memory stubs (PRD OQ1).
- **FR-4 escalation** on timeout (alert channel) and **FR-5's gate-time conversion** of irreversible on_loop actions to in_loop are M4 work. I only enforced FR-5's fail-closed default inside the *auto-approve* path (no heartbeat or no reversibility ⇒ reject), which can only tighten.
- **FR-9 telemetry / audit table / LangSmith**: M7. Nothing is tagged or persisted beyond the checkpointer today.
- Paper mode only: `QS_PAPER_ONLY` still refuses anything but `1`; no broker, exchange, Supabase or network code exists in this branch.

## 9. Environment notes

- `@langchain/langgraph@1.5.3` pulled 22 packages; the Phase-1 `package.json` metadata survived this install (it did not the first time — re-check after adding dependencies).
- LangGraph rejects a node name that collides with a state channel (`gate`), hence `gateNode`.
- `new Command({ resume })` with a **falsy** payload (null/0/false) is rejected by LangGraph as an empty command, so those payloads are covered by a direct `parseResumePayload` unit test rather than through the graph.
- `MemorySaver` is per-compiled-graph; the harness shares one saver across graph instances so a "restart" resumes the same checkpoints.
