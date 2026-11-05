/**
 * Gate subgraph nodes (FR-3).
 *
 * Rules baked in here:
 *  - `gateNode` is PURE and deterministic: no clock, no db, no network, no mutation. It closes over
 *    the frozen policy config and the frozen tier registry only.
 *  - `approval` and `vetoWindow` call interrupt() FIRST and have no side effect before it, because
 *    LangGraph re-runs an interrupting node from the top when it resumes.
 *  - `prepare` is the only node with repeatable side effects, keyed by sha256(thread_id +
 *    tool_call_id): one notification, one scheduler registration, no matter how often it runs.
 *  - every unreadable/unexpected input fails closed (reject), never open.
 */
import { interrupt } from "@langchain/langgraph";

import { clamp, effectiveMode, bindingCap, floorSource, strictest } from "../policy/modes.js";
import { TOOL_TIER, type ToolTierRegistry } from "../policy/toolTiers.js";
import {
  isHarnessId,
  isMode,
  isSmeId,
  type HarnessId,
  type Binding,
  type Mode,
  type SmeId,
  type Tier,
} from "../policy/types.js";
import type { PolicyConfig } from "../policy/config.js";
import { idempotencyKey } from "./keys.js";
import type { AlertOutbox, DeadlineKind, NotificationOutbox, SchedulerStub } from "./outbox.js";
import type { SideEffectLedger } from "./executor.js";
import type { DecidedBy, GateDecision, GateState, GateStateUpdate, PendingAction } from "./state.js";
import { GateInputError } from "./state.js";
import { assertAutonomySlot, autonomySlotFor } from "./router.js";

export interface GateDeps {
  readonly policy: PolicyConfig;
  readonly registry: ToolTierRegistry;
  readonly outbox: NotificationOutbox;
  readonly scheduler: SchedulerStub;
  readonly ledger: SideEffectLedger;
  /** M4 (FR-4): where timeout escalations go. Channel is PRD open question 1; stub only. */
  readonly alerts: AlertOutbox;
  /** Wired to readGlobalFloor() from the Phase 1 agent_flags store. */
  readonly readGlobalFloor: () => Mode;
  /** FR-5 on-call heartbeat; false (fail closed) when it cannot be read. */
  readonly heartbeatActive: () => boolean;
  /**
   * M4 (FR-5): has the channel confirmed delivery of this action's notification? Required, but not
   * sufficient, for an on_loop auto-approve. False on any doubt.
   */
  readonly deliveryConfirmed: (key: string | undefined) => boolean;
  /** Clock. Used by intake/prepare/approval/veto only - never by gate. */
  readonly now: () => number;
}

export interface ApprovalPrompt {
  readonly kind: "approval";
  readonly action: PendingAction | undefined;
  readonly tier: Tier;
  readonly expiresAt: number;
}

export interface VetoPrompt {
  readonly kind: "veto";
  readonly action: PendingAction | undefined;
  readonly expiresAt: number;
}

export type GateRoute = "execute" | "vetoWindow" | "approval" | "reject";
export type DecisionRoute = "execute" | "reject" | "escalate";
export type DeadlineRoute = "approval" | "vetoWindow" | "reject";

// ---------------------------------------------------------------------------------------------
// Pure gate evaluation
// ---------------------------------------------------------------------------------------------

export interface GateInput {
  readonly tool: string;
  readonly wantedMode: Mode;
  readonly harness: HarnessId | string;
  readonly sme: SmeId | string;
  readonly runFloor: Mode;
  readonly globalFloor: Mode;
  /** M4 (FR-5): on-call heartbeat at gate time. Anything but literal true counts as inactive. */
  readonly oncallActive: boolean;
}

/**
 * The whole gate decision as one pure function: tier from the static registry, floor =
 * strictest(runFloor, globalFloor), mode = five-way reduction, binding = which cap bound it.
 * Unknown tool => tier 4 / in_loop / binding "unregistered" (FR-3).
 *
 * M4 (FR-5) runs AFTER the reduction and can only tighten: an on_loop result stays on_loop only
 * if the action is reversible or has a registered compensating action, AND an on-call heartbeat
 * is active. Otherwise it becomes in_loop, with binding "reversibility" or "oncall". The
 * five-way math in effectiveMode() is unchanged.
 */
