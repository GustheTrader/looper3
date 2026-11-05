/**
 * M5 (FR-6) global floor monitor.
 *
 * Evaluates the desk-wide signals (daily spend and loss across agents, kill switch,
 * reconciliation) and raises the global floor in agent_flags for every run. Meant to be called
 * on a timer by the desk; here it is a plain function with an explicit clock.
 *
 *  - Raises go through raiseGlobalFloorFor(): only stricter, recorded in floor_raises.
 *  - One alert per trigger per UTC day, whatever the call frequency.
 *  - If the signals cannot be read, the floor goes to in_loop (`global_signals_unreadable`).
 *  - If agent_flags cannot be read, the floor already counts as in_loop and nothing is written,
 *    but the alert is still sent.
 *  - This never lowers the floor. Lowering stays lowerGlobalFloor(): a logged operator action.
 */
import { raiseGlobalFloorFor, tryReadGlobalFloor } from "../flags/agentFlags.js";
import type { PolicyConfig } from "../policy/config.js";
import type { Mode } from "../policy/types.js";
import type { FloorAlertSink } from "../telemetry/floorAlerts.js";
import { evaluateGlobalTriggers, utcDay, type GlobalSignals, type GlobalTrigger, type TriggerHit } from "./triggers.js";

export interface GlobalMonitorDeps {
  readonly dbPath: string;
  readonly policy: PolicyConfig;
  readonly alerts: FloorAlertSink;
  readonly readSignals: () => GlobalSignals | undefined;
  readonly now: () => number;
}

export interface GlobalMonitorResult {
  readonly hits: readonly TriggerHit<GlobalTrigger>[];
  readonly floor: Mode;
  readonly changed: boolean;
}

export function applyGlobalTriggers(deps: GlobalMonitorDeps): GlobalMonitorResult {
  let hits: TriggerHit<GlobalTrigger>[];
  try {
    hits = evaluateGlobalTriggers(deps.readSignals(), deps.policy.floorTriggers);
  } catch (error) {
    hits = [
      {
        trigger: "global_signals_unreadable",
        raiseTo: "in_loop",
        detail: `cannot read global signals: ${error instanceof Error ? error.message : String(error)}`,
      },
    ];
  }

  let changed = false;
  const raiseErrors: string[] = [];
  for (const hit of hits) {
    try {
      const result = raiseGlobalFloorFor(deps.dbPath, hit.raiseTo, { trigger: hit.trigger, detail: hit.detail });
      changed = changed || result.changed;
    } catch (error) {
      raiseErrors.push(`${hit.trigger}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  const floor = tryReadGlobalFloor(deps.dbPath).floor;
  const day = utcDay(deps.now());
  for (const hit of hits) {
    deps.alerts.emitOnce({
      key: `global:${day}:${hit.trigger}`,
      scope: "global",
      trigger: hit.trigger,
      raiseTo: hit.raiseTo,
      floorAfter: floor,
      detail: hit.detail,
      configVersion: deps.policy.configVersion,
    });
  }
  // Alerts first, then fail loudly: a raise that could not be written must not pass silently.
  if (raiseErrors.length > 0) {
    throw new Error(`global floor raise failed (alerts sent): ${raiseErrors.join("; ")}`);
  }
  return { hits, floor, changed };
}
