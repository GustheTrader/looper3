/**
 * Autonomy modes and the five-way effective-mode reduction (FR-2).
 *
 * Everything in this module is pure and deterministic: no I/O, no clock, no randomness. The only
 * ambient input is the active policy config, which can always be injected explicitly as the
 * optional trailing `policy` argument (used by tests and by the future gate node).
 */
import { activePolicy } from "./activePolicy.js";
import type { PolicyConfig, TierCapKey } from "./config.js";
import {
  MODES,
  MODE_ORDER,
  STRICTEST_MODE,
  isMode,
  isTier,
  modeRank,
  type Binding,
  type HarnessId,
  type Mode,
  type SmeId,
  type Tier,
} from "./types.js";

export { MODES, MODE_ORDER, STRICTEST_MODE, isMode, modeRank };
export type { Mode };

export type SmeCaps = { readonly maxAutonomy: Mode };

/** An SME can be referenced by id (looked up in the config) or passed as a resolved cap object. */
export type SmeRef = SmeId | string | SmeCaps;

function isSmeCaps(value: unknown): value is SmeCaps {
  return typeof value === "object" && value !== null && "maxAutonomy" in (value as Record<string, unknown>);
}

/**
 * Rank of any value, with anything unrecognised treated as the strictest mode.
 * NOTE the direction: 0 == in_loop (strictest), 2 == autonomous (loosest). Strictness is the
 * MINIMUM rank, so every reduction below uses Math.min. Using Math.max here would fail OPEN.
 */
function rankOf(value: unknown): number {
  return isMode(value) ? modeRank(value) : 0;
}

function atRank(rank: number): Mode {
  return MODE_ORDER[rank] ?? STRICTEST_MODE;
}

/**
 * clamp(wanted, cap) -> the stricter of the two (minimum rank).
 * Anything that is not a known mode is normalised to in_loop, so garbage tightens, never frees.
 */
export function clamp(wanted: Mode, cap: Mode): Mode {
  return atRank(Math.min(rankOf(wanted), rankOf(cap)));
}

/**
 * strictest(...) -> strictest of all inputs; in_loop for no inputs or any unrecognised input.
 */
export function strictest(...modes: readonly unknown[]): Mode {
  if (modes.length === 0) return STRICTEST_MODE;
  let rank = MODE_ORDER.length - 1;
  for (const mode of modes) rank = Math.min(rank, rankOf(mode));
  return atRank(rank);
}

export function harnessCapFor(harness: HarnessId | string, policy: PolicyConfig = activePolicy()): Mode {
  if (typeof harness !== "string") return STRICTEST_MODE;
  const cap = policy.harnessCaps[harness as HarnessId];
  return isMode(cap) ? cap : STRICTEST_MODE;
}

export function smeCapFor(sme: SmeRef, policy: PolicyConfig = activePolicy()): Mode {
  if (isSmeCaps(sme)) return isMode(sme.maxAutonomy) ? sme.maxAutonomy : STRICTEST_MODE;
  if (typeof sme !== "string") return STRICTEST_MODE;
  const cap = policy.smeMaxAutonomy[sme as SmeId];
  return isMode(cap) ? cap : STRICTEST_MODE;
}

/**
 * Tier cap. `undefined`/invalid means the tool is unregistered, which is tier 4 -> in_loop.
 */
export function tierCapFor(tier: Tier | null | undefined, policy: PolicyConfig = activePolicy()): Mode {
  if (!isTier(tier)) return STRICTEST_MODE;
  const cap = policy.tierCaps[String(tier) as TierCapKey];
  return isMode(cap) ? cap : STRICTEST_MODE;
}

/**
 * FR-2: effectiveMode = strictest(wanted, harnessCap, smeCap, tierCap, floor).
 *
 * `floor` must already be strictest(runFloor, globalFloor); passing a looser floor can only
 * tighten the result, never loosen it.
 */
export function effectiveMode(
  wanted: Mode,
  harness: HarnessId | string,
  sme: SmeRef,
  tier: Tier | null | undefined,
  floor: Mode,
  policy: PolicyConfig = activePolicy(),
): Mode {
  const caps: readonly Mode[] = [
    harnessCapFor(harness, policy),
    smeCapFor(sme, policy),
    tierCapFor(tier, policy),
    isMode(floor) ? floor : STRICTEST_MODE,
  ];
  return caps.reduce<Mode>((mode, cap) => clamp(mode, cap), isMode(wanted) ? wanted : STRICTEST_MODE);
}

/**
 * Deterministic tie-break order for `binding`. Lower index wins when several caps sit at the
 * effective rank: floor (runtime safety) > tier (intrinsic risk) > sme (domain policy) > harness
 * (broadest cap). Documented in PHASE_1_REPORT.md.
 */
export const BINDING_PRECEDENCE = ["floor", "tier", "sme", "harness"] as const satisfies readonly Binding[];

/**
 * Which cap produced the decision (FR-2/FR-3). Returns "unregistered" whenever the tier is not a
 * valid 1-4 value, matching the gate's unregistered-tool path.
 *
 * If `wanted` alone is stricter than every cap there is no cap to blame; the strictest cap is
 * reported so the field is always populated (PRD ambiguity, listed in PHASE_1_REPORT.md).
 */
export function bindingCap(
  wanted: Mode,
  harness: HarnessId | string,
  sme: SmeRef,
  tier: Tier | null | undefined,
  floor: Mode,
  policy: PolicyConfig = activePolicy(),
): Binding {
  if (!isTier(tier)) return "unregistered";

  const mode = effectiveMode(wanted, harness, sme, tier, floor, policy);
  const rank = rankOf(mode);

  const caps: Record<Exclude<Binding, "unregistered">, Mode> = {
    floor: isMode(floor) ? floor : STRICTEST_MODE,
    tier: tierCapFor(tier, policy),
    sme: smeCapFor(sme, policy),
    harness: harnessCapFor(harness, policy),
  };

  for (const binding of BINDING_PRECEDENCE) {
    if (rankOf(caps[binding]) === rank) return binding;
  }

  // No cap sits at the effective rank: `wanted` was stricter than all of them.
  let strictestCap: Exclude<Binding, "unregistered"> = BINDING_PRECEDENCE[0];
  for (const binding of BINDING_PRECEDENCE) {
    if (rankOf(caps[binding]) < rankOf(caps[strictestCap])) strictestCap = binding;
  }
  return strictestCap;
}

/** Convenience: the four cap values plus the resulting mode, for telemetry (FR-9). */
export function gateCaps(
  harness: HarnessId | string,
  sme: SmeRef,
  tier: Tier | null | undefined,
  floor: Mode,
  policy: PolicyConfig = activePolicy(),
): { harness: Mode; sme: Mode; tier: Mode; floor: Mode } {
  return {
    harness: harnessCapFor(harness, policy),
    sme: smeCapFor(sme, policy),
    tier: tierCapFor(tier, policy),
    floor: isMode(floor) ? floor : STRICTEST_MODE,
  };
}
