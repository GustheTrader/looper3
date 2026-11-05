# PHASE 4 REPORT — M5 (automatic floor-tightening triggers)

Status: **done, committed, stopped.** Phase 5 (M6 roll-out) not started.
Branch: `looper3/m5-floor-triggers` (branched from `looper3/m4-veto-heartbeat-timeouts`).
Project root: `C:\Users\jeffg\Projects\looper3`. Nothing outside it was touched. Paper mode only, no network.

M5 exit criterion (PRD): *each trigger covered by a test and a LangSmith alert.* **Met.** Every trigger has its own test, and each test asserts the alert it produces, shaped as a LangSmith event. Sending that alert to LangSmith is a stub (M7).
Acceptance criteria now covered: **AC6** (a run budget raises that run's floor; the daily portfolio limit raises the global floor for every run) and **AC7** for the new paths (no automatic path lowers a floor).

**All thresholds are placeholders** (PRD OQ6 + FR-6). They live in `floorTriggers` in the policy file, with TODO entries. Set them before this leaves paper mode (§4).

---

## 1. What was built

### Run floor (per run, checkpointed)

```
START → intake → readFloor → runTriggers* → prepare → gateNode → ...   (* new)
```

| Trigger | Fires when (placeholder) | Raises run floor to |
|---|---|---|
| `router_confidence_low` | router confidence < 0.6 | `raiseTo.run` (in_loop) |
| `run_budget_tokens` / `_dollars` / `_turns` | > 2,000,000 tokens / > $25 / > 200 turns | `raiseTo.run` |
| `data_stale` | newest market data > 300 s old | `raiseTo.run` |
| `paper_drift` | paper result outside the backtest's expected range (range comes with the signal) | `raiseTo.run` |
| `run_signals_invalid` | a reported signal is malformed (NaN, negative, wrong type, inverted range) | in_loop, always |
| `run_signals_unreadable` | the signal read throws | in_loop, always |

- `runTriggers` is a node before the gate, so the gate stays pure. It reads `deps.readRunSignals(threadId)`, evaluates with the pure `evaluateRunTriggers`, and writes `runFloorRaised`.
- **`runFloorRaised` is a tighten-only reducer channel.** Every write is merged with `strictest()`: node updates, a new invoke's input, and resumes alike. Once raised, nothing in the run can loosen it, including a caller passing `runFloor: "autonomous"` or `runFloorRaised: "autonomous"`. The only way back is a new run. The gate uses `strictest(runFloor, runFloorRaised)` as the run floor.
- Alerts: one per thread and trigger (`run:<thread>:<trigger>`), so a re-run after a crash doesn't repeat them.

### Global floor (every run)

| Trigger | Fires when (placeholder) | Raises global floor to |
|---|---|---|
| `daily_spend` | spend across all agents today (UTC) > $100 | `raiseTo.global` (in_loop) |
| `daily_loss` | loss today (UTC) > $250 | `raiseTo.global` |
| `kill_switch` | kill switch tripped | **in_loop, not configurable** (FR-6) |
| `unresolved_orders` | > 0 orders unresolved after reconciliation | `raiseTo.global` |
| `global_signals_invalid` / `_unreadable` | malformed / unreadable desk signals | in_loop, always |

- `applyGlobalTriggers()` (`src/floors/globalMonitor.ts`) is meant to run on a timer. It raises the floor through the new `raiseGlobalFloorFor()`: stricter only, and recorded in a new append-only **`floor_raises`** table in the same transaction.
- One alert per trigger per UTC day.
- If a raise can't be written, it sends the alerts first and then throws.
- It never lowers anything. `lowerGlobalFloor()` is still the only loosening path.

### Supporting pieces

- `src/floors/triggers.ts`: pure evaluators and signal types.
- `src/telemetry/floorAlerts.ts`:
  - `FloorAlertSink` and `InMemoryFloorAlertSink`.
  - `toLangSmithEvent()`: name `looper3.floor_raised`, tags `scope:` / `trigger:` / `floor:`, plus metadata.
  - `LangSmithFloorAlertSinkStub`, which throws on construction (no client, no key, no network).
- Config:
  - `floorTriggers` is validated fail closed: unknown keys, non-positive limits, a confidence threshold outside 0..1, a raise "to autonomous", or a day boundary other than UTC are all rejected.
  - `configVersion` 1.1.0 → **1.2.0**.

## 2. Choices the PRD left open

**Confirmed by the owner on 2026-11-05: keep all seven as built, and keep the placeholder thresholds for now.** The numbers in §4 are still placeholders. They're kept on purpose and still have to be set before anything leaves paper mode.

1. **Absent signal vs. malformed signal.** A signal a run doesn't report was not measured, so it doesn't trigger. One that is reported but broken raises to in_loop. Otherwise every run with no metrics would end up in_loop, while a broken meter has to fail closed.
2. **Limits are "more than", not "at".** At exactly the budget, or exactly on the edge of the drift range, nothing fires.
3. **Raise targets** are one value per scope (`raiseTo.run` / `raiseTo.global`), both set to in_loop as the strictest choice while undecided. Kill switch and invalid or unreadable signals are always in_loop.
4. **Separate `floor_raises` table.** Automatic raises are not logged in `floor_changes`. The Phase 1 anti-loosening SQL trigger trusts recent `floor_changes` rows, and an automatic raise must never be able to vouch for a later raw-SQL loosening. That's tested (AC7).
5. **The run floor can't be lowered within a run** (no operator path). Lowering means starting a new run. The PRD only describes manual lowering for "a floor" generally.
6. **"Daily" is the UTC calendar day.** Desk-local days (e.g. Chicago) would need a timezone decision.
7. **The kill switch trigger only sets the floor.** Halting execution stays the kill switch's own job, which is out of scope per the PRD.

## 3. Verification (real output)

```
$ npm run verify      # typecheck + check:tiers + tests
OK - every tool definition declares a tier and matches the registry.
 Test Files  14 passed (14)
      Tests  262 passed (262)        (Phase 3: 202; +60)
$ npm run build       → dist/ + policy JSON copied
$ npm run bench:gate  → autonomous gate decision p95 0.0066 ms (unchanged)
```

New `tests/floorTriggers.test.ts`:

- **Pure run triggers:** each of the 6 fires just over its line and not at it. Absent signals don't fire. 8 kinds of malformed input each raise to in_loop. A property test (1000 runs, random junk) never returns a raise "to autonomous".
- **Pure global triggers:** each of the 4 fires over its limit and not at it. The kill switch stays in_loop even when the config says on_loop. Malformed input raises to in_loop.
- **Config:** 7 fail-closed validation cases. The placeholders are listed as TODO.
- **Through the gate:**
  - Each run trigger raises the run floor and pauses the action, with `binding floor` / `floorSource run` and exactly one LangSmith-shaped alert.
  - **AC6 run half:** run A over budget pauses while run B executes, and the global floor is untouched.
  - The raise sticks after signals recover, and even against a caller asking for `autonomous`.
  - Unreadable signals raise to in_loop.
  - With no signals, M3/M4 behaviour is unchanged.
  - A crash at each node boundary still gives one alert per trigger and no effect.
  - Property test: the reducer can never loosen.
- **Global monitor:**
  - Each trigger raises the floor, records a `floor_raises` row (and no `floor_changes` row), and sends one alert.
  - **AC6 global half:** three different runs all pause with `floorSource global`.
  - One alert per day, and a new one the next day.
  - Unreadable signals raise to in_loop. An unreadable store already counts as in_loop, and the alert still goes out.
  - Healthy signals never lower the floor.
  - **AC7:** after an automatic raise, a raw SQL loosening is rejected, `floor_raises` can't be deleted, and the manual logged lower still works.

Existing suites changed only for the new node: the expected node traces, and the chaos loops extended by one boundary (in_loop 1..8, autonomous 1..7, reject 1..8). They still show one notification, one execution, no re-ask and no skipped gate.

One unrelated fix: the report-only "compiled-graph pass" benchmark test hit the 20 s default timeout once under full-suite load. It asserts nothing about speed, so it now gets 60 s.

## 4. Open decisions (not decided here)

**New from M5:**
- **The numbers**: router confidence 0.6; run budget 2M tokens / $25 / 200 turns; data freshness 300 s; daily spend $100 and loss $250; max unresolved orders 0.
- Kept as placeholders by the owner on 2026-11-05. Raise targets (in_loop), UTC day, and §2 items 1, 2 and 5 are confirmed as built.

**Carried forward:** build-harness tier-1 read; SME `maxAutonomy` placeholders; per-harness veto window (OQ2); approval and alert channel (OQ1); batched approvals (OQ5); NFR-1 scope (gate µs vs graph ~40–70 ms); duplicate submission of a completed `tool_call_id`.

## 5. Not verified / not done

- **No real signal sources.** Run signals (router confidence, token/dollar/turn usage, data age, paper vs backtest range) and desk signals (daily spend and loss, kill switch, reconciliation) come from injected functions. Production has to wire them to the real router, the cost meter, the market data feed, the backtest store, the desk ledger, and QS's kill switch and reconciliation.
- **Nothing runs the global monitor on a timer.** A scheduler has to call `applyGlobalTriggers()`.
- **No LangSmith.** Alerts are LangSmith-shaped records in memory. The real adapter and tags on traces are M7.
- **The real trading graph** is still not in this repo.
