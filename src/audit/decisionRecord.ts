/**
 * M7 (FR-9): one gate decision as an audit record, plus the LangSmith-shaped event for it.
 *
 * Pure: the record is derived from gate state and an explicit clock reading.
 */
import type { Binding, FloorSource, HarnessId, Mode, SmeId, Tier } from "../policy/types.js";
import type { DecidedBy, GateState } from "../gate/state.js";
import { idempotencyKey } from "../gate/keys.js";

/** PRD gate_decisions.decision check constraint. */
export const DECISIONS = ["approved", "rejected", "vetoed", "timeout"] as const;
export type Decision = (typeof DECISIONS)[number];

export interface GateDecisionRecord {
  /** sha256(thread_id + tool_call_id) - primary key, so a replay never writes a second row. */
  readonly idemKey: string;
  readonly threadId: string;
  readonly tool: string;
  readonly tier: Tier;
  readonly effectiveMode: Mode;
  readonly binding: Binding;
  readonly floorSource: FloorSource;
  readonly harness: HarnessId;
  readonly smeId: SmeId;
  readonly configVersion: string;
  readonly decision: Decision;
  readonly decidedBy: DecidedBy;
  /** From the gate-pending notification (`prepare`) to the decision being recorded. */
  readonly latencyMs: number;
  readonly rejectReason: string | null;
  readonly createdAtMs: number;
}

/**
 * Maps the outcome to the PRD's four decision values:
 *   approved - executes (autonomous, a person approved, or a veto window auto-approved)
 *   timeout  - a deadline ended it (in_loop expiry, or a refused auto-approve at veto expiry)
 *   vetoed   - a person said no inside a veto window
 *   rejected - everything else that does not execute (a person said no to an approval,
 *              invalid response, fail-closed refusals)
 */
export function decisionFor(state: Partial<GateState>): Decision {
  const cleared = state.gate?.mode === "autonomous" || state.approved === true;
  if (cleared) return "approved";
  if (state.timedOut === true) return "timeout";
  if (state.gate?.mode === "on_loop" && state.decidedBy === "human") return "vetoed";
  return "rejected";
}

export function decisionRecordFor(state: Partial<GateState>, nowMs: number): GateDecisionRecord {
  const gate = state.gate;
  const threadId = state.threadId ?? "";
  const toolCallId = state.pending?.toolCallId ?? "";
  const decision = decisionFor(state);
  const decidedBy: DecidedBy = gate?.mode === "autonomous" ? "policy" : (state.decidedBy ?? "policy");
  return {
    idemKey: state.prepareKey ?? idempotencyKey(threadId, toolCallId),
    threadId,
    tool: state.pending?.tool ?? "",
    tier: gate?.tier ?? 4,
    effectiveMode: gate?.mode ?? "in_loop",
    binding: gate?.binding ?? "unregistered",
    floorSource: gate?.floorSource ?? "both",
    harness: state.harness as HarnessId,
    smeId: state.sme as SmeId,
    configVersion: gate?.configVersion ?? state.configVersion ?? "unknown",
    decision,
    decidedBy,
    latencyMs: Math.max(0, nowMs - (state.preparedAtMs ?? nowMs)),
    rejectReason: decision === "approved" ? null : (state.rejectReason ?? null),
    createdAtMs: nowMs,
  };
}

export interface LangSmithDecisionEvent {
  readonly name: "looper3.gate_decision";
  /** FR-9 tags. `key:value`, so LangSmith filters can group by any of them. */
  readonly tags: readonly string[];
  readonly metadata: Readonly<Record<string, string | number>>;
}

export function toLangSmithDecisionEvent(record: GateDecisionRecord): LangSmithDecisionEvent {
  return {
    name: "looper3.gate_decision",
    tags: [
      `effectiveMode:${record.effectiveMode}`,
      `tier:${record.tier}`,
      `binding:${record.binding}`,
      `harness:${record.harness}`,
      `smeId:${record.smeId}`,
      `configVersion:${record.configVersion}`,
      `decision:${record.decision}`,
      `decidedBy:${record.decidedBy}`,
    ],
    metadata: {
      idemKey: record.idemKey,
      threadId: record.threadId,
      tool: record.tool,
      floorSource: record.floorSource,
      latencyMs: record.latencyMs,
      ...(record.rejectReason === null ? {} : { rejectReason: record.rejectReason }),
    },
  };
}

export interface DecisionTelemetrySink {
  emitOnce(key: string, event: LangSmithDecisionEvent): { sent: boolean };
}

export class InMemoryDecisionTelemetry implements DecisionTelemetrySink {
  private readonly byKey = new Map<string, LangSmithDecisionEvent>();

  emitOnce(key: string, event: LangSmithDecisionEvent): { sent: boolean } {
    if (this.byKey.has(key)) return { sent: false };
    this.byKey.set(key, event);
    return { sent: true };
  }

  get events(): readonly LangSmithDecisionEvent[] {
    return [...this.byKey.values()];
  }
}

export const LANGSMITH_DECISION_STUB_STATUS =
  "STUB: LangSmith decision tags are not sent (no client, no API key, no network). Use InMemoryDecisionTelemetry.";

export class LangSmithDecisionTelemetryStub implements DecisionTelemetrySink {
  constructor() {
    throw new Error(LANGSMITH_DECISION_STUB_STATUS);
  }

  emitOnce(_key: string, _event: LangSmithDecisionEvent): { sent: boolean } {
    throw new Error(LANGSMITH_DECISION_STUB_STATUS);
  }
}
