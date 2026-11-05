/**
 * M6: roll-out to every harness. PRD exit criterion: "all side-effecting tools in all harnesses
 * gated". PRD acceptance criterion 1 (second half): a tier-4 merge in a build run hits approval.
 */
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { gateDecisionFor } from "../src/gate/nodes.js";
import { HARNESS_MANIFEST, harnessAllows } from "../src/harness/manifest.js";
import { createGatedToolRunner, createHarnessRunners } from "../src/harness/toolRunner.js";
import { TOOL_TIER } from "../src/policy/toolTiers.js";
import { HARNESS_IDS, type HarnessId, type SmeId } from "../src/policy/types.js";
import { checkHarnessGating } from "../scripts/check-harness-gating.js";
import { createHarness, type Harness } from "./helpers/gateHarness.js";

const ROOT = path.resolve(import.meta.dirname, "..");
const harnesses: Harness[] = [];
const tempDirs: string[] = [];
function harness(options: Parameters<typeof createHarness>[0] = {}): Harness {
  const created = createHarness(options);
  harnesses.push(created);
  return created;
}
afterEach(() => {
  while (harnesses.length > 0) harnesses.pop()?.cleanup();
  while (tempDirs.length > 0) rmSync(tempDirs.pop() as string, { recursive: true, force: true });
});

const SME_FOR: Record<HarnessId, SmeId> = {
  research: "research",
  analysis: "research",
  monitoring: "research",
  business_process: "coding",
  build: "coding",
  execution: "crypto",
};

const EXPECTED_STATUS = { autonomous: "executed", on_loop: "awaiting_veto", in_loop: "awaiting_approval" } as const;

describe("every harness, every manifest tool, through the gate", () => {
  for (const oncallActive of [true, false]) {
    it(`status matches the gate decision for all ${Object.values(HARNESS_MANIFEST).flat().length} pairs (on call: ${oncallActive})`, async () => {
      const target = harness({ heartbeatActive: oncallActive });
      const runners = createHarnessRunners(target.graph(), SME_FOR);
      let calls = 0;
      for (const harnessId of HARNESS_IDS) {
        for (const tool of HARNESS_MANIFEST[harnessId]) {
          calls += 1;
          const threadId = `thread_${harnessId}_${tool}_${oncallActive}`;
          const result = await runners[harnessId].call({ threadId, toolCallId: `call_${calls}`, tool });
          const expected = gateDecisionFor(
            { tool, wantedMode: "autonomous", harness: harnessId, sme: SME_FOR[harnessId], runFloor: "autonomous", globalFloor: "autonomous", oncallActive },
            target.policy,
          );
          const label = `${harnessId}/${tool}`;
          expect(result.status, label).toBe(EXPECTED_STATUS[expected.mode]);
          expect(result.gate?.mode, label).toBe(expected.mode);
          expect(result.harness, label).toBe(harnessId);
        }
      }
      // every gated call went through the gate node exactly once
      expect(target.nodeCounts["gateNode"]).toBe(calls);
      // only autonomous decisions executed anything
      const executedNow = target.ledger.appliedKeys.length;
      expect(executedNow).toBeLessThan(calls);
    });
  }

  it("AC1 (second half): a tier-4 merge in a build run hits approval; approving executes it once", async () => {
    const target = harness();
    const build = createGatedToolRunner({ graph: target.graph(), harness: "build", sme: "coding" });
    const parked = await build.call({ threadId: "thread_build_merge", toolCallId: "call_merge", tool: "merge_pull_request" });
    expect(parked.status).toBe("awaiting_approval");
    expect(parked.gate).toMatchObject({ mode: "in_loop", tier: 4 });
    expect(target.ledger.totalEffects).toBe(0);

    const done = await build.resume("thread_build_merge", "merge_pull_request", { approved: true, by: "jeffg" });
    expect(done.status).toBe("executed");
    expect(target.ledger.totalEffects).toBe(1);
  });

  it("the trap tool read_and_delete is tier 4 in both harnesses that list it", async () => {
    const target = harness();
    for (const harnessId of ["build", "business_process"] as const) {
      const runner = createGatedToolRunner({ graph: target.graph(), harness: harnessId, sme: "coding" });
      const result = await runner.call({ threadId: `thread_trap_${harnessId}`, toolCallId: `call_trap_${harnessId}`, tool: "read_and_delete" });
      expect(result.status, harnessId).toBe("awaiting_approval");
      expect(result.gate?.tier, harnessId).toBe(4);
    }
  });
});

