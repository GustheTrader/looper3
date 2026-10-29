import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { activePolicy, resetActivePolicy } from "../src/policy/activePolicy.js";
import {
  BASELINE_HARNESS_CAPS,
  PolicyConfigError,
  assertPaperOnly,
  defaultPolicyPath,
  loadPolicyFromFile,
  parsePolicyConfig,
} from "../src/policy/config.js";
import { SME_IDS, type Mode } from "../src/policy/types.js";

const POLICY_PATH = defaultPolicyPath();
const tmpRoot = process.env["TMPDIR"] ?? os.tmpdir();

function shippedRaw(): Record<string, unknown> {
  return JSON.parse(readFileSync(POLICY_PATH, "utf8")) as Record<string, unknown>;
}

function withMutation(mutate: (raw: Record<string, unknown>) => void): Record<string, unknown> {
  const raw = structuredClone(shippedRaw());
  mutate(raw);
  return raw;
}

function harnessCaps(raw: Record<string, unknown>): Record<string, unknown> {
  return raw["harnessCaps"] as Record<string, unknown>;
}

function tierCaps(raw: Record<string, unknown>): Record<string, unknown> {
  return raw["tierCaps"] as Record<string, unknown>;
}

function smeCaps(raw: Record<string, unknown>): Record<string, unknown> {
  return raw["smeMaxAutonomy"] as Record<string, unknown>;
}

function timeouts(raw: Record<string, unknown>): Record<string, unknown> {
  return raw["inLoopTimeoutsMinutes"] as Record<string, unknown>;
}

function expectInvalid(raw: Record<string, unknown>, matcher: RegExp): void {
  expect(() => parsePolicyConfig(raw)).toThrow(PolicyConfigError);
  try {
    parsePolicyConfig(raw);
  } catch (error) {
    expect((error as Error).message).toMatch(matcher);
  }
}

afterEach(() => {
  resetActivePolicy();
  delete process.env["LOOPER3_POLICY_PATH"];
  delete process.env["QS_PAPER_ONLY"];
});

describe("shipped policy config", () => {
  it("loads and pins the PRD values", () => {
    const config = loadPolicyFromFile(POLICY_PATH);

    expect(config.configVersion).toBe("1.0.0");
    expect(config.paperOnly).toBe(true);
    expect(config.harnessCaps).toEqual(BASELINE_HARNESS_CAPS);
    expect(config.harnessCaps.execution).toBe("in_loop");
    expect(config.tierCaps).toEqual({ "1": "autonomous", "2": "autonomous", "3": "on_loop", "4": "in_loop" });
    expect(config.inLoopTimeoutsMinutes).toEqual({ tier3Forced: 30, tier4: 15 });
    expect(config.oncallHeartbeatHours).toBe(8);
    expect(Object.keys(config.smeMaxAutonomy).sort()).toEqual([...SME_IDS].sort());
  });

  it("carries all six PRD open questions as named TODOs and marks the SME caps as placeholders", () => {
    const config = loadPolicyFromFile(POLICY_PATH);
    const keys = Object.keys(config.todo);
    for (const index of [1, 2, 3, 4, 5, 6]) {
      expect(keys.some((key) => key.includes(`PRD_OQ${index}`)), `missing PRD_OQ${index}`).toBe(true);
    }
    expect(keys.some((key) => /placeholder/i.test(key))).toBe(true);
    for (const value of Object.values(config.todo)) expect(value).toMatch(/TODO/);
  });

  it("is deeply frozen", () => {
    const config = loadPolicyFromFile(POLICY_PATH);
    expect(Object.isFrozen(config)).toBe(true);
    expect(Object.isFrozen(config.harnessCaps)).toBe(true);
    expect(Object.isFrozen(config.smeMaxAutonomy)).toBe(true);
    expect(Object.isFrozen(config.tierCaps)).toBe(true);
    expect(Object.isFrozen(config.inLoopTimeoutsMinutes)).toBe(true);
    expect(Object.isFrozen(config.todo)).toBe(true);
  });
});

