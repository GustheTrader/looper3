import { rmSync } from "node:fs";

import { Command } from "@langchain/langgraph";
import { describe, expect, it } from "vitest";

import {
  createGateNodes,
  gateDecisionFor,
  parseResumePayload,
  routeAfterDecision,
  routeAfterGate,
  routeForMode,
} from "../src/gate/nodes.js";
import { PostgresCheckpointSaverStub, POSTGRES_SAVER_STUB_STATUS } from "../src/gate/postgresSaver.js";
import type { GateState } from "../src/gate/state.js";
import { TOOL_TIER, type ToolTierEntry, type ToolTierRegistry } from "../src/policy/toolTiers.js";
import type { Tier } from "../src/policy/types.js";
import { BASE_NOW, createHarness, fixtureById, keyFor, promptValues, stateOf, threadConfig } from "./helpers/gateHarness.js";

const APPROVE = { approved: true, by: "jeffg" };
const REJECT = { approved: false, by: "jeffg", reason: "not today" };
const MIN = 60_000;

function prompts(result: unknown): { kind?: string; tier?: number; expiresAt?: number }[] {
  return promptValues(result) as { kind?: string; tier?: number; expiresAt?: number }[];
}

describe("autonomous path", () => {
  it("executes with no pause and one notification", async () => {
    const fixture = fixtureById("r15"); // research / read_wiki
    const harness = createHarness();
    try {
      const result = await harness.graph().invoke(harness.input(fixture), threadConfig(fixture.threadId));
      const state = stateOf(result) as GateState;

      expect(prompts(result)).toHaveLength(0);
      expect(state.gate?.mode).toBe("autonomous");
      expect(state.executed).toBe(true);
      expect(state.approved).toBeUndefined();
      expect(harness.ledger.effectsFor(keyFor(fixture))).toBe(1);
      expect(harness.outbox.deliveries).toHaveLength(1);
      // M4: deadlines are armed only for interrupting modes; an autonomous action needs none.
      expect(harness.scheduler.all).toHaveLength(0);
      expect(harness.nodeTrace).toEqual(["intake", "readFloor", "runTriggers", "prepare", "gateNode", "record", "execute"]);
    } finally {
      harness.cleanup();
    }
  });

  it("never touches the ledger before the gate routes", async () => {
    const harness = createHarness();
    try {
      // Autonomous: the execute node runs, but only after intake/prepare/gate.
      const fixture = fixtureById("r16");
      const result = await harness.graph().invoke(harness.input(fixture), threadConfig(fixture.threadId));
      expect(stateOf(result).executed).toBe(true);
      expect(harness.nodeTrace.indexOf("execute")).toBe(harness.nodeTrace.length - 1);
    } finally {
      harness.cleanup();
    }
  });
});