export function gateDecisionFor(input: GateInput, policy: PolicyConfig, registry: ToolTierRegistry = TOOL_TIER): GateDecision {
  // Membership must be checked against the SAME registry the tier is read from; using the
  // default registry here would mark a tool from an injected registry as unregistered.
  const tier: Tier | undefined = registeredTierIn(input.tool, registry);
  const floor = strictest(input.runFloor, input.globalFloor);
  const reduced = effectiveMode(input.wantedMode, input.harness, input.sme, tier, floor, policy);
  let mode: Mode = reduced;
  let binding: Binding = bindingCap(input.wantedMode, input.harness, input.sme, tier, floor, policy);
  let convertedFromOnLoop: GateDecision["convertedFromOnLoop"];
  if (reduced === "on_loop") {
    // Reversibility first: it is a property of the action, the heartbeat is a property of the moment.
    if (!onLoopEligible(input.tool, registry)) {
      mode = "in_loop";
      binding = "reversibility";
      convertedFromOnLoop = "irreversible";
    } else if (input.oncallActive !== true) {
      mode = "in_loop";
      binding = "oncall";
      convertedFromOnLoop = "no_oncall";
    }
  }
  const touchesMoney = tier === undefined ? false : registry[input.tool]?.touchesMoney === true;
  return {
    mode,
    tier: tier ?? 4,
    binding,
    floor,
    floorSource: floorSource(input.runFloor, input.globalFloor),
    configVersion: policy.configVersion,
    touchesMoney,
    ...(convertedFromOnLoop === undefined ? {} : { convertedFromOnLoop }),
  };
}

function registeredTierIn(tool: string, registry: ToolTierRegistry): Tier | undefined {
  if (!Object.prototype.hasOwnProperty.call(registry, tool)) return undefined;
  return registry[tool]?.tier;
}

/** Mode -> route. Exactly the PRD's routeGate. */
export function routeForMode(mode: Mode): GateRoute {
  switch (mode) {
    case "autonomous":
      return "execute";
    case "on_loop":
      return "vetoWindow";
    case "in_loop":
      return "approval";
    default:
      return "reject";
  }
}

export function routeAfterGate(state: GateState): GateRoute {
  const gate = state.gate;
  if (gate === undefined) return "reject"; // fail closed: no decision, no execution
  return routeForMode(gate.mode);
}

/**
 * After approval / veto. FR-4: a decision that came from a deadline is escalated once before it
 * goes on - always when it rejected the action, and on EVERY timeout for touchesMoney actions.
 */
export function routeAfterDecision(state: GateState): DecisionRoute {
  if (state.timedOut === true && state.escalated !== true) {
    if (state.approved !== true || state.gate?.touchesMoney === true) return "escalate";
  }
  return state.approved === true ? "execute" : "reject";
}

/** After armDeadline: only the two interrupting modes get here; anything else fails closed. */
export function routeAfterDeadline(state: GateState): DeadlineRoute {
  switch (state.gate?.mode) {
    case "in_loop":
      return "approval";
    case "on_loop":
      return "vetoWindow";
    default:
      return "reject";
  }
}

/** FR-4 approval expiry: tier 4 => tier4 minutes, any other tier forced into in_loop => tier3Forced. */
export function approvalTimeoutMs(tier: Tier, policy: PolicyConfig): number {
  const minutes = tier === 4 ? policy.inLoopTimeoutsMinutes.tier4 : policy.inLoopTimeoutsMinutes.tier3Forced;
  return minutes * 60_000;
}

// ---------------------------------------------------------------------------------------------
// Resume-payload parsing - anything malformed is a rejection, never an approval
// ---------------------------------------------------------------------------------------------

interface ParsedResume {
  readonly approved: boolean;
  readonly by: DecidedBy;
  readonly reason: string;
}

export function parseResumePayload(raw: unknown): ParsedResume | null {
  if (typeof raw !== "object" || raw === null) return null;
  const value = raw as Record<string, unknown>;
  if (typeof value["approved"] !== "boolean") return null;

  const byRaw = value["by"];
  const by: DecidedBy = byRaw === "timeout" ? "timeout" : byRaw === "policy" ? "policy" : "human";

  const reasonRaw = value["reason"];
  const reason =
    typeof reasonRaw === "string" && reasonRaw.trim() !== ""
      ? reasonRaw.trim()
      : value["approved"] === true
        ? "approved"
        : "rejected";

  return { approved: value["approved"], by, reason };
}

