/**
 * M4: on_loop reversibility rule + on-call heartbeat + in_loop timeouts (FR-4, FR-5).
 *
 * PRD exit criterion for M4: "fail-closed paths tested; no auto-approve without heartbeat".
 * PRD acceptance criteria covered here: AC4 (no heartbeat => expired on_loop action REJECTED)
 * and AC5 (irreversible action under an on_loop harness resolves to in_loop).
 */
import { Command } from "@langchain/langgraph";
import fc from "fast-check";
import { afterEach, describe, expect, it } from "vitest";

import { checkInOnCall, checkOutOnCall, readOncallHeartbeat } from "../src/flags/agentFlags.js";
import { fireDueDeadlines, timeoutPayload } from "../src/gate/deadlines.js";
import { createGateGraph } from "../src/gate/graph.js";
import { approvalTimeoutMs, gateDecisionFor, onLoopEligible, routeAfterDecision } from "../src/gate/nodes.js";
import type { GateState } from "../src/gate/state.js";
import { effectiveMode, strictest } from "../src/policy/modes.js";
import { TOOL_TIER, type ToolTierEntry, type ToolTierRegistry } from "../src/policy/toolTiers.js";
import { HARNESS_IDS, MODES, SME_IDS, modeRank, type Tier } from "../src/policy/types.js";
import {
  BASE_NOW,
  createHarness,
  fixtureById,
  keyFor,
  loadReplayFixtures,
  promptValues,
  stateOf,
  threadConfig,
  type Harness,
} from "./helpers/gateHarness.js";

const MIN = 60_000;
const harnesses: Harness[] = [];

function harness(options: Parameters<typeof createHarness>[0] = {}): Harness {
  const created = createHarness(options);
  harnesses.push(created);
  return created;
}

afterEach(() => {
  while (harnesses.length > 0) harnesses.pop()?.cleanup();
});

function prompts(result: unknown): { kind?: string; tier?: number; expiresAt?: number }[] {
  return promptValues(result) as { kind?: string; tier?: number; expiresAt?: number }[];
}

const ON_LOOP = fixtureById("r08"); // build / coding / write_raw_output: tier 2, reversible
const TIER4 = fixtureById("r04"); // execution / merge_pull_request
const TIER3_FORCED = fixtureById("r03"); // execution / send_telegram_alert (tier 3 -> in_loop)
const MONEY = fixtureById("r01"); // execution / submit_broker_order_stub (touchesMoney)

const IRREVERSIBLE_REGISTRY: ToolTierRegistry = {
  ...TOOL_TIER,
  flush_and_forget: { tool: "flush_and_forget", tier: 3 as Tier, reversible: false } satisfies ToolTierEntry,
};

// ---------------------------------------------------------------------------------------------
// FR-5 at gate time (pure)
// ---------------------------------------------------------------------------------------------

