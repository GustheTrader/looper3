/**
 * M7: LangSmith binding-cap tags + weekly report + append-only audit table (FR-9).
 * PRD exit criterion: "report produced; audit table rejects UPDATE and DELETE".
 * PRD acceptance criterion 8: every gate decision appears (in telemetry and in gate_decisions)
 * with all FR-9 fields.
 */
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import Database from "better-sqlite3";
import { Command } from "@langchain/langgraph";
import { afterEach, describe, expect, it } from "vitest";

import { GATE_DECISIONS_COLUMNS, GateAuditError, readGateDecisions, SqliteGateAuditStore } from "../src/audit/auditStore.js";
import {
  decisionFor,
  decisionRecordFor,
  LANGSMITH_DECISION_STUB_STATUS,
  LangSmithDecisionTelemetryStub,
  type GateDecisionRecord,
} from "../src/audit/decisionRecord.js";
import { buildWeeklyReport, renderWeeklyReportMarkdown, WEEK_MS } from "../src/audit/weeklyReport.js";
import { createGateGraph } from "../src/gate/graph.js";
import type { GateState } from "../src/gate/state.js";
import { createGatedToolRunner } from "../src/harness/toolRunner.js";
import { main as weeklyReportMain } from "../scripts/weekly-report.js";
import { BASE_NOW, createHarness, fixtureById, keyFor, stateOf, threadConfig, type Harness } from "./helpers/gateHarness.js";

const ROOT = path.resolve(import.meta.dirname, "..");
const MIN = 60_000;
const harnesses: Harness[] = [];
const temp: string[] = [];
function harness(options: Parameters<typeof createHarness>[0] = {}): Harness {
  const created = createHarness(options);
  harnesses.push(created);
  return created;
}
afterEach(() => {
  while (harnesses.length > 0) harnesses.pop()?.cleanup();
  while (temp.length > 0) rmSync(temp.pop() as string, { recursive: true, force: true });
});

const FR9_TAGS = ["effectiveMode", "tier", "binding", "harness", "smeId", "configVersion", "decision", "decidedBy"];

const AUTONOMOUS = fixtureById("r15"); // research / read_wiki
const IN_LOOP = fixtureById("r04"); // execution / merge_pull_request
const ON_LOOP = fixtureById("r08"); // build / write_raw_output

function only(target: Harness): GateDecisionRecord {
  const rows = readGateDecisions(target.auditPath);
  expect(rows).toHaveLength(1);
  return rows[0] as GateDecisionRecord;
}

async function park(target: Harness, fixture = IN_LOOP) {
  const graph = target.graph();
  await graph.invoke(target.input(fixture), threadConfig(fixture.threadId));
  return graph;
}

describe("every outcome writes exactly one audit row with all FR-9 fields (AC8)", () => {
  it("autonomous execute: approved / policy", async () => {
    const target = harness();
    await target.graph().invoke(target.input(AUTONOMOUS), threadConfig(AUTONOMOUS.threadId));
    expect(only(target)).toMatchObject({
      idemKey: keyFor(AUTONOMOUS),
      threadId: AUTONOMOUS.threadId,
      tool: "read_wiki",
      tier: 1,
      effectiveMode: "autonomous",
      harness: "research",
      smeId: "research",
      configVersion: target.policy.configVersion,
      decision: "approved",
      decidedBy: "policy",
      rejectReason: null,
    });
    const event = target.decisionTelemetry.events[0];
    expect(event?.name).toBe("looper3.gate_decision");
    for (const tag of FR9_TAGS) expect(event?.tags.some((item) => item.startsWith(`${tag}:`)), tag).toBe(true);
    expect(typeof event?.metadata["latencyMs"]).toBe("number");
  });

  it("in_loop, a person approves: approved / human, latency measured from the pending notification", async () => {
    const target = harness();
    const graph = await park(target);
    target.clock.advance(3 * MIN);
    await graph.invoke(new Command({ resume: { approved: true, by: "jeffg" } }), threadConfig(IN_LOOP.threadId));
    expect(only(target)).toMatchObject({ decision: "approved", decidedBy: "human", effectiveMode: "in_loop", tier: 4, latencyMs: 3 * MIN });
  });

  it("in_loop, a person says no: rejected / human, with the reason", async () => {
    const target = harness();
    const graph = await park(target);
    await graph.invoke(new Command({ resume: { approved: false, by: "jeffg", reason: "not today" } }), threadConfig(IN_LOOP.threadId));
    expect(only(target)).toMatchObject({ decision: "rejected", decidedBy: "human", rejectReason: "not today" });
  });

  it("on_loop, a person vetoes in the window: vetoed / human", async () => {
    const target = harness();
    const graph = await park(target, ON_LOOP);
    await graph.invoke(new Command({ resume: { approved: false, by: "jeffg", reason: "veto" } }), threadConfig(ON_LOOP.threadId));
    expect(only(target)).toMatchObject({ decision: "vetoed", decidedBy: "human", effectiveMode: "on_loop" });
  });

  it("in_loop deadline: timeout / timeout", async () => {
    const target = harness();
    await park(target);
    target.clock.advance(target.policy.inLoopTimeoutsMinutes.tier4 * MIN);
    await target.fireDeadlines();
    expect(only(target)).toMatchObject({ decision: "timeout", decidedBy: "timeout", rejectReason: "timeout" });
  });

  it("veto window auto-approves at expiry: approved / timeout", async () => {
    const target = harness();
    await park(target, ON_LOOP);
    target.clock.advance(target.policy.vetoWindowSeconds * 1000);
    await target.fireDeadlines();
    expect(only(target)).toMatchObject({ decision: "approved", decidedBy: "timeout" });
  });

  it("veto auto-approve refused at expiry (no one on call): timeout / policy", async () => {
    const target = harness();
    await park(target, ON_LOOP);
    target.setHeartbeat(false);
    target.clock.advance(target.policy.vetoWindowSeconds * 1000);
    await target.fireDeadlines();
    expect(only(target)).toMatchObject({ decision: "timeout", decidedBy: "policy" });
    expect(only(target).rejectReason).toMatch(/no active on-call heartbeat/);
  });

  it("malformed approval response: rejected / policy", async () => {
    const target = harness();
    const graph = await park(target);
    await graph.invoke(new Command({ resume: { approved: "yes" } }), threadConfig(IN_LOOP.threadId));
    expect(only(target)).toMatchObject({ decision: "rejected", decidedBy: "policy" });
  });

  it("records which cap bound it: a run trigger shows as floor / run", async () => {
    const target = harness();
    target.setRunSignals(AUTONOMOUS.threadId, { routerConfidence: 0.01 });
    const graph = await park(target, AUTONOMOUS);
    await graph.invoke(new Command({ resume: { approved: false, by: "jeffg", reason: "no" } }), threadConfig(AUTONOMOUS.threadId));
    expect(only(target)).toMatchObject({ binding: "floor", floorSource: "run", effectiveMode: "in_loop" });
  });

  it("runner calls are audited under the runner's harness", async () => {
    const target = harness();
    const monitoring = createGatedToolRunner({ graph: target.graph(), harness: "monitoring", sme: "research" });
    await monitoring.call({ threadId: "thread_mon", toolCallId: "call_mon", tool: "write_raw_output" });
    expect(only(target)).toMatchObject({ harness: "monitoring", tool: "write_raw_output", decision: "approved" });
  });
});

