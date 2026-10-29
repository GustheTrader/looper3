# PHASE 1 REPORT — M1 (core types + tool-tier registry) and M2 (global floor store)

Status: **done, committed, stopped.** Phase 2 (M3) not started.
Branch: `looper3/m1-m2-core-and-floor-store` · Commit: `d12964f` · Author: GustheTrader <jeffgus@gmail.com>
Project root: `C:\Users\jeffg\Projects\looper3` (new standalone project, `git init`'d here; nothing outside this folder was touched).

---

## 1. What was built

### File manifest

| Path | Purpose |
|---|---|
| `src/policy/types.ts` | Leaf module (imports nothing): `Mode`/`MODES`/`MODE_ORDER`/`modeRank`/`isMode`, `Tier`/`isTier`, `HarnessId`, `SmeId`, `Binding`. Exists so the policy graph stays acyclic (`types <- config <- activePolicy <- modes`, `types <- toolTiers`). `modes.ts` re-exports the mode items so callers import from the module the brief names. |
| `src/policy/modes.ts` | `clamp`, `strictest`, `effectiveMode` (FR-2), `bindingCap` (FR-2/FR-3), `harnessCapFor`, `smeCapFor`, `tierCapFor`, `gateCaps` (telemetry helper), `BINDING_PRECEDENCE`. Pure: no I/O, no clock, no randomness. |
| `src/policy/config.ts` | `PolicyConfig`, `parsePolicyConfig` (schema + only-tighten invariants), `loadPolicyFromFile`, `defaultPolicyPath`, `assertPaperOnly`, `PolicyConfigError`, `BASELINE_HARNESS_CAPS`, `BASELINE_TIER_CAPS`. |
| `src/policy/activePolicy.ts` | Lazy, validated, process-wide config singleton + `resetActivePolicy()` for tests. |
| `src/policy/looper3.policy.json` | Versioned config: `configVersion 1.0.0`, six harness caps, four placeholder SME caps, FR-2 tier caps, `inLoopTimeoutsMinutes {30, 15}`, `oncallHeartbeatHours 8`, `todo` block with all six PRD open questions. |
| `src/policy/toolTiers.ts` | `TOOL_TIER` registry (versioned `1.0.0`, 8 entries), `lookupTier` (unknown ⇒ tier 4 / `unregistered`), `raiseTier` (raise-only), `validateRegistry` (structural invariants), `registeredTier`, `isRegisteredTool`, `UNREGISTERED_TIER`. |
| `src/policy/toolDefinition.ts` | `defineTool()` helper + `__looper3Tool` marker so a definition lives next to its tier. Type requires `tier`; runtime deliberately does not validate it (so the CI check can report it instead of the import throwing). |
| `src/flags/agentFlags.ts` | SQLite `agent_flags` store: `initializeAgentFlagsStore`, `tryReadGlobalFloor`, `readGlobalFloor`, `raiseGlobalFloor`, `lowerGlobalFloor`, `readFloorChanges`, `readOncallHeartbeat`, `setOncallHeartbeat`, `AgentFlagsError`. |
| `tools/*.ts` (8 files) | `read_wiki` (1), `query_backtest_store` (1), `write_raw_output` (2), `send_telegram_alert` (3), `post_wiki_page` (3), `read_and_delete` (4 — trap), `merge_pull_request` (4), `submit_broker_order_stub` (4, `touchesMoney: true`, clearly marked STUB). |
| `scripts/check-tool-tiers.ts` | `npm run check:tiers` CI gate. Imports the definitions, checks tier presence/validity, registry membership, and tier/`reversible`/`compensatingAction`/`touchesMoney` drift. Exports `checkToolTiers`, `listToolFiles`, `parseToolsDir`, `main` for tests. |
| `scripts/copy-assets.mjs` | Copies the policy JSON into `dist/` so the compiled build can resolve it. |
| `src/index.ts` | Barrel export of the policy + flags API. |
| `tests/*.test.ts` (6 files, 106 tests) | See §3. |
| `test/fixtures/**` | Negative fixtures: untiered tool, tier drift, unregistered tool (+ `_helper.ts` proving the underscore skip), non-tool module. |
| `package.json`, `tsconfig.json`, `tsconfig.build.json`, `vitest.config.ts`, `.gitignore` | Project scaffolding. `type: module`, NodeNext ESM, `strict`, `noUncheckedIndexedAccess`, `verbatimModuleSyntax`. |

### M1 behaviour

- `effectiveMode(wanted, harness, sme, tier, floor)` = `[harnessCap, smeCap, tierCap, floor]` folded into `wanted` with `clamp`. Rank direction is documented in code: **0 = `in_loop` = strictest**, so strictness is `Math.min` of ranks.
- `tier = undefined` (unregistered tool) ⇒ `in_loop`, and `bindingCap` returns `"unregistered"` before touching any cap.
- Tier caps: `1,2 → autonomous`, `3 → on_loop`, `4 → in_loop`. Config may tighten tier 3 to `in_loop`, never loosen 1/2/4.
- Harness caps exactly per PRD: research/analysis/monitoring `autonomous`; business_process/build `on_loop`; execution `in_loop` (and the config loader rejects any diff that loosens these, so execution cannot leave `in_loop` through config).
- `raiseTier(current, proposed)` returns the higher tier; invalid inputs collapse to 4.
- Registry loads through `validateRegistry`, which throws on: key/name mismatch, invalid tier, tier-1 not reversible, tier-4 marked reversible, empty `compensatingAction`, non-boolean `reversible`/`touchesMoney`. A bad registry fails at import time.

### M2 behaviour

Schema (created only by `initializeAgentFlagsStore`):

- `agent_flags(key text primary key, value text not null)`, seeded with `global_floor='autonomous'` and `oncall_heartbeat_until='0'` via `insert ... on conflict (key) do nothing`.
- `floor_changes(id, created_at, scope, from_value, to_value, operator, reason)` — append-only, enforced by two SQLite triggers that reject `UPDATE` and `DELETE`.
- A third trigger on `agent_flags` rejects any write that would move `global_floor` to a **looser known mode** unless a matching `floor_changes` row was inserted in the last 10 s. That means raw SQL (or a future convenience setter) cannot loosen the floor behind the logged API.

Read path: `new Database(path, { readonly: true, fileMustExist: true, timeout: 0 })`.
`readGlobalFloor()` returns `in_loop` for: missing file, missing parent dir, non-SQLite file, valid SQLite file without the table, missing row, empty value, unknown value (`"sudo"`), and a locked database (`BEGIN EXCLUSIVE` held elsewhere). A read never creates the file.

Write paths:

- `raiseGlobalFloor(path, to)` applies `strictest(current, to)`; asks to loosen ⇒ `{changed:false}`; already at target ⇒ `{changed:false}`; unreadable floor ⇒ `{changed:false, floor:'in_loop'}` and **no write** (nothing stricter exists); missing DB ⇒ no file is created. Verifies by read-back before returning.
- `lowerGlobalFloor(path, {to, operator, reason})` is the only loosening path. Requires non-empty trimmed `operator` and `reason`, requires a strictly looser target (equal/stricter ⇒ no-op, no log row), refuses when the current floor is unreadable, requires the DB to exist, writes the `floor_changes` row and the flag in one transaction, and verifies by read-back.
- `readFloorChanges()` throws when the log cannot be read — it never returns `[]` and pretends the audit trail is empty.
- `readOncallHeartbeat(path, now?)` treats missing DB, missing row, non-text, non-numeric, `0`, expired, and non-finite `now` as **inactive**.

Paper mode: `QS_PAPER_ONLY` defaults to `1`; any other value (including `"true"`) throws from `activePolicy()`. Config requires `paperOnly: true`.

## 2. Fail-closed map

| Failure | Result | Where proven |
|---|---|---|
| Tool not in registry | tier 4, mode `in_loop`, binding `unregistered` | `toolTiers.test.ts`, `integration.test.ts` |
| Definition missing/invalid tier | `check:tiers` exit 1 | `checkToolTiers.test.ts` + CLI run |
| Definition drifted from registry | `check:tiers` exit 1 | `checkToolTiers.test.ts` |
| Config file missing / bad JSON / invalid | throw (`PolicyConfigError`), never default | `config.test.ts` |
| Config loosens a cap | throw at load | `config.test.ts` |
| `QS_PAPER_ONLY != 1` | throw | `config.test.ts` |
| `agent_flags` missing / corrupt / locked / no table / no row / garbage | `in_loop` | `agentFlags.test.ts` |
| Floor cannot be read when raising | `in_loop`, no write | `agentFlags.test.ts` |
| Floor cannot be read when lowering | throws, refuses | `agentFlags.test.ts` |
| Unlogged loosening via raw SQL | SQLite trigger aborts | `agentFlags.test.ts` |
| Audit `floor_changes` rewritten | trigger aborts | `agentFlags.test.ts` |
| Heartbeat unreadable / expired / garbage | inactive | `agentFlags.test.ts` |

## 3. Verification (real output)

```
$ npm run typecheck          # tsc -p tsconfig.json --noEmit
TSC_EXIT:0

$ npx vitest run
 Test Files  6 passed (6)
      Tests  106 passed (106)
   Duration  2.46s

  tests/agentFlags.test.ts        35
  tests/config.test.ts            22
  tests/toolTiers.test.ts         18
  tests/modes.test.ts             15
  tests/checkToolTiers.test.ts    12
  tests/integration.test.ts        4

$ npm run check:tiers
  tools dir : C:\Users\jeffg\Projects\looper3\tools
  files     : 8
  tools     : 8 (8 tiered, 0 untiered)
  registry  : v1.0.0, 8 entries
OK - every tool definition declares a tier and matches the registry.        (exit 0)

$ npx tsx scripts/check-tool-tiers.ts --tools test/fixtures/tools-missing-tier
  [error] ...\legacy_tool.ts legacy_tool: MISSING/INVALID TIER: every tool definition must declare tier 1-4
FAILED - 1 error(s). Untiered or drifted tools cannot ship.                 (exit 1)

$ npm run build && node --input-type=module -e "<import dist/index.js>"
  copied ...\src\policy\looper3.policy.json -> ...\dist\policy\looper3.policy.json
  configVersion 1.0.0 | execution cap in_loop
  tier(read_and_delete) 4
  mode(...floor=read from missing db) in_loop
```

Exit-criteria proofs:

- **M1 — 100 % of registered tools tiered, CI blocks untiered:** 8/8 definitions tiered and registry-matched; `test/fixtures/tools-missing-tier` makes the check exit 1 (asserted in `checkToolTiers.test.ts` and demonstrated above by the CLI).
- **M2 — unreadable flag means `in_loop` in tests:** 7 distinct unreadable-store cases (`agentFlags.test.ts`) plus 2 cross-module cases (`integration.test.ts`) all resolve to `in_loop`.
- **Exhaustive table test:** all `3 × 6 × 4 × 5 × 3 = 1080` (wanted × harness × sme × tier incl. unregistered × floor) combinations assert `effectiveMode == strictest(wanted, all caps)`, that the result is never looser than any single cap or than `wanted`, and that `bindingCap` names a cap actually sitting at the effective rank.
- **Property test (fast-check, 750 runs):** random/malformed inputs; any malformed input anywhere in the five inputs must yield `in_loop`; valid inputs must equal the reference reduction and stay never-looser than every cap.

### Bug the tests caught (fixed before commit)

First implementation of `clamp`/`strictest` used `Math.max` over `MODE_ORDER` indices, which are **strictest-first** — i.e. it silently *loosened* (a malformed harness returned `autonomous`). The fast-check property test failed on counterexample `["autonomous","","",null,""]` and the exhaustive table test failed with it. Both now use `Math.min`, the direction is spelled out in the code comment, and the regression is locked by the property test.

## 4. Decisions I made (implementation-level, not PRD open questions)

1. **`bindingCap` tie-break.** When several caps sit at the effective rank: `floor > tier > sme > harness` (runtime safety, then intrinsic risk, then domain policy, then the broadest cap). Documented in `BINDING_PRECEDENCE`.
2. **`effectiveMode` signature.** Implemented exactly as FR-2 plus one optional trailing `policy` argument (defaults to the validated active config). This keeps the documented five-argument call and lets the future gate node stay pure by injecting the version-pinned config for the thread.
3. **Unknown harness / unknown SME at gate time ⇒ `in_loop`.** Config validation already rejects unknown harness/SME keys, so this only fires on a router bug — and it fails strict.
4. **`tier` is passed as `Tier | undefined`**; `undefined` is the unregistered representation, and `bindingCap` returns `"unregistered"` for it. `undefined` maps to tier 4 for mode purposes.
5. **Heartbeat format = epoch milliseconds string**, `'0'` = never set. The PRD only fixes the default `'0'`, so the unit had to be chosen; M4 should confirm ms vs s before shipping.
6. **Config bounds I picked:** `inLoopTimeoutsMinutes` integers 1–1440; `oncallHeartbeatHours` in `(0, 168]`; `configVersion` semver; unknown keys rejected everywhere.
7. **Registry layout.** The tier registry lives in `src/policy/toolTiers.ts` (versioned, importable, frozen) while each definition declares its tier next to it in `tools/`; `check:tiers` fails on any disagreement, so there is one source of truth enforced mechanically.
8. **`tools/` scan convention:** every `*.ts` there must export a tool definition; helper modules must be `_`-prefixed. Orphan registry entries (no definition file) are warnings, not errors, because later phases add the real tool directories per harness.
9. **`lowerGlobalFloor` no-ops rather than throws** on an equal-or-stricter target (it is not a loosening, so it is not this path's business) — the test asserts no log row is written.
10. **`raiseGlobalFloor` does not write when the store is unreadable**, because the floor already counts as `in_loop` and nothing stricter exists.
11. **Config path resolution:** `LOOPER3_POLICY_PATH` (explicit ⇒ honoured or throw), else the JSON shipped next to the module. No implicit fallback to a looser built-in default anywhere.

## 5. PRD open questions — left undecided, as instructed

All six live in `src/policy/looper3.policy.json` → `todo`, with matching keys `PRD_OQ1…PRD_OQ6`, and `smeMaxAutonomy` is flagged as placeholders:

| Key | Undecided thing |
|---|---|
| `PRD_OQ1_approvalNotificationChannel` | Telegram vs Pushover vs custom PWA. No notifier exists in this phase. |
| `PRD_OQ2_defaultVetoWindowPerHarness` | e.g. 120 s for business_process. No veto window implemented. |
| `PRD_OQ3_compensatingActions` | Which build/process tools have real compensating actions. `send_telegram_alert`'s `send_correction_message` is provisional. |
| `PRD_OQ4_heartbeatUx` | Manual vs calendar vs both. Only storage exists. |
| `PRD_OQ5_batchedApprovals` | Batching repeated tier-3 approvals. |
| `PRD_OQ6_dailyPortfolioThresholds` | Exact daily spend/loss limits that raise the global floor. |
| `smeMaxAutonomyPlaceholders` | `crypto=on_loop, nfl=on_loop, research=autonomous, coding=on_loop` are conservative guesses needing SME-owner sign-off. |

## 6. Ambiguities found in the PRD (not decided by me)

1. **Acceptance criterion 1 contradicts the harness cap table.** "Tier-1 read in build harness executes with no pause" cannot hold while `build = on_loop`, because `on_loop` routes to the veto window, which pauses the thread. I kept `build = on_loop` per FR-2/§Current model and did **not** special-case tier 1. Needs an owner decision (either build becomes `autonomous` for tier ≤ 2, or criterion 1 is rewritten).
2. **`bindingCap` has no label for "no cap bound".** FR-3's union is `harness|sme|tier|floor|unregistered`. When `wanted` is stricter than every cap (e.g. run floor `in_loop` with all caps `autonomous`), no cap is at the effective rank. I report the strictest cap so the field is always populated; if telemetry (M7) needs to distinguish this, the union probably needs a `wanted`/`runFloor` member.
3. **`"floor"` cannot say whether the run floor or the global floor bound.** FR-2 defines `floor = strictest(runFloor, globalFloor)` and FR-9 wants per-cap attribution. M5 should add a `floorSource` (or extend the binding union); I did not invent it now.
4. **Tier-1 semantics vs tier caps.** `TIER_CAP[1] = autonomous` but a harness cap can still force `on_loop` for a tier-1 read; whether tier-1 read-only actions should bypass harness caps entirely is the same question as (1).
5. **`on_loop` "does not block" vs "thread pauses"** (FR-3/FR-5): FR-3 says the veto window does not block and the thread pauses; both cannot be literally true. Not exercised in this phase (no node split yet) but M4 needs the resolution.
6. **`raiseGlobalFloor` from an unreadable store.** The PRD says the floor *counts as* `in_loop` when the flag can't be read; it does not say whether a tightening caller should then attempt a write. I chose not to write (nothing stricter exists) — worth a one-line confirmation.

## 7. Deliberately NOT done (later phases)

M3 gate subgraph/`gateNode`/`routeGate`/`approvalNode`/`vetoWindowNode`, M4 on_loop reversibility rule + heartbeat window + in_loop timeouts, M5 automatic run/global floor triggers, M6 roll-out to the other harnesses, M7 LangSmith tags + weekly report + Postgres `gate_decisions` audit table. No `@langchain/langgraph` dependency is installed yet (nothing imports it). No network, broker, exchange, Supabase, LangSmith, Telegram, or secret handling anywhere in this phase; `submit_broker_order_stub` is a definition only, with no adapter.

## 8. Environment notes

- Shell had `NODE_ENV=production` and `npm config omit=dev`, so the first `npm install` skipped devDependencies; installed with `npm install --include=dev`.
- `npm install` rewrote `package.json` (dropped `"type": "module"`, scripts, metadata), which made tsc treat every file as CommonJS. Restored afterwards; `package-lock.json` is committed. If a future phase runs `npm install <pkg>`, re-check `package.json`.
- Toolchain actually used: Node v26.7.0, npm 11.19.0, TypeScript 7.0.2 (`tsgo`), vitest 5.0.5, tsx 4.23.15, better-sqlite3 13.0.3 (prebuilt binary loaded fine despite npm's "install scripts not approved" warning), fast-check 4.10.2.
- Tests and the CI gate run against TypeScript sources (vitest / tsx); `npm run build` additionally emits runnable `dist/` ESM for a Node runtime and copies the policy JSON.

## 9. Suggested starting point for M3 (not started)

`effectiveMode` + `bindingCap` are pure and already take an injectable `PolicyConfig`, and `gateCaps` gives the four cap values for FR-9 tagging — the gate node should call these three with `floor = strictest(state.runFloor, readGlobalFloor())` and return `{gate:{mode, tier, binding}}`. The unregistered path is already `tier = undefined ⇒ in_loop/unregistered`, so the router's pending tool can be passed straight through without a pre-check.
