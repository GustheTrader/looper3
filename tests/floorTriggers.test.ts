/**
 * M5: automatic floor-tightening triggers (FR-6), run + global.
 *
 * PRD exit criterion for M5: "each trigger covered by a test and a LangSmith alert".
 * PRD acceptance criteria covered here: AC6 (run budget raises that run's floor; daily portfolio
 * limit raises the global floor for every run) and AC7 (no automatic path lowers a floor).
 */
import { readFileSync } from "node:fs";
import { rmSync } from "node:fs";

import Database from "better-sqlite3";
import fc from "fast-check";
import { afterEach, describe, expect, it } from "vitest";

import { lowerGlobalFloor, readFloorChanges, readFloorRaises, readGlobalFloor, raiseGlobalFloor } from "../src/flags/agentFlags.js";
import { applyGlobalTriggers } from "../src/floors/globalMonitor.js";
import {
  evaluateGlobalTriggers,
  evaluateRunTriggers,
  utcDay,
  type GlobalSignals,
  type RunSignals,
} from "../src/floors/triggers.js";
import { strictestReducer, type GateState } from "../src/gate/state.js";
import { defaultPolicyPath, parsePolicyConfig, type PolicyConfig } from "../src/policy/config.js";
import { MODES, modeRank } from "../src/policy/types.js";
import {
  InMemoryFloorAlertSink,
  LangSmithFloorAlertSinkStub,
  LANGSMITH_ALERT_STUB_STATUS,
} from "../src/telemetry/floorAlerts.js";
import { BASE_NOW, createHarness, fixtureById, promptValues, stateOf, threadConfig, type Harness } from "./helpers/gateHarness.js";

const harnesses: Harness[] = [];
function harness(options: Parameters<typeof createHarness>[0] = {}): Harness {
  const created = createHarness(options);
  harnesses.push(created);
  return created;
}
afterEach(() => {
  while (harnesses.length > 0) harnesses.pop()?.cleanup();
});

function rawPolicy(): Record<string, unknown> {
  return JSON.parse(readFileSync(defaultPolicyPath(), "utf8")) as Record<string, unknown>;
}
function policyWith(mutate: (raw: Record<string, any>) => void): PolicyConfig {
  const raw = rawPolicy();
  mutate(raw);
  return parsePolicyConfig(raw);
}

const POLICY = parsePolicyConfig(rawPolicy());
const T = POLICY.floorTriggers;
const AUTONOMOUS = fixtureById("r15"); // research / read_wiki: executes with no pause when floors allow

/** One over-the-line signal per run trigger. */
const RUN_CASES: readonly { trigger: string; signals: RunSignals }[] = [
  { trigger: "router_confidence_low", signals: { routerConfidence: T.routerConfidenceMin - 0.01 } },
  { trigger: "run_budget_tokens", signals: { usage: { tokens: T.runBudget.maxTokens + 1 } } },
  { trigger: "run_budget_dollars", signals: { usage: { dollars: T.runBudget.maxDollars + 0.01 } } },
  { trigger: "run_budget_turns", signals: { usage: { turns: T.runBudget.maxTurns + 1 } } },
  { trigger: "data_stale", signals: { dataAgeSeconds: T.dataFreshnessMaxSeconds + 1 } },
  { trigger: "paper_drift", signals: { paperDrift: { observed: -0.2, expectedLow: -0.1, expectedHigh: 0.3 } } },
];

/** One over-the-line signal per global trigger. */
const GLOBAL_CASES: readonly { trigger: string; signals: GlobalSignals }[] = [
  { trigger: "daily_spend", signals: { dailySpendUsd: T.daily.maxSpendUsd + 1 } },
  { trigger: "daily_loss", signals: { dailyLossUsd: T.daily.maxLossUsd + 1 } },
  { trigger: "kill_switch", signals: { killSwitchTripped: true } },
  { trigger: "unresolved_orders", signals: { unresolvedOrders: T.maxUnresolvedOrders + 1 } },
];

// ---------------------------------------------------------------------------------------------
// Pure evaluators
// ---------------------------------------------------------------------------------------------