describe("the audit table is append-only and replay-safe (M7 exit criterion)", () => {
  it("rejects UPDATE and DELETE, even as raw SQL", async () => {
    const target = harness();
    await target.graph().invoke(target.input(AUTONOMOUS), threadConfig(AUTONOMOUS.threadId));
    const db = new Database(target.auditPath);
    try {
      expect(() => db.prepare("update gate_decisions set decision = 'rejected'").run()).toThrow(/append-only: updates are rejected/);
      expect(() => db.prepare("delete from gate_decisions").run()).toThrow(/append-only: deletes are rejected/);
    } finally {
      db.close();
    }
    expect(only(target).decision).toBe("approved");
  });

  it("a second write of the same action is ignored", () => {
    const target = harness();
    const record = decisionRecordFor(
      { threadId: "t", pending: { tool: "read_wiki", toolCallId: "c" }, harness: "research", sme: "research", gate: { mode: "autonomous", tier: 1, binding: "wanted", floor: "autonomous", floorSource: "both", configVersion: "1.2.0", touchesMoney: false } },
      BASE_NOW,
    );
    expect(target.audit.record(record).inserted).toBe(true);
    expect(target.audit.record({ ...record, decision: "rejected" }).inserted).toBe(false);
    expect(readGateDecisions(target.auditPath)).toHaveLength(1);
  });

  it("a crash at every node boundary still leaves exactly one row and one event", async () => {
    for (let crashAt = 2; crashAt <= 10; crashAt += 1) {
      const target = harness();
      const config = threadConfig(IN_LOOP.threadId);
      const label = `crash at ${crashAt}`;
      let graph = target.graph();
      target.crashAtNodeVisit(crashAt);
      let input: unknown = target.input(IN_LOOP);
      for (let guard = 0; guard < 6; guard += 1) {
        try {
          const out = (await graph.invoke(input as never, config)) as { __interrupt__?: unknown[] };
          if (Array.isArray(out.__interrupt__) && out.__interrupt__.length > 0) {
            input = new Command({ resume: { approved: true, by: "jeffg" } });
            continue;
          }
          break;
        } catch (error) {
          if (!/simulated process death/.test(String(error))) throw error;
          graph = target.graph();
          input = null;
        }
      }
      expect(readGateDecisions(target.auditPath), label).toHaveLength(1);
      expect(target.decisionTelemetry.events, label).toHaveLength(1);
      expect(target.ledger.totalEffects, label).toBe(1);
    }
  });

  it("if the audit write fails, the action does not execute (fail closed)", async () => {
    const target = harness();
    const deps = {
      ...target.deps,
      audit: {
        record: () => {
          throw new GateAuditError("disk full");
        },
      },
    };
    const result = await createGateGraph(deps).compile().invoke(target.input(AUTONOMOUS), threadConfig(AUTONOMOUS.threadId));
    const state = stateOf(result) as GateState;
    expect(state.executed).toBe(false);
    expect(state.auditFailed).toBe(true);
    expect(state.rejectReason).toMatch(/audit write failed, not executed/);
    expect(target.ledger.totalEffects).toBe(0);
    expect(target.decisionTelemetry.events[0]?.tags).toContain("decision:rejected");
  });

  it("decisionFor never calls a non-executing outcome approved", () => {
    expect(decisionFor({ approved: false, gate: { mode: "in_loop" } as GateState["gate"] })).toBe("rejected");
    expect(decisionFor({ approved: true, timedOut: true })).toBe("approved");
    expect(decisionFor({})).toBe("rejected");
  });
});

