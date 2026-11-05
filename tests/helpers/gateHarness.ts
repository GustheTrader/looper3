/**
 * Shared test harness: a gate graph wired to a REAL (temporary) agent_flags store, an in-memory
 * outbox/scheduler/ledger, a controllable clock and node instrumentation.
 *
 * No network, no broker, no Supabase: everything here is the paper-mode stand-in.
 */
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { MemorySaver, type BaseCheckpointSaver } from "@langchain/langgraph";

import { initializeAgentFlagsStore, raiseGlobalFloor, readGlobalFloor } from "../../src/flags/agentFlags.js";
import { SideEffectLedger } from "../../src/gate/executor.js";
import { createGateGraph } from "../../src/gate/graph.js";
import { idempotencyKey } from "../../src/gate/keys.js";
import type { GateDeps } from "../../src/gate/nodes.js";
import { fireDueDeadlines, type DeadlineFiring } from "../../src/gate/deadlines.js";
import { AlertOutbox, NotificationOutbox, SchedulerStub } from "../../src/gate/outbox.js";
import type { GateState } from "../../src/gate/state.js";
import type { RunSignals } from "../../src/floors/triggers.js";
import { InMemoryFloorAlertSink } from "../../src/telemetry/floorAlerts.js";
import { SqliteGateAuditStore } from "../../src/audit/auditStore.js";
import { InMemoryDecisionTelemetry } from "../../src/audit/decisionRecord.js";
import { defaultPolicyPath, loadPolicyFromFile, type PolicyConfig } from "../../src/policy/config.js";
import { TOOL_TIER, type ToolTierRegistry } from "../../src/policy/toolTiers.js";
import type { Mode } from "../../src/policy/types.js";
import type { ReplayFixture } from "../../src/trading/fakeTradingGraph.js";

export const BASE_NOW = 1_700_000_000_000;

export interface HarnessOptions {
  readonly policy?: PolicyConfig;
  readonly registry?: ToolTierRegistry;
  readonly globalFloor?: Mode;
  /**
   * Test convenience, NOT a production default: true unless a test says otherwise, so the M3
   * suites keep modelling "someone is on call". Every M4 off-call path sets it to false explicitly.
   */
  readonly heartbeatActive?: boolean;
  /** Does the stub channel confirm delivery? Default true here; the outbox itself defaults to false. */
  readonly deliveryConfirmed?: boolean;
}

export interface Harness {
  readonly deps: GateDeps;
  readonly outbox: NotificationOutbox;
  readonly scheduler: SchedulerStub;
  readonly ledger: SideEffectLedger;
  readonly alerts: AlertOutbox;
  readonly floorAlerts: InMemoryFloorAlertSink;
  readonly audit: SqliteGateAuditStore;
  readonly decisionTelemetry: InMemoryDecisionTelemetry;
  readonly dbPath: string;
  readonly auditPath: string;
  readonly policy: PolicyConfig;
  readonly nodeCounts: Record<string, number>;
  readonly nodeTrace: string[];
  readonly clock: { now(): number; advance(ms: number): void };
  setHeartbeat(active: boolean): void;
  /** M5: what this harness reports as a thread's run signals (undefined = nothing measured). */
  setRunSignals(threadId: string, signals: RunSignals | undefined): void;
  /** Runs the M4 deadline runner against the latest compiled graph at the harness clock. */
  fireDeadlines(): Promise<DeadlineFiring[]>;
  /**
   * One-shot crash injection: the Nth node visit throws BEFORE the node body runs, aborting the
   * run at that node boundary exactly as a killed process would. The checkpoint stays at the last
   * completed node, and `graph()` can be called again to resume from it.
   */
  crashAtNodeVisit(visit: number | null): void;
  nodeVisits(): number;
  graph(): ReturnType<ReturnType<typeof createGateGraph>["compile"]>;
  input(fixture: ReplayFixture): Partial<GateState>;
  cleanup(): void;
}

