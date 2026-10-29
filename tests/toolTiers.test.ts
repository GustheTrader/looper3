import { describe, expect, it } from "vitest";

import {
  TOOL_TIER,
  TOOL_TIER_REGISTRY_VERSION,
  ToolTierRegistryError,
  UNREGISTERED_TIER,
  isRegisteredTool,
  lookupTier,
  raiseTier,
  registeredTier,
  validateRegistry,
  type ToolTierEntry,
  type ToolTierRegistry,
} from "../src/policy/toolTiers.js";
import { isTier, type Tier } from "../src/policy/types.js";

describe("tool-tier registry", () => {
  it("is versioned with a semver registry version", () => {
    expect(TOOL_TIER_REGISTRY_VERSION).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("has 100% of entries tiered 1-4", () => {
    const entries = Object.values(TOOL_TIER);
    expect(entries.length).toBeGreaterThan(0);
    for (const entry of entries) {
      expect(isTier(entry.tier), `${entry.tool} tier=${String(entry.tier)}`).toBe(true);
      expect(typeof entry.reversible).toBe("boolean");
    }
  });

  it("keeps the misleadingly named read_and_delete at tier 4", () => {
    const entry = lookupTier("read_and_delete");
    expect(entry.tier).toBe(4);
    expect(entry.reversible).toBe(false);
    expect(entry.unregistered).toBeUndefined();
  });

  it("keeps the money-touching stub at tier 4", () => {
    const entry = lookupTier("submit_broker_order_stub");
    expect(entry.tier).toBe(4);
    expect(entry.touchesMoney).toBe(true);
  });

  it("is frozen, entries included", () => {
    expect(Object.isFrozen(TOOL_TIER)).toBe(true);
    for (const entry of Object.values(TOOL_TIER)) expect(Object.isFrozen(entry)).toBe(true);
    expect(() => {
      (TOOL_TIER as Record<string, ToolTierEntry>)["read_wiki"] = {
        tool: "read_wiki",
        tier: 4,
        reversible: false,
      };
    }).toThrow();
  });
});

describe("lookupTier fails closed", () => {
  it("returns tier 4 / unregistered for unknown tools", () => {
    const entry = lookupTier("totally_made_up_tool");
    expect(entry.tier).toBe(UNREGISTERED_TIER);
    expect(entry.tier).toBe(4);
    expect(entry.unregistered).toBe(true);
    expect(entry.reversible).toBe(false);
    expect(Object.isFrozen(entry)).toBe(true);
  });

  it("does not resolve prototype keys as registered tools", () => {
    for (const name of ["__proto__", "constructor", "toString", "hasOwnProperty"]) {
      const entry = lookupTier(name);
      expect(entry.tier).toBe(4);
      expect(entry.unregistered).toBe(true);
      expect(isRegisteredTool(name)).toBe(false);
      expect(registeredTier(name)).toBeUndefined();
    }
  });

  it("never mutates the registry when an unknown tool is looked up", () => {
    const before = Object.keys(TOOL_TIER).length;
    lookupTier("ghost");
    expect(Object.keys(TOOL_TIER).length).toBe(before);
  });

  it("handles non-string input without throwing", () => {
    expect(lookupTier(undefined as unknown as string).tier).toBe(4);
    expect(lookupTier(null as unknown as string).unregistered).toBe(true);
  });
});

describe("raiseTier can only raise", () => {
  it("raises when the proposal is stricter", () => {
    expect(raiseTier(1, 3)).toBe(3);
    expect(raiseTier(2, 4)).toBe(4);
    expect(raiseTier(3, 4)).toBe(4);
  });

  it("ignores attempts to lower the tier", () => {
    expect(raiseTier(4, 1)).toBe(4);
    expect(raiseTier(3, 2)).toBe(3);
    expect(raiseTier(2, 1)).toBe(2);
    expect(raiseTier(1, 1)).toBe(1);
  });

  it("collapses invalid tiers to 4", () => {
    expect(raiseTier(1, 5 as Tier)).toBe(4);
    expect(raiseTier(0 as Tier, 1)).toBe(4);
    expect(raiseTier(1, "3" as unknown as Tier)).toBe(4);
    expect(raiseTier(Number.NaN as Tier, 2)).toBe(4);
  });

  it("is monotonic over the whole tier matrix", () => {
    const tiers: Tier[] = [1, 2, 3, 4];
    for (const current of tiers) {
      for (const proposed of tiers) {
        const raised = raiseTier(current, proposed);
        expect(raised).toBeGreaterThanOrEqual(current);
        expect(raised).toBeGreaterThanOrEqual(proposed);
      }
    }
  });
});

describe("validateRegistry", () => {
  const valid: ToolTierRegistry = {
    read_something: { tool: "read_something", tier: 1, reversible: true },
  };

  it("accepts a valid registry", () => {
    expect(() => validateRegistry(valid)).not.toThrow();
  });

  it("rejects a tier-1 tool that claims to be irreversible", () => {
    expect(() =>
      validateRegistry({ bad: { tool: "bad", tier: 1, reversible: false } }),
    ).toThrow(ToolTierRegistryError);
  });

  it("rejects a tier-4 tool that claims to be reversible", () => {
    expect(() =>
      validateRegistry({ bad: { tool: "bad", tier: 4, reversible: true } }),
    ).toThrow(/tier 4 \(critical\/irreversible\) but marked reversible/);
  });

  it("rejects a key/name mismatch and an empty compensating action", () => {
    expect(() =>
      validateRegistry({ other_key: { tool: "name", tier: 2, reversible: true } }),
    ).toThrow(/does not match entry\.tool/);
    expect(() =>
      validateRegistry({ tool: { tool: "tool", tier: 3, reversible: false, compensatingAction: "  " } }),
    ).toThrow(/empty compensatingAction/);
  });

  it("rejects an invalid tier and a missing reversible flag", () => {
    expect(() =>
      validateRegistry({ tool: { tool: "tool", tier: 9 as Tier, reversible: true } }),
    ).toThrow(/invalid tier/);
    expect(() =>
      validateRegistry({ tool: { tool: "tool", tier: 2 } as unknown as ToolTierEntry }),
    ).toThrow(/must declare reversible/);
  });
});