describe("run triggers (pure)", () => {
  it.each(RUN_CASES)("$trigger fires over its threshold, at the configured raise target", ({ trigger, signals }) => {
    expect(evaluateRunTriggers(signals, T)).toEqual([expect.objectContaining({ trigger, raiseTo: T.raiseTo.run })]);
  });

  it("does not fire AT a threshold (limits are inclusive; drift range is inclusive)", () => {
    const atLine: RunSignals = {
      routerConfidence: T.routerConfidenceMin,
      usage: { tokens: T.runBudget.maxTokens, dollars: T.runBudget.maxDollars, turns: T.runBudget.maxTurns },
      dataAgeSeconds: T.dataFreshnessMaxSeconds,
      paperDrift: { observed: 0.3, expectedLow: -0.1, expectedHigh: 0.3 },
    };
    expect(evaluateRunTriggers(atLine, T)).toEqual([]);
  });

  it("absent signals were not measured and do not trigger", () => {
    expect(evaluateRunTriggers(undefined, T)).toEqual([]);
    expect(evaluateRunTriggers({}, T)).toEqual([]);
    expect(evaluateRunTriggers({ usage: {} }, T)).toEqual([]);
  });

  it.each([
    ["confidence NaN", { routerConfidence: Number.NaN }],
    ["confidence > 1", { routerConfidence: 1.5 }],
    ["negative tokens", { usage: { tokens: -1 } }],
    ["string dollars", { usage: { dollars: "3" as unknown as number } }],
    ["usage not an object", { usage: 5 as unknown as RunSignals["usage"] }],
    ["infinite data age", { dataAgeSeconds: Number.POSITIVE_INFINITY }],
    ["inverted drift range", { paperDrift: { observed: 0, expectedLow: 1, expectedHigh: -1 } }],
    ["drift with NaN", { paperDrift: { observed: Number.NaN, expectedLow: 0, expectedHigh: 1 } }],
  ] as const)("a malformed signal is a trigger at in_loop (%s)", (_label, signals) => {
    const hits = evaluateRunTriggers(signals as RunSignals, T);
    expect(hits).toContainEqual(expect.objectContaining({ trigger: "run_signals_invalid", raiseTo: "in_loop" }));
  });

  it("raises to the configured target; a malformed signal is in_loop regardless", () => {
    const softer = policyWith((raw) => {
      raw.floorTriggers.raiseTo.run = "on_loop";
    }).floorTriggers;
    expect(evaluateRunTriggers({ routerConfidence: 0 }, softer)[0]?.raiseTo).toBe("on_loop");
    expect(evaluateRunTriggers({ routerConfidence: -3 }, softer)[0]?.raiseTo).toBe("in_loop");
  });

  it("never returns a target looser than on_loop (property)", () => {
    const anything = fc.oneof(fc.double(), fc.integer(), fc.constant(undefined), fc.string(), fc.constant(null));
    fc.assert(
      fc.property(anything, anything, anything, anything, anything, anything, (a, b, c, d, e, f) => {
        const signals = { routerConfidence: a, usage: { tokens: b, dollars: c, turns: d }, dataAgeSeconds: e, paperDrift: { observed: f, expectedLow: 0, expectedHigh: 1 } };
        for (const hit of evaluateRunTriggers(signals as unknown as RunSignals, T)) {
          expect(hit.raiseTo).not.toBe("autonomous");
        }
      }),
      { numRuns: 1000 },
    );
  });
});

describe("global triggers (pure)", () => {
  it.each(GLOBAL_CASES)("$trigger fires over its limit", ({ trigger, signals }) => {
    expect(evaluateGlobalTriggers(signals, T)).toEqual([expect.objectContaining({ trigger })]);
  });

  it("does not fire at a limit, or with nothing reported", () => {
    expect(
      evaluateGlobalTriggers(
        { dailySpendUsd: T.daily.maxSpendUsd, dailyLossUsd: T.daily.maxLossUsd, unresolvedOrders: T.maxUnresolvedOrders, killSwitchTripped: false },
        T,
      ),
    ).toEqual([]);
    expect(evaluateGlobalTriggers(undefined, T)).toEqual([]);
  });

  it("the kill switch is always in_loop, even when the config would raise only to on_loop", () => {
    const softer = policyWith((raw) => {
      raw.floorTriggers.raiseTo.global = "on_loop";
    }).floorTriggers;
    expect(evaluateGlobalTriggers({ killSwitchTripped: true }, softer)).toEqual([
      expect.objectContaining({ trigger: "kill_switch", raiseTo: "in_loop" }),
    ]);
    expect(evaluateGlobalTriggers({ dailyLossUsd: 1e9 }, softer)[0]?.raiseTo).toBe("on_loop");
  });

  it.each([
    ["kill switch 'yes'", { killSwitchTripped: "yes" as unknown as boolean }],
    ["negative loss", { dailyLossUsd: -5 }],
    ["NaN spend", { dailySpendUsd: Number.NaN }],
    ["fractional garbage orders", { unresolvedOrders: Number.NEGATIVE_INFINITY }],
  ] as const)("a malformed signal is a trigger at in_loop (%s)", (_label, signals) => {
    expect(evaluateGlobalTriggers(signals as GlobalSignals, T)).toContainEqual(
      expect.objectContaining({ trigger: "global_signals_invalid", raiseTo: "in_loop" }),
    );
  });

  it("uses UTC days", () => {
    expect(utcDay(Date.UTC(2026, 10, 4, 23, 59, 59))).toBe("2026-11-04");
    expect(utcDay(Date.UTC(2026, 10, 5, 0, 0, 0))).toBe("2026-11-05");
  });
});

