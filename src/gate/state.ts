/**
 * Gate subgraph state (FR-3).
 *
 * Fields are exactly the PRD's thread-state additions plus the minimum the node split needs:
 *   pending, gate, runFloor, globalFloor, wantedMode, harness, sme, approved, rejectReason,
 *   vetoExpiresAt, configVersion
 * plus internal bookkeeping produced by `intake`/`prepare` (router clamp, prepare keys, prepare
 * clock) and `execute` (execution key). No field is writable by the model.
 */
import { Annotation } from "@langchain/langgraph";

import { isMode, modeRank, type Binding, type FloorSource, type HarnessId, type Mode, type SmeId, type Tier } from "../policy/types.js";

/**
 * M5: the run floor raised by triggers is a REDUCER channel. Every write - a node update, a new
 * invoke's input, a resume - is merged with strictest(), so nothing can loosen it once raised:
 * the only way back is a new run. An unknown value merges as in_loop (fail closed).
 */
export function strictestReducer(current: Mode, update: Mode | undefined): Mode {
  if (update === undefined) return current;
  const next: Mode = isMode(update) ? update : "in_loop";
  return modeRank(next) < modeRank(current) ? next : current;
}

function unionReducer(current: readonly string[], update: readonly string[] | undefined): readonly string[] {
  if (!Array.isArray(update)) return current;
  const merged = [...current];
  for (const item of update) if (typeof item === "string" && !merged.includes(item)) merged.push(item);
  return merged;
}

export interface PendingAction {
  readonly tool: string;
  readonly toolCallId: string;
  readonly args?: Readonly<Record<string, unknown>>;
}

export interface GateDecision {
  readonly mode: Mode;
  readonly tier: Tier;
  readonly binding: Binding;
  /** strictest(runFloor, globalFloor) that produced this decision. */
  readonly floor: Mode;
  readonly floorSource: FloorSource;
  readonly configVersion: string;
  /** M4: registry flag, copied here so routing after a timeout needs no registry lookup. */
  readonly touchesMoney: boolean;
  /** M4 (FR-5): set when the reduction said on_loop but the gate converted it to in_loop. */
  readonly convertedFromOnLoop?: "irreversible" | "no_oncall";
}

export type DecidedBy = "policy" | "human" | "timeout";

export class GateInputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GateInputError";
  }
}

export const GateStateAnnotation = Annotation.Root({
  threadId: Annotation<string>,
  pending: Annotation<PendingAction | undefined>,
  wantedMode: Annotation<Mode>,
  /** M3 addition: what the router proposed, before the intake clamp. */
  routerWantedMode: Annotation<Mode | undefined>,
  /** True once the router decision has been applied - a resume never re-routes (FR-7). */
  routed: Annotation<boolean | undefined>,
  runFloor: Annotation<Mode>,
  globalFloor: Annotation<Mode | undefined>,
  harness: Annotation<HarnessId>,
  sme: Annotation<SmeId>,
  configVersion: Annotation<string | undefined>,
  gate: Annotation<GateDecision | undefined>,
  approved: Annotation<boolean | undefined>,
  rejectReason: Annotation<string | undefined>,
  decidedBy: Annotation<DecidedBy | undefined>,
  vetoExpiresAt: Annotation<number | undefined>,
  /** M4 (FR-5): on-call heartbeat read by `readFloor`, before the pure gate. Missing = inactive. */
  oncallActive: Annotation<boolean | undefined>,
  /** M5 (FR-6): run floor raised by run triggers; tighten-only reducer, checkpointed. */
  runFloorRaised: Annotation<Mode, Mode | undefined>({ reducer: strictestReducer, default: () => "autonomous" }),
  /** M5: names of the run triggers that fired on this thread (append-only). */
  runFloorTriggers: Annotation<readonly string[], readonly string[] | undefined>({ reducer: unionReducer, default: () => [] }),
  /** M4 (FR-4): approval deadline, derived from preparedAtMs and the tier in `armDeadline`. */
  approvalExpiresAt: Annotation<number | undefined>,
  /** M4: true when the decision came from a deadline (or a human answer after it). */
  timedOut: Annotation<boolean | undefined>,
  /** M4 (FR-4): true once the timeout escalation was handed to the alert outbox. */
  escalated: Annotation<boolean | undefined>,
  /** M7 (FR-9): true once this action's decision is in the audit table. */
  recorded: Annotation<boolean | undefined>,
  /** M7: the audit write failed, so the action must not execute (fail closed). */
  auditFailed: Annotation<boolean | undefined>,
  /** Clock reading taken once, in `prepare`, so later nodes never need their own. */
  preparedAtMs: Annotation<number | undefined>,
  prepareKey: Annotation<string | undefined>,
  executed: Annotation<boolean | undefined>,
  executeKey: Annotation<string | undefined>,
});

export type GateState = typeof GateStateAnnotation.State;
export type GateStateUpdate = Partial<GateState>;
