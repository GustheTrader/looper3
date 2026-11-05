/**
 * Leaf module: primitive identifiers and mode algebra shared by every policy module.
 *
 * It deliberately imports nothing so the policy import graph stays acyclic:
 *   types <- config <- activePolicy <- modes
 *   types <- toolTiers
 * `modes.ts` re-exports Mode/MODES/MODE_ORDER/isMode/modeRank so callers can keep importing
 * from the module the build brief names.
 */

/** Autonomy modes, ordered strictest-first. Index 0 is the strictest. */
export const MODES = ["in_loop", "on_loop", "autonomous"] as const;
export type Mode = (typeof MODES)[number];

/** Canonical order. Index 0 == strictest. Never reorder: rank is compared numerically. */
export const MODE_ORDER: readonly Mode[] = MODES;

export const STRICTEST_MODE: Mode = "in_loop";
export const LOOSEST_MODE: Mode = "autonomous";

export function isMode(value: unknown): value is Mode {
  return typeof value === "string" && (MODES as readonly string[]).includes(value);
}

/** Rank of a mode: 0 (in_loop, strictest) .. 2 (autonomous, loosest). Throws on non-modes. */
export function modeRank(mode: Mode): number {
  const rank = MODE_ORDER.indexOf(mode);
  if (rank < 0) throw new RangeError(`not a mode: ${JSON.stringify(mode)}`);
  return rank;
}

/** Risk tiers, 1 (read-only) .. 4 (critical/irreversible). */
export const TIERS = [1, 2, 3, 4] as const;
export type Tier = (typeof TIERS)[number];

export function isTier(value: unknown): value is Tier {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 4;
}

/** The six Looper3 harness types. */
export const HARNESS_IDS = [
  "research",
  "analysis",
  "monitoring",
  "business_process",
  "build",
  "execution",
] as const;
export type HarnessId = (typeof HARNESS_IDS)[number];

export function isHarnessId(value: unknown): value is HarnessId {
  return typeof value === "string" && (HARNESS_IDS as readonly string[]).includes(value);
}

/** SME ids that carry an autonomy cap. Values are placeholders - see policy config `todo`. */
export const SME_IDS = ["crypto", "nfl", "research", "coding"] as const;
export type SmeId = (typeof SME_IDS)[number];

export function isSmeId(value: unknown): value is SmeId {
  return typeof value === "string" && (SME_IDS as readonly string[]).includes(value);
}

/**
 * Which cap produced the decision (FR-2/FR-3).
 *
 * "wanted" is an M3 addition: FR-3's original union (harness|sme|tier|floor|unregistered) has no
 * label for "no cap bound, the caller asked for the strictest mode". Reporting a cap in that case
 * mis-attributes the decision in telemetry, so "wanted" is added and the mode math is unchanged.
 */
/**
 * "reversibility" and "oncall" are M4 additions (FR-5): the five-way reduction produced on_loop,
 * but the action was converted to in_loop because it is irreversible with no compensating action
 * ("reversibility") or because no on-call heartbeat was active at gate time ("oncall").
 */
export const BINDINGS = ["harness", "sme", "tier", "floor", "wanted", "unregistered", "reversibility", "oncall"] as const;
export type Binding = (typeof BINDINGS)[number];

/** Which of the two floors bound the decision when binding === "floor". */
export const FLOOR_SOURCES = ["run", "global", "both"] as const;
export type FloorSource = (typeof FLOOR_SOURCES)[number];

export function isFloorSource(value: unknown): value is FloorSource {
  return typeof value === "string" && (FLOOR_SOURCES as readonly string[]).includes(value);
}

export function isBinding(value: unknown): value is Binding {
  return typeof value === "string" && (BINDINGS as readonly string[]).includes(value);
}