export function createHarness(options: HarnessOptions = {}): Harness {
  const dir = mkdtempSync(path.join(process.env["TMPDIR"] ?? os.tmpdir(), "looper3-gate-"));
  const dbPath = path.join(dir, "agent_flags.sqlite");
  initializeAgentFlagsStore(dbPath);
  if (options.globalFloor !== undefined) raiseGlobalFloor(dbPath, options.globalFloor);

  const policy = options.policy ?? loadPolicyFromFile(defaultPolicyPath());
  const registry = options.registry ?? TOOL_TIER;
  const outbox = new NotificationOutbox({ autoConfirm: options.deliveryConfirmed ?? true });
  const alerts = new AlertOutbox();
  const floorAlerts = new InMemoryFloorAlertSink();
  const runSignals = new Map<string, RunSignals | undefined>();
  const auditPath = path.join(dir, "gate_audit.sqlite");
  const audit = new SqliteGateAuditStore(auditPath);
  const decisionTelemetry = new InMemoryDecisionTelemetry();
  const scheduler = new SchedulerStub();
  const ledger = new SideEffectLedger();
  const nodeCounts: Record<string, number> = {};
  const nodeTrace: string[] = [];
  // ONE saver per harness: two graph instances must share checkpoints to model a real resume.
  const saver: BaseCheckpointSaver = new MemorySaver();

  let nowMs = BASE_NOW;
  let heartbeat = options.heartbeatActive ?? true;
  let visits = 0;
  let crashAtVisit: number | null = null;

  const deps: GateDeps = {
    policy,
    registry,
    outbox,
    scheduler,
    ledger,
    alerts,
    readGlobalFloor: () => readGlobalFloor(dbPath),
    heartbeatActive: () => heartbeat,
    deliveryConfirmed: (key) => outbox.isDeliveryConfirmed(key),
    now: () => nowMs,
    readRunSignals: (threadId) => runSignals.get(threadId),
    floorAlerts,
    audit,
    decisionTelemetry,
  };

  const onNode = (node: string) => {
    visits += 1;
    if (crashAtVisit !== null && visits >= crashAtVisit) {
      crashAtVisit = null; // one-shot: the "process" is dead
      throw new Error(`simulated process death at node boundary ${visits} (${node})`);
    }
    nodeCounts[node] = (nodeCounts[node] ?? 0) + 1;
    nodeTrace.push(node);
  };
  const compileGraph = () => createGateGraph(deps, { onNode }).compile(saver);

  return {
    deps,
    outbox,
    scheduler,
    ledger,
    alerts,
    floorAlerts,
    audit,
    decisionTelemetry,
    dbPath,
    auditPath,
    policy,
    nodeCounts,
    nodeTrace,
    clock: {
      now: () => nowMs,
      advance: (ms: number) => {
        nowMs += ms;
      },
    },
    setHeartbeat: (active: boolean) => {
      heartbeat = active;
    },
    setRunSignals: (threadId, signals) => {
      runSignals.set(threadId, signals);
    },
    fireDeadlines: () => fireDueDeadlines(compileGraph(), scheduler, nowMs),
    crashAtNodeVisit: (visit: number | null) => {
      crashAtVisit = visit;
    },
    nodeVisits: () => visits,
    graph: compileGraph,
    input: (fixture: ReplayFixture) => ({
      threadId: fixture.threadId,
      pending: { tool: fixture.tool, toolCallId: fixture.toolCallId, args: { fixture: fixture.id } },
      wantedMode: fixture.wantedMode,
      runFloor: fixture.runFloor,
      harness: fixture.harness,
      sme: fixture.sme,
    }),
    cleanup: () => rmSync(dir, { recursive: true, force: true }),
  };
}

export function threadConfig(threadId: string) {
  return { configurable: { thread_id: threadId } };
}

/** Interrupt payloads from an invoke result. */
export function promptValues(result: unknown): unknown[] {
  const interrupts = (result as { __interrupt__?: { value?: unknown }[] } | undefined)?.__interrupt__;
  if (!Array.isArray(interrupts)) return [];
  return interrupts.map((item) => item?.value);
}

export function stateOf(result: unknown): Partial<GateState> {
  return result as Partial<GateState>;
}

export function keyFor(fixture: ReplayFixture): string {
  return idempotencyKey(fixture.threadId, fixture.toolCallId);
}

export function loadReplayFixtures(): ReplayFixture[] {
  const url = new URL("../../test/fixtures/replay-set.json", import.meta.url);
  return JSON.parse(readFileSync(fileURLToPath(url), "utf8")) as ReplayFixture[];
}

export function fixtureById(id: string): ReplayFixture {
  const fixture = loadReplayFixtures().find((item) => item.id === id);
  if (fixture === undefined) throw new Error(`no replay fixture ${id}`);
  return fixture;
}