describe("in_loop approval path (FR-4)", () => {
  const fixture = fixtureById("r04"); // execution / merge_pull_request (tier 4)

  it("interrupts with an approval prompt, no side effect before it", async () => {
    const harness = createHarness();
    try {
      const result = await harness.graph().invoke(harness.input(fixture), threadConfig(fixture.threadId));
      const prompt = prompts(result)[0];

      expect(prompt?.kind).toBe("approval");
      expect(prompt?.tier).toBe(4);
      expect(prompt?.expiresAt).toBe(BASE_NOW + 15 * MIN); // tier-4 timeout
      expect(harness.ledger.totalEffects).toBe(0); // nothing ran before interrupt()
      expect(harness.outbox.deliveries).toHaveLength(1); // prepare already notified (safe to repeat)
      expect(stateOf(result).gate?.mode).toBe("in_loop");
    } finally {
      harness.cleanup();
    }
  });

  it("executes after a human approves", async () => {
    const harness = createHarness();
    try {
      const graph = harness.graph();
      const config = threadConfig(fixture.threadId);
      await graph.invoke(harness.input(fixture), config);
      const final = await graph.invoke(new Command({ resume: APPROVE }), config);
      const state = stateOf(final) as GateState;

      expect(state.approved).toBe(true);
      expect(state.decidedBy).toBe("human");
      expect(state.executed).toBe(true);
      expect(harness.ledger.effectsFor(keyFor(fixture))).toBe(1);
    } finally {
      harness.cleanup();
    }
  });

  it("records a human rejection and does not execute", async () => {
    const harness = createHarness();
    try {
      const graph = harness.graph();
      const config = threadConfig(fixture.threadId);
      await graph.invoke(harness.input(fixture), config);
      const final = await graph.invoke(new Command({ resume: REJECT }), config);
      const state = stateOf(final) as GateState;

      expect(state.approved).toBe(false);
      expect(state.decidedBy).toBe("human");
      expect(state.rejectReason).toBe("not today");
      expect(state.executed).toBe(false);
      expect(harness.ledger.totalEffects).toBe(0);
    } finally {
      harness.cleanup();
    }
  });

  it("rejects malformed approval responses instead of treating them as approval", async () => {
    const harness = createHarness();
    try {
      const graph = harness.graph();
      // NB: LangGraph refuses a falsy resume value ("empty Command input"), so null/0/false are
      // covered by the parseResumePayload unit test below instead of through the graph.
      const badPayloads: unknown[] = ["yes", true, { approved: "true" }, {}, { approved: 1 }, [1, 2]];
      let index = 0;

      for (const bad of badPayloads) {
        index += 1;
        const custom = { ...fixture, threadId: `thread_bad_${index}`, toolCallId: `call_bad_${index}` };
        const config = threadConfig(custom.threadId);
        await graph.invoke(harness.input(custom), config);
        const final = await graph.invoke(new Command({ resume: bad }), config);
        const state = stateOf(final) as GateState;

        expect(state.approved, `payload ${JSON.stringify(bad)}`).toBe(false);
        expect(state.rejectReason, `payload ${JSON.stringify(bad)}`).toMatch(/invalid approval response/);
        expect(state.executed).toBe(false);
      }

      expect(harness.ledger.totalEffects).toBe(0);
    } finally {
      harness.cleanup();
    }
  });

  it("never auto-approves an in_loop action on a timeout resume (FR-4)", async () => {
    const harness = createHarness();
    try {
      const graph = harness.graph();
      const config = threadConfig(fixture.threadId);
      await graph.invoke(harness.input(fixture), config);
      const final = await graph.invoke(new Command({ resume: { approved: true, by: "timeout" } }), config);
      const state = stateOf(final) as GateState;

      expect(state.approved).toBe(false);
      expect(state.decidedBy).toBe("timeout");
      expect(state.rejectReason).toBe("timeout");
      expect(harness.ledger.totalEffects).toBe(0);
    } finally {
      harness.cleanup();
    }
  });

  it("rejects a human approval that lands after the tier-4 deadline", async () => {
    const harness = createHarness();
    try {
      const graph = harness.graph();
      const config = threadConfig(fixture.threadId);
      await graph.invoke(harness.input(fixture), config);

      harness.clock.advance(16 * MIN);
      const final = await graph.invoke(new Command({ resume: APPROVE }), config);
      const state = stateOf(final) as GateState;

      expect(state.approved).toBe(false);
      expect(state.rejectReason).toBe("timeout");
      expect(harness.ledger.totalEffects).toBe(0);
    } finally {
      harness.cleanup();
    }
  });
});

