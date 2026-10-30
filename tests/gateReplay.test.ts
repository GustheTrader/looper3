import { Command } from "@langchain/langgraph";
import { afterAll, describe, expect, it } from "vitest";

import type { GateState } from "../src/gate/state.js";
import { gateDecisionFor, routeForMode } from "../src/gate/nodes.js";
import { defaultPolicyPath, loadPolicyFromFile } from "../src/policy/config.js";
import { TOOL_TIER } from "../src/policy/toolTiers.js";
import {
  ROUTE_STRICTNESS,
  createFakeTradingGraph,
  gateRouteFor,
  isAtLeastAsStrict,
  legacyRouteFor,
  type LegacyRoute,
} from "../src/trading/fakeTradingGraph.js";
import { createHarness, fixtureById, keyFor, loadReplayFixtures, promptValues, stateOf, threadConfig } from "./helpers/gateHarness.js";

const policy = loadPolicyFromFile(defaultPolicyPath());
const fixtures = loadReplayFixtures();
const legacyGraph = createFakeTradingGraph(policy);

const routeMode = (route: LegacyRoute): number => ROUTE_STRICTNESS[route];

afterAll(() => {
  // nothing global to tear down; kept as a marker if the replay set grows a shared store
});

describe("replay set integrity", () => {
  it("holds 30 recorded pending actions with unique ids and locked expectations", () => {
    expect(fixtures.length).toBe(30);
    expect(new Set(fixtures.map((f) => f.id)).size).toBe(30);
    for (const fixture of fixtures) {
      expect(fixture.threadId).toBeTruthy();
      expect(fixture.toolCallId).toBeTruthy();
      expect(["execute", "vetoWindow", "approval", "reject"]).toContain(fixture.gateRoute);
      expect(["execute", "vetoWindow", "approval", "reject"]).toContain(fixture.legacyRoute);
    }
  });

  it("covers every harness type", () => {
    const harnesses = new Set(fixtures.map((f) => f.harness));
    expect([...harnesses].sort()).toEqual(
      ["analysis", "build", "business_process", "execution", "monitoring", "research"].sort(),
    );
  });
});

describe("gate routing on the recorded replay set", () => {
  it("matches the locked gate outcome for all 30 fixtures", () => {
    for (const fixture of fixtures) {
      expect(gateRouteFor(fixture, policy), `${fixture.id} ${fixture.tool} in ${fixture.harness}`).toBe(
        fixture.gateRoute,
      );
    }
  });

  it("reproduces the fake trading graph's legacy outcome for all 30 fixtures", () => {
    for (const fixture of fixtures) {
      expect(legacyRouteFor(fixture, policy), `${fixture.id}`).toBe(fixture.legacyRoute);
      expect(legacyGraph.route(fixture), `${fixture.id}`).toBe(fixture.legacyRoute);
    }
  });

  it("is IDENTICAL to the legacy router for the execution harness (already in_loop)", () => {
    const executionFixtures = fixtures.filter((fixture) => fixture.harness === "execution");
    expect(executionFixtures.length).toBe(6);
    for (const fixture of executionFixtures) {
      const legacy = legacyGraph.route(fixture);
      const gate = gateRouteFor(fixture, policy);
      expect(legacy, `${fixture.id}`).toBe("approval");
      expect(gate, `${fixture.id}`).toBe(legacy);
    }
  });

  it("is never LOOSER than the legacy router for any fixture", () => {
    for (const fixture of fixtures) {
      const legacy = legacyGraph.route(fixture);
      const gate = gateRouteFor(fixture, policy);
      expect(isAtLeastAsStrict(gate, legacy), `${fixture.id}: gate ${gate} vs legacy ${legacy}`).toBe(true);
      expect(routeMode(gate)).toBeGreaterThanOrEqual(routeMode(legacy));
    }
  });

  it("tightens exactly 10 fixtures (tier cap, floor, or unregistered tool) and loosens none", () => {
    const tightened = fixtures.filter((fixture) => gateRouteFor(fixture, policy) !== legacyGraph.route(fixture));
    expect(tightened.map((fixture) => fixture.id)).toEqual([
      "r09",
      "r10",
      "r11",
      "r14",
      "r17",
      "r18",
      "r19",
      "r23",
      "r24",
      "r25",
    ]);
    for (const fixture of tightened) {
      expect(routeMode(gateRouteFor(fixture, policy))).toBeGreaterThan(routeMode(legacyGraph.route(fixture)));
    }
  });

  it("keeps the recorded gate mode in step with routeForMode", () => {
    for (const fixture of fixtures) {
      expect(routeForMode(fixture.gateMode)).toBe(fixture.gateRoute);
      expect(routeForMode(fixture.legacyMode)).toBe(fixture.legacyRoute);
    }
  });

  it("routes an unregistered tool to approval with binding 'unregistered'", async () => {
    const fixture = fixtureById("r05");
    const harness = createHarness();
    try {
      const result = await harness.graph().invoke(harness.input(fixture), threadConfig(fixture.threadId));
      const state = stateOf(result) as GateState;
      expect(state.gate?.tier).toBe(4);
      expect(state.gate?.mode).toBe("in_loop");
      expect(state.gate?.binding).toBe("unregistered");
      expect(promptValues(result)).toHaveLength(1);
    } finally {
      harness.cleanup();
    }
  });
});

