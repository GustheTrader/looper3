/**
 * M5 (FR-6) automatic floor-tightening triggers. PURE: no clock, no db, no network.
 *
 * Run triggers raise one run's floor; global triggers raise the floor for every run. Both only
 * ever return targets that are stricter than autonomous, and callers apply them with
 * strictest(), so a trigger can tighten a floor and never loosen one.
 *
 * Fail-closed rules:
 *  - a signal that is ABSENT was not measured and does not trigger;
 *  - a signal that is PRESENT but malformed (NaN, negative, wrong type, inverted range) triggers
 *    `*_signals_invalid` at in_loop - a broken meter must not read as "all fine";
 *  - the kill switch always raises the global floor to in_loop, whatever the config says.
 */
import type { FloorTriggerConfig } from "../policy/config.js";
import type { Mode } from "../policy/types.js";

export type RunTrigger =
  | "router_confidence_low"
  | "run_budget_tokens"
  | "run_budget_dollars"
  | "run_budget_turns"
  | "data_stale"
  | "paper_drift"
  | "run_signals_invalid"
  | "run_signals_unreadable";

export type GlobalTrigger =
  | "daily_spend"
  | "daily_loss"
  | "kill_switch"
  | "unresolved_orders"
  | "global_signals_invalid"
  | "global_signals_unreadable";

export interface TriggerHit<T extends string = RunTrigger | GlobalTrigger> {
  readonly trigger: T;
  readonly raiseTo: Mode;
  readonly detail: string;
}

/** What a run reports about itself. Every field is optional: absent = not measured. */
export interface RunSignals {
  /** Router confidence for this run's routing decision, 0..1. */
  readonly routerConfidence?: number;
  readonly usage?: { readonly tokens?: number; readonly dollars?: number; readonly turns?: number };
  /** Age of the newest market data the run relies on. */
  readonly dataAgeSeconds?: number;
  /** Paper result vs the backtest's expected range for the same metric. */
  readonly paperDrift?: { readonly observed: number; readonly expectedLow: number; readonly expectedHigh: number };
}

/** What the desk reports across all agents for the current (UTC) day. */
export interface GlobalSignals {
  readonly dailySpendUsd?: number;
  /** Realised + marked loss for the day, as a POSITIVE number (a gain is 0). */
  readonly dailyLossUsd?: number;
  readonly killSwitchTripped?: boolean;
  /** Orders reconciliation could not resolve. */
  readonly unresolvedOrders?: number;
}

const isNonNegative = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

export function evaluateRunTriggers(signals: RunSignals | undefined, config: FloorTriggerConfig): TriggerHit<RunTrigger>[] {
  if (signals === undefined || signals === null) return [];
  const to = config.raiseTo.run;
  const hits: TriggerHit<RunTrigger>[] = [];
  const invalid: string[] = [];

  if (typeof signals !== "object") {
    return [{ trigger: "run_signals_invalid", raiseTo: "in_loop", detail: "run signals are not an object" }];
  }

  const confidence = signals.routerConfidence;
  if (confidence !== undefined) {
    if (typeof confidence !== "number" || !Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      invalid.push("routerConfidence");
    } else if (confidence < config.routerConfidenceMin) {
      hits.push({ trigger: "router_confidence_low", raiseTo: to, detail: `router confidence ${confidence} < ${config.routerConfidenceMin}` });
    }
  }

  const usage = signals.usage;
  if (usage !== undefined) {
    if (typeof usage !== "object" || usage === null) {
      invalid.push("usage");
    } else {
      const checks = [
        ["tokens", usage.tokens, config.runBudget.maxTokens, "run_budget_tokens"],
        ["dollars", usage.dollars, config.runBudget.maxDollars, "run_budget_dollars"],
        ["turns", usage.turns, config.runBudget.maxTurns, "run_budget_turns"],
      ] as const;
      for (const [label, value, limit, trigger] of checks) {
        if (value === undefined) continue;
        if (!isNonNegative(value)) invalid.push(`usage.${label}`);
        else if (value > limit) hits.push({ trigger, raiseTo: to, detail: `${label} ${value} > budget ${limit}` });
      }
    }
  }

  const age = signals.dataAgeSeconds;
  if (age !== undefined) {
    if (!isNonNegative(age)) invalid.push("dataAgeSeconds");
    else if (age > config.dataFreshnessMaxSeconds) {
      hits.push({ trigger: "data_stale", raiseTo: to, detail: `data is ${age}s old > ${config.dataFreshnessMaxSeconds}s` });
    }
  }

  const drift = signals.paperDrift;
  if (drift !== undefined) {
    const finite = (value: unknown) => typeof value === "number" && Number.isFinite(value);
    if (
      typeof drift !== "object" ||
      drift === null ||
      !finite(drift.observed) ||
      !finite(drift.expectedLow) ||
      !finite(drift.expectedHigh) ||
      drift.expectedLow > drift.expectedHigh
    ) {
      invalid.push("paperDrift");
    } else if (drift.observed < drift.expectedLow || drift.observed > drift.expectedHigh) {
      hits.push({
        trigger: "paper_drift",
        raiseTo: to,
        detail: `paper result ${drift.observed} outside backtest range [${drift.expectedLow}, ${drift.expectedHigh}]`,
      });
    }
  }

  if (invalid.length > 0) {
    hits.push({ trigger: "run_signals_invalid", raiseTo: "in_loop", detail: `malformed run signals: ${invalid.join(", ")}` });
  }
  return hits;
}

export function evaluateGlobalTriggers(
  signals: GlobalSignals | undefined,
  config: FloorTriggerConfig,
): TriggerHit<GlobalTrigger>[] {
  if (signals === undefined || signals === null) return [];
  if (typeof signals !== "object") {
    return [{ trigger: "global_signals_invalid", raiseTo: "in_loop", detail: "global signals are not an object" }];
  }
  const to = config.raiseTo.global;
  const hits: TriggerHit<GlobalTrigger>[] = [];
  const invalid: string[] = [];

  const kill = signals.killSwitchTripped;
  if (kill !== undefined) {
    if (typeof kill !== "boolean") invalid.push("killSwitchTripped");
    // FR-6: not configurable. The kill switch itself halts execution; this only sets the floor.
    else if (kill) hits.push({ trigger: "kill_switch", raiseTo: "in_loop", detail: "kill switch tripped" });
  }

  const checks = [
    ["dailySpendUsd", signals.dailySpendUsd, config.daily.maxSpendUsd, "daily_spend"],
    ["dailyLossUsd", signals.dailyLossUsd, config.daily.maxLossUsd, "daily_loss"],
    ["unresolvedOrders", signals.unresolvedOrders, config.maxUnresolvedOrders, "unresolved_orders"],
  ] as const;
  for (const [label, value, limit, trigger] of checks) {
    if (value === undefined) continue;
    if (!isNonNegative(value)) invalid.push(label);
    else if (value > limit) hits.push({ trigger, raiseTo: to, detail: `${label} ${value} > limit ${limit}` });
  }

  if (invalid.length > 0) {
    hits.push({ trigger: "global_signals_invalid", raiseTo: "in_loop", detail: `malformed global signals: ${invalid.join(", ")}` });
  }
  return hits;
}

/** UTC calendar day of a timestamp, e.g. "2026-11-04". The only day boundary supported. */
export function utcDay(nowMs: number): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}
