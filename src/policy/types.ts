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

/** Which cap produced the decision (FR-2/FR-3). */
export const BINDINGS = ["harness", "sme", "tier", "floor", "unregistered"] as const;
export type Binding = (typeof BINDINGS)[number];

export function isBinding(value: unknown): value is Binding {
  return typeof value === "string" && (BINDINGS as readonly string[]).includes(value);
}