describe("FR-5 gate-time conversion (pure gate)", () => {
  const policy = createHarness().policy;
  const base = {
    wantedMode: "autonomous" as const,
    harness: "build",
    sme: "coding",
    runFloor: "autonomous" as const,
    globalFloor: "autonomous" as const,
  };

  it("AC5: an irreversible action with no compensation under an on_loop harness resolves to in_loop", () => {
    for (const oncallActive of [true, false]) {
      const decision = gateDecisionFor({ ...base, tool: "flush_and_forget", oncallActive }, policy, IRREVERSIBLE_REGISTRY);
      expect(decision.mode).toBe("in_loop");
      expect(decision.binding).toBe("reversibility");
      expect(decision.convertedFromOnLoop).toBe("irreversible");
    }
  });

  it("a compensating action keeps an irreversible tier-3 action eligible for on_loop", () => {
    expect(TOOL_TIER["send_telegram_alert"]?.reversible).toBe(false);
    const decision = gateDecisionFor({ ...base, tool: "send_telegram_alert", oncallActive: true }, policy);
    expect(decision.mode).toBe("on_loop");
    expect(decision.convertedFromOnLoop).toBeUndefined();
  });

  it("no on-call heartbeat at gate time turns on_loop into in_loop, binding 'oncall'", () => {
    const decision = gateDecisionFor({ ...base, tool: "write_raw_output", oncallActive: false }, policy);
    expect(decision.mode).toBe("in_loop");
    expect(decision.binding).toBe("oncall");
    expect(decision.convertedFromOnLoop).toBe("no_oncall");
  });

  it("anything but literal true counts as no heartbeat", () => {
    for (const bogus of [undefined, null, 1, "true", {}]) {
      const decision = gateDecisionFor({ ...base, tool: "write_raw_output", oncallActive: bogus as unknown as boolean }, policy);
      expect(decision.mode, String(bogus)).toBe("in_loop");
    }
  });

  it("only ever touches on_loop results, and only ever tightens (property)", () => {
    const tools = [...Object.keys(IRREVERSIBLE_REGISTRY), "mystery_tool"];
    fc.assert(
      fc.property(
        fc.constantFrom(...tools),
        fc.constantFrom(...MODES),
        fc.constantFrom(...HARNESS_IDS),
        fc.constantFrom(...SME_IDS),
        fc.constantFrom(...MODES),
        fc.constantFrom(...MODES),
        fc.boolean(),
        (tool, wantedMode, harnessId, sme, runFloor, globalFloor, oncallActive) => {
          const tier = IRREVERSIBLE_REGISTRY[tool]?.tier;
          const reduced = effectiveMode(wantedMode, harnessId, sme, tier, strictest(runFloor, globalFloor), policy);
          const decision = gateDecisionFor(
            { tool, wantedMode, harness: harnessId, sme, runFloor, globalFloor, oncallActive },
            policy,
            IRREVERSIBLE_REGISTRY,
          );
          expect(modeRank(decision.mode)).toBeLessThanOrEqual(modeRank(reduced));
          if (reduced !== "on_loop") expect(decision.mode).toBe(reduced);
          if (decision.mode === "on_loop") {
            expect(oncallActive).toBe(true);
            expect(onLoopEligible(tool, IRREVERSIBLE_REGISTRY)).toBe(true);
          }
        },
      ),
      { numRuns: 2000 },
    );
  });

  it("with someone on call, every M3 replay lock is unchanged; without, on_loop locks become approval", () => {
    for (const fixture of loadReplayFixtures()) {
      const input = { ...fixture, oncallActive: true };
      const onCall = gateDecisionFor(input, policy);
      expect(onCall.mode, fixture.id).toBe(fixture.gateMode);

      const offCall = gateDecisionFor({ ...input, oncallActive: false }, policy);
      expect(offCall.mode, fixture.id).toBe(fixture.gateMode === "on_loop" ? "in_loop" : fixture.gateMode);
    }
  });

  it("copies touchesMoney from the registry; unregistered tools never claim it", () => {
    expect(gateDecisionFor({ ...base, tool: "submit_broker_order_stub", oncallActive: true }, policy).touchesMoney).toBe(true);
    expect(gateDecisionFor({ ...base, tool: "write_raw_output", oncallActive: true }, policy).touchesMoney).toBe(false);
    expect(gateDecisionFor({ ...base, tool: "mystery_tool", oncallActive: true }, policy).touchesMoney).toBe(false);
  });
});

// ---------------------------------------------------------------------------------------------
// FR-5 through the graph
// ---------------------------------------------------------------------------------------------

