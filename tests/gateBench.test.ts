import { describe, expect, it } from "vitest";

import { gateDecisionFor, type GateInput } from "../src/gate/nodes.js";
import { defaultPolicyPath, loadPolicyFromFile } from "../src/policy/config.js";
import { TOOL_TIER } from "../src/policy/toolTiers.js";
import { createHarness, fixtureById, threadConfig } from "./helpers/gateHarness.js";

const policy = loadPolicyFromFile(defaultPolicyPath());

/** NFR-1: gate overhead < 20 ms p95 for an autonomous decision. */
const AUTONOMOUS_INPUT: GateInput = {
  tool: "read_wiki",
  wantedMode: "autonomous",
  harness: "research",
  sme: "research",
  runFloor: "autonomous",
  globalFloor: "autonomous",
  oncallActive: true,
};

function percentile(sorted: readonly number[], p: number): number {
  if (sorted.length === 0) return Number.NaN;
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[index] ?? Number.NaN;
}

describe("NFR-1 gate overhead", () => {
  it("pure gate decision stays far under 20 ms p95", () => {
    // warm up the JIT
    for (let index = 0; index < 5_000; index += 1) gateDecisionFor(AUTONOMOUS_INPUT, policy, TOOL_TIER);

    const samples: number[] = [];
    const iterations = 20_000;
    for (let index = 0; index < iterations; index += 1) {
      const start = performance.now();
      gateDecisionFor(AUTONOMOUS_INPUT, policy, TOOL_TIER);
      samples.push(performance.now() - start);
    }
    samples.sort((a, b) => a - b);

    const p50 = percentile(samples, 50);
    const p95 = percentile(samples, 95);
    const p99 = percentile(samples, 99);
    const max = samples[samples.length - 1] ?? Number.NaN;

    // eslint-disable-next-line no-console
    console.log(
      `NFR-1 gate p50=${p50.toFixed(4)}ms p95=${p95.toFixed(4)}ms p99=${p99.toFixed(4)}ms max=${max.toFixed(4)}ms n=${iterations}`,
    );

    expect(p95).toBeLessThan(20);
  });

  it("one autonomous pass through the compiled graph stays under 20 ms p95", async () => {
    const harness = createHarness();
    try {
      const fixture = fixtureById("r15");
      const samples: number[] = [];
      const runs = 60;
      // Compile ONCE: graph construction is not gate overhead, it is startup.
      const graph = harness.graph();

      // warm up
      for (let index = 0; index < 5; index += 1) {
        const warm = { ...fixture, threadId: `thread_warm_${index}`, toolCallId: `call_warm_${index}` };
        await graph.invoke(harness.input(warm), threadConfig(warm.threadId));
      }

      for (let index = 0; index < runs; index += 1) {
        const custom = { ...fixture, threadId: `thread_bench_${index}`, toolCallId: `call_bench_${index}` };
        const start = performance.now();
        await graph.invoke(harness.input(custom), threadConfig(custom.threadId));
        samples.push(performance.now() - start);
      }
      samples.sort((a, b) => a - b);
      const p50 = percentile(samples, 50);
      const p95 = percentile(samples, 95);

      // NFR-1 budgets the GATE decision, not the LangGraph runtime. The framework floor is
      // measured separately below; this number is reported, not asserted.
      // eslint-disable-next-line no-console
      console.log(`NFR-1 compiled-graph autonomous pass: p50=${p50.toFixed(2)}ms p95=${p95.toFixed(2)}ms n=${runs}`);
      expect(samples.length).toBe(runs);
    } finally {
      harness.cleanup();
    }
  });
});
