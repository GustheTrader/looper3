import { Command } from "@langchain/langgraph";
import { afterEach, describe, expect, it } from "vitest";

import type { GateState } from "../src/gate/state.js";
import type { ReplayFixture } from "../src/trading/fakeTradingGraph.js";
import { createHarness, fixtureById, keyFor, promptValues, stateOf, threadConfig, type Harness } from "./helpers/gateHarness.js";

const harnesses: Harness[] = [];

function harness(options: Parameters<typeof createHarness>[0] = {}): Harness {
  const created = createHarness(options);
  harnesses.push(created);
  return created;
}

afterEach(() => {
  while (harnesses.length > 0) harnesses.pop()?.cleanup();
});

interface ChaosRun {
  readonly decisionsDelivered: number;
  readonly promptsAfterDecision: number;
  readonly final: Partial<GateState>;
}

/**
 * NFR-2 crash simulation.
 *
 * `crashAt` = the node visit at which the process dies (1-based). The hook throws BEFORE the node
 * body runs, so the run aborts exactly at that node boundary and the checkpoint stays at the last
 * completed node. Then a NEW compiled graph (same checkpointer, same external side-effect stores)
 * resumes from that checkpoint - which is what a real restart does.
 */
async function runWithCrash(
  target: Harness,
  fixture: ReplayFixture,
  crashAt: number,
  decision: unknown = { approved: true, by: "jeffg" },
): Promise<ChaosRun> {
  const input = target.input(fixture);
  const config = threadConfig(fixture.threadId);

  let graph = target.graph();
  // crashAt === 1 means death before the first node completed: there is no checkpoint to resume,
  // so the work must be re-submitted. Any later crash resumes from the last checkpoint.
  let pendingInput: unknown = crashAt === 1 ? input : null;

  target.crashAtNodeVisit(crashAt);

  let decisionsDelivered = 0;
  let promptsAfterDecision = 0;
  let guard = 0;
  let current: unknown = undefined;

  for (;;) {
    guard += 1;
    if (guard > 10) throw new Error("chaos driver did not converge");

    try {
      current = await graph.invoke(pendingInput as never, config);
    } catch (error) {
      const message = String(error);
      if (/simulated process death/.test(message)) {
        graph = target.graph(); // process restart, same checkpointer
        pendingInput = null;
        continue;
      }
      // Death before the run became durable (no checkpoint / no input writes yet): the supervisor
      // re-submits the same pending action. Dedupe is what keeps this safe.
      if (/EmptyInputError|No checkpoint|no input writes/i.test(message) && pendingInput === null) {
        pendingInput = input;
        continue;
      }
      throw error;
    }

    if (promptValues(current).length === 0) break;
    if (decisionsDelivered > 0) promptsAfterDecision += 1;
    pendingInput = new Command({ resume: decision });
    decisionsDelivered += 1;
  }

  return { decisionsDelivered, promptsAfterDecision, final: stateOf(current) };
}

const IN_LOOP_FIXTURE = (): ReplayFixture => fixtureById("r04"); // execution / merge_pull_request
const AUTONOMOUS_FIXTURE = (): ReplayFixture => fixtureById("r15"); // research / read_wiki