// ---------------------------------------------------------------------------------------------
// Nodes
// ---------------------------------------------------------------------------------------------

export interface GateNodes {
  readonly intake: (state: GateState) => GateStateUpdate;
  readonly readFloor: (state: GateState) => GateStateUpdate;
  readonly prepare: (state: GateState) => GateStateUpdate;
  readonly gate: (state: GateState) => GateStateUpdate;
  readonly armDeadline: (state: GateState) => GateStateUpdate;
  readonly escalate: (state: GateState) => GateStateUpdate;
  readonly approval: (state: GateState) => GateStateUpdate;
  readonly vetoWindow: (state: GateState) => GateStateUpdate;
  readonly execute: (state: GateState) => GateStateUpdate;
  readonly reject: (state: GateState) => GateStateUpdate;
  readonly routeAfterGate: (state: GateState) => GateRoute;
  readonly routeAfterDecision: (state: GateState) => DecisionRoute;
  readonly routeAfterDeadline: (state: GateState) => DeadlineRoute;
}

export function createGateNodes(deps: GateDeps): GateNodes {
  const { policy, registry } = deps;

  /** Validates the hand-off from the router and applies the (tighten-only) router clamp once. */
  const intake = (state: GateState): GateStateUpdate => {
    const issues: string[] = [];
    if (typeof state.threadId !== "string" || state.threadId.trim() === "") issues.push("threadId");
    if (state.pending === undefined || typeof state.pending.tool !== "string" || state.pending.tool.trim() === "") {
      issues.push("pending.tool");
    }
    if (state.pending === undefined || typeof state.pending.toolCallId !== "string" || state.pending.toolCallId.trim() === "") {
      issues.push("pending.toolCallId");
    }
    if (!isMode(state.wantedMode)) issues.push("wantedMode");
    if (!isMode(state.runFloor)) issues.push("runFloor");
    if (!isHarnessId(state.harness)) issues.push("harness");
    if (!isSmeId(state.sme)) issues.push("sme");
    if (issues.length > 0) {
      throw new GateInputError(`gate intake refused: invalid or missing ${issues.join(", ")}`);
    }

    // FR-7: the autonomy slot is never wiki-fillable and has no default for trading/betting SMEs.
    assertAutonomySlot(autonomySlotFor(state.sme), state.sme);

    // FR-7: apply the router output once, tightening only; a resume never re-routes.
    const wanted =
      state.routed === true
        ? state.wantedMode
        : clamp(state.wantedMode, state.routerWantedMode ?? state.wantedMode);

    return { wantedMode: wanted, routed: true, configVersion: policy.configVersion };
  };

  /**
   * Reads both agent_flags values the pure gate needs, so the gate itself does no I/O:
   * the global floor (failure => in_loop) and, M4, the on-call heartbeat (failure => inactive).
   */
  const readFloor = (_state: GateState): GateStateUpdate => {
    let floor: Mode = "in_loop";
    try {
      const read = deps.readGlobalFloor();
      floor = isMode(read) ? read : "in_loop";
    } catch {
      floor = "in_loop";
    }
    return { globalFloor: floor, oncallActive: safeBool(deps.heartbeatActive) };
  };

  /**
   * Repeatable side effect, keyed by sha(thread_id + tool_call_id). Runs BEFORE gate, so the
   * notification is an unconditional "gate pending" intent: no approval decision has been made yet
   * and nothing here may depend on one.
   */
  const prepare = (state: GateState): GateStateUpdate => {
    const pending = state.pending;
    if (pending === undefined) throw new GateInputError("prepare reached without a pending action");
    const key = idempotencyKey(state.threadId, pending.toolCallId);
    const nowMs = deps.now();
    const vetoExpiresAt = nowMs + policy.vetoWindowSeconds * 1000;

    deps.outbox.notifyOnce({
      key,
      threadId: state.threadId,
      tool: pending.tool,
      toolCallId: pending.toolCallId,
      kind: "gate_pending",
      createdAtMs: nowMs,
      vetoExpiresAt,
    });
    // M4: the deadline is registered by `armDeadline`, after the gate, because only then is the
    // mode (and so which deadline applies, if any) known.
    return { prepareKey: key, preparedAtMs: nowMs, vetoExpiresAt };
  };

  /** PURE. Deterministic five-way reduction; no I/O of any kind. */
  const gate = (state: GateState): GateStateUpdate => ({
    gate: gateDecisionFor(
      {
        tool: state.pending?.tool ?? "",
        wantedMode: state.wantedMode,
        harness: state.harness,
        sme: state.sme,
        runFloor: state.runFloor,
        globalFloor: state.globalFloor ?? "in_loop",
        oncallActive: state.oncallActive === true,
      },
      policy,
      registry,
    ),
  });

  /**
   * M4 (FR-4/FR-5): registers the ONE deadline that applies to the chosen mode, keyed like
   * prepare, so a replay never registers a second job. Uses no clock: the deadline is derived
   * from preparedAtMs, so a re-run computes the same value.
   */
  const armDeadline = (state: GateState): GateStateUpdate => {
    const pending = state.pending;
    const gateDecision = state.gate;
    if (pending === undefined || gateDecision === undefined || state.preparedAtMs === undefined) {
      return { approved: false, rejectReason: "deadline could not be armed (fail closed)" };
    }
    const key = state.prepareKey ?? idempotencyKey(state.threadId, pending.toolCallId);
    let kind: DeadlineKind;
    let resumeAtMs: number;
    if (gateDecision.mode === "in_loop") {
      kind = "approval_deadline";
      resumeAtMs = state.preparedAtMs + approvalTimeoutMs(gateDecision.tier, policy);
    } else if (gateDecision.mode === "on_loop") {
      kind = "veto_deadline";
      resumeAtMs = state.vetoExpiresAt ?? state.preparedAtMs + policy.vetoWindowSeconds * 1000;
    } else {
      return { approved: false, rejectReason: "deadline armed for a non-interrupting mode (fail closed)" };
    }
    deps.scheduler.scheduleOnce({ key, threadId: state.threadId, toolCallId: pending.toolCallId, resumeAtMs, kind });
    return kind === "approval_deadline" ? { approvalExpiresAt: resumeAtMs } : { vetoExpiresAt: resumeAtMs };
  };

  /** FR-4: in_loop approval. interrupt() first, no side effects before it. */
  const approval = (state: GateState): GateStateUpdate => {
    const tier: Tier = state.gate?.tier ?? 4;
    const expiresAt =
      state.approvalExpiresAt ?? (state.preparedAtMs ?? deps.now()) + approvalTimeoutMs(tier, policy);

    const raw = interrupt<ApprovalPrompt, unknown>({
      kind: "approval",
      action: state.pending,
      tier,
      expiresAt,
    });

    const decision = parseResumePayload(raw);
    if (decision === null) {
      return { approved: false, decidedBy: "policy", rejectReason: "invalid approval response (fail closed)" };
    }
    if (decision.by === "timeout") {
      // FR-4: an in_loop approval NEVER auto-approves on expiry.
      return { approved: false, decidedBy: "timeout", rejectReason: "timeout", timedOut: true };
    }
    if (deps.now() > expiresAt) {
      return { approved: false, decidedBy: "timeout", rejectReason: "timeout", timedOut: true };
    }
    if (!decision.approved) {
      return { approved: false, decidedBy: "human", rejectReason: decision.reason };
    }
    return { approved: true, decidedBy: "human" };
  };

  /** FR-5 veto window. interrupt() first; auto-approve on expiry requires an active heartbeat. */
  const vetoWindow = (state: GateState): GateStateUpdate => {
    const expiresAt =
      state.vetoExpiresAt ?? (state.preparedAtMs ?? deps.now()) + policy.vetoWindowSeconds * 1000;

    const raw = interrupt<VetoPrompt, unknown>({ kind: "veto", action: state.pending, expiresAt });

    const decision = parseResumePayload(raw);
    if (decision === null) {
      return { approved: false, decidedBy: "policy", rejectReason: "invalid veto response (fail closed)" };
    }

    if (decision.by === "timeout") {
      // A deadline that claims to have fired before the window closed is not a deadline.
      if (deps.now() < expiresAt) {
        return {
          approved: false,
          decidedBy: "policy",
          rejectReason: "timeout resume before the veto window closed (fail closed)",
        };
      }
      if (!decision.approved) {
        return { approved: false, decidedBy: "timeout", rejectReason: "timeout", timedOut: true };
      }
      // FR-5: the heartbeat is checked again at expiry - it may have lapsed during the window.
      if (!safeBool(deps.heartbeatActive)) {
        return {
          approved: false,
          decidedBy: "policy",
          rejectReason: "on_loop auto-approve refused: no active on-call heartbeat (FR-5 fail closed)",
          timedOut: true,
        };
      }
      if (!onLoopEligible(state.pending?.tool ?? "", registry)) {
        return {
          approved: false,
          decidedBy: "policy",
          rejectReason:
            "on_loop auto-approve refused: action is not reversible and has no registered compensating action (FR-5 fail closed)",
          timedOut: true,
        };
      }
      // FR-5: confirmed delivery is required but not sufficient on its own (checked last).
      if (!safeBool(() => deps.deliveryConfirmed(state.prepareKey))) {
        return {
          approved: false,
          decidedBy: "policy",
          rejectReason: "on_loop auto-approve refused: notification delivery not confirmed (FR-5 fail closed)",
          timedOut: true,
        };
      }
      return { approved: true, decidedBy: "timeout", timedOut: true };
    }

    if (deps.now() > expiresAt) {
      return { approved: false, decidedBy: "timeout", rejectReason: "veto window expired (fail closed)", timedOut: true };
    }
    if (!decision.approved) {
      return { approved: false, decidedBy: "human", rejectReason: decision.reason };
    }
    return { approved: true, decidedBy: "human" };
  };

  /** Executes at most once for tier>=3 via the idempotency key. Never runs an uncleared action. */
  const execute = (state: GateState): GateStateUpdate => {
    const pending = state.pending;
    const gateDecision = state.gate;
    if (pending === undefined) {
      return { executed: false, approved: false, rejectReason: "no pending action at execute (fail closed)" };
    }
    if (gateDecision === undefined) {
      return { executed: false, approved: false, rejectReason: "no gate decision at execute (fail closed)" };
    }
    const cleared = gateDecision.mode === "autonomous" || state.approved === true;
    if (!cleared) {
      return { executed: false, approved: false, rejectReason: "execute blocked: gate did not clear the action" };
    }

    const key = state.executeKey ?? idempotencyKey(state.threadId, pending.toolCallId);
    deps.ledger.execute({
      key,
      threadId: state.threadId,
      toolCallId: pending.toolCallId,
      tool: pending.tool,
      tier: gateDecision.tier,
      args: pending.args,
    });
    return { executed: true, executeKey: key };
  };

  /**
   * FR-4 escalation, once per action key. Runs only after a deadline-driven decision; the alert
   * channel itself is PRD open question 1, so this hands the alert to an idempotent stub outbox.
   */
  const escalate = (state: GateState): GateStateUpdate => {
    const pending = state.pending;
    if (pending !== undefined) {
      deps.alerts.escalateOnce({
        key: state.prepareKey ?? idempotencyKey(state.threadId, pending.toolCallId),
        threadId: state.threadId,
        tool: pending.tool,
        toolCallId: pending.toolCallId,
        kind: "gate_timeout",
        severity: state.gate?.touchesMoney === true ? "critical" : "high",
        approved: state.approved === true,
        reason: state.rejectReason ?? (state.approved === true ? "auto-approved on veto expiry" : "timeout"),
      });
    }
    return { escalated: true };
  };

  /** Terminal reject. Pure: records why, touches nothing else. */
  const reject = (state: GateState): GateStateUpdate => ({
    executed: false,
    approved: false,
    rejectReason: state.rejectReason ?? (state.gate === undefined ? "no gate decision (fail closed)" : "rejected (fail closed)"),
  });

  return {
    intake,
    readFloor,
    prepare,
    gate,
    armDeadline,
    approval,
    vetoWindow,
    escalate,
    execute,
    reject,
    routeAfterGate,
    routeAfterDecision,
    routeAfterDeadline,
  };
}

function safeBool(read: () => boolean): boolean {
  try {
    return read() === true;
  } catch {
    return false;
  }
}

/** FR-5 eligibility: an action may stay on_loop only if it is reversible or has a compensation. */
export function onLoopEligible(tool: string, registry: ToolTierRegistry = TOOL_TIER): boolean {
  if (!Object.prototype.hasOwnProperty.call(registry, tool)) return false;
  const entry = registry[tool];
  if (entry === undefined) return false;
  return entry.reversible === true || (typeof entry.compensatingAction === "string" && entry.compensatingAction !== "");
}
