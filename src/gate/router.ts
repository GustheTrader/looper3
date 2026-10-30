/**
 * FR-7 (partial, as scoped for M3).
 *
 * - the router's (harness, sme, wantedMode) land in state;
 * - it can only LOWER wantedMode - `applyRouterDecision` clamps, never loosens;
 * - it never re-routes on resume: `intake` applies it once and sets `routed`;
 * - context intake exposes an `autonomy_mode` slot that is not wiki-fillable and has no default
 *   for the trading and betting SMEs.
 */
import { clamp } from "../policy/modes.js";
import { isMode, isSmeId, type HarnessId, type Mode, type SmeId } from "../policy/types.js";

/** SMEs that put money at risk: crypto and nfl (the desk's trading and betting SMEs). */
export const TRADING_AND_BETTING_SMES: readonly SmeId[] = ["crypto", "nfl"];

export interface RouterOutput {
  readonly harness: HarnessId;
  readonly sme: SmeId;
  readonly wantedMode: Mode;
}

export interface RoutedIntake {
  readonly harness: HarnessId;
  readonly sme: SmeId;
  readonly wantedMode: Mode;
}

export interface RouterState {
  readonly harness: HarnessId;
  readonly sme: SmeId;
  readonly wantedMode: Mode;
  readonly routed?: boolean | undefined;
  readonly routerWantedMode?: Mode | undefined;
}

/**
 * Applies the router decision to the current state. The only legal effect on wantedMode is
 * tightening: `clamp(current, proposal)`. On an already-routed state nothing changes at all.
 */
export function applyRouterDecision(state: RouterState, router: RouterOutput): RoutedIntake {
  if (state.routed === true) {
    return { harness: state.harness, sme: state.sme, wantedMode: state.wantedMode };
  }
  if (!isMode(router.wantedMode)) {
    // Unreadable router output: fall back to the current mode and let the caps do the work.
    return { harness: state.harness, sme: state.sme, wantedMode: state.wantedMode };
  }
  return {
    harness: router.harness,
    sme: router.sme,
    wantedMode: clamp(state.wantedMode, router.wantedMode),
  };
}

export interface AutonomySlot {
  readonly name: "autonomy_mode";
  readonly wikiFillable: false;
  readonly allowDefault: boolean;
  readonly sme: SmeId;
}

/**
 * Context-intake autonomy slot. Never wiki-fillable; `allowDefault` is false for the trading and
 * betting SMEs, so an absent value must be supplied explicitly rather than defaulted.
 */
export function autonomySlotFor(sme: SmeId): AutonomySlot {
  if (!isSmeId(sme)) {
    throw new Error(`autonomySlotFor: unknown SME ${JSON.stringify(sme)}`);
  }
  return {
    name: "autonomy_mode",
    wikiFillable: false,
    allowDefault: !TRADING_AND_BETTING_SMES.includes(sme),
    sme,
  };
}

/** Fail-closed validator for a slot that arrives from outside (e.g. a wiki or a caller). */
export function assertAutonomySlot(slot: AutonomySlot, sme: SmeId): void {
  if (slot.wikiFillable !== false) {
    throw new Error("autonomy_mode must never be wiki-fillable (FR-7)");
  }
  if (TRADING_AND_BETTING_SMES.includes(sme) && slot.allowDefault !== false) {
    throw new Error(`autonomy_mode must not have a default for the ${sme} SME (FR-7)`);
  }
  if (slot.sme !== sme) {
    throw new Error(`autonomy_mode slot was built for ${slot.sme}, not ${sme}`);
  }
}