describe("NFR-2 chaos: kill the process at every node boundary and resume", () => {
  it("counts the node boundaries of each flow (baseline)", async () => {
    const inLoop = harness();
    await inLoop.graph().invoke(inLoop.input(IN_LOOP_FIXTURE()), threadConfig("thread_baseline_inloop"));
    expect(inLoop.nodeTrace).toEqual(["intake", "readFloor", "prepare", "gateNode", "approval"]);
    expect(inLoop.nodeVisits()).toBe(5);

    const autonomous = harness();
    await autonomous.graph().invoke(autonomous.input(AUTONOMOUS_FIXTURE()), threadConfig("thread_baseline_auto"));
    expect(autonomous.nodeTrace).toEqual(["intake", "readFloor", "prepare", "gateNode", "execute"]);
    expect(autonomous.nodeVisits()).toBe(5);
  });

  it("in_loop flow: one notification, one execution, one decision, no re-ask, no skipped gate", async () => {
    const fixture = IN_LOOP_FIXTURE();
    // 1..6 covers death before intake, before each intermediate node, before the interrupt and
    // before execute (the 6th visit only happens after the approval resume).
    for (let crashAt = 1; crashAt <= 6; crashAt += 1) {
      const target = harness();
      const run = await runWithCrash(target, fixture, crashAt);
      const label = `crash at node visit ${crashAt}`;

      // never a second notification / scheduler registration
      expect(target.outbox.deliveries.length, label).toBe(1);
      expect(target.scheduler.all.length, label).toBe(1);
      // never a second execution
      expect(target.ledger.effectsFor(keyFor(fixture)), label).toBe(1);
      // never re-asks a decided gate
      expect(run.promptsAfterDecision, label).toBe(0);
      expect(run.decisionsDelivered, label).toBeLessThanOrEqual(1);
      // never skips the gate
      expect(target.nodeCounts["gateNode"] ?? 0, label).toBeGreaterThanOrEqual(1);
      // and the action still ends approved and executed
      expect(run.final.approved, label).toBe(true);
      expect(run.final.executed, label).toBe(true);
    }
  });

  it("autonomous flow: one notification, one execution, no interrupt at any boundary", async () => {
    const fixture = AUTONOMOUS_FIXTURE();
    for (let crashAt = 1; crashAt <= 6; crashAt += 1) {
      const target = harness();
      const run = await runWithCrash(target, fixture, crashAt);
      const label = `crash at node visit ${crashAt}`;

      expect(target.outbox.deliveries.length, label).toBe(1);
      expect(target.ledger.effectsFor(keyFor(fixture)), label).toBe(1);
      expect(run.decisionsDelivered, label).toBe(0);
      expect(run.final.executed, label).toBe(true);
      expect(target.nodeCounts["gateNode"] ?? 0, label).toBeGreaterThanOrEqual(1);
    }
  });

  it("a rejected approval stays rejected across a crash at every boundary", async () => {
    const fixture = IN_LOOP_FIXTURE();
    for (let crashAt = 1; crashAt <= 5; crashAt += 1) {
      const target = harness();
      const run = await runWithCrash(target, fixture, crashAt, { approved: false, by: "jeffg", reason: "no" });
      const label = `crash at node visit ${crashAt}`;

      expect(run.final.approved, label).toBe(false);
      expect(run.final.executed, label).toBe(false);
      expect(run.final.rejectReason, label).toBe("no");
      expect(target.ledger.effectsFor(keyFor(fixture)), label).toBe(0);
      expect(target.outbox.deliveries.length, label).toBe(1);
    }
  });

  it("re-running `prepare` (crash before its checkpoint) does not send a second notification", async () => {
    const fixture = IN_LOOP_FIXTURE();
    const target = harness();

    // Drive the same prepare twice through the outbox: exactly what a replay of that node does.
    const key = keyFor(fixture);
    const payload = {
      key,
      threadId: fixture.threadId,
      tool: fixture.tool,
      toolCallId: fixture.toolCallId,
      kind: "gate_pending" as const,
      createdAtMs: 1,
      vetoExpiresAt: 2,
    };
    expect(target.outbox.notifyOnce(payload).sent).toBe(true);
    expect(target.outbox.notifyOnce(payload).sent).toBe(false);
    expect(target.outbox.deliveries).toHaveLength(1);
    expect(target.outbox.attemptsCount).toBe(2);
  });
});

describe("NFR-2 chaos: crash in the middle of the side effect", () => {
  it("resumes without applying a tier-4 effect twice (idempotency key)", async () => {
    const fixture = IN_LOOP_FIXTURE(); // tier 4 -> non-replayable
    const target = harness();
    const config = threadConfig(fixture.threadId);
    const graph = target.graph();

    await graph.invoke(target.input(fixture), config); // parks at approval
    target.ledger.failNextApply = true; // effect applied, process dies before the checkpoint

    await expect(graph.invoke(new Command({ resume: { approved: true, by: "jeffg" } }), config)).rejects.toThrow(
      /simulated crash/,
    );
    expect(target.ledger.effectsFor(keyFor(fixture))).toBe(1);

    const resumed = await graph.invoke(null, config);
    expect(stateOf(resumed).executed).toBe(true);
    expect(promptValues(resumed)).toHaveLength(0); // the decided gate is not re-asked
    expect(target.ledger.effectsFor(keyFor(fixture))).toBe(1); // still exactly one effect
    expect(target.ledger.attemptsFor(keyFor(fixture))).toBe(2);
  });

  it("a completed thread is not re-executed by a further resume", async () => {
    const fixture = IN_LOOP_FIXTURE();
    const target = harness();
    const config = threadConfig(fixture.threadId);
    const graph = target.graph();

    await graph.invoke(target.input(fixture), config);
    await graph.invoke(new Command({ resume: { approved: true, by: "jeffg" } }), config);
    expect(target.ledger.effectsFor(keyFor(fixture))).toBe(1);

    const idle = await graph.invoke(null, config);
    expect(stateOf(idle).executed).toBe(true);
    expect(target.ledger.effectsFor(keyFor(fixture))).toBe(1);
    expect(target.ledger.attemptsFor(keyFor(fixture))).toBe(1);
    expect(promptValues(idle)).toHaveLength(0);
  });
});