describe("runner fail-closed paths", () => {
  it("refuses a tool the harness manifest does not list, before the gate runs", async () => {
    const target = harness();
    const research = createGatedToolRunner({ graph: target.graph(), harness: "research", sme: "research" });
    const result = await research.call({ threadId: "thread_borrow", toolCallId: "call_borrow", tool: "submit_broker_order_stub" });
    expect(result.status).toBe("rejected");
    expect(result.reason).toMatch(/not in the research harness manifest/);
    expect(target.nodeTrace).toEqual([]);
    expect(target.ledger.totalEffects).toBe(0);
  });

  it("refuses an unknown harness or SME at construction", () => {
    const target = harness();
    expect(() => createGatedToolRunner({ graph: target.graph(), harness: "ops" as HarnessId, sme: "research" })).toThrow(/unknown harness/);
    expect(() => createGatedToolRunner({ graph: target.graph(), harness: "build", sme: "poker" as SmeId })).toThrow(/unknown SME/);
  });

  it("an invalid hand-off is a rejection, not an error and not a retry", async () => {
    const target = harness();
    const build = createGatedToolRunner({ graph: target.graph(), harness: "build", sme: "coding" });
    const result = await build.call({ threadId: " ", toolCallId: "call_x", tool: "read_wiki" });
    expect(result.status).toBe("rejected");
    expect(result.reason).toMatch(/intake refused/);
    expect(target.ledger.totalEffects).toBe(0);
  });

  it("a caller cannot loosen anything by asking: wantedMode only ever tightens", async () => {
    const target = harness();
    const monitoring = createGatedToolRunner({ graph: target.graph(), harness: "monitoring", sme: "research" });
    const asked = await monitoring.call({ threadId: "thread_ask", toolCallId: "call_ask", tool: "write_raw_output", wantedMode: "in_loop" });
    expect(asked.status).toBe("awaiting_approval");
    expect(asked.gate?.binding).toBe("wanted");
  });

  it("the manifest lookup is exact (no prefix or case games)", () => {
    expect(harnessAllows(HARNESS_MANIFEST, "build", "merge_pull_request")).toBe(true);
    expect(harnessAllows(HARNESS_MANIFEST, "build", "merge_pull")).toBe(false);
    expect(harnessAllows(HARNESS_MANIFEST, "build", "MERGE_PULL_REQUEST")).toBe(false);
  });
});

describe("npm run check:gating (CI)", () => {
  const sourceDirs = [path.join(ROOT, "src"), path.join(ROOT, "tools"), path.join(ROOT, "scripts")];

  it("passes on the real tree and covers every manifest pair", async () => {
    const result = await checkHarnessGating({ root: ROOT, toolsDir: path.join(ROOT, "tools"), sourceDirs });
    expect(result.errors).toEqual([]);
    expect(result.coverage).toHaveLength(Object.values(HARNESS_MANIFEST).flat().length);
    for (const tool of Object.values(HARNESS_MANIFEST).flat()) expect(TOOL_TIER[tool], tool).toBeDefined();
  });

  it("fails when a tool definition names no harness", async () => {
    const result = await checkHarnessGating({ root: ROOT, toolsDir: path.join(ROOT, "test/fixtures/tools-no-harness"), sourceDirs: [] });
    expect(result.errors.join("\n")).toMatch(/declares no harnesses/);
  });

  it("fails when the manifest drifts from the definitions", async () => {
    const drifted = { ...HARNESS_MANIFEST, research: [...HARNESS_MANIFEST.research, "merge_pull_request"] };
    const result = await checkHarnessGating({ root: ROOT, toolsDir: path.join(ROOT, "tools"), sourceDirs: [], manifest: drifted });
    expect(result.errors.join("\n")).toMatch(/manifest for research lists \["merge_pull_request"\]/);
  });

  it("fails when a manifest lists an untiered tool", async () => {
    const untiered = { ...HARNESS_MANIFEST, monitoring: [...HARNESS_MANIFEST.monitoring, "mystery_tool"] };
    const result = await checkHarnessGating({ root: ROOT, toolsDir: path.join(ROOT, "tools"), sourceDirs: [], manifest: untiered });
    expect(result.errors.join("\n")).toMatch(/"mystery_tool", which has no tier/);
  });

  it("fails when any other file calls the executor (ungated execution path)", async () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "looper3-gating-"));
    tempDirs.push(dir);
    writeFileSync(path.join(dir, "shortcut.ts"), "export const go = (ledger: any) => ledger.execute({});\n");
    const result = await checkHarnessGating({ root: dir, toolsDir: path.join(ROOT, "tools"), sourceDirs: [dir] });
    expect(result.errors.join("\n")).toMatch(/shortcut\.ts calls the side-effect executor directly/);
  });
});
