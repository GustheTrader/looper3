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

import type { Binding, FloorSource, HarnessId, Mode, SmeId, Tier } from "../policy/types.js";

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
  /** Clock reading taken once, in `prepare`, so later nodes never need their own. */
  preparedAtMs: Annotation<number | undefined>,
  prepareKey: Annotation<string | undefined>,
  executed: Annotation<boolean | undefined>,
  executeKey: Annotation<string | undefined>,
});

export type GateState = typeof GateStateAnnotation.State;
export type GateStateUpdate = Partial<GateState>;