describe("on_loop veto window (FR-5 fail-closed defaults)", () => {
  const fixture = fixtureById("r08"); // build / write_raw_output (tier 2, reversible)

  it("interrupts with a veto prompt and the placeholder window deadline", async () => {
    const harness = createHarness();
    try {
      const result = await harness.graph().invoke(harness.input(fixture), threadConfig(fixture.threadId));
      const prompt = prompts(result)[0];
      expect(prompt?.kind).toBe("veto");
      expect(prompt?.expiresAt).toBe(BASE_NOW + harness.policy.vetoWindowSeconds * 1000);
      expect(stateOf(result).gate?.mode).toBe("on_loop");
      expect(harness.ledger.totalEffects).toBe(0);
    } finally {
      harness.cleanup();
    }
  });

  it("executes when a human approves inside the window", async () => {
    const harness = createHarness();
    try {
      const graph = harness.graph();
      const config = threadConfig(fixture.threadId);
      await graph.invoke(harness.input(fixture), config);
      const final = await graph.invoke(new Command({ resume: APPROVE }), config);
      expect(stateOf(final).approved).toBe(true);
      expect(stateOf(final).executed).toBe(true);
      expect(harness.ledger.effectsFor(keyFor(fixture))).toBe(1);
    } finally {
      harness.cleanup();
    }
  });

  it("auto-approves on timeout ONLY with an active heartbeat and an eligible action", async () => {
    const harness = createHarness({ heartbeatActive: true });
    try {
      const graph = harness.graph();
      const config = threadConfig(fixture.threadId);
      await graph.invoke(harness.input(fixture), config);
      // M4: a timeout resume only counts once the window has actually closed.
      harness.clock.advance(harness.policy.vetoWindowSeconds * 1000);
      const final = await graph.invoke(new Command({ resume: { approved: true, by: "timeout" } }), config);
      const state = stateOf(final) as GateState;
      expect(state.approved).toBe(true);
      expect(state.decidedBy).toBe("timeout");
      expect(state.executed).toBe(true);
    } finally {
      harness.cleanup();
    }
  });

  it("REJECTS the timeout auto-approve when the on-call heartbeat lapsed during the window", async () => {
    // M4: with no heartbeat at gate time the action never reaches the veto window at all (see
    // gateM4.test.ts). This covers the other half: on call at gate time, gone at expiry.
    const harness = createHarness({ heartbeatActive: true });
    try {
      const graph = harness.graph();
      const config = threadConfig(fixture.threadId);
      const first = await graph.invoke(harness.input(fixture), config);
      expect(prompts(first)[0]?.kind).toBe("veto");

      harness.setHeartbeat(false);
      harness.clock.advance(harness.policy.vetoWindowSeconds * 1000);
      const final = await graph.invoke(new Command({ resume: { approved: true, by: "timeout" } }), config);
      const state = stateOf(final) as GateState;

      expect(state.approved).toBe(false);
      expect(state.decidedBy).toBe("policy");
      expect(state.rejectReason).toMatch(/no active on-call heartbeat/);
      expect(state.executed).toBe(false);
      expect(harness.ledger.totalEffects).toBe(0);
    } finally {
      harness.cleanup();
    }
  });

  it("never offers a veto window for an irreversible action with no compensating action (M4: in_loop instead)", async () => {
    const registry: ToolTierRegistry = {
      ...TOOL_TIER,
      flush_and_forget: { tool: "flush_and_forget", tier: 3 as Tier, reversible: false } satisfies ToolTierEntry,
    };
    const harness = createHarness({ heartbeatActive: true, registry });
    try {
      const custom = { ...fixture, id: "x1", tool: "flush_and_forget", threadId: "thread_x1", toolCallId: "call_x1" };
      const graph = harness.graph();
      const config = threadConfig(custom.threadId);
      const first = await graph.invoke(harness.input(custom), config);
      expect(prompts(first)[0]?.kind).toBe("approval");
      expect(stateOf(first).gate?.binding).toBe("reversibility");
      expect(stateOf(first).gate?.convertedFromOnLoop).toBe("irreversible");
    } finally {
      harness.cleanup();
    }
  });

  it("rejects a human approval that arrives after the veto window closed", async () => {
    const harness = createHarness();
    try {
      const graph = harness.graph();
      const config = threadConfig(fixture.threadId);
      await graph.invoke(harness.input(fixture), config);

      harness.clock.advance(harness.policy.vetoWindowSeconds * 1000 + 1);
      const final = await graph.invoke(new Command({ resume: APPROVE }), config);
      const state = stateOf(final) as GateState;
      expect(state.approved).toBe(false);
      expect(state.rejectReason).toMatch(/veto window expired/);
      expect(harness.ledger.totalEffects).toBe(0);
    } finally {
      harness.cleanup();
    }
  });
});

