# PHASE 3 REPORT — M4 (on_loop reversibility + on-call heartbeat + in_loop timeouts)

Status: **done, committed, stopped.** Phase 4 (M5) not started.
Branch: `looper3/m4-veto-heartbeat-timeouts` (branched from `looper3/m3-gate-subgraph`).
Project root: `C:\Users\jeffg\Projects\looper3`. Nothing outside this folder was touched. Paper mode only, no network.

M4 exit criterion (PRD): *fail-closed paths tested; no auto-approve without heartbeat.* **Met** (§3).
Acceptance criteria now covered: **AC4** (no heartbeat ⇒ expired on_loop action REJECTED) and **AC5** (irreversible action under an on_loop harness ⇒ in_loop).

---

## 1. What was built

### Graph (new nodes marked *)

```
START → intake → readFloor → prepare → gateNode → routeGate
                                                     ├─ autonomous        → execute
                                                     ├─ on_loop | in_loop → armDeadline* → vetoWindow | approval
                                                     └─ no decision       → reject
approval | vetoWindow → routeDecision → execute | escalate* | reject
escalate* → routeDecision → execute | reject
```

| Change | Where | What it does |
|---|---|---|
| FR-5 gate-time conversion | `gateDecisionFor` (pure) | After the unchanged five-way reduction: an `on_loop` result stays on_loop only if the tool is reversible or has a compensating action **and** an on-call heartbeat is active. Otherwise `in_loop`, binding `reversibility` or `oncall`, plus `gate.convertedFromOnLoop`. Can only tighten. |
| Heartbeat read | `readFloor` | Now reads both agent_flags values before the pure gate: global floor (failure ⇒ in_loop) and heartbeat (failure ⇒ inactive) into `state.oncallActive`. |
| Deadline registration | `armDeadline`* | Registers exactly one job per action, keyed like `prepare`: `approval_deadline` (tier 4 ⇒ 15 min, any other tier forced into in_loop ⇒ 30 min) or `veto_deadline`. Uses no clock (derived from `preparedAtMs`), so a replay computes the same deadline. Moved out of `prepare`, which no longer knows the mode; autonomous actions now get no job (M3 registered one for every action). |
| Deadline runner | `src/gate/deadlines.ts` | `fireDueDeadlines(graph, scheduler, now)`: resumes a thread only if it is **still parked** at the matching prompt; otherwise marks the job `stale`. Approval ⇒ `{approved:false, by:"timeout"}`. Veto ⇒ a *request* to auto-approve that the node may refuse. |
| Veto expiry checks | `vetoWindow` | Auto-approve needs, in order: window really closed (a premature "timeout" is rejected), heartbeat active **at expiry** (re-read), action eligible, notification delivery **confirmed**. Any failure ⇒ reject. |
| FR-4 escalation | `escalate`* + `AlertOutbox` | Every deadline-driven rejection is escalated once (`high`); `touchesMoney` actions escalate on **every** timeout, `critical`. Idempotent per action key. |
| Delivery confirmation | `NotificationOutbox` | `confirmDelivery(key)` / `isDeliveryConfirmed(key)`; outbox default is unconfirmed (fail closed). |
| Check-in mechanism | `agentFlags.ts` | `checkInOnCall(db, operator, hours, now)` and `checkOutOnCall(...)`. Mechanism only; the UX is PRD OQ4. |

Files: `src/gate/{nodes,graph,state,outbox}.ts`, `src/gate/deadlines.ts` (new), `src/policy/types.ts` (two binding values), `src/flags/agentFlags.ts`, `src/trading/fakeTradingGraph.ts` (`gateRouteFor` now requires `{ oncallActive }`), `src/index.ts`, `scripts/bench-gate.ts`, tests.

## 2. Choices the PRD left open (made minimally, all tighten-only)

**Confirmed by the owner on 2026-11-04: keep all eight as built.** Items 3, 4 and 8 are therefore decided, no longer open.

