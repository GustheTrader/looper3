import { describe, expect, it } from "vitest";

import { SideEffectLedger } from "../src/gate/executor.js";
import { idempotencyKey } from "../src/gate/keys.js";
import { createGateNodes } from "../src/gate/nodes.js";
import type { GateState } from "../src/gate/state.js";
import type { Tier } from "../src/policy/types.js";
import { BASE_NOW, createHarness } from "./helpers/gateHarness.js";

describe("idempotency keys", () => {
  it("is sha256(thread_id + tool_call_id), 64 hex chars, stable", () => {
    const a = idempotencyKey("thread-1", "call-1");
    const b = idempotencyKey("thread-1", "call-1");
    expect(a).toBe(b);
    expect(a).toMatch(/^[0-9a-f]{64}$/);
  });

  it("changes when either half changes, and cannot be confused by concatenation", () => {
    expect(idempotencyKey("a", "bc")).not.toBe(idempotencyKey("ab", "c"));
    expect(idempotencyKey("t1", "c1")).not.toBe(idempotencyKey("t1", "c2"));
    expect(idempotencyKey("t1", "c1")).not.toBe(idempotencyKey("t2", "c1"));
  });

  it("refuses empty inputs", () => {
    expect(() => idempotencyKey("", "c")).toThrow(/threadId/);
    expect(() => idempotencyKey("t", "")).toThrow(/toolCallId/);
  });
});

describe("side-effect ledger: tier>=3 is non-replayable", () => {
  const request = (tier: Tier, key = "k1") => ({
    key,
    threadId: "t",
    toolCallId: "c",
    tool: "merge_pull_request",
    tier,
  });

  it("applies a tier-4 effect once even when executed twice", () => {
    const ledger = new SideEffectLedger();
    expect(ledger.execute(request(4)).status).toBe("applied");
    expect(ledger.execute(request(4)).status).toBe("deduplicated");
    expect(ledger.effectsFor("k1")).toBe(1);
    expect(ledger.attemptsFor("k1")).toBe(2);
  });

  it("applies a tier-3 effect once even when executed twice", () => {
    const ledger = new SideEffectLedger();
    ledger.execute(request(3));
    ledger.execute(request(3));
    expect(ledger.effectsFor("k1")).toBe(1);
  });

  it("REPLAYS tier 1-2 calls, because reads and internal writes are repeatable", () => {
    const ledger = new SideEffectLedger();
    ledger.execute(request(1));
    ledger.execute(request(1));
    expect(ledger.effectsFor("k1")).toBe(2);
    ledger.execute(request(2, "k2"));
    ledger.execute(request(2, "k2"));
    expect(ledger.effectsFor("k2")).toBe(2);
  });

  it("keys are independent", () => {
    const ledger = new SideEffectLedger();
    ledger.execute(request(4, "a"));
    ledger.execute(request(4, "b"));
    expect(ledger.effectsFor("a")).toBe(1);
    expect(ledger.effectsFor("b")).toBe(1);
    expect(ledger.totalEffects).toBe(2);
  });

  it("can simulate a crash after the effect but before the checkpoint", () => {
    const ledger = new SideEffectLedger();
    ledger.failNextApply = true;
    expect(() => ledger.execute(request(4))).toThrow(/simulated crash/);
    expect(ledger.effectsFor("k1")).toBe(1);
    expect(ledger.execute(request(4)).status).toBe("deduplicated");
    expect(ledger.effectsFor("k1")).toBe(1);
  });
});

describe("execute node uses the idempotency key", () => {
  it("runs the side effect once for a tier-4 action and blocks an uncleared action", () => {
    const harness = createHarness();
    try {
      const nodes = createGateNodes(harness.deps);
      const state = {
        threadId: "thread-x",
        pending: { tool: "merge_pull_request", toolCallId: "call-x" },
        wantedMode: "autonomous",
        runFloor: "autonomous",
        globalFloor: "autonomous",
        harness: "execution",
        sme: "coding",
        gate: { mode: "in_loop", tier: 4, binding: "tier", floor: "in_loop", floorSource: "both", configVersion: harness.policy.configVersion },
        approved: true,
        preparedAtMs: BASE_NOW,
      } as unknown as GateState;

      const first = nodes.execute(state);
      const second = nodes.execute(state);
      expect(first.executed).toBe(true);
      expect(second.executed).toBe(true);
      const key = idempotencyKey("thread-x", "call-x");
      expect(first.executeKey).toBe(key);
      expect(harness.ledger.effectsFor(key)).toBe(1);
      expect(harness.ledger.attemptsFor(key)).toBe(2);

      const blocked = nodes.execute({ ...state, approved: false, gate: { ...state.gate!, mode: "in_loop" } } as GateState);
      expect(blocked.executed).toBe(false);
      expect(blocked.rejectReason).toMatch(/did not clear/);
      expect(harness.ledger.effectsFor(key)).toBe(1);
    } finally {
      harness.cleanup();
    }
  });
});