describe("FR-5 through the graph", () => {
  it("no heartbeat at gate time: the action goes to approval, not to a veto window", async () => {
    const target = harness({ heartbeatActive: false });
    const result = await target.graph().invoke(target.input(ON_LOOP), threadConfig(ON_LOOP.threadId));
    expect(prompts(result)[0]?.kind).toBe("approval");
    const state = stateOf(result) as GateState;
    expect(state.oncallActive).toBe(false);
    expect(state.gate?.binding).toBe("oncall");
    // tier 2 forced into in_loop gets the non-tier-4 timeout (tier3Forced, 30 min)
    expect(state.approvalExpiresAt).toBe(BASE_NOW + target.policy.inLoopTimeoutsMinutes.tier3Forced * MIN);
    expect(target.scheduler.all.map((job) => job.kind)).toEqual(["approval_deadline"]);
  });

  it("a heartbeat read that throws counts as no heartbeat", async () => {
    const target = harness();
    const deps = {
      ...target.deps,
      heartbeatActive: () => {
        throw new Error("agent_flags locked");
      },
    };
    const graph = createGateGraph(deps).compile();
    const result = await graph.invoke(target.input(ON_LOOP), threadConfig(ON_LOOP.threadId));
    expect(prompts(result)[0]?.kind).toBe("approval");
    expect(stateOf(result).gate?.binding).toBe("oncall");
  });

  it("is wired to the real agent_flags heartbeat: check-in keeps on_loop, check-out forces in_loop", async () => {
    const target = harness();
    const deps = { ...target.deps, heartbeatActive: () => readOncallHeartbeat(target.dbPath, target.clock.now()).active };
    const graph = createGateGraph(deps).compile();

    checkInOnCall(target.dbPath, "jeffg", target.policy.oncallHeartbeatHours, target.clock.now());
    const onCall = await graph.invoke(target.input(ON_LOOP), threadConfig("thread_checkin"));
    expect(prompts(onCall)[0]?.kind).toBe("veto");

    checkOutOnCall(target.dbPath, "jeffg", target.clock.now());
    const offCall = await graph.invoke(
      { ...target.input(ON_LOOP), pending: { tool: ON_LOOP.tool, toolCallId: "call_after_checkout" } },
      threadConfig("thread_checkout"),
    );
    expect(prompts(offCall)[0]?.kind).toBe("approval");
  });
});

// ---------------------------------------------------------------------------------------------
// FR-5 at veto expiry (AC4)
// ---------------------------------------------------------------------------------------------

