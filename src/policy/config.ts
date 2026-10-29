/**
 * Versioned policy config (FR-8).
 *
 * Fail-closed rules enforced here:
 *  - a missing, unreadable, unparsable or invalid config THROWS - it never falls back to a
 *    looser built-in default;
 *  - the config may only TIGHTEN the PRD baseline caps. Anything looser than the baseline is
 *    rejected, so a bad diff cannot silently grant autonomy;
 *  - `paperOnly` must be true and QS_PAPER_ONLY must not be set to anything but "1".
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  HARNESS_IDS,
  MODES,
  SME_IDS,
  isMode,
  isTier,
  modeRank,
  type HarnessId,
  type Mode,
  type SmeId,
  type Tier,
} from "./types.js";

export type TierCapKey = "1" | "2" | "3" | "4";

export interface PolicyConfig {
  readonly configVersion: string;
  readonly paperOnly: true;
  readonly harnessCaps: Readonly<Record<HarnessId, Mode>>;
  readonly smeMaxAutonomy: Readonly<Record<SmeId, Mode>>;
  readonly tierCaps: Readonly<Record<TierCapKey, Mode>>;
  readonly inLoopTimeoutsMinutes: Readonly<{ tier3Forced: number; tier4: number }>;
  readonly oncallHeartbeatHours: number;
  /** PRD open questions, kept as named TODO values (never decided by code). */
  readonly todo: Readonly<Record<string, string>>;
}

/**
 * PRD "Current model" baseline. A config value must be equal to or STRICTER than this
 * (numeric rank <= baseline rank); anything looser is rejected at load.
 */
export const BASELINE_HARNESS_CAPS: Readonly<Record<HarnessId, Mode>> = Object.freeze({
  research: "autonomous",
  analysis: "autonomous",
  monitoring: "autonomous",
  business_process: "on_loop",
  build: "on_loop",
  execution: "in_loop",
});

/** FR-2 tier ceiling. Config may tighten tier 3 to in_loop but never loosen 1/2/4. */
export const BASELINE_TIER_CAPS: Readonly<Record<TierCapKey, Mode>> = Object.freeze({
  "1": "autonomous",
  "2": "autonomous",
  "3": "on_loop",
  "4": "in_loop",
});

export class PolicyConfigError extends Error {
  readonly issues: readonly string[];

  constructor(message: string, issues: readonly string[] = []) {
    super(issues.length > 0 ? `${message}: ${issues.join("; ")}` : message);
    this.name = "PolicyConfigError";
    this.issues = issues;
  }
}

const REQUIRED_KEYS = [
  "configVersion",
  "paperOnly",
  "harnessCaps",
  "smeMaxAutonomy",
  "tierCaps",
  "inLoopTimeoutsMinutes",
  "oncallHeartbeatHours",
  "todo",
] as const;