describe("floorTriggers config validation (fail closed)", () => {
  const invalid = (mutate: (raw: Record<string, any>) => void, pattern: RegExp) =>
    expect(() => policyWith(mutate)).toThrow(pattern);

  it("is required", () => invalid((raw) => delete raw.floorTriggers, /floorTriggers must be an object/));
  it("rejects a raise to autonomous", () =>
    invalid((raw) => {
      raw.floorTriggers.raiseTo.global = "autonomous";
    }, /raiseTo.global must be in_loop or on_loop/));
  it("rejects unknown keys", () =>
    invalid((raw) => {
      raw.floorTriggers.maxSpendUsd = 5;
    }, /unknown key "maxSpendUsd"/));
  it("rejects non-positive budgets", () =>
    invalid((raw) => {
      raw.floorTriggers.runBudget.maxDollars = 0;
    }, /runBudget.maxDollars/));
  it("rejects a confidence threshold outside 0..1", () =>
    invalid((raw) => {
      raw.floorTriggers.routerConfidenceMin = 2;
    }, /routerConfidenceMin/));
  it("only supports a UTC day boundary", () =>
    invalid((raw) => {
      raw.floorTriggers.daily.dayBoundary = "America/Chicago";
    }, /dayBoundary must be "UTC"/));
  it("keeps the placeholder values listed as TODO", () => {
    expect(POLICY.todo["M5_floorTriggerThresholds"]).toMatch(/placeholders, not decisions/);
    expect(POLICY.todo["PRD_OQ6_dailyPortfolioThresholds"]).toMatch(/placeholders, not decisions/);
  });
});

// ---------------------------------------------------------------------------------------------
// Run floor through the gate graph
// ---------------------------------------------------------------------------------------------

