/**
 * The gate subgraph (FR-3).
 *
 *   START -> intake -> readFloor -> prepare -> gateNode -> routeGate
 *                                                       |-> execute        (autonomous)
 *                                                       |-> vetoWindow     (on_loop)
 *                                                       |-> approval       (in_loop)
 *                                                       |-> reject         (no decision)
 *   approval   -> routeDecision -> execute | reject
 *   vetoWindow -> routeDecision -> execute | reject
 *
 * `readFloor` is a separate node BEFORE `gate` so that `gate` stays pure: the only I/O in the
 * path (reading agent_flags) happens in its own node and lands in state.
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
    .addNode("prepare", wrap("prepare", nodes.prepare))
    .addNode("gateNode", wrap("gateNode", nodes.gate))
    .addNode("approval", wrap("approval", nodes.approval))
    .addNode("vetoWindow", wrap("vetoWindow", nodes.vetoWindow))
    .addNode("execute", wrap("execute", nodes.execute))
    .addNode("reject", wrap("reject", nodes.reject))
    .addEdge(START, "intake")
    .addEdge("intake", "readFloor")
    .addEdge("readFloor", "prepare")
    .addEdge("prepare", "gateNode")
    .addConditionalEdges("gateNode", nodes.routeAfterGate, {
      execute: "execute",
      vetoWindow: "vetoWindow",
      approval: "approval",
      reject: "reject",
    })
    .addConditionalEdges("approval", nodes.routeAfterDecision, { execute: "execute", reject: "reject" })
    .addConditionalEdges("vetoWindow", nodes.routeAfterDecision, { execute: "execute", reject: "reject" })
    .addEdge("execute", END)
    .addEdge("reject", END);

  return {
    nodes,
    compile: (checkpointer: BaseCheckpointSaver = new MemorySaver()) => builder.compile({ checkpointer }),
  };
}

export type GateGraph = ReturnType<typeof createGateGraph>;
