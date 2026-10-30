/**
 * FAKE trading graph fixture.
 *
 * The real trading graph is NOT in this repository (it lives in the desk's trading repo), so this
 * module is a tiny stand-in that reproduces the CURRENT behaviour the gate has to replace:
 *
 *   legacyEffectiveMode(wanted, harness, sme)   // per-run, per PRD "Current model"
 *   routeByMode(mode) -> execute | vetoWindow | approval
 *
 * It is used only for the replay comparison (M3 exit evidence): routing the same recorded
 * pending-action fixtures through the new gate must never be LOOSER than this, and must be
 * identical for the execution harness (which is already in_loop).
 */
import type { PolicyConfig } from "../policy/config.js";
import { clamp } from "../policy/modes.js";
import { harnessCapFor, smeCapFor } from "../policy/modes.js";
import { TOOL_TIER, type ToolTierRegistry } from "../policy/toolTiers.js";
import type { HarnessId, Mode, SmeId } from "../policy/types.js";
import { gateDecisionFor, routeForMode, type GateRoute } from "../gate/nodes.js";

export type LegacyRoute = GateRoute;

/** Strictness order: execute < vetoWindow < approval < reject (reject = no execution at all). */
export const ROUTE_STRICTNESS: Readonly<Record<LegacyRoute, number>> = Object.freeze({
  execute: 0,
  vetoWindow: 1,
  approval: 2,
  reject: 3,
});

/** The trading graph's current mode router. */
export function routeByMode(mode: Mode): LegacyRoute {
  if (mode === "autonomous") return "execute";
  if (mode === "on_loop") return "vetoWindow";
  if (mode === "in_loop") return "approval";
  return "reject";
}

/** PRD "Current model": five-way reduction with NO tier and NO floor. */
export function legacyEffectiveMode(
  wanted: Mode,
  harness: HarnessId,
  sme: SmeId,
  policy: PolicyConfig,
): Mode {
  return clamp(clamp(wanted, harnessCapFor(harness, policy)), smeCapFor(sme, policy));
}

export interface ReplayFixture {
  readonly id: string;
  readonly note: string;
  readonly tool: string;
  readonly toolCallId: string;
  readonly threadId: string;
  readonly harness: HarnessId;
  readonly sme: SmeId;
  readonly wantedMode: Mode;
  readonly runFloor: Mode;
  readonly globalFloor: Mode;
  /** Recorded outcome of the legacy router for this fixture. */
  readonly legacyRoute: LegacyRoute;
  readonly legacyMode: Mode;
  /** Recorded outcome of the gate (locked so a routing change shows up as a failure). */
  readonly gateRoute: LegacyRoute;
  readonly gateMode: Mode;
}

export function legacyRouteFor(fixture: ReplayFixture, policy: PolicyConfig): LegacyRoute {
  return routeByMode(legacyEffectiveMode(fixture.wantedMode, fixture.harness, fixture.sme, policy));
}

export function gateRouteFor(
  fixture: ReplayFixture,
  policy: PolicyConfig,
  registry: ToolTierRegistry = TOOL_TIER,
): LegacyRoute {
  const decision = gateDecisionFor(
    {
      tool: fixture.tool,
      wantedMode: fixture.wantedMode,
      harness: fixture.harness,
      sme: fixture.sme,
      runFloor: fixture.runFloor,
      globalFloor: fixture.globalFloor,
    },
    policy,
    registry,
  );
  return routeForMode(decision.mode);
}

/** Never looser: approval >= vetoWindow >= execute. */
export function isAtLeastAsStrict(next: LegacyRoute, previous: LegacyRoute): boolean {
  return ROUTE_STRICTNESS[next] >= ROUTE_STRICTNESS[previous];
}

export interface FakeTradingGraph {
  readonly route: (fixture: ReplayFixture) => LegacyRoute;
  readonly modeFor: (fixture: ReplayFixture) => Mode;
}

export function createFakeTradingGraph(policy: PolicyConfig): FakeTradingGraph {
  return {
    route: (fixture) => legacyRouteFor(fixture, policy),
    modeFor: (fixture) => legacyEffectiveMode(fixture.wantedMode, fixture.harness, fixture.sme, policy),
  };
}