describe("FR-5 veto expiry: no auto-approve without heartbeat (AC4)", () => {
  async function parkInVeto(target: Harness) {
    const result = await target.graph().invoke(target.input(ON_LOOP), threadConfig(ON_LOOP.threadId));
    expect(prompts(result)[0]?.kind).toBe("veto");
    return result;
  }

  it("does nothing before the window closes", async () => {
    const target = harness();
    await parkInVeto(target);
    target.clock.advance(target.policy.vetoWindowSeconds * 1000 - 1);
    expect(await target.fireDeadlines()).toEqual([]);
    expect(target.ledger.totalEffects).toBe(0);
  });

  it("auto-approves at expiry with heartbeat + eligible action + confirmed delivery, and does not escalate", async () => {
    const target = harness();
    await parkInVeto(target);
    target.clock.advance(target.policy.vetoWindowSeconds * 1000);
    const firings = await target.fireDeadlines();
    expect(firings.map((firing) => firing.outcome)).toEqual(["resumed"]);

    const state = (await target.graph().getState(threadConfig(ON_LOOP.threadId))).values as GateState;
    expect(state.approved).toBe(true);
    expect(state.decidedBy).toBe("timeout");
    expect(state.executed).toBe(true);
    expect(target.ledger.effectsFor(keyFor(ON_LOOP))).toBe(1);
    expect(target.alerts.alerts).toHaveLength(0);
  });

  it("AC4: REJECTS on expiry when the heartbeat lapsed during the window, and escalates once", async () => {
    const target = harness();
    await parkInVeto(target);
    target.setHeartbeat(false);
    target.clock.advance(target.policy.vetoWindowSeconds * 1000);
    await target.fireDeadlines();

    const state = (await target.graph().getState(threadConfig(ON_LOOP.threadId))).values as GateState;
    expect(state.approved).toBe(false);
    expect(state.executed).toBe(false);
    expect(state.rejectReason).toMatch(/no active on-call heartbeat/);
    expect(state.escalated).toBe(true);
    expect(target.ledger.totalEffects).toBe(0);
    expect(target.alerts.alerts).toHaveLength(1);
    expect(target.alerts.alerts[0]?.severity).toBe("high");
  });

  it("REJECTS on expiry when the heartbeat read throws", async () => {
    const target = harness();
    let broken = false;
    const deps = {
      ...target.deps,
      heartbeatActive: () => {
        if (broken) throw new Error("db gone");
        return true;
      },
    };
    const graph = createGateGraph(deps).compile(); // one graph, one saver, for both runs
    const config = threadConfig(ON_LOOP.threadId);
    const first = await graph.invoke(target.input(ON_LOOP), config);
    expect(prompts(first)[0]?.kind).toBe("veto");

    broken = true;
    target.clock.advance(target.policy.vetoWindowSeconds * 1000);
    expect((await fireDueDeadlines(graph, target.scheduler, target.clock.now())).map((f) => f.outcome)).toEqual(["resumed"]);
    const state = (await graph.getState(config)).values as GateState;
    expect(state.approved).toBe(false);
    expect(state.rejectReason).toMatch(/no active on-call heartbeat/);
    expect(target.ledger.totalEffects).toBe(0);
  });

  it("REJECTS on expiry when delivery of the notification was never confirmed", async () => {
    const target = harness({ deliveryConfirmed: false });
    await parkInVeto(target);
    target.clock.advance(target.policy.vetoWindowSeconds * 1000);
    await target.fireDeadlines();
    const state = (await target.graph().getState(threadConfig(ON_LOOP.threadId))).values as GateState;
    expect(state.approved).toBe(false);
    expect(state.rejectReason).toMatch(/delivery not confirmed/);
    expect(target.ledger.totalEffects).toBe(0);
  });

  it("confirmed delivery alone is not enough: no heartbeat still rejects", async () => {
    const target = harness({ deliveryConfirmed: true });
    await parkInVeto(target);
    expect(target.outbox.isDeliveryConfirmed(keyFor(ON_LOOP))).toBe(true);
    target.setHeartbeat(false);
    target.clock.advance(target.policy.vetoWindowSeconds * 1000);
    await target.fireDeadlines();
    const state = (await target.graph().getState(threadConfig(ON_LOOP.threadId))).values as GateState;
    expect(state.approved).toBe(false);
  });

  it("refuses a timeout resume that arrives before the window closed", async () => {
    const target = harness();
    await parkInVeto(target);
    const final = await target.graph().invoke(new Command({ resume: timeoutPayload("veto_deadline") }), threadConfig(ON_LOOP.threadId));
    const state = stateOf(final) as GateState;
    expect(state.approved).toBe(false);
    expect(state.rejectReason).toMatch(/before the veto window closed/);
    expect(target.ledger.totalEffects).toBe(0);
  });

  it("the outbox only confirms notifications it actually sent", () => {
    const target = harness({ deliveryConfirmed: false });
    expect(target.outbox.confirmDelivery("never-sent")).toBe(false);
    expect(target.outbox.isDeliveryConfirmed("never-sent")).toBe(false);
    expect(target.outbox.isDeliveryConfirmed(undefined)).toBe(false);
  });
});

// ---------------------------------------------------------------------------------------------
// FR-4 in_loop timeouts
// ---------------------------------------------------------------------------------------------

