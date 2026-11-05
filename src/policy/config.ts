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
  /**
   * TODO PLACEHOLDER (PRD open question 2): single veto-window duration. The PRD asks for a
   * per-harness window (e.g. 120 s for business_process); per-harness is undecided, so one
   * placeholder value is used and the decision is left to the owner.
   */
  readonly vetoWindowSeconds: number;
  /**
   * M5 (FR-6) automatic floor-tightening thresholds. EVERY number here is a TODO PLACEHOLDER
   * (PRD open question 6 and FR-6 leave them undecided); see `todo.M5_floorTriggerThresholds`.
   */
  readonly floorTriggers: FloorTriggerConfig;
  /** PRD open questions, kept as named TODO values (never decided by code). */
  readonly todo: Readonly<Record<string, string>>;
}

export interface FloorTriggerConfig {
  /** Run floor rises when router confidence is BELOW this (0..1). */
  readonly routerConfidenceMin: number;
  /** Run floor rises when a run uses MORE than any of these. */
  readonly runBudget: Readonly<{ maxTokens: number; maxDollars: number; maxTurns: number }>;
  /** Run floor rises when the run's market data is OLDER than this. */
  readonly dataFreshnessMaxSeconds: number;
  /** Global floor rises when the day's totals across all agents are MORE than these. */
  readonly daily: Readonly<{ maxSpendUsd: number; maxLossUsd: number; dayBoundary: "UTC" }>;
  /** Global floor rises when MORE than this many orders are unresolved after reconciliation. */
  readonly maxUnresolvedOrders: number;
  /**
   * What each scope's floor is raised TO. Must be stricter than autonomous. The kill switch is
   * not configurable: it always raises the global floor to in_loop (FR-6).
   */
  readonly raiseTo: Readonly<{ run: Mode; global: Mode }>;
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
  "vetoWindowSeconds",
  "floorTriggers",
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

  // --- vetoWindowSeconds (TODO placeholder for PRD OQ2) -------------------
  const vetoWindowSeconds = raw["vetoWindowSeconds"];
  if (
    typeof vetoWindowSeconds !== "number" ||
    !Number.isInteger(vetoWindowSeconds) ||
    vetoWindowSeconds <= 0 ||
    vetoWindowSeconds > 3600
  ) {
    issues.push(`vetoWindowSeconds must be an integer between 1 and 3600, got ${describe(vetoWindowSeconds)}`);
  }

  const floorTriggers = parseFloorTriggers(raw["floorTriggers"], issues);

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
    vetoWindowSeconds: vetoWindowSeconds as number,
    floorTriggers: floorTriggers as FloorTriggerConfig,
    todo: Object.freeze(todo),
  });
}

function parseFloorTriggers(rawValue: unknown, issues: string[]): FloorTriggerConfig | undefined {
  if (!isPlainObject(rawValue)) {
    issues.push(`floorTriggers must be an object, got ${describe(rawValue)}`);
    return undefined;
  }
  const before = issues.length;
  const known = ["routerConfidenceMin", "runBudget", "dataFreshnessMaxSeconds", "daily", "maxUnresolvedOrders", "raiseTo"];
  for (const key of Object.keys(rawValue)) {
    if (!known.includes(key)) issues.push(`floorTriggers has unknown key ${JSON.stringify(key)}`);
  }

  const number = (value: unknown, label: string, opts: { min: number; max: number; integer?: boolean; minExclusive?: boolean }) => {
    const ok =
      typeof value === "number" &&
      Number.isFinite(value) &&
      (opts.integer !== true || Number.isInteger(value)) &&
      (opts.minExclusive === true ? value > opts.min : value >= opts.min) &&
      value <= opts.max;
    if (!ok) {
      issues.push(`floorTriggers.${label} must be a ${opts.integer === true ? "whole " : ""}number in ${opts.minExclusive === true ? "(" : "["}${opts.min}, ${opts.max}], got ${describe(value)}`);
    }
    return value as number;
  };
  const object = (value: unknown, label: string, keys: readonly string[]): Record<string, unknown> => {
    if (!isPlainObject(value)) {
      issues.push(`floorTriggers.${label} must be an object, got ${describe(value)}`);
      return {};
    }
    for (const key of Object.keys(value)) {
      if (!keys.includes(key)) issues.push(`floorTriggers.${label} has unknown key ${JSON.stringify(key)}`);
    }
    return value;
  };

  const routerConfidenceMin = number(rawValue["routerConfidenceMin"], "routerConfidenceMin", { min: 0, max: 1 });
  const budget = object(rawValue["runBudget"], "runBudget", ["maxTokens", "maxDollars", "maxTurns"]);
  const maxTokens = number(budget["maxTokens"], "runBudget.maxTokens", { min: 0, max: 1e12, integer: true, minExclusive: true });
  const maxDollars = number(budget["maxDollars"], "runBudget.maxDollars", { min: 0, max: 1e7, minExclusive: true });
  const maxTurns = number(budget["maxTurns"], "runBudget.maxTurns", { min: 0, max: 1e6, integer: true, minExclusive: true });
  const dataFreshnessMaxSeconds = number(rawValue["dataFreshnessMaxSeconds"], "dataFreshnessMaxSeconds", {
    min: 0,
    max: 86_400,
    integer: true,
    minExclusive: true,
  });
  const daily = object(rawValue["daily"], "daily", ["maxSpendUsd", "maxLossUsd", "dayBoundary"]);
  const maxSpendUsd = number(daily["maxSpendUsd"], "daily.maxSpendUsd", { min: 0, max: 1e9, minExclusive: true });
  const maxLossUsd = number(daily["maxLossUsd"], "daily.maxLossUsd", { min: 0, max: 1e9, minExclusive: true });
  if (daily["dayBoundary"] !== "UTC") {
    issues.push(`floorTriggers.daily.dayBoundary must be "UTC" (the only supported boundary), got ${describe(daily["dayBoundary"])}`);
  }
  const maxUnresolvedOrders = number(rawValue["maxUnresolvedOrders"], "maxUnresolvedOrders", { min: 0, max: 1e6, integer: true });
  const raiseTo = object(rawValue["raiseTo"], "raiseTo", ["run", "global"]);
  for (const scope of ["run", "global"] as const) {
    const target = raiseTo[scope];
    if (!isMode(target) || target === "autonomous") {
      issues.push(`floorTriggers.raiseTo.${scope} must be in_loop or on_loop (a raise to autonomous tightens nothing), got ${describe(target)}`);
    }
  }

  if (issues.length > before) return undefined;
  return Object.freeze({
    routerConfidenceMin,
    runBudget: Object.freeze({ maxTokens, maxDollars, maxTurns }),
    dataFreshnessMaxSeconds,
    daily: Object.freeze({ maxSpendUsd, maxLossUsd, dayBoundary: "UTC" as const }),
    maxUnresolvedOrders,
    raiseTo: Object.freeze({ run: raiseTo["run"] as Mode, global: raiseTo["global"] as Mode }),
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
