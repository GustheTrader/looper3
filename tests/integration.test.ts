import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterAll, describe, expect, it } from "vitest";

import { initializeAgentFlagsStore, raiseGlobalFloor, readGlobalFloor } from "../src/flags/agentFlags.js";
import { defaultPolicyPath, loadPolicyFromFile } from "../src/policy/config.js";
import { bindingCap, effectiveMode } from "../src/policy/modes.js";
import { lookupTier } from "../src/policy/toolTiers.js";
import type { Mode, Tier } from "../src/policy/types.js";

/**
 * M1 x M2 seam: the global floor read fails closed to in_loop, so a broken flag store can only
 * make the gate stricter - never looser. This is the contract M3's gate node inherits.
 */
const policy = loadPolicyFromFile(defaultPolicyPath());
const tmpRoot = process.env["TMPDIR"] ?? os.tmpdir();
const dirs: string[] = [];

function freshDbPath(): string {
  const dir = mkdtempSync(path.join(tmpRoot, "looper3-integration-"));
  dirs.push(dir);
  return path.join(dir, "agent_flags.sqlite");
}

afterAll(() => {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true });
});

function decide(dbPath: string, tool: string): { mode: Mode; tier: Tier | undefined; floor: Mode } {
  const floor = readGlobalFloor(dbPath);
  const tier = lookupTier(tool).unregistered === true ? undefined : lookupTier(tool).tier;
  const mode = effectiveMode("autonomous", "research", "research", tier, floor, policy);
  expect(bindingCap("autonomous", "research", "research", tier, floor, policy)).toBeTruthy();
  return { mode, tier, floor };
}

describe("floor store -> gate decision", () => {
  it("runs a tier-1 read autonomously on a healthy store", () => {
    const dbPath = freshDbPath();
    initializeAgentFlagsStore(dbPath);
    expect(decide(dbPath, "read_wiki")).toEqual({ mode: "autonomous", tier: 1, floor: "autonomous" });
  });

  it("binds to in_loop as soon as the global floor is raised", () => {
    const dbPath = freshDbPath();
    initializeAgentFlagsStore(dbPath);
    raiseGlobalFloor(dbPath, "in_loop");

    const decision = decide(dbPath, "read_wiki");
    expect(decision.floor).toBe("in_loop");
    expect(decision.mode).toBe("in_loop");
    expect(bindingCap("autonomous", "research", "research", 1, decision.floor, policy)).toBe("floor");
  });

  it("still fails closed when the store is corrupt", () => {
    const dbPath = freshDbPath();
    writeFileSync(dbPath, "corrupted beyond recognition", "utf8");
    expect(decide(dbPath, "read_wiki").mode).toBe("in_loop");
  });

  it("fails closed for a tool missing from the registry even on a healthy store", () => {
    const dbPath = freshDbPath();
    initializeAgentFlagsStore(dbPath);
    const decision = decide(dbPath, "some_unlisted_tool");
    expect(decision.tier).toBeUndefined();
    expect(decision.mode).toBe("in_loop");
    expect(bindingCap("autonomous", "research", "research", undefined, decision.floor, policy)).toBe("unregistered");
  });
});