const SEMVER = /^\d+\.\d+\.\d+$/;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function describe(value: unknown): string {
  if (typeof value === "string") return JSON.stringify(value);
  if (value === undefined) return "undefined";
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

/**
 * Validates raw JSON into a frozen PolicyConfig, or throws PolicyConfigError with every issue
 * found. Never returns a partially-defaulted object.
 */
export function parsePolicyConfig(raw: unknown, source = "<memory>"): PolicyConfig {
  const issues: string[] = [];

  if (!isPlainObject(raw)) {
    throw new PolicyConfigError(`policy config ${source} must be a JSON object, got ${describe(raw)}`);
  }

  const allowed = new Set<string>(REQUIRED_KEYS);
  for (const key of Object.keys(raw)) {
    if (!allowed.has(key)) issues.push(`unknown top-level key ${JSON.stringify(key)} (fail closed: config typos are never ignored)`);
  }
  for (const key of REQUIRED_KEYS) {
    if (!(key in raw)) issues.push(`missing required key ${JSON.stringify(key)}`);
  }

  const configVersion = raw["configVersion"];
  if (typeof configVersion !== "string" || !SEMVER.test(configVersion)) {
    issues.push(`configVersion must be a semver string, got ${describe(configVersion)}`);
  }

  if (raw["paperOnly"] !== true) {
    issues.push(`paperOnly must be true (paper mode only), got ${describe(raw["paperOnly"])}`);
  }

  // --- harnessCaps -------------------------------------------------------
  const harnessCaps: Partial<Record<HarnessId, Mode>> = {};
  const rawHarnessCaps = raw["harnessCaps"];
  if (!isPlainObject(rawHarnessCaps)) {
    issues.push(`harnessCaps must be an object, got ${describe(rawHarnessCaps)}`);
  } else {
    for (const key of Object.keys(rawHarnessCaps)) {
      if (!(HARNESS_IDS as readonly string[]).includes(key)) {
        issues.push(`harnessCaps has unknown harness ${JSON.stringify(key)}`);
      }
    }
    for (const harness of HARNESS_IDS) {
      const cap = rawHarnessCaps[harness];
      if (cap === undefined) {
        issues.push(`harnessCaps is missing harness ${JSON.stringify(harness)}`);
        continue;
      }
      if (!isMode(cap)) {
        issues.push(`harnessCaps.${harness} must be one of ${MODES.join("|")}, got ${describe(cap)}`);
        continue;
      }
      const baseline = BASELINE_HARNESS_CAPS[harness];
      if (modeRank(cap) > modeRank(baseline)) {
        issues.push(
          `harnessCaps.${harness} = ${cap} is LOOSER than the PRD baseline ${baseline}; the config may only tighten autonomy`,
        );
        continue;
      }
      harnessCaps[harness] = cap;
    }
  }

  // --- smeMaxAutonomy ----------------------------------------------------
  const smeMaxAutonomy: Partial<Record<SmeId, Mode>> = {};
  const rawSme = raw["smeMaxAutonomy"];
  if (!isPlainObject(rawSme)) {
    issues.push(`smeMaxAutonomy must be an object, got ${describe(rawSme)}`);
  } else {
    for (const key of Object.keys(rawSme)) {
      if (!(SME_IDS as readonly string[]).includes(key)) {
        issues.push(`smeMaxAutonomy has unknown SME ${JSON.stringify(key)}`);
      }
    }
    for (const sme of SME_IDS) {
      const cap = rawSme[sme];
      if (cap === undefined) {
        issues.push(`smeMaxAutonomy is missing SME ${JSON.stringify(sme)}`);
        continue;
      }
      if (!isMode(cap)) {
        issues.push(`smeMaxAutonomy.${sme} must be one of ${MODES.join("|")}, got ${describe(cap)}`);
        continue;
      }
      smeMaxAutonomy[sme] = cap;
    }
  }

  // --- tierCaps ---------------------------------------------------------
  const tierCaps: Partial<Record<TierCapKey, Mode>> = {};
  const rawTierCaps = raw["tierCaps"];
  if (!isPlainObject(rawTierCaps)) {
    issues.push(`tierCaps must be an object, got ${describe(rawTierCaps)}`);
  } else {
    for (const key of Object.keys(rawTierCaps)) {
      if (!["1", "2", "3", "4"].includes(key)) {
        issues.push(`tierCaps has unknown tier key ${JSON.stringify(key)}`);
      }
    }
    for (const key of ["1", "2", "3", "4"] as const) {
      const cap = rawTierCaps[key];
      if (cap === undefined) {
        issues.push(`tierCaps is missing tier ${JSON.stringify(key)}`);
        continue;
      }
      if (!isMode(cap)) {
        issues.push(`tierCaps.${key} must be one of ${MODES.join("|")}, got ${describe(cap)}`);
        continue;
      }
      const baseline = BASELINE_TIER_CAPS[key];
      if (modeRank(cap) > modeRank(baseline)) {
        issues.push(
          `tierCaps.${key} = ${cap} is LOOSER than the FR-2 tier cap ${baseline}; tiers may only be tightened`,
        );
        continue;
      }
      tierCaps[key] = cap;
    }
  }

  // --- inLoopTimeoutsMinutes --------------------------------------------
  let inLoopTimeoutsMinutes: { tier3Forced: number; tier4: number } | undefined;
  const rawTimeouts = raw["inLoopTimeoutsMinutes"];
  if (!isPlainObject(rawTimeouts)) {
    issues.push(`inLoopTimeoutsMinutes must be an object, got ${describe(rawTimeouts)}`);
  } else {
    for (const key of Object.keys(rawTimeouts)) {
      if (key !== "tier3Forced" && key !== "tier4") {
        issues.push(`inLoopTimeoutsMinutes has unknown key ${JSON.stringify(key)}`);
      }
    }
    const tier3 = rawTimeouts["tier3Forced"];
    const tier4 = rawTimeouts["tier4"];
    for (const [label, value] of [
      ["tier3Forced", tier3],
      ["tier4", tier4],
    ] as const) {
      if (typeof value !== "number" || !Number.isInteger(value) || value <= 0 || value > 1440) {
        issues.push(`inLoopTimeoutsMinutes.${label} must be an integer between 1 and 1440, got ${describe(value)}`);
      }
    }
    if (typeof tier3 === "number" && typeof tier4 === "number" && Number.isInteger(tier3) && Number.isInteger(tier4)) {
      inLoopTimeoutsMinutes = { tier3Forced: tier3, tier4 };
    }
  }

  // --- oncallHeartbeatHours ---------------------------------------------
  const heartbeat = raw["oncallHeartbeatHours"];
  if (typeof heartbeat !== "number" || !Number.isFinite(heartbeat) || heartbeat <= 0 || heartbeat > 168) {
    issues.push(`oncallHeartbeatHours must be a number in (0, 168], got ${describe(heartbeat)}`);
  }

  // --- todo --------------------------------------------------------------
  const todo: Record<string, string> = {};
  const rawTodo = raw["todo"];
  if (!isPlainObject(rawTodo)) {
    issues.push(`todo must be an object of string values, got ${describe(rawTodo)}`);
  } else {
    for (const [key, value] of Object.entries(rawTodo)) {
      if (typeof value !== "string" || value.trim() === "") {
        issues.push(`todo.${key} must be a non-empty string, got ${describe(value)}`);
        continue;
      }
      todo[key] = value;
    }
  }

  if (issues.length > 0) {
    throw new PolicyConfigError(`invalid policy config ${source}`, issues);
  }

  return Object.freeze({
    configVersion: configVersion as string,
    paperOnly: true as const,
    harnessCaps: Object.freeze(harnessCaps as Record<HarnessId, Mode>),
    smeMaxAutonomy: Object.freeze(smeMaxAutonomy as Record<SmeId, Mode>),
    tierCaps: Object.freeze(tierCaps as Record<TierCapKey, Mode>),
    inLoopTimeoutsMinutes: Object.freeze(inLoopTimeoutsMinutes as { tier3Forced: number; tier4: number }),
    oncallHeartbeatHours: heartbeat as number,
    todo: Object.freeze(todo),
  });
}

/** Reads and validates a policy config file. Missing/garbage/unreadable files throw. */
export function loadPolicyFromFile(filePath: string): PolicyConfig {
  let text: string;
  try {
    text = readFileSync(filePath, "utf8");
  } catch (error) {
    throw new PolicyConfigError(`cannot read policy config at ${filePath}: ${errorMessage(error)}`);
  }

  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    throw new PolicyConfigError(`policy config at ${filePath} is not valid JSON: ${errorMessage(error)}`);
  }

  return parsePolicyConfig(raw, filePath);
}

