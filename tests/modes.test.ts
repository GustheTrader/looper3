import { describe, expect, it } from "vitest";
import fc from "fast-check";

import { defaultPolicyPath, loadPolicyFromFile, type TierCapKey } from "../src/policy/config.js";
import {
  BINDING_PRECEDENCE,
  bindingCap,
  clamp,
  effectiveMode,
  strictest,
  type SmeCaps,
} from "../src/policy/modes.js";
import {
  HARNESS_IDS,
  MODE_ORDER,
  MODES,
  SME_IDS,
  STRICTEST_MODE,
  TIERS,
  isHarnessId,
  isMode,
  isSmeId,
  isTier,
  modeRank,
  type Binding,
  type Mode,
  type Tier,
} from "../src/policy/types.js";

const policy = loadPolicyFromFile(defaultPolicyPath());

type CapBinding = Exclude<Binding, "unregistered">;

const tiersUnderTest: readonly (Tier | undefined)[] = [...TIERS, undefined];

/** Independent reference implementation: the strictest of wanted + every cap (minimum rank). */
function referenceMode(wanted: Mode, caps: readonly Mode[]): Mode {
  const ranks = [wanted, ...caps].map((mode) => modeRank(mode));
  return MODE_ORDER[Math.min(...ranks)] ?? STRICTEST_MODE;
}

function capsFor(harness: string, sme: string, tier: Tier | undefined, floor: Mode): Record<CapBinding, Mode> {
  const tierCap = tier === undefined ? STRICTEST_MODE : (policy.tierCaps[String(tier) as TierCapKey] ?? STRICTEST_MODE);
  return {
    harness: policy.harnessCaps[harness as keyof typeof policy.harnessCaps] ?? STRICTEST_MODE,
    sme: policy.smeMaxAutonomy[sme as keyof typeof policy.smeMaxAutonomy] ?? STRICTEST_MODE,
    tier: tierCap,
    floor,
  };
}

describe("mode algebra", () => {
  it("clamp keeps the stricter mode", () => {
    expect(clamp("autonomous", "on_loop")).toBe("on_loop");
    expect(clamp("on_loop", "autonomous")).toBe("on_loop");
    expect(clamp("in_loop", "autonomous")).toBe("in_loop");
    expect(clamp("autonomous", "in_loop")).toBe("in_loop");
    expect(clamp("autonomous", "autonomous")).toBe("autonomous");
  });

  it("clamp normalises garbage to in_loop instead of loosening", () => {
    expect(clamp("bogus" as Mode, "autonomous")).toBe("in_loop");
    expect(clamp("autonomous", "bogus" as Mode)).toBe("in_loop");
    expect(clamp(undefined as unknown as Mode, "autonomous")).toBe("in_loop");
  });

  it("strictest returns in_loop for empty or unrecognised input", () => {
    expect(strictest()).toBe("in_loop");
    expect(strictest("autonomous", "junk" as Mode)).toBe("in_loop");
    expect(strictest(undefined, null, 42)).toBe("in_loop");
    expect(strictest("autonomous", "on_loop")).toBe("on_loop");
    expect(strictest("autonomous", "on_loop", "in_loop")).toBe("in_loop");
    expect(strictest("autonomous", "autonomous")).toBe("autonomous");
  });

  it("mode order is strictest-first", () => {
    expect([...MODES]).toEqual(["in_loop", "on_loop", "autonomous"]);
    expect(MODE_ORDER[0]).toBe("in_loop");
    expect(modeRank("in_loop")).toBeLessThan(modeRank("on_loop"));
    expect(modeRank("on_loop")).toBeLessThan(modeRank("autonomous"));
  });
});