describe("policy config validation fails closed", () => {
  it("rejects a non-object", () => {
    for (const value of [null, [], "config", 42] as unknown[]) {
      expect(() => parsePolicyConfig(value)).toThrow(PolicyConfigError);
    }
  });

  it("rejects a missing required key", () => {
    expectInvalid(withMutation((raw) => delete raw["harnessCaps"]), /missing required key "harnessCaps"/);
    expectInvalid(
      withMutation((raw) => {
        const caps = harnessCaps(raw);
        delete caps["execution"];
      }),
      /harnessCaps is missing harness "execution"/,
    );
    expectInvalid(
      withMutation((raw) => {
        const caps = smeCaps(raw);
        delete caps["coding"];
      }),
      /smeMaxAutonomy is missing SME "coding"/,
    );
  });

  it("rejects unknown keys instead of ignoring typos", () => {
    expectInvalid(withMutation((raw) => { raw["harnessCap"] = {}; }), /unknown top-level key "harnessCap"/);
    expectInvalid(
      withMutation((raw) => {
        harnessCaps(raw)["ops"] = "autonomous";
      }),
      /unknown harness "ops"/,
    );
    expectInvalid(
      withMutation((raw) => {
        smeCaps(raw)["legal"] = "autonomous";
      }),
      /unknown SME "legal"/,
    );
    expectInvalid(
      withMutation((raw) => {
        tierCaps(raw)["5"] = "in_loop";
      }),
      /unknown tier key "5"/,
    );
  });

  it("rejects any loosening of the harness caps (config may only tighten)", () => {
    expectInvalid(
      withMutation((raw) => {
        harnessCaps(raw)["execution"] = "autonomous";
      }),
      /LOOSER than the PRD baseline/,
    );
    expectInvalid(
      withMutation((raw) => {
        harnessCaps(raw)["build"] = "autonomous";
      }),
      /LOOSER than the PRD baseline/,
    );
    expectInvalid(
      withMutation((raw) => {
        harnessCaps(raw)["business_process"] = "autonomous";
      }),
      /LOOSER than the PRD baseline/,
    );
  });

  it("allows tightening the harness caps", () => {
    const config = parsePolicyConfig(
      withMutation((raw) => {
        harnessCaps(raw)["build"] = "in_loop";
        harnessCaps(raw)["research"] = "on_loop";
      }),
    );
    expect(config.harnessCaps.build).toBe("in_loop");
    expect(config.harnessCaps.research).toBe("on_loop");
  });

  it("rejects any loosening of the tier caps but allows tightening them", () => {
    expectInvalid(
      withMutation((raw) => {
        tierCaps(raw)["4"] = "autonomous";
      }),
      /LOOSER than the FR-2 tier cap/,
    );
    expectInvalid(
      withMutation((raw) => {
        tierCaps(raw)["3"] = "autonomous";
      }),
      /LOOSER than the FR-2 tier cap/,
    );

    const tightened = parsePolicyConfig(
      withMutation((raw) => {
        tierCaps(raw)["3"] = "in_loop";
      }),
    );
    expect(tightened.tierCaps["3"]).toBe("in_loop");
  });

  it("rejects invalid modes", () => {
    expectInvalid(
      withMutation((raw) => {
        harnessCaps(raw)["build"] = "sudo";
      }),
      /must be one of in_loop\|on_loop\|autonomous/,
    );
    expectInvalid(
      withMutation((raw) => {
        smeCaps(raw)["crypto"] = "sudo";
      }),
      /must be one of/,
    );
    expectInvalid(
      withMutation((raw) => {
        tierCaps(raw)["2"] = "sudo";
      }),
      /must be one of/,
    );
  });

  it("requires paperOnly: true", () => {
    expectInvalid(withMutation((raw) => { raw["paperOnly"] = false; }), /paperOnly must be true/);
    expectInvalid(withMutation((raw) => delete raw["paperOnly"]), /paperOnly must be true/);
  });

  it("requires a semver configVersion", () => {
    expectInvalid(withMutation((raw) => { raw["configVersion"] = "1"; }), /configVersion must be a semver/);
    expectInvalid(withMutation((raw) => delete raw["configVersion"]), /configVersion must be a semver/);
  });

  it("bounds the in_loop timeouts and the heartbeat duration", () => {
    expectInvalid(withMutation((raw) => { timeouts(raw)["tier4"] = 0; }), /inLoopTimeoutsMinutes\.tier4/);
    expectInvalid(withMutation((raw) => { timeouts(raw)["tier3Forced"] = 1.5; }), /inLoopTimeoutsMinutes\.tier3Forced/);
    expectInvalid(withMutation((raw) => { timeouts(raw)["tier4"] = 5000; }), /inLoopTimeoutsMinutes\.tier4/);
    expectInvalid(withMutation((raw) => { timeouts(raw)["tier2"] = 5; }), /unknown key "tier2"/);
    expectInvalid(withMutation((raw) => { raw["oncallHeartbeatHours"] = 0; }), /oncallHeartbeatHours/);
    expectInvalid(withMutation((raw) => { raw["oncallHeartbeatHours"] = 169; }), /oncallHeartbeatHours/);
    expectInvalid(withMutation((raw) => { raw["oncallHeartbeatHours"] = "8"; }), /oncallHeartbeatHours/);
  });

  it("requires non-empty string TODO entries", () => {
    expectInvalid(
      withMutation((raw) => {
        (raw["todo"] as Record<string, unknown>)["PRD_OQ1_approvalNotificationChannel"] = "";
      }),
      /todo\.PRD_OQ1_approvalNotificationChannel/,
    );
  });
});