describe("documented gap: tier-1 read in the build harness", () => {
  it("resolves to on_loop -> veto window, exactly as the legacy router did", async () => {
    // PRD acceptance criterion 1 ("tier-1 read in build harness executes with no pause") cannot
    // hold while build = on_loop. Phase 2 was told NOT to change the mode math, so this is the
    // current, documented result and it stays an open decision (see PHASE_2_REPORT.md).
    const fixture = fixtureById("r07");
    expect(fixture.tool).toBe("read_wiki");
    expect(fixture.harness).toBe("build");

    const harness = createHarness();
    try {
      const result = await harness.graph().invoke(harness.input(fixture), threadConfig(fixture.threadId));
      const state = stateOf(result) as GateState;
      expect(state.gate?.mode).toBe("on_loop");
      // Both the build harness cap and the coding SME cap sit at on_loop; the documented
      // tie-break (floor > tier > sme > harness) reports "sme". With a research SME the harness
      // cap is the only on_loop constraint and reports "harness" (asserted below).
      expect(state.gate?.binding).toBe("sme");
      expect(
        gateDecisionFor(
          {
            tool: "read_wiki",
            wantedMode: "autonomous",
            harness: "build",
            sme: "research",
            runFloor: "autonomous",
            globalFloor: "autonomous",
          },
          policy,
        ).binding,
      ).toBe("harness");
      const prompts = promptValues(result) as { kind?: string }[];
      expect(prompts).toHaveLength(1);
      expect(prompts[0]?.kind).toBe("veto");
    } finally {
      harness.cleanup();
    }
  });
});

describe("end-to-end: execution-harness fixtures through the gate", () => {
  it("pauses for approval and executes after a human approves", async () => {
    const executionFixtures = fixtures.filter((fixture) => fixture.harness === "execution");
    const harness = createHarness({ globalFloor: "autonomous" });
    try {
      const graph = harness.graph();
      for (const fixture of executionFixtures) {
        const config = threadConfig(fixture.threadId);
        const first = await graph.invoke(harness.input(fixture), config);
        const prompts = promptValues(first) as { kind?: string; tier?: number }[];
        expect(prompts, `${fixture.id}`).toHaveLength(1);
        expect(prompts[0]?.kind).toBe("approval");
        expect(prompts[0]?.tier).toBe(TOOL_TIER[fixture.tool]?.tier ?? 4);

        const final = await graph.invoke(new Command({ resume: { approved: true, by: "jeffg" } }), config);
        const state = stateOf(final) as GateState;
        expect(state.approved, `${fixture.id}`).toBe(true);
        expect(state.executed, `${fixture.id}`).toBe(true);
        expect(harness.ledger.effectsFor(keyFor(fixture)), `${fixture.id}`).toBe(1);
      }
      expect(harness.ledger.appliedKeys.length).toBe(executionFixtures.length);
    } finally {
      harness.cleanup();
    }
  });
});