describe("effectiveMode - exhaustive (wanted x harness x sme x tier x floor)", () => {
  it("equals strictest(wanted, harnessCap, smeCap, tierCap, floor) in all 1080 combinations", () => {
    let combinations = 0;
    let bindingChecked = 0;

    for (const wanted of MODES) {
      for (const harness of HARNESS_IDS) {
        for (const sme of SME_IDS) {
          for (const tier of tiersUnderTest) {
            for (const floor of MODES) {
              combinations += 1;

              const caps = capsFor(harness, sme, tier, floor);
              const capList = [caps.harness, caps.sme, caps.tier, caps.floor];
              const expected = referenceMode(wanted, capList);
              const mode = effectiveMode(wanted, harness, sme, tier, floor, policy);

              // 1. exact five-way reduction
              expect(mode, `${wanted}/${harness}/${sme}/tier=${String(tier)}/${floor}`).toBe(expected);

              // 2. never looser than any single cap (rank 0 is strictest, so <=)
              for (const cap of capList) expect(modeRank(mode)).toBeLessThanOrEqual(modeRank(cap));
              // 3. never looser than what was wanted
              expect(modeRank(mode)).toBeLessThanOrEqual(modeRank(wanted));

              // 4. binding is always one of the documented labels
              const binding = bindingCap(wanted, harness, sme, tier, floor, policy);
              if (tier === undefined) {
                expect(binding).toBe("unregistered");
                expect(mode).toBe("in_loop");
                continue;
              }

              bindingChecked += 1;
              expect(["harness", "sme", "tier", "floor"]).toContain(binding);

              // 5. binding names a cap that actually sits at the effective rank, or - when only
              //    `wanted` is strictest - the strictest cap.
              const atRank = BINDING_PRECEDENCE.filter((name) => modeRank(caps[name]) === modeRank(mode));
              if (atRank.length > 0) {
                expect(atRank).toContain(binding as CapBinding);
              } else {
                const strictestCapRank = Math.min(...BINDING_PRECEDENCE.map((name) => modeRank(caps[name])));
                expect(modeRank(caps[binding as CapBinding])).toBe(strictestCapRank);
              }
            }
          }
        }
      }
    }

    expect(combinations).toBe(1080);
    expect(bindingChecked).toBe(864);
  });

  it("tightening the result never loosens it (idempotence of a second gate pass)", () => {
    for (const wanted of MODES) {
      for (const harness of HARNESS_IDS) {
        for (const sme of SME_IDS) {
          for (const tier of TIERS) {
            for (const floor of MODES) {
              const once = effectiveMode(wanted, harness, sme, tier, floor, policy);
              const twice = effectiveMode(once, harness, sme, tier, floor, policy);
              expect(twice).toBe(once);
            }
          }
        }
      }
    }
  });
});

describe("effectiveMode - FR-2/FR-3 scenarios", () => {
  it("execution harness + tier-4 tool resolves to in_loop bound by the tier cap", () => {
    expect(effectiveMode("autonomous", "execution", "research", 4, "autonomous", policy)).toBe("in_loop");
    expect(bindingCap("autonomous", "execution", "research", 4, "autonomous", policy)).toBe("tier");
  });

  it("build harness caps a tier-2 write at on_loop (bound by the harness)", () => {
    expect(effectiveMode("autonomous", "build", "research", 2, "autonomous", policy)).toBe("on_loop");
    expect(bindingCap("autonomous", "build", "research", 2, "autonomous", policy)).toBe("harness");
  });

  it("the global floor binds when it is the only strict cap", () => {
    expect(effectiveMode("autonomous", "research", "research", 1, "in_loop", policy)).toBe("in_loop");
    expect(bindingCap("autonomous", "research", "research", 1, "in_loop", policy)).toBe("floor");
  });

  it("a money-touching SME cap binds", () => {
    expect(effectiveMode("autonomous", "research", "crypto", 1, "autonomous", policy)).toBe("on_loop");
    expect(bindingCap("autonomous", "research", "crypto", 1, "autonomous", policy)).toBe("sme");
  });

  it("unregistered (undefined tier) is always in_loop / unregistered", () => {
    for (const wanted of MODES) {
      expect(effectiveMode(wanted, "research", "research", undefined, "autonomous", policy)).toBe("in_loop");
      expect(bindingCap(wanted, "research", "research", undefined, "autonomous", policy)).toBe("unregistered");
    }
  });

  it("unknown harness, unknown SME and garbage floor all fail closed", () => {
    expect(effectiveMode("autonomous", "ops", "research", 1, "autonomous", policy)).toBe("in_loop");
    expect(effectiveMode("autonomous", "research", "legal", 1, "autonomous", policy)).toBe("in_loop");
    expect(effectiveMode("autonomous", "research", "research", 1, "sudo" as Mode, policy)).toBe("in_loop");
  });

  it("accepts a resolved SME cap object as well as an SME id", () => {
    const sme: SmeCaps = { maxAutonomy: "in_loop" };
    expect(effectiveMode("autonomous", "research", sme, 1, "autonomous", policy)).toBe("in_loop");
    expect(effectiveMode("autonomous", "research", { maxAutonomy: "nope" as Mode }, 1, "autonomous", policy)).toBe("in_loop");
  });

  it("reports the strictest cap when wanted alone is stricter than every cap", () => {
    expect(effectiveMode("in_loop", "research", "research", 1, "autonomous", policy)).toBe("in_loop");
    expect(["floor", "tier", "sme", "harness"]).toContain(
      bindingCap("in_loop", "research", "research", 1, "autonomous", policy),
    );
  });
});

