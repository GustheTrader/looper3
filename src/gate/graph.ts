/**
 * The gate subgraph (FR-3, with the M4 deadline and escalation steps).
 *
 *   START -> intake -> readFloor -> runTriggers -> prepare -> gateNode -> routeGate
 *                                                       |-> execute        (autonomous)
 *                                                       |-> armDeadline    (on_loop | in_loop)
 *                                                       |-> reject         (no decision)
 *   armDeadline -> routeDeadline -> vetoWindow (on_loop) | approval (in_loop) | reject
 *   approval   -> routeDecision -> execute | escalate | reject
 *   vetoWindow -> routeDecision -> execute | escalate | reject
 *   escalate   -> routeDecision -> execute | reject
 *   (M7) every "execute" / "reject" above goes through `record` first:
 *   record -> routeRecord -> execute (cleared AND written) | reject
 *
 * `readFloor` is a separate node BEFORE `gate` so that `gate` stays pure: the only I/O in the
 * path (reading agent_flags: global floor + on-call heartbeat) happens in its own node and lands
 * in state.
 *
 * `runTriggers` (M5) reads this run's signals and raises the run floor (tighten-only reducer)
 * before the gate, for the same reason: the gate itself stays pure.
 *
 * `armDeadline` (M4) registers the one deadline the chosen mode needs. It sits after the gate
 * because the mode is not known before it, and before the interrupting nodes because those must
 * call interrupt() first with nothing before it.
 *
 * Not one awaiting node: approval and vetoWindow are separate interrupting nodes downstream of
 * routeGate, exactly as the PRD requires.
 */
import { END, START, MemorySaver, StateGraph, type BaseCheckpointSaver } from "@langchain/langgraph";

import { createGateNodes, type GateDeps } from "./nodes.js";
import { GateStateAnnotation, type GateState, type GateStateUpdate } from "./state.js";

export interface GateGraphHooks {
  /** Test/telemetry hook, wrapped OUTSIDE the node so the node bodies stay pure. */
  readonly onNode?: (node: string) => void;
}

export function createGateGraph(deps: GateDeps, hooks: GateGraphHooks = {}) {
  const nodes = createGateNodes(deps);

  const wrap = (name: string, fn: (state: GateState) => GateStateUpdate) => (state: GateState): GateStateUpdate => {
    hooks.onNode?.(name);
    return fn(state);
  };

  const builder = new StateGraph(GateStateAnnotation)
    .addNode("intake", wrap("intake", nodes.intake))
    .addNode("readFloor", wrap("readFloor", nodes.readFloor))
    .addNode("runTriggers", wrap("runTriggers", nodes.runTriggers))
    .addNode("prepare", wrap("prepare", nodes.prepare))
    .addNode("gateNode", wrap("gateNode", nodes.gate))
    .addNode("armDeadline", wrap("armDeadline", nodes.armDeadline))
    .addNode("approval", wrap("approval", nodes.approval))
    .addNode("vetoWindow", wrap("vetoWindow", nodes.vetoWindow))
    .addNode("escalate", wrap("escalate", nodes.escalate))
    .addNode("record", wrap("record", nodes.record))
    .addNode("execute", wrap("execute", nodes.execute))
    .addNode("reject", wrap("reject", nodes.reject))
    .addEdge(START, "intake")
    .addEdge("intake", "readFloor")
    .addEdge("readFloor", "runTriggers")
    .addEdge("runTriggers", "prepare")
    .addEdge("prepare", "gateNode")
    .addConditionalEdges("gateNode", nodes.routeAfterGate, {
      execute: "record",
      vetoWindow: "armDeadline",
      approval: "armDeadline",
      reject: "record",
    })
    .addConditionalEdges("armDeadline", nodes.routeAfterDeadline, {
      approval: "approval",
      vetoWindow: "vetoWindow",
      reject: "record",
    })
    .addConditionalEdges("approval", nodes.routeAfterDecision, { execute: "record", escalate: "escalate", reject: "record" })
    .addConditionalEdges("vetoWindow", nodes.routeAfterDecision, { execute: "record", escalate: "escalate", reject: "record" })
    .addConditionalEdges("escalate", nodes.routeAfterDecision, { execute: "record", escalate: "record", reject: "record" })
    .addConditionalEdges("record", nodes.routeAfterRecord, { execute: "execute", reject: "reject" })
    .addEdge("execute", END)
    .addEdge("reject", END);

  return {
    nodes,
    compile: (checkpointer: BaseCheckpointSaver = new MemorySaver()) => builder.compile({ checkpointer }),
  };
}

export type GateGraph = ReturnType<typeof createGateGraph>;
export type CompiledGateGraph = ReturnType<GateGraph["compile"]>;
