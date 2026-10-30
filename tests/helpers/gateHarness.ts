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
import { NotificationOutbox, SchedulerStub } from "../../src/gate/outbox.js";
import type { GateState } from "../../src/gate/state.js";
import { defaultPolicyPath, loadPolicyFromFile, type PolicyConfig } from "../../src/policy/config.js";
import { TOOL_TIER, type ToolTierRegistry } from "../../src/policy/toolTiers.js";
import type { Mode } from "../../src/policy/types.js";
import type { ReplayFixture } from "../../src/trading/fakeTradingGraph.js";

export const BASE_NOW = 1_700_000_000_000;

export interface HarnessOptions {
  readonly policy?: PolicyConfig;
  readonly registry?: ToolTierRegistry;
  readonly globalFloor?: Mode;
  readonly heartbeatActive?: boolean;
}

export interface Harness {
  readonly deps: GateDeps;
  readonly outbox: NotificationOutbox;
  readonly scheduler: SchedulerStub;
  readonly ledger: SideEffectLedger;
  readonly dbPath: string;
  readonly policy: PolicyConfig;
  readonly nodeCounts: Record<string, number>;
  readonly nodeTrace: string[];
  readonly clock: { now(): number; advance(ms: number): void };
  setHeartbeat(active: boolean): void;
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
  const outbox = new NotificationOutbox();
  const scheduler = new SchedulerStub();
  const ledger = new SideEffectLedger();
  const nodeCounts: Record<string, number> = {};
  const nodeTrace: string[] = [];
  // ONE saver per harness: two graph instances must share checkpoints to model a real resume.
  const saver: BaseCheckpointSaver = new MemorySaver();

  let nowMs = BASE_NOW;
  let heartbeat = options.heartbeatActive ?? false;
  let visits = 0;
  let crashAtVisit: number | null = null;

  const deps: GateDeps = {
    policy,
    registry,
    outbox,
    scheduler,
    ledger,
    readGlobalFloor: () => readGlobalFloor(dbPath),
    heartbeatActive: () => heartbeat,
    now: () => nowMs,
  };

  return {
    deps,
    outbox,
    scheduler,
    ledger,
    dbPath,
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
    crashAtNodeVisit: (visit: number | null) => {
      crashAtVisit = visit;
    },
    nodeVisits: () => visits,
    graph: () =>
      createGateGraph(deps, {
        onNode: (node: string) => {
          visits += 1;
          if (crashAtVisit !== null && visits >= crashAtVisit) {
            crashAtVisit = null; // one-shot: the "process" is dead
            throw new Error(`simulated process death at node boundary ${visits} (${node})`);
          }
          nodeCounts[node] = (nodeCounts[node] ?? 0) + 1;
          nodeTrace.push(node);
        },
      }).compile(saver),
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
