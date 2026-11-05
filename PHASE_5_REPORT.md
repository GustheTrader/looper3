# PHASE 5 REPORT — M6 (roll-out to every harness)

Status: **done, committed.** Branch `looper3/m6-harness-rollout` (from `looper3/m5-floor-triggers`). Paper mode only, no network, nothing outside this folder touched.

M6 exit criterion (PRD): *all side-effecting tools in all harnesses gated.* **Met**, and checked in CI (`npm run check:gating`, now part of `npm run verify`).
Acceptance criterion 1, second half (a tier-4 merge in a build run hits approval): **covered**. The first half (a tier-1 read in build runs with no pause) is still the open build-harness decision; see §3.

## 1. What was built

| Piece | File | What it does |
|---|---|---|
| Harness manifest | `src/harness/manifest.ts` | Which tools each of the six harnesses may call. Hand-maintained next to the tier registry, like `TOOL_TIER`, and checked against each tool definition's `harnesses` field. |
| Gated tool runner | `src/harness/toolRunner.ts` | The one way a harness runs a tool. A runner is bound to its harness and SME when created, so a call cannot name a looser harness. Every call goes through the gate subgraph. Returns `executed`, `awaiting_approval`, `awaiting_veto` or `rejected`. `resume()` hands a decision to a parked thread. `createHarnessRunners()` builds all six on one gate graph. |
| CI check | `scripts/check-harness-gating.ts` | Fails when: a tool definition names no harness or an unknown one; the manifest drifts from the definitions; a manifest tool has no tier; or **any file other than the gate's `execute` node calls the executor** (an ungated path). Prints the harness × tool coverage table. |

Fail closed in the runner:
- A tool the harness doesn't list is rejected before the gate runs.
- An invalid hand-off is a rejection, not a retry.
- An outcome the runner doesn't recognise counts as rejected.

### Coverage (from `npm run check:gating`, best case: wanted autonomous, research SME, no floors)

| Harness | Tools (tier → on call / not on call) |
|---|---|
| research | query_backtest_store 1 → auto/auto · read_wiki 1 → auto/auto · write_raw_output 2 → auto/auto · post_wiki_page 3 → on/in |
| analysis | same as research + send_telegram_alert 3 → on/in |
| monitoring | query_backtest_store, read_wiki, write_raw_output → auto/auto · send_telegram_alert 3 → on/in |
| business_process | read_wiki 1 → on/in · post_wiki_page 3 → on/in · send_telegram_alert 3 → on/in · read_and_delete 4 → in/in |
| build | read_wiki 1 → on/in · merge_pull_request 4 → in/in · read_and_delete 4 → in/in |
| execution | submit_broker_order_stub 4 → in/in |

That's 21 harness/tool pairs, every one routed through the gate.

## 2. Verification (real output)

```
$ npm run verify    # typecheck + check:tiers + check:gating + tests
OK - every tool definition declares a tier and matches the registry.
OK - 21 harness/tool pairs, every one routed through the gate; no ungated execution path.
 Test Files  15 passed (15)
      Tests  276 passed (276)        (Phase 4: 262; +14)
```

New `tests/harnessRollout.test.ts`:
- **All pairs, through each harness's runner,** with someone on call and without. Each status matches the pure gate decision, and the gate node ran once per call.
- **AC1, second half:** a build merge is `awaiting_approval` at tier 4. After approval it runs once.
- **The trap tool** `read_and_delete` is tier 4 / approval in both harnesses that list it.
- **Fail-closed paths:** a tool outside the manifest is rejected with zero gate nodes run; an unknown harness or SME throws at construction; an invalid hand-off is rejected; asking for a stricter mode is honoured with binding `wanted`; and manifest lookups are exact.
- **CI check:** passes on the real tree. It fails on a tool definition with no harness (fixture), manifest drift, an untiered manifest tool, and a file that calls the executor directly.

## 3. Open decisions (not decided here)

- **Build-harness tier-1 read (PRD AC1, first half).** It still pauses (`on_loop`) because `build = on_loop`. PRD goal G1 says the gate covers side-effecting calls of **tier ≥ 2**. Reading that as "tier-1 reads skip the gate" would resolve AC1, but it loosens, so it was not done. Your call.
- **Handoffs (FR-7).** "Handoffs into a stricter harness take the target's caps." Each harness has its own runner, so a handoff is simply a call through the target harness's runner. No separate handoff code was added.
- Everything carried from earlier phases: SME caps, veto window, approval channel, batched approvals, M5 thresholds, NFR-1 scope, duplicate submissions.

## 4. Not verified / not done

- **The real harnesses are not in this repo.** The runner is the integration point they must use, and the CI check guards this repo only. Wiring Looper³'s real six harnesses (and the real trading graph) to `createGatedToolRunner` happens where they live.
- **The executor is still the fake `SideEffectLedger`.** Real tool implementations plug in behind the gate's execute node.