describe("floors (M2 store -> gate)", () => {
  it("binds the global floor and says so", async () => {
    const harness = createHarness({ globalFloor: "in_loop" });
    try {
      const fixture = fixtureById("r15"); // would be autonomous without the floor
      const result = await harness.graph().invoke(harness.input(fixture), threadConfig(fixture.threadId));
      const state = stateOf(result) as GateState;

      expect(state.gate?.mode).toBe("in_loop");
      expect(state.gate?.binding).toBe("floor");
      expect(state.gate?.floorSource).toBe("global");
      expect(prompts(result)[0]?.kind).toBe("approval");
      expect(harness.ledger.totalEffects).toBe(0);
    } finally {
      harness.cleanup();
    }
  });

  it("binds the run floor and says so", async () => {
    const harness = createHarness();
    try {
      const fixture = fixtureById("r25"); // business_process + runFloor in_loop
      const result = await harness.graph().invoke(harness.input(fixture), threadConfig(fixture.threadId));
      const state = stateOf(result) as GateState;
      expect(state.gate?.mode).toBe("in_loop");
      expect(state.gate?.binding).toBe("floor");
      expect(state.gate?.floorSource).toBe("run");
      expect(state.globalFloor).toBe("autonomous");
    } finally {
      harness.cleanup();
    }
  });

  it("reports floorSource 'both' when both floors agree", async () => {
    const harness = createHarness({ globalFloor: "in_loop" });
    try {
      const fixture = { ...fixtureById("r15"), runFloor: "in_loop" as const };
      const result = await harness.graph().invoke(harness.input(fixture), threadConfig(fixture.threadId));
      expect(stateOf(result).gate?.floorSource).toBe("both");
    } finally {
      harness.cleanup();
    }
  });

  it("treats an unreadable agent_flags store as in_loop", async () => {
    const harness = createHarness();
    try {
      rmSync(harness.dbPath, { force: true }); // store gone: read must fail closed
      const fixture = fixtureById("r15");
      const result = await harness.graph().invoke(harness.input(fixture), threadConfig(fixture.threadId));
      const state = stateOf(result) as GateState;

      expect(state.globalFloor).toBe("in_loop");
      expect(state.gate?.mode).toBe("in_loop");
      expect(prompts(result)[0]?.kind).toBe("approval");
    } finally {
      harness.cleanup();
    }
  });
});

describe("intake (FR-7 partial)", () => {
  it("refuses an underspecified hand-off instead of guessing", async () => {
    const harness = createHarness();
    try {
      const fixture = fixtureById("r15");
      const bad = { ...harness.input(fixture), harness: "ops" as never };
      await expect(harness.graph().invoke(bad, threadConfig("thread_bad_1"))).rejects.toThrow(/intake refused/);

      const noPending = { ...harness.input(fixture), pending: undefined };
      await expect(harness.graph().invoke(noPending, threadConfig("thread_bad_2"))).rejects.toThrow(/intake refused/);
    } finally {
      harness.cleanup();
    }
  });

  it("applies the router clamp once and never re-routes on resume", async () => {
    const harness = createHarness();
    try {
      const fixture = fixtureById("r15");
      const config = threadConfig("thread_router");
      const input = { ...harness.input(fixture), wantedMode: "autonomous" as const, routerWantedMode: "on_loop" as const };

      const first = await harness.graph().invoke(input, config);
      expect(stateOf(first).wantedMode).toBe("on_loop"); // router tightened it
      expect(prompts(first)[0]?.kind).toBe("veto");

      const resumed = await harness.graph().invoke(
        new Command({ resume: { approved: false, by: "jeffg", reason: "stop" } }),
        config,
      );
      expect(stateOf(resumed).approved).toBe(false);
      expect(stateOf(resumed).wantedMode).toBe("on_loop");
    } finally {
      harness.cleanup();
    }
  });
});

