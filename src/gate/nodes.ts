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
  type Mode,
  type SmeId,
  type Tier,
} from "../policy/types.js";
import type { PolicyConfig } from "../policy/config.js";
import { idempotencyKey } from "./keys.js";
import type { NotificationOutbox, SchedulerStub } from "./outbox.js";
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
  /** Wired to readGlobalFloor() from the Phase 1 agent_flags store. */
  readonly readGlobalFloor: () => Mode;
  /** FR-5 on-call heartbeat; false (fail closed) when it cannot be read. */
  readonly heartbeatActive: () => boolean;
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
export type DecisionRoute = "execute" | "reject";

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
}

/**
 * The whole gate decision as one pure function: tier from the static registry, floor =
 * strictest(runFloor, globalFloor), mode = five-way reduction, binding = which cap bound it.
 * Unknown tool => tier 4 / in_loop / binding "unregistered" (FR-3).
 */
export function gateDecisionFor(input: GateInput, policy: PolicyConfig, registry: ToolTierRegistry = TOOL_TIER): GateDecision {
  // Membership must be checked against the SAME registry the tier is read from; using the
  // default registry here would mark a tool from an injected registry as unregistered.
  const tier: Tier | undefined = registeredTierIn(input.tool, registry);
  const floor = strictest(input.runFloor, input.globalFloor);
  const mode = effectiveMode(input.wantedMode, input.harness, input.sme, tier, floor, policy);
  const binding = bindingCap(input.wantedMode, input.harness, input.sme, tier, floor, policy);
  return {
    mode,
    tier: tier ?? 4,
    binding,
    floor,
    floorSource: floorSource(input.runFloor, input.globalFloor),
    configVersion: policy.configVersion,
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

export function routeAfterDecision(state: GateState): DecisionRoute {
  return state.approved === true ? "execute" : "reject";
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
  readonly approval: (state: GateState) => GateStateUpdate;
  readonly vetoWindow: (state: GateState) => GateStateUpdate;
  readonly execute: (state: GateState) => GateStateUpdate;
  readonly reject: (state: GateState) => GateStateUpdate;
  readonly routeAfterGate: (state: GateState) => GateRoute;
  readonly routeAfterDecision: (state: GateState) => DecisionRoute;
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

  /** Reads the global floor from agent_flags (Phase 1 store). Failure => in_loop. */
  const readFloor = (_state: GateState): GateStateUpdate => {
    let floor: Mode = "in_loop";
    try {
      const read = deps.readGlobalFloor();
      floor = isMode(read) ? read : "in_loop";
    } catch {
      floor = "in_loop";
    }
    return { globalFloor: floor };
  };

  /**
   * Repeatable side effects, keyed by sha(thread_id + tool_call_id). Runs BEFORE gate, so the
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
    deps.scheduler.scheduleOnce({
      key,
      threadId: state.threadId,
      toolCallId: pending.toolCallId,
      resumeAtMs: vetoExpiresAt,
      kind: "veto_deadline",
    });

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
      },
      policy,
      registry,
    ),
  });

  /** FR-4: in_loop approval. interrupt() first, no side effects before it. */
  const approval = (state: GateState): GateStateUpdate => {
    const tier: Tier = state.gate?.tier ?? 4;
    const timeoutMinutes =
      tier === 4 ? policy.inLoopTimeoutsMinutes.tier4 : policy.inLoopTimeoutsMinutes.tier3Forced;
    const expiresAt = (state.preparedAtMs ?? deps.now()) + timeoutMinutes * 60_000;

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
      return { approved: false, decidedBy: "timeout", rejectReason: "timeout" };
    }
    if (deps.now() > expiresAt) {
      return { approved: false, decidedBy: "timeout", rejectReason: "timeout" };
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
      if (!decision.approved) {
        return { approved: false, decidedBy: "timeout", rejectReason: "timeout" };
      }
      if (!deps.heartbeatActive()) {
        return {
          approved: false,
          decidedBy: "policy",
          rejectReason: "on_loop auto-approve refused: no active on-call heartbeat (FR-5 fail closed)",
        };
      }
      if (!onLoopEligible(state.pending?.tool ?? "", registry)) {
        return {
          approved: false,
          decidedBy: "policy",
          rejectReason: "on_loop auto-approve refused: action is not reversible and has no registered compensating action (FR-5 fail closed)",
        };
      }
      return { approved: true, decidedBy: "timeout" };
    }

    if (deps.now() > expiresAt) {
      return { approved: false, decidedBy: "timeout", rejectReason: "veto window expired (fail closed)" };
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

  /** Terminal reject. Pure: records why, touches nothing else. */
  const reject = (state: GateState): GateStateUpdate => ({
    executed: false,
    approved: false,
    rejectReason: state.rejectReason ?? (state.gate === undefined ? "no gate decision (fail closed)" : "rejected (fail closed)"),
  });

  return { intake, readFloor, prepare, gate, approval, vetoWindow, execute, reject, routeAfterGate, routeAfterDecision };
}

/** FR-5 eligibility: an action may stay on_loop only if it is reversible or has a compensation. */
export function onLoopEligible(tool: string, registry: ToolTierRegistry = TOOL_TIER): boolean {
  if (!Object.prototype.hasOwnProperty.call(registry, tool)) return false;
  const entry = registry[tool];
  if (entry === undefined) return false;
  return entry.reversible === true || (typeof entry.compensatingAction === "string" && entry.compensatingAction !== "");
}
