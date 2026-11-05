/**
 * Gate overhead benchmark (NFR-1).
 *
 * Measures the PURE gate decision (the thing the 20 ms p95 budget applies to) and, separately, a
 * full autonomous pass through the compiled LangGraph for context. No network, no db: the pure
 * measure uses the frozen policy config + tier registry only.
 *
 *   npm run bench:gate
 */
import { gateDecisionFor, type GateInput } from "../src/gate/nodes.js";
import { defaultPolicyPath, loadPolicyFromFile } from "../src/policy/config.js";
import { TOOL_TIER } from "../src/policy/toolTiers.js";

const policy = loadPolicyFromFile(defaultPolicyPath());

const CASES: readonly { name: string; input: GateInput }[] = [
  {
    name: "autonomous tier-1 read (research)",
    input: {
      tool: "read_wiki",
      wantedMode: "autonomous",
      harness: "research",
      sme: "research",
      runFloor: "autonomous",
      globalFloor: "autonomous",
      oncallActive: true,
    },
  },
  {
    name: "tier-4 merge (build)",
    input: {
      tool: "merge_pull_request",
      wantedMode: "autonomous",
      harness: "build",
      sme: "coding",
      runFloor: "autonomous",
      globalFloor: "autonomous",
      oncallActive: true,
    },
  },
  {
    name: "unregistered tool (research)",
    input: {
      tool: "mystery_tool",
      wantedMode: "autonomous",
      harness: "research",
      sme: "research",
      runFloor: "autonomous",
      globalFloor: "autonomous",
      oncallActive: true,
    },
  },
];

function percentile(sorted: readonly number[], p: number): number {
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[index] ?? Number.NaN;
}

const ITERATIONS = 50_000;

for (const testCase of CASES) {
  for (let index = 0; index < 10_000; index += 1) gateDecisionFor(testCase.input, policy, TOOL_TIER);

  const samples: number[] = [];
  for (let index = 0; index < ITERATIONS; index += 1) {
    const start = performance.now();
    gateDecisionFor(testCase.input, policy, TOOL_TIER);
    samples.push(performance.now() - start);
  }
  samples.sort((a, b) => a - b);

  const p50 = percentile(samples, 50);
  const p95 = percentile(samples, 95);
  const p99 = percentile(samples, 99);
  const max = samples[samples.length - 1] ?? Number.NaN;

  console.log(
    `${testCase.name}:\n  p50=${p50.toFixed(4)}ms  p95=${p95.toFixed(4)}ms  p99=${p99.toFixed(4)}ms  max=${max.toFixed(4)}ms  n=${ITERATIONS}`,
  );
}