/**
 * Resolves the policy config path: explicit env override first, then the config shipped next to
 * this module. Throws if neither exists - a missing config is a hard failure, never a default.
 */
export function defaultPolicyPath(env: NodeJS.ProcessEnv = process.env): string {
  const override = env["LOOPER3_POLICY_PATH"];
  if (typeof override === "string" && override.trim() !== "") {
    // An explicit path is honoured or the process stops; it never silently falls back.
    const explicit = path.resolve(override);
    if (!existsSync(explicit)) {
      throw new PolicyConfigError(`LOOPER3_POLICY_PATH points at a missing file: ${explicit}`);
    }
    return explicit;
  }

  const shipped = fileURLToPath(new URL("./looper3.policy.json", import.meta.url));
  if (existsSync(shipped)) return shipped;

  throw new PolicyConfigError(
    `no policy config found at ${shipped}; refusing to run without a versioned config`,
  );
}

export function tierCapKey(tier: Tier): TierCapKey {
  if (!isTier(tier)) throw new RangeError(`not a tier: ${JSON.stringify(tier)}`);
  return String(tier) as TierCapKey;
}

/**
 * Paper-mode guard. QS_PAPER_ONLY defaults to "1"; any other value refuses to run because live
 * mode is out of scope for this project.
 */
export function assertPaperOnly(env: NodeJS.ProcessEnv = process.env): void {
  const raw = env["QS_PAPER_ONLY"];
  if (raw === undefined || raw === "") return;
  if (raw !== "1") {
    throw new PolicyConfigError(`QS_PAPER_ONLY=${JSON.stringify(raw)} refused: this build runs in paper mode only (QS_PAPER_ONLY=1)`);
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