describe("FR-4 in_loop timeouts", () => {
  it("tier 4: deadline at 15 min, nothing fires a millisecond early, REJECT + escalate at the deadline", async () => {
    const target = harness();
    const config = threadConfig(TIER4.threadId);
    const first = await target.graph().invoke(target.input(TIER4), config);
    const deadline = BASE_NOW + target.policy.inLoopTimeoutsMinutes.tier4 * MIN;
    expect(prompts(first)[0]?.expiresAt).toBe(deadline);
    expect(target.scheduler.get(keyFor(TIER4))).toMatchObject({ kind: "approval_deadline", resumeAtMs: deadline });

    target.clock.advance(deadline - BASE_NOW - 1);
    expect(await target.fireDeadlines()).toEqual([]);

    target.clock.advance(1);
    expect((await target.fireDeadlines()).map((firing) => firing.outcome)).toEqual(["resumed"]);

    const state = (await target.graph().getState(config)).values as GateState;
    expect(state.approved).toBe(false);
    expect(state.decidedBy).toBe("timeout");
    expect(state.rejectReason).toBe("timeout");
    expect(state.escalated).toBe(true);
    expect(target.ledger.totalEffects).toBe(0);
    expect(target.alerts.alerts).toEqual([
      expect.objectContaining({ key: keyFor(TIER4), kind: "gate_timeout", severity: "high", approved: false }),
    ]);
  });

  it("tier 3 forced into in_loop: deadline at 30 min", async () => {
    const target = harness();
    const first = await target.graph().invoke(target.input(TIER3_FORCED), threadConfig(TIER3_FORCED.threadId));
    expect(prompts(first)[0]?.kind).toBe("approval");
    expect(prompts(first)[0]?.expiresAt).toBe(BASE_NOW + target.policy.inLoopTimeoutsMinutes.tier3Forced * MIN);
    expect(approvalTimeoutMs(3, target.policy)).toBe(30 * MIN);
    expect(approvalTimeoutMs(4, target.policy)).toBe(15 * MIN);
  });

  it("touchesMoney: the timeout escalation is critical", async () => {
    const target = harness();
    await target.graph().invoke(target.input(MONEY), threadConfig(MONEY.threadId));
    target.clock.advance(target.policy.inLoopTimeoutsMinutes.tier4 * MIN);
    await target.fireDeadlines();
    expect(target.alerts.alerts).toEqual([expect.objectContaining({ severity: "critical", tool: "submit_broker_order_stub" })]);
    expect(target.ledger.totalEffects).toBe(0);
  });

  it("a human approval after the deadline is a timeout: rejected and escalated", async () => {
    const target = harness();
    const config = threadConfig(TIER4.threadId);
    const graph = target.graph();
    await graph.invoke(target.input(TIER4), config);
    target.clock.advance(target.policy.inLoopTimeoutsMinutes.tier4 * MIN + 1);
    const final = await graph.invoke(new Command({ resume: { approved: true, by: "jeffg" } }), config);
    const state = stateOf(final) as GateState;
    expect(state.approved).toBe(false);
    expect(state.rejectReason).toBe("timeout");
    expect(state.escalated).toBe(true);
    expect(target.alerts.alerts).toHaveLength(1);
    expect(target.ledger.totalEffects).toBe(0);
  });

  it("a human decision before the deadline wins: the later deadline is stale and does nothing", async () => {
    const target = harness();
    const config = threadConfig(TIER4.threadId);
    const graph = target.graph();
    await graph.invoke(target.input(TIER4), config);
    await graph.invoke(new Command({ resume: { approved: true, by: "jeffg" } }), config);
    expect(target.ledger.effectsFor(keyFor(TIER4))).toBe(1);

    target.clock.advance(target.policy.inLoopTimeoutsMinutes.tier4 * MIN);
    expect((await target.fireDeadlines()).map((firing) => firing.outcome)).toEqual(["stale"]);
    expect(target.ledger.effectsFor(keyFor(TIER4))).toBe(1);
    expect(target.alerts.alerts).toHaveLength(0);
    expect(target.scheduler.firedOutcome(keyFor(TIER4))).toBe("stale");
  });

  it("running the deadline runner twice fires once; a runner that died before marking is harmless", async () => {
    const target = harness();
    const config = threadConfig(TIER4.threadId);
    await target.graph().invoke(target.input(TIER4), config);
    target.clock.advance(target.policy.inLoopTimeoutsMinutes.tier4 * MIN);

    // a previous runner resumed the thread and died before markFired()
    await target.graph().invoke(new Command({ resume: timeoutPayload("approval_deadline") }), config);
    expect(target.alerts.alerts).toHaveLength(1);

    expect((await target.fireDeadlines()).map((firing) => firing.outcome)).toEqual(["stale"]);
    expect(await target.fireDeadlines()).toEqual([]);
    expect(target.alerts.alerts).toHaveLength(1);
    expect(target.alerts.attemptsCount).toBe(1);
  });

  it("crash at every node boundary of the timeout path: one rejection, one alert, no effect, no re-ask", async () => {
    // initial run = 7 visits (intake..approval); the timeout resume adds approval, escalate, reject
    for (let extra = 1; extra <= 3; extra += 1) {
      const target = harness();
      const config = threadConfig(TIER4.threadId);
      await target.graph().invoke(target.input(TIER4), config);
      target.clock.advance(target.policy.inLoopTimeoutsMinutes.tier4 * MIN);
      target.crashAtNodeVisit(target.nodeVisits() + extra);
      const label = `crash at resume visit ${extra}`;

      await expect(target.fireDeadlines(), label).rejects.toThrow(/simulated process death/);
      // restart: the supervisor finishes any run that died mid-flight, then the runner runs again
      const snapshot = await target.graph().getState(config);
      if (snapshot.next.length > 0 && snapshot.tasks.every((task) => (task.interrupts ?? []).length === 0)) {
        await target.graph().invoke(null, config);
      }
      await target.fireDeadlines();

      const state = (await target.graph().getState(config)).values as GateState;
      expect(state.approved, label).toBe(false);
      expect(state.rejectReason, label).toBe("timeout");
      expect(target.ledger.totalEffects, label).toBe(0);
      expect(target.alerts.alerts, label).toHaveLength(1);
      expect(target.outbox.deliveries, label).toHaveLength(1);
      expect(target.scheduler.all, label).toHaveLength(1);
      expect((await target.graph().getState(config)).next, label).toEqual([]);
    }
  });
});