// ---------------------------------------------------------------------------------------------
// Property test: no input combination may produce a mode looser than any single cap, and any
// malformed input must fail closed to in_loop.
// ---------------------------------------------------------------------------------------------

const modeArb: fc.Arbitrary<unknown> = fc.oneof(
  fc.constantFrom<unknown>(...MODES),
  fc.string(),
  fc.constant(null),
  fc.constant(undefined),
  fc.integer(),
  fc.boolean(),
);

const harnessArb: fc.Arbitrary<unknown> = fc.oneof(
  fc.constantFrom<unknown>(...HARNESS_IDS),
  fc.string(),
  fc.constant(undefined),
  fc.integer(),
);

const smeArb: fc.Arbitrary<unknown> = fc.oneof(
  fc.constantFrom<unknown>(...SME_IDS),
  fc.string(),
  fc.record({ maxAutonomy: modeArb }),
  fc.constant(null),
  fc.constant({ maxAutonomy: "sudo" }),
);

const tierArb: fc.Arbitrary<unknown> = fc.oneof(
  fc.constantFrom<unknown>(...TIERS),
  fc.constant(undefined),
  fc.constant(null),
  fc.integer({ min: -3, max: 9 }),
  fc.string(),
);

function isValidSme(value: unknown): boolean {
  if (isSmeId(value)) return true;
  if (typeof value === "object" && value !== null && "maxAutonomy" in value) {
    return isMode((value as { maxAutonomy: unknown }).maxAutonomy);
  }
  return false;
}

function capsForArbitrary(harness: unknown, sme: unknown, tier: unknown, floor: unknown): readonly Mode[] {
  const harnessCap = isHarnessId(harness) ? policy.harnessCaps[harness] : STRICTEST_MODE;
  const smeCap = isSmeId(sme)
    ? policy.smeMaxAutonomy[sme]
    : isValidSme(sme)
      ? ((sme as SmeCaps).maxAutonomy ?? STRICTEST_MODE)
      : STRICTEST_MODE;
  const tierCap = isTier(tier) ? (policy.tierCaps[String(tier) as TierCapKey] ?? STRICTEST_MODE) : STRICTEST_MODE;
  const floorCap = isMode(floor) ? floor : STRICTEST_MODE;
  return [harnessCap, smeCap, tierCap, floorCap];
}

describe("effectiveMode - properties", () => {
  it("is never looser than any single cap and fails closed on malformed input", () => {
    fc.assert(
      fc.property(modeArb, harnessArb, smeArb, tierArb, modeArb, (wanted, harness, sme, tier, floor) => {
        const mode = effectiveMode(
          wanted as Mode,
          harness as string,
          sme as SmeCaps,
          tier as Tier | undefined,
          floor as Mode,
          policy,
        );
        expect(isMode(mode)).toBe(true);

        const allValid =
          isMode(wanted) && isHarnessId(harness) && isValidSme(sme) && isTier(tier) && isMode(floor);

        if (!allValid) {
          // Anything unrecognised anywhere in the inputs collapses to the strictest mode.
          expect(mode).toBe("in_loop");
          return;
        }

        const caps = capsForArbitrary(harness, sme, tier, floor);
        expect(mode).toBe(referenceMode(wanted as Mode, caps));
        for (const cap of caps) expect(modeRank(mode)).toBeLessThanOrEqual(modeRank(cap));
        expect(modeRank(mode)).toBeLessThanOrEqual(modeRank(wanted as Mode));
        expect(bindingCap(wanted as Mode, harness as string, sme as SmeCaps, tier as Tier, floor as Mode, policy)).not.toBe(
          "unregistered",
        );
      }),
      { numRuns: 750 },
    );
  });
});
