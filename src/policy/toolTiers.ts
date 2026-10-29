/**
 * Static tool-tier registry (FR-1).
 *
 * - versioned: bump TOOL_TIER_REGISTRY_VERSION whenever a tier or a risk flag changes;
 * - every entry declares tier + reversibility metadata, which M4 needs for FR-5;
 * - unknown tools are tier 4 (fail closed) - lookupTier() never throws and never returns a
 *   looser tier than 4 for anything it does not know;
 * - raiseTier() can only raise; lowering a tier requires a reviewed registry change.
 */
import { isTier, type Tier } from "./types.js";

export const TOOL_TIER_REGISTRY_VERSION = "1.0.0";

export interface ToolTierEntry {
  readonly tool: string;
  readonly tier: Tier;
  /** True when the side effect can be undone by the agent itself (FR-5). */
  readonly reversible: boolean;
  /** Registered compensating action for an irreversible action (FR-5, PRD open question 3). */
  readonly compensatingAction?: string;
  readonly touchesMoney?: boolean;
  readonly rationale?: string;
  /** Set only on the synthetic entry returned for unknown tools. */
  readonly unregistered?: boolean;
}

export type ToolTierRegistry = Readonly<Record<string, ToolTierEntry>>;

/** Unregistered tool => tier 4. */
export const UNREGISTERED_TIER: Tier = 4;

export class ToolTierRegistryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ToolTierRegistryError";
  }
}

const ENTRIES: readonly ToolTierEntry[] = [
  {
    tool: "read_wiki",
    tier: 1,
    reversible: true,
    rationale: "Read-only fetch of a compiled wiki page; no mutation, no external delivery.",
  },
  {
    tool: "query_backtest_store",
    tier: 1,
    reversible: true,
    rationale: "Read-only query over stored backtest results.",
  },
  {
    tool: "write_raw_output",
    tier: 2,
    reversible: true,
    rationale: "Internal mutable write into raw/outputs/; rate-limited and logged by the caller.",
  },
  {
    tool: "send_telegram_alert",
    tier: 3,
    reversible: false,
    compensatingAction: "send_correction_message",
    rationale:
      "External delivery that cannot be unsent. Reversibility comes only from the registered compensating action (PRD open question 3 keeps this provisional).",
  },
  {
    tool: "post_wiki_page",
    tier: 3,
    reversible: true,
    rationale: "External write to the wiki; reverted by the agent with revert_wiki_page.",
  },
  {
    tool: "read_and_delete",
    tier: 4,
    reversible: false,
    rationale:
      "TRAP tool: the name suggests a read but it deletes files. Static tier 4 - exactly the case the old name-matching classifier misclassified as tier 1 (P6).",
  },
  {
    tool: "merge_pull_request",
    tier: 4,
    reversible: false,
    rationale: "Irreversible repository mutation; the PRD acceptance case for a tier-4 build-harness action.",
  },
  {
    tool: "submit_broker_order_stub",
    tier: 4,
    reversible: false,
    touchesMoney: true,
    rationale: "STUB/fake broker order definition - no adapter exists in this project; touchesMoney escalates on every timeout (FR-4).",
  },
];

function buildRegistry(entries: readonly ToolTierEntry[]): ToolTierRegistry {
  const registry: Record<string, ToolTierEntry> = {};
  for (const entry of entries) {
    if (registry[entry.tool] !== undefined) {
      throw new ToolTierRegistryError(`duplicate registry entry for tool ${JSON.stringify(entry.tool)}`);
    }
    registry[entry.tool] = Object.freeze({ ...entry });
  }
  return Object.freeze(registry);
}

/**
 * Structural invariants. Throws ToolTierRegistryError so a bad registry fails at import time
 * rather than at gate time.
 */
export function validateRegistry(registry: ToolTierRegistry): void {
  const issues: string[] = [];
  for (const [key, entry] of Object.entries(registry)) {
    if (entry.tool !== key && entry.unregistered !== true) {
      issues.push(`registry key ${JSON.stringify(key)} does not match entry.tool ${JSON.stringify(entry.tool)}`);
    }
    if (!isTier(entry.tier)) {
      issues.push(`tool ${JSON.stringify(key)} has an invalid tier ${JSON.stringify(entry.tier)}`);
      continue;
    }
    if (typeof entry.reversible !== "boolean") {
      issues.push(`tool ${JSON.stringify(key)} must declare reversible: boolean`);
    }
    if (entry.tier === 1 && entry.reversible !== true) {
      issues.push(`tool ${JSON.stringify(key)} is tier 1 (read-only) but not reversible`);
    }
    if (entry.tier === 1 && entry.compensatingAction !== undefined) {
      issues.push(`tool ${JSON.stringify(key)} is tier 1 but declares a compensating action`);
    }
    if (entry.tier === 4 && entry.reversible !== false) {
      issues.push(`tool ${JSON.stringify(key)} is tier 4 (critical/irreversible) but marked reversible`);
    }
    if (entry.compensatingAction !== undefined && entry.compensatingAction.trim() === "") {
      issues.push(`tool ${JSON.stringify(key)} has an empty compensatingAction`);
    }
    if (entry.touchesMoney !== undefined && typeof entry.touchesMoney !== "boolean") {
      issues.push(`tool ${JSON.stringify(key)} has a non-boolean touchesMoney`);
    }
  }
  if (issues.length > 0) {
    throw new ToolTierRegistryError(`invalid tool-tier registry: ${issues.join("; ")}`);
  }
}

export const TOOL_TIER: ToolTierRegistry = buildRegistry(ENTRIES);
validateRegistry(TOOL_TIER);

export function isRegisteredTool(tool: string): boolean {
  return Object.prototype.hasOwnProperty.call(TOOL_TIER, tool);
}

/** Tier of a registered tool, or undefined when the tool is not in the registry. */
export function registeredTier(tool: string): Tier | undefined {
  return isRegisteredTool(tool) ? TOOL_TIER[tool]?.tier : undefined;
}

/**
 * Registry lookup. Unknown tools resolve to a synthetic tier-4 entry (fail closed).
 * Never throws, never returns undefined, never caches the synthetic entry into the registry.
 */
export function lookupTier(tool: string, registry: ToolTierRegistry = TOOL_TIER): ToolTierEntry {
  // hasOwnProperty, not a truthy lookup: "__proto__"/"constructor" must not resolve to
  // Object.prototype and be mistaken for a registered tool.
  const entry =
    typeof tool === "string" && Object.prototype.hasOwnProperty.call(registry, tool) ? registry[tool] : undefined;
  if (entry !== undefined) return entry;
  return Object.freeze({
    tool: typeof tool === "string" ? tool : String(tool),
    tier: UNREGISTERED_TIER,
    reversible: false,
    unregistered: true,
    rationale: "Tool not present in the tier registry: fail closed to tier 4 / in_loop.",
  });
}

/**
 * Tier raising only (FR-1: a learned classifier may only RAISE a tier).
 * Invalid inputs collapse to tier 4 - the strictest reading.
 */
export function raiseTier(current: Tier, proposed: Tier): Tier {
  if (!isTier(current) || !isTier(proposed)) return UNREGISTERED_TIER;
  return proposed > current ? proposed : current;
}