describe("routeAfterDecision escalation rules", () => {
  const money = { touchesMoney: true } as GateState["gate"];
  const plain = { touchesMoney: false } as GateState["gate"];

  it("escalates a timed-out rejection exactly once", () => {
    expect(routeAfterDecision({ timedOut: true, approved: false, gate: plain } as GateState)).toBe("escalate");
    expect(routeAfterDecision({ timedOut: true, approved: false, escalated: true, gate: plain } as GateState)).toBe("reject");
  });

  it("escalates a touchesMoney timeout even when it was approved, then executes", () => {
    expect(routeAfterDecision({ timedOut: true, approved: true, gate: money } as GateState)).toBe("escalate");
    expect(routeAfterDecision({ timedOut: true, approved: true, escalated: true, gate: money } as GateState)).toBe("execute");
  });

  it("does not escalate human decisions or a non-money auto-approve", () => {
    expect(routeAfterDecision({ approved: false, gate: plain } as GateState)).toBe("reject");
    expect(routeAfterDecision({ timedOut: true, approved: true, gate: plain } as GateState)).toBe("execute");
  });
});

describe("on-call check-in helpers (mechanism only; UX is PRD OQ4)", () => {
  it("check-in lasts the policy's hours, check-out ends it now, bad input throws", () => {
    const target = harness();
    const hours = target.policy.oncallHeartbeatHours;
    checkInOnCall(target.dbPath, "jeffg", hours, BASE_NOW);
    expect(readOncallHeartbeat(target.dbPath, BASE_NOW + hours * 3_600_000 - 1).active).toBe(true);
    expect(readOncallHeartbeat(target.dbPath, BASE_NOW + hours * 3_600_000).active).toBe(false);

    checkInOnCall(target.dbPath, "jeffg", hours, BASE_NOW);
    checkOutOnCall(target.dbPath, "jeffg", BASE_NOW + 1);
    expect(readOncallHeartbeat(target.dbPath, BASE_NOW + 1).active).toBe(false);

    expect(() => checkInOnCall(target.dbPath, "jeffg", 0, BASE_NOW)).toThrow(/positive number of hours/);
    expect(() => checkInOnCall(target.dbPath, " ", hours, BASE_NOW)).toThrow(/operator identity/);
  });
});