1. **Where FR-5 applies.** At gate time, inside the pure gate, after `effectiveMode()` (whose math is untouched), and again at veto expiry. Order: reversibility first (property of the action), then heartbeat (property of the moment).
2. **New binding labels** `reversibility` and `oncall`. FR-9's binding field needs to say why an on_loop action ended up in_loop.
3. **Timeout for tier 1–2 actions forced into in_loop** (e.g. a read in the execution harness): FR-4 names only "tier-3 forced" (30 min) and tier 4 (15 min). Tiers 1–3 all use the 30-minute value.
4. **Escalation scope.** FR-4 says escalate on expiry, and "touchesMoney escalates on every timeout". Read as: every timeout *rejection* escalates; touchesMoney also escalates a timeout that ends in approval. A human answer that lands after the deadline counts as a timeout.
5. **`decidedBy` for refused auto-approves** stays `policy` (as in M3); `timedOut: true` marks that a deadline triggered it.
6. **Delivery confirmation** is checked last and is never sufficient alone. The test harness auto-confirms by default; the outbox itself does not.
7. **Test harness default heartbeat is now `true`** (was `false`). It's a test convenience only, so the M3 suites keep modelling "someone on call". Every off-call path sets `false` explicitly, and there is no production default.
8. **`configVersion` not bumped** (no config value changed). The gate's behaviour did change for threads pinned to `1.1.0`. If version pinning should cover code-level policy changes, bump it. That's your call.

## 3. Verification (real output)

```
$ npm run verify      # typecheck + check:tiers + tests
OK - every tool definition declares a tier and matches the registry.
 Test Files  13 passed (13)
      Tests  202 passed (202)        (Phase 2: 173; +29)
$ npm run build       → dist/ + policy JSON copied
```

New `tests/gateM4.test.ts` (27 tests) plus 2 rewritten in `gateGraph.test.ts`:

- **AC5:** an irreversible tier-3 tool with no compensation under the build harness ⇒ in_loop / `reversibility`, with or without someone on call. A compensating action keeps `send_telegram_alert` eligible.
- **AC4:** heartbeat lapses during the window ⇒ rejected at expiry, one `high` alert, no effect. The same holds when the heartbeat read throws, or when delivery is unconfirmed. Confirmed delivery with no heartbeat still rejects.
- **No heartbeat at gate time** ⇒ approval, not a veto window, with a 30-minute deadline. Also wired to the real agent_flags store: check-in ⇒ veto, check-out ⇒ approval.
- **Property test** (2000 runs over tool × mode × harness × SME × floors × heartbeat): the conversion only ever touches on_loop results and never loosens.
- **Replay set:** with someone on call, all 30 M3 locks are unchanged. Without, exactly the on_loop locks become approval.
- **FR-4 deadlines:**
  - Tier 4 at 15 minutes and tier 3 at 30 minutes. Nothing fires 1 ms early.
  - At the deadline: rejected with `rejectReason "timeout"`, one alert. touchesMoney alerts are `critical`.
  - A human decision before the deadline wins, and the deadline then goes `stale`.
  - Running the runner twice fires once. A runner that died before marking is harmless.
- **Chaos on the timeout path:** crash before each of approval / escalate / reject on resume ⇒ one rejection, one alert, one notification, one job, no effect, nothing left pending.
- **Existing chaos suites** were extended to the new boundary: in_loop 1..7, reject 1..7. They still show one notification, one execution, no re-ask and no skipped gate.

NFR-1 gate decision, `npm run bench:gate` (50 000 iterations): autonomous read p95 **0.0072 ms**, tier-4 p95 0.0056 ms, unregistered p95 0.0025 ms. The conversion adds no measurable cost.

## 4. Open decisions (not decided here)

New from M4: none (§2 confirmed).

Carried forward: build-harness tier-1 read (still on_loop, pinned by test); SME `maxAutonomy` placeholders; heartbeat format (epoch-ms, unchanged); per-harness veto window (OQ2, still 120 s placeholder); approval/alert channel (OQ1, still stub outboxes); batched approvals (OQ5); daily portfolio thresholds (OQ6, M5); NFR-1 scope (gate µs vs graph 40–70 ms); duplicate submission of a completed `tool_call_id` re-asks (needs a persisted decision record, M5/M7).

## 5. Not verified / not done

- **No real scheduler or alert channel.** `SchedulerStub`, `AlertOutbox` and `fireDueDeadlines` are driven by an explicit clock in tests. In production something must call the runner on a timer, and a supervisor must finish runs that died mid-flight (`invoke(null)`). The runner only detects stale jobs; it doesn't recover runs that died mid-flight.
- **Delivery receipts** are simulated. Which channel confirms delivery, and how, depends on OQ1.
- **The real trading graph** is still not in this repo (same as M3).
- **FR-6 floor triggers (M5), roll-out to other harnesses (M6), and LangSmith tags plus the audit table (M7)** are not started.
