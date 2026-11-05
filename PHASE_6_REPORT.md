# PHASE 6 REPORT — M7 (decision tags, weekly report, append-only audit table)

Status: **done, committed.** Branch `looper3/m7-telemetry-audit` (from `looper3/m6-harness-rollout`). This is the last rollout phase in the PRD. Paper mode only, no network, nothing outside this folder touched.

M7 exit criterion (PRD): *report produced; audit table rejects UPDATE and DELETE.* **Met** (§3).
Acceptance criterion 8 (every gate decision appears with all FR-9 fields): **met locally.** Each decision lands in the `gate_decisions` table and in a LangSmith-shaped event. Sending those events to LangSmith itself is a stub (§4).

With M7 done, every acceptance criterion except AC1 first half (the open build-harness question) is covered by tests.

## 1. What was built

### Graph: every outcome is recorded before anything runs

```
... → gateNode ─ autonomous ───────────────────────────→ record* → execute | reject
             └─ on/in_loop → armDeadline → veto|approval → [escalate] → record* → execute | reject
```

`record`* (new) runs once per action, before execution:
1. Writes the decision to `gate_decisions`. The key is `idem_key` = sha256(thread_id + tool_call_id), and a repeat insert is ignored.
2. Emits the decision's LangSmith-shaped event.
3. **If the write fails, the action does not execute** (fail closed). The event still goes out, marked `decision:rejected`.

`execute` now only runs when the action is cleared **and** its decision has been recorded.

### FR-9 fields

Each row and event carries:
- `effectiveMode`, `tier`, `binding`, `harness`, `smeId`, `configVersion`, `decision`, `decidedBy`, `latencyMs`
- plus `idem_key`, `thread_id`, `tool`, `floor_source`, `reject_reason` and `created_at`.

Event: name `looper3.gate_decision`, with the eight FR-9 `key:value` tags. Metadata holds latency, idemKey, threadId, tool, floorSource and rejectReason.

Decision values (the PRD's four):

| Outcome | decision | decidedBy |
|---|---|---|
| Autonomous execute | approved | policy |
| A person approves (approval or veto window) | approved | human |
| Veto window auto-approves at expiry | approved | timeout |
| A person says no to an approval | rejected | human |
| A person vetoes inside a veto window | vetoed | human |
| Approval deadline, or a person answering after it | timeout | timeout |
| Auto-approve refused at veto expiry (no one on call, irreversible, delivery unconfirmed) | timeout | policy |
| Malformed response, premature "timeout", other fail-closed refusals | rejected | policy |

### Audit table

| | SQLite (used here) | Postgres / Supabase |
|---|---|---|
| File | `src/audit/auditStore.ts` → `data/gate_audit.sqlite` (or `LOOPER3_AUDIT_DB`) | `migrations/postgres/001_gate_decisions.sql` (**not applied**) |
| Append-only | triggers abort UPDATE and DELETE | `revoke update, delete, truncate … from public`, plus a trigger that refuses even the table owner |
| Columns | identical; a test checks they match | |

Reading fails loudly: a missing or unreadable db throws, never returns `[]`.

### Weekly report

- `src/audit/weeklyReport.ts` + `npm run report:weekly -- --week YYYY-MM-DD [--db …] [--out …]`.
- The week runs 7 days from the given date, in UTC.
- Shows the **share of decisions bound by each cap, by harness and by SME**: all 8 bindings, including `wanted`, `reversibility` and `oncall`.
- Also shows outcome counts, the config versions seen, and median latency. Output is Markdown.

## 2. Choices the PRD left open (please confirm)

1. **The decision is recorded before execution, not after.** The audit then holds what was decided even if execution crashes. A failed audit write blocks execution, per G4 "fail closed everywhere". The cost: if the audit store is down, nothing runs.
2. **The decision mapping** in the table above, especially: a refused auto-approve at veto expiry is `timeout` (it ended at a deadline), and a person saying no in a veto window is `vetoed`, not `rejected`.
3. **`latencyMs`** is measured from the gate-pending notification (`prepare`) to the recorded decision. For a human decision that includes the wait. FR-9 doesn't say which latency it means.
4. **Extra columns** beyond the PRD DDL: `floor_source`, `harness`, `sme_id`, `latency_ms`, `reject_reason`. The weekly report needs harness and SME, and FR-9 lists latency.
5. **Week boundary:** 7 days from the given date, UTC, consistent with the M5 daily triggers.

## 3. Verification (real output)

```
$ npm run verify    # typecheck + check:tiers + check:gating + tests
OK - every tool definition declares a tier and matches the registry.
OK - 21 harness/tool pairs, every one routed through the gate; no ungated execution path.
 Test Files  16 passed (16)
      Tests  296 passed (296)        (Phase 5: 276; +20)
$ npm run build       → dist/ + policy JSON
$ npm run bench:gate  → autonomous gate decision p95 0.0045 ms
```

New `tests/audit.test.ts`:
- **AC8:** each of the 8 outcomes in the table writes exactly one row with the expected decision, decidedBy, mode, tier and reason, and the event carries all eight FR-9 tags.
  - Latency is checked (3 minutes waited ⇒ 180 000 ms).
  - A run-trigger decision records `binding floor` / `floorSource run`.
  - Calls through a harness runner are recorded under that harness.
- **M7 exit criterion:** raw SQL UPDATE and DELETE on `gate_decisions` are rejected, and the row is unchanged.
- **Replay safety:** a second insert of the same action is ignored. A crash at each of 9 node boundaries in the approval flow leaves exactly one row, one event and one execution.
- **Fail closed:** when the audit store throws, the action doesn't execute, and the event says `decision:rejected`.
- **Postgres parity:** the migration's columns equal the SQLite columns. It is append-only and warns against port 6543.
- **Report:**
  - The pure report counts shares by harness and SME within the week only.
  - `npm run report:weekly` produces the file end to end from real gate decisions.
  - A bad `--week` exits 2.

Existing suites changed only for the new node: expected traces gained `record`, and the chaos loops now reach every boundary (in_loop and reject 1..10, autonomous 1..8, the M4 timeout path 1..4 after resume). All still show one notification, one execution, no re-ask and no skipped gate.

## 4. Not verified / not done

- **LangSmith:** decisions and floor alerts are LangSmith-shaped events in memory. `LangSmithDecisionTelemetryStub` and `LangSmithFloorAlertSinkStub` throw on construction. Sending them needs a client and an API key, which means network, out of scope here.
- **Postgres / Supabase (NFR-4):** the migration is written but not applied, and failover is not tested. The checkpointer is still the M3 stub.
- **Nothing schedules the weekly report.** Running `report:weekly` every Monday is a cron or job on the desk.
- **The real harnesses and the real trading graph** are still not in this repo. Everything is proven against stand-ins.

## 5. Open decisions, all phases (for the owner)

From M7: §2 items 1–5.

Still open from earlier phases:
- Build-harness tier-1 read: PRD AC1 first half vs G1's "tier ≥ 2".
- SME `maxAutonomy` values.
- Per-harness veto window (OQ2, 120 s placeholder).
- Approval and alert channel (OQ1).
- Batched approvals (OQ5).
- M5 threshold numbers (OQ6; kept as placeholders by the owner).
- NFR-1 scope: the gate decision takes µs; a full graph pass takes about 40–70 ms.
- Duplicate submission of a completed `tool_call_id` re-asks. A fix would check `gate_decisions` by `idem_key` in `intake`, which M7 now makes possible.