describe("run floor through the gate (AC6, run half)", () => {
  it.each(RUN_CASES)("$trigger: the run floor rises, the gate pauses, one LangSmith-shaped alert", async ({ trigger, signals }) => {
    const target = harness();
    target.setRunSignals(AUTONOMOUS.threadId, signals);
    const result = await target.graph().invoke(target.input(AUTONOMOUS), threadConfig(AUTONOMOUS.threadId));
    const state = stateOf(result) as GateState;

    expect(state.runFloorRaised).toBe("in_loop");
    expect(state.runFloorTriggers).toEqual([trigger]);
    expect(state.gate?.mode).toBe("in_loop");
    expect(state.gate?.binding).toBe("floor");
    expect(state.gate?.floorSource).toBe("run");
    expect(target.ledger.totalEffects).toBe(0);

    expect(target.floorAlerts.alerts).toEqual([
      expect.objectContaining({ key: `run:${AUTONOMOUS.threadId}:${trigger}`, scope: "run", trigger, floorAfter: "in_loop" }),
    ]);
    expect(target.floorAlerts.events[0]).toMatchObject({
      name: "looper3.floor_raised",
      tags: ["scope:run", `trigger:${trigger}`, "floor:in_loop"],
    });
  });

  it("AC6: exceeding a run budget raises THAT run's floor only", async () => {
    const target = harness();
    const runA = { ...AUTONOMOUS, threadId: "thread_run_a", toolCallId: "call_a" };
    const runB = { ...AUTONOMOUS, threadId: "thread_run_b", toolCallId: "call_b" };
    target.setRunSignals(runA.threadId, { usage: { dollars: T.runBudget.maxDollars * 2 } });

    const a = await target.graph().invoke(target.input(runA), threadConfig(runA.threadId));
    const b = await target.graph().invoke(target.input(runB), threadConfig(runB.threadId));
    expect(promptValues(a)).toHaveLength(1);
    expect(stateOf(a).gate?.mode).toBe("in_loop");
    expect(promptValues(b)).toHaveLength(0);
    expect(stateOf(b).executed).toBe(true);
    expect(readGlobalFloor(target.dbPath)).toBe("autonomous"); // a run trigger never touches the global floor
  });

  it("stays raised for the rest of the run, even when signals recover or the caller asks for less", async () => {
    const target = harness();
    const config = threadConfig(AUTONOMOUS.threadId);
    target.setRunSignals(AUTONOMOUS.threadId, { routerConfidence: 0.1 });
    await target.graph().invoke(target.input(AUTONOMOUS), config);

    target.setRunSignals(AUTONOMOUS.threadId, { routerConfidence: 0.99 });
    const next = await target.graph().invoke(
      {
        ...target.input(AUTONOMOUS),
        pending: { tool: AUTONOMOUS.tool, toolCallId: "call_second" },
        runFloor: "autonomous",
        runFloorRaised: "autonomous",
      } as Partial<GateState>,
      config,
    );
    expect(stateOf(next).runFloorRaised).toBe("in_loop");
    expect(stateOf(next).gate?.mode).toBe("in_loop");
    expect(target.floorAlerts.alerts).toHaveLength(1);
  });

  it("unreadable run signals raise the run floor to in_loop", async () => {
    const target = harness();
    const deps = {
      ...target.deps,
      readRunSignals: () => {
        throw new Error("metrics store down");
      },
    };
    const { createGateGraph } = await import("../src/gate/graph.js");
    const result = await createGateGraph(deps).compile().invoke(target.input(AUTONOMOUS), threadConfig(AUTONOMOUS.threadId));
    expect(stateOf(result).runFloorTriggers).toEqual(["run_signals_unreadable"]);
    expect(stateOf(result).gate?.mode).toBe("in_loop");
    expect(target.floorAlerts.alerts[0]?.trigger).toBe("run_signals_unreadable");
  });

  it("no signals, no change: the M3/M4 behaviour is untouched", async () => {
    const target = harness();
    const result = await target.graph().invoke(target.input(AUTONOMOUS), threadConfig(AUTONOMOUS.threadId));
    expect(stateOf(result).runFloorRaised).toBe("autonomous");
    expect(stateOf(result).executed).toBe(true);
    expect(target.floorAlerts.alerts).toHaveLength(0);
  });

  it("a crash at every node boundary still yields one alert per trigger", async () => {
    for (let crashAt = 2; crashAt <= 6; crashAt += 1) {
      const target = harness();
      const config = threadConfig(AUTONOMOUS.threadId);
      target.setRunSignals(AUTONOMOUS.threadId, { usage: { turns: T.runBudget.maxTurns + 1 }, dataAgeSeconds: 1e5 });
      target.crashAtNodeVisit(crashAt);
      await expect(target.graph().invoke(target.input(AUTONOMOUS), config)).rejects.toThrow(/simulated process death/);
      await target.graph().invoke(null, config);
      const label = `crash at ${crashAt}`;
      expect(target.floorAlerts.alerts.map((alert) => alert.trigger).sort(), label).toEqual(["data_stale", "run_budget_turns"]);
      expect(stateOf((await target.graph().getState(config)).values).gate?.mode, label).toBe("in_loop");
      expect(target.ledger.totalEffects, label).toBe(0);
    }
  });

  it("the run-floor channel can never loosen (property over every pair of writes)", () => {
    fc.assert(
      fc.property(fc.constantFrom(...MODES), fc.oneof(fc.constantFrom(...MODES), fc.constant(undefined), fc.string()), (current, update) => {
        const next = strictestReducer(current, update as never);
        expect(modeRank(next)).toBeLessThanOrEqual(modeRank(current));
      }),
    );
    expect(strictestReducer("autonomous", "bogus" as never)).toBe("in_loop");
  });
});

// ---------------------------------------------------------------------------------------------
// Global floor monitor
// ---------------------------------------------------------------------------------------------