describe("gate node purity", () => {
  it("takes no clock reading, no db access and no side effect", () => {
    const harness = createHarness();
    try {
      let clockReads = 0;
      const nodes = createGateNodes({
        ...harness.deps,
        now: () => {
          clockReads += 1;
          return BASE_NOW;
        },
      });

      // Remove the store entirely: a pure gate cannot care.
      rmSync(harness.dbPath, { force: true });

      const fixture = fixtureById("r09");
      const state = {
        threadId: fixture.threadId,
        pending: { tool: fixture.tool, toolCallId: fixture.toolCallId },
        wantedMode: fixture.wantedMode,
        runFloor: fixture.runFloor,
        globalFloor: fixture.globalFloor,
        harness: fixture.harness,
        sme: fixture.sme,
      } as unknown as GateState;

      const first = nodes.gate(state);
      const second = nodes.gate(state);

      expect(clockReads).toBe(0);
      expect(first).toEqual(second);
      expect(first.gate?.mode).toBe("in_loop");
      expect(harness.ledger.totalEffects).toBe(0);
      expect(harness.outbox.deliveries).toHaveLength(0);
      expect(harness.scheduler.all).toHaveLength(0);
    } finally {
      harness.cleanup();
    }
  });

  it("is deterministic across a thousand evaluations", () => {
    const harness = createHarness();
    try {
      const input = {
        tool: "submit_broker_order_stub",
        wantedMode: "autonomous" as const,
        harness: "execution" as const,
        sme: "crypto" as const,
        runFloor: "autonomous" as const,
        globalFloor: "autonomous" as const,
        oncallActive: true,
      };
      const first = gateDecisionFor(input, harness.policy);
      for (let index = 0; index < 1000; index += 1) {
        expect(gateDecisionFor(input, harness.policy)).toEqual(first);
      }
    } finally {
      harness.cleanup();
    }
  });
});

describe("routers fail closed", () => {
  it("routes a missing gate decision to reject", () => {
    expect(routeAfterGate({} as GateState)).toBe("reject");
  });

  it("routes unknown modes to reject", () => {
    expect(routeForMode("sudo" as never)).toBe("reject");
    expect(routeForMode("in_loop")).toBe("approval");
    expect(routeForMode("on_loop")).toBe("vetoWindow");
    expect(routeForMode("autonomous")).toBe("execute");
  });

  it("only routes to execute when approved is literally true", () => {
    expect(routeAfterDecision({ approved: true } as GateState)).toBe("execute");
    expect(routeAfterDecision({ approved: false } as GateState)).toBe("reject");
    expect(routeAfterDecision({} as GateState)).toBe("reject");
    expect(routeAfterDecision({ approved: "true" } as unknown as GateState)).toBe("reject");
  });

  it("reject node records a fail-closed reason and executes nothing", () => {
    const harness = createHarness();
    try {
      const nodes = createGateNodes(harness.deps);
      const state = nodes.reject({} as GateState);
      expect(state.executed).toBe(false);
      expect(state.approved).toBe(false);
      expect(state.rejectReason).toMatch(/no gate decision/);
      expect(harness.ledger.totalEffects).toBe(0);
    } finally {
      harness.cleanup();
    }
  });
});

describe("resume payload parsing fails closed", () => {
  it("returns null for every non-boolean / missing approval field", () => {
    for (const bad of [null, undefined, 0, false, "", "yes", [], {}, { approved: "true" }, { approved: 1 }, { reason: "x" }]) {
      expect(parseResumePayload(bad), JSON.stringify(bad)).toBeNull();
    }
  });

  it("parses a human decision and an explicit timeout", () => {
    expect(parseResumePayload({ approved: true, by: "jeffg" })).toEqual({ approved: true, by: "human", reason: "approved" });
    expect(parseResumePayload({ approved: false, by: "jeffg", reason: "no" })).toEqual({
      approved: false,
      by: "human",
      reason: "no",
    });
    expect(parseResumePayload({ approved: true, by: "timeout" })).toEqual({ approved: true, by: "timeout", reason: "approved" });
    expect(parseResumePayload({ approved: false })).toEqual({ approved: false, by: "human", reason: "rejected" });
  });
});

describe("postgres checkpointer adapter", () => {
  it("is a clearly marked stub that refuses to be used", () => {
    expect(POSTGRES_SAVER_STUB_STATUS).toMatch(/STUB/);
    expect(POSTGRES_SAVER_STUB_STATUS).toMatch(/never opens a connection/);
    expect(() => new PostgresCheckpointSaverStub()).toThrow(/STUB/);
  });
});
