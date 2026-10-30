import { describe, expect, it } from "vitest";

import { TRADING_AND_BETTING_SMES, applyRouterDecision, assertAutonomySlot, autonomySlotFor } from "../src/gate/router.js";
import type { AutonomySlot } from "../src/gate/router.js";
import type { Mode } from "../src/policy/types.js";

describe("router clamp (FR-7): only lowers autonomy", () => {
  it("tightens wantedMode when the router proposes a stricter mode", () => {
    const result = applyRouterDecision(
      { harness: "research", sme: "research", wantedMode: "autonomous" },
      { harness: "research", sme: "research", wantedMode: "on_loop" },
    );
    expect(result.wantedMode).toBe("on_loop");
  });

  it("refuses to loosen when the router proposes a looser mode", () => {
    for (const [current, proposed] of [
      ["in_loop", "autonomous"],
      ["in_loop", "on_loop"],
      ["on_loop", "autonomous"],
    ] as [Mode, Mode][]) {
      const result = applyRouterDecision(
        { harness: "research", sme: "research", wantedMode: current },
        { harness: "research", sme: "research", wantedMode: proposed },
      );
      expect(result.wantedMode).toBe(current);
    }
  });

  it("never re-routes once the state is marked routed", () => {
    const result = applyRouterDecision(
      { harness: "build", sme: "coding", wantedMode: "in_loop", routed: true },
      { harness: "research", sme: "research", wantedMode: "autonomous" },
    );
    expect(result).toEqual({ harness: "build", sme: "coding", wantedMode: "in_loop" });
  });

  it("keeps the current mode when the router output is unreadable", () => {
    const result = applyRouterDecision(
      { harness: "research", sme: "research", wantedMode: "on_loop" },
      { harness: "research", sme: "research", wantedMode: "sudo" as Mode },
    );
    expect(result.wantedMode).toBe("on_loop");
  });
});

describe("autonomy_mode intake slot (FR-7)", () => {
  it("is never wiki-fillable", () => {
    for (const sme of ["crypto", "nfl", "research", "coding"] as const) {
      expect(autonomySlotFor(sme).wikiFillable).toBe(false);
    }
  });

  it("has no default for the trading and betting SMEs", () => {
    for (const sme of TRADING_AND_BETTING_SMES) {
      expect(autonomySlotFor(sme).allowDefault).toBe(false);
    }
    expect(autonomySlotFor("research").allowDefault).toBe(true);
    expect(autonomySlotFor("coding").allowDefault).toBe(true);
  });

  it("rejects a slot that is wiki-fillable or defaulted for a trading/betting SME", () => {
    const cryptoSlot = autonomySlotFor("crypto");
    expect(() => assertAutonomySlot(cryptoSlot, "crypto")).not.toThrow();

    const wikiFilled = { ...cryptoSlot, wikiFillable: true } as unknown as AutonomySlot;
    expect(() => assertAutonomySlot(wikiFilled, "crypto")).toThrow(/never be wiki-fillable/);

    const defaulted = { ...cryptoSlot, allowDefault: true } as unknown as AutonomySlot;
    expect(() => assertAutonomySlot(defaulted, "crypto")).toThrow(/must not have a default/);
    expect(() => assertAutonomySlot({ ...defaulted, sme: "nfl" }, "nfl")).toThrow(/must not have a default/);

    // The same slot is fine for a non-money SME.
    expect(() => assertAutonomySlot(autonomySlotFor("research"), "research")).not.toThrow();
  });

  it("rejects a slot built for a different SME", () => {
    expect(() => assertAutonomySlot(autonomySlotFor("research"), "coding")).toThrow(/built for research/);
  });
});