describe("global floor monitor (AC6, global half)", () => {
  function monitor(target: Harness, signals: () => GlobalSignals | undefined, policy: PolicyConfig = POLICY, now = BASE_NOW) {
    const alerts = target.floorAlerts;
    return applyGlobalTriggers({ dbPath: target.dbPath, policy, alerts, readSignals: signals, now: () => now });
  }

  it.each(GLOBAL_CASES)("$trigger: raises the global floor, records why, alerts once", ({ trigger, signals }) => {
    const target = harness();
    const result = monitor(target, () => signals);
    expect(result.changed).toBe(true);
    expect(result.floor).toBe("in_loop");
    expect(readGlobalFloor(target.dbPath)).toBe("in_loop");
    expect(readFloorRaises(target.dbPath)).toEqual([expect.objectContaining({ from: "autonomous", to: "in_loop", trigger })]);
    expect(readFloorChanges(target.dbPath)).toEqual([]); // automatic raises never pose as operator changes
    expect(target.floorAlerts.alerts).toEqual([
      expect.objectContaining({ key: `global:${utcDay(BASE_NOW)}:${trigger}`, scope: "global", floorAfter: "in_loop" }),
    ]);
  });

  it("AC6: the daily loss limit raises the floor for EVERY run", async () => {
    const target = harness();
    monitor(target, () => ({ dailyLossUsd: T.daily.maxLossUsd + 50 }));
    for (const id of ["r15", "r16", "r27"]) {
      const fixture = fixtureById(id);
      const result = await target.graph().invoke(target.input(fixture), threadConfig(fixture.threadId));
      expect(stateOf(result).gate?.mode, id).toBe("in_loop");
      expect(stateOf(result).gate?.floorSource, id).toBe("global");
    }
    expect(target.ledger.totalEffects).toBe(0);
  });

  it("one alert per trigger per UTC day, however often it runs", () => {
    const target = harness();
    const signals = () => ({ dailySpendUsd: T.daily.maxSpendUsd + 1 });
    monitor(target, signals);
    monitor(target, signals, POLICY, BASE_NOW + 60_000);
    expect(target.floorAlerts.alerts).toHaveLength(1);
    expect(readFloorRaises(target.dbPath)).toHaveLength(1); // the second run found nothing to raise
    monitor(target, signals, POLICY, BASE_NOW + 86_400_000);
    expect(target.floorAlerts.alerts).toHaveLength(2);
  });

  it("unreadable signals raise the global floor to in_loop", () => {
    const target = harness();
    const result = monitor(target, () => {
      throw new Error("desk ledger offline");
    });
    expect(result.hits.map((hit) => hit.trigger)).toEqual(["global_signals_unreadable"]);
    expect(readGlobalFloor(target.dbPath)).toBe("in_loop");
  });

  it("with agent_flags unreadable the floor already counts as in_loop; the alert still goes out", () => {
    const target = harness();
    rmSync(target.dbPath, { force: true });
    const result = monitor(target, () => ({ killSwitchTripped: true }));
    expect(result.floor).toBe("in_loop");
    expect(target.floorAlerts.alerts).toHaveLength(1);
  });

  it("never lowers: healthy signals leave a raised floor where it is", () => {
    const target = harness();
    raiseGlobalFloor(target.dbPath, "in_loop");
    const softer = policyWith((raw) => {
      raw.floorTriggers.raiseTo.global = "on_loop";
    });
    expect(monitor(target, () => ({ dailyLossUsd: 0 })).floor).toBe("in_loop");
    expect(monitor(target, () => ({ dailyLossUsd: 1e9 }), softer).floor).toBe("in_loop");
    expect(readFloorRaises(target.dbPath)).toEqual([]);
  });

  it("AC7: an automatic raise cannot vouch for a later silent loosening", () => {
    const target = harness();
    const softer = policyWith((raw) => {
      raw.floorTriggers.raiseTo.global = "on_loop";
    });
    monitor(target, () => ({ dailySpendUsd: 1e9 }), softer); // autonomous -> on_loop, logged in floor_raises
    raiseGlobalFloor(target.dbPath, "in_loop");

    const db = new Database(target.dbPath);
    try {
      expect(() => db.prepare("update agent_flags set value = 'on_loop' where key = 'global_floor'").run()).toThrow(
        /may only be loosened through lowerGlobalFloor/,
      );
      expect(() => db.prepare("delete from floor_raises").run()).toThrow(/append-only/);
    } finally {
      db.close();
    }
    expect(readGlobalFloor(target.dbPath)).toBe("in_loop");

    // the manual, logged path still works
    lowerGlobalFloor(target.dbPath, { to: "autonomous", operator: "jeffg", reason: "limits reviewed" });
    expect(readGlobalFloor(target.dbPath)).toBe("autonomous");
    expect(readFloorChanges(target.dbPath)).toHaveLength(1);
  });
});

describe("LangSmith alert adapter", () => {
  it("is a clearly marked stub that refuses to be used", () => {
    expect(() => new LangSmithFloorAlertSinkStub()).toThrow(LANGSMITH_ALERT_STUB_STATUS);
    expect(LANGSMITH_ALERT_STUB_STATUS).toMatch(/STUB/);
  });

  it("dedupes in memory by key", () => {
    const sink = new InMemoryFloorAlertSink();
    const alert = { key: "k", scope: "run" as const, trigger: "t", raiseTo: "in_loop" as const, floorAfter: "in_loop" as const, detail: "", configVersion: "1.2.0" };
    expect(sink.emitOnce(alert).sent).toBe(true);
    expect(sink.emitOnce(alert).sent).toBe(false);
    expect(sink.attemptsCount).toBe(2);
  });
});
