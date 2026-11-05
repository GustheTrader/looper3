/**
 * M6: the one way a harness runs a tool.
 *
 * Every harness (research, analysis, monitoring, business_process, build, execution) gets a
 * runner bound to its own harness id and SME when it is created. A call cannot name a different
 * harness, so it cannot borrow a looser cap. Every call goes through the gate subgraph. The
 * runner never executes anything itself: the only execution path is the gate's `execute` node,
 * and `npm run check:gating` fails the build if any other code calls the executor.
 *
 * Fail closed:
 *  - a tool the harness manifest does not list is rejected before the gate runs;
 *  - an invalid hand-off (GateInputError from intake) is rejected, never retried looser;
 *  - an outcome the runner does not recognise is reported as rejected.
 */
import { Command } from "@langchain/langgraph";

import { isHarnessId, isSmeId, type HarnessId, type Mode, type SmeId } from "../policy/types.js";
import type { CompiledGateGraph } from "../gate/graph.js";
import { GateInputError, type GateDecision, type GateState } from "../gate/state.js";
import { HARNESS_MANIFEST, harnessAllows, type HarnessManifest } from "./manifest.js";

export interface ToolCall {
  readonly threadId: string;
  readonly toolCallId: string;
  readonly tool: string;
  readonly args?: Readonly<Record<string, unknown>>;
  /** What the agent asks for. Defaults to autonomous: the caps decide, and they only tighten. */
  readonly wantedMode?: Mode;
  /** The router's proposal (FR-7: may only lower wantedMode). */
  readonly routerWantedMode?: Mode;
  readonly runFloor?: Mode;
}

export type RunnerStatus = "executed" | "awaiting_approval" | "awaiting_veto" | "rejected";

export interface RunnerResult {
  readonly status: RunnerStatus;
  readonly harness: HarnessId;
  readonly threadId: string;
  readonly tool: string;
  readonly gate?: GateDecision;
  readonly reason?: string;
  readonly prompt?: unknown;
}

export interface GatedToolRunner {
  readonly harness: HarnessId;
  readonly sme: SmeId;
  call(call: ToolCall): Promise<RunnerResult>;
  /** Hands a human decision (or a deadline) to a thread parked at approval or veto. */
  resume(threadId: string, tool: string, decision: unknown): Promise<RunnerResult>;
}

export interface GatedToolRunnerOptions {
  readonly graph: CompiledGateGraph;
  readonly harness: HarnessId;
  readonly sme: SmeId;
  readonly manifest?: HarnessManifest;
}

export function createGatedToolRunner(options: GatedToolRunnerOptions): GatedToolRunner {
  const { graph, harness, sme } = options;
  const manifest = options.manifest ?? HARNESS_MANIFEST;
  if (!isHarnessId(harness)) throw new GateInputError(`unknown harness ${JSON.stringify(harness)}`);
  if (!isSmeId(sme)) throw new GateInputError(`unknown SME ${JSON.stringify(sme)}`);

  const toResult = (threadId: string, tool: string, output: unknown): RunnerResult => {
    const state = output as Partial<GateState> & { __interrupt__?: { value?: { kind?: unknown } }[] };
    const prompt = Array.isArray(state.__interrupt__) ? state.__interrupt__[0]?.value : undefined;
    const base = { harness, threadId, tool, ...(state.gate === undefined ? {} : { gate: state.gate }) };
    if (prompt?.kind === "approval") return { ...base, status: "awaiting_approval", prompt };
    if (prompt?.kind === "veto") return { ...base, status: "awaiting_veto", prompt };
    if (state.executed === true) return { ...base, status: "executed" };
    return { ...base, status: "rejected", reason: state.rejectReason ?? "not executed (fail closed)" };
  };

  const run = async (threadId: string, tool: string, input: unknown): Promise<RunnerResult> => {
    try {
      return toResult(threadId, tool, await graph.invoke(input as never, { configurable: { thread_id: threadId } }));
    } catch (error) {
      if (error instanceof GateInputError) {
        return { harness, threadId, tool, status: "rejected", reason: error.message };
      }
      throw error;
    }
  };

  return {
    harness,
    sme,
    call: async (call) => {
      if (!harnessAllows(manifest, harness, call.tool)) {
        return {
          harness,
          threadId: call.threadId,
          tool: call.tool,
          status: "rejected",
          reason: `tool ${JSON.stringify(call.tool)} is not in the ${harness} harness manifest (fail closed)`,
        };
      }
      return run(call.threadId, call.tool, {
        threadId: call.threadId,
        pending: { tool: call.tool, toolCallId: call.toolCallId, ...(call.args === undefined ? {} : { args: call.args }) },
        harness,
        sme,
        wantedMode: call.wantedMode ?? "autonomous",
        runFloor: call.runFloor ?? "autonomous",
        ...(call.routerWantedMode === undefined ? {} : { routerWantedMode: call.routerWantedMode }),
      });
    },
    resume: (threadId, tool, decision) => run(threadId, tool, new Command({ resume: decision })),
  };
}

/** One runner per harness, all on the same gate graph. */
export function createHarnessRunners(
  graph: CompiledGateGraph,
  smeFor: Readonly<Record<HarnessId, SmeId>>,
  manifest: HarnessManifest = HARNESS_MANIFEST,
): Readonly<Record<HarnessId, GatedToolRunner>> {
  const entries = (Object.keys(smeFor) as HarnessId[]).map(
    (harness) => [harness, createGatedToolRunner({ graph, harness, sme: smeFor[harness], manifest })] as const,
  );
  return Object.freeze(Object.fromEntries(entries) as Record<HarnessId, GatedToolRunner>);
}