describe("Postgres migration matches the SQLite table", () => {
  it("has the same columns and is append-only", () => {
    const sql = readFileSync(path.join(ROOT, "migrations/postgres/001_gate_decisions.sql"), "utf8");
    const body = /create table if not exists gate_decisions \(([\s\S]*?)\n\);/.exec(sql)?.[1] ?? "";
    const columns = body
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => /^[a-z_]+\s/.test(line))
      .map((line) => line.split(/\s+/)[0]);
    expect(columns).toEqual([...GATE_DECISIONS_COLUMNS]);
    expect(sql).toMatch(/revoke update, delete, truncate on gate_decisions from public/);
    expect(sql).toMatch(/before update or delete on gate_decisions/);
    expect(sql).toMatch(/never the transaction pooler \(port 6543\)/);
  });
});

describe("weekly report (FR-9)", () => {
  const weekStart = Date.UTC(2026, 10, 2); // Monday 2026-11-02
  const base = decisionRecordFor(
    { threadId: "t", pending: { tool: "read_wiki", toolCallId: "c" }, harness: "build", sme: "coding", gate: { mode: "on_loop", tier: 1, binding: "harness", floor: "autonomous", floorSource: "both", configVersion: "1.2.0", touchesMoney: false } },
    weekStart,
  );
  const rows: GateDecisionRecord[] = [
    { ...base, idemKey: "a", binding: "harness", createdAtMs: weekStart + 1 },
    { ...base, idemKey: "b", binding: "harness", createdAtMs: weekStart + 2 },
    { ...base, idemKey: "c", binding: "tier", createdAtMs: weekStart + 3, latencyMs: 50 },
    { ...base, idemKey: "d", binding: "oncall", harness: "research", smeId: "research", createdAtMs: weekStart + 4 },
    { ...base, idemKey: "late", binding: "floor", createdAtMs: weekStart + WEEK_MS }, // next week
  ];

  it("counts the share bound by each cap, by harness and by SME, within the week only", () => {
    const report = buildWeeklyReport(rows, weekStart);
    expect(report.total).toBe(4);
    expect(report.byHarness["build"]).toMatchObject({ total: 3, byBinding: expect.objectContaining({ harness: 2, tier: 1, floor: 0 }) });
    expect(report.byHarness["research"]?.byBinding.oncall).toBe(1);
    expect(report.bySme["coding"]?.total).toBe(3);
    expect(report.configVersions).toEqual(["1.2.0"]);

    const markdown = renderWeeklyReportMarkdown(report);
    expect(markdown).toMatch(/2026-11-02 to 2026-11-08/);
    expect(markdown).toMatch(/\| build \| 3 \| 66\.7% \| 0\.0% \| 33\.3% \|/); // harness | sme | tier
    expect(markdown).toMatch(/## By SME/);
  });

  it("is produced end to end from real gate decisions by `npm run report:weekly`", async () => {
    const target = harness();
    await target.graph().invoke(target.input(AUTONOMOUS), threadConfig(AUTONOMOUS.threadId));
    const dir = mkdtempSync(path.join(os.tmpdir(), "looper3-report-"));
    temp.push(dir);
    const out = path.join(dir, "weekly.md");
    const day = new Date(BASE_NOW).toISOString().slice(0, 10);
    expect(weeklyReportMain(["--week", day, "--db", target.auditPath, "--out", out])).toBe(0);
    const text = readFileSync(out, "utf8");
    expect(text).toMatch(/Decisions: \*\*1\*\*/);
    expect(text).toMatch(/\| research \| 1 \|/);
    expect(weeklyReportMain(["--week", "last-monday"])).toBe(2);
  });
});

describe("LangSmith decision adapter", () => {
  it("is a clearly marked stub", () => {
    expect(() => new LangSmithDecisionTelemetryStub()).toThrow(LANGSMITH_DECISION_STUB_STATUS);
  });

  it("the SQLite store refuses to read a missing db instead of returning nothing", () => {
    expect(() => readGateDecisions(path.join(os.tmpdir(), "looper3-no-such-audit.sqlite"))).toThrow(GateAuditError);
    const dir = mkdtempSync(path.join(os.tmpdir(), "looper3-a-"));
    temp.push(dir);
    expect(new SqliteGateAuditStore(path.join(dir, "a.sqlite"))).toBeTruthy();
  });
});