describe("config loading fails closed", () => {
  it("throws when the file is missing", () => {
    expect(() => loadPolicyFromFile(path.join(tmpRoot, "definitely-missing-looper3-policy.json"))).toThrow(
      PolicyConfigError,
    );
  });

  it("throws when the file is not valid JSON", () => {
    const dir = mkdtempSync(path.join(tmpRoot, "looper3-policy-"));
    const file = path.join(dir, "looper3.policy.json");
    writeFileSync(file, "{ this is not json ", "utf8");
    expect(() => loadPolicyFromFile(file)).toThrow(/not valid JSON/);
  });

  it("throws when LOOPER3_POLICY_PATH points at a missing file instead of falling back", () => {
    process.env["LOOPER3_POLICY_PATH"] = path.join(tmpRoot, "nope", "looper3.policy.json");
    expect(() => defaultPolicyPath()).toThrow(/points at a missing file/);
    expect(() => activePolicy()).toThrow(PolicyConfigError);
  });

  it("defaults to the config shipped next to the module", () => {
    expect(defaultPolicyPath()).toMatch(/looper3\.policy\.json$/);
    expect(defaultPolicyPath()).toBe(POLICY_PATH);
  });
});

describe("paper mode guard", () => {
  it("accepts the default and QS_PAPER_ONLY=1", () => {
    expect(() => assertPaperOnly({})).not.toThrow();
    expect(() => assertPaperOnly({ QS_PAPER_ONLY: "" })).not.toThrow();
    expect(() => assertPaperOnly({ QS_PAPER_ONLY: "1" })).not.toThrow();
  });

  it("refuses anything other than 1", () => {
    for (const value of ["0", "true", "yes", "2", "1 "]) {
      expect(() => assertPaperOnly({ QS_PAPER_ONLY: value })).toThrow(/paper mode only/);
    }
  });

  it("activePolicy refuses to load when QS_PAPER_ONLY=0", () => {
    resetActivePolicy();
    process.env["QS_PAPER_ONLY"] = "0";
    expect(() => activePolicy()).toThrow(/paper mode only/);
  });

  it("activePolicy caches one validated config per process", () => {
    resetActivePolicy();
    const first = activePolicy();
    const second = activePolicy();
    expect(second).toBe(first);
    expect(first.configVersion).toBe("1.0.0");
    expect(first.harnessCaps.execution).toBe<Mode>("in_loop");
  });
});
