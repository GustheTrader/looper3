/**
 * M4 deadline runner (FR-4 / FR-5).
 *
 * Fires due deadlines registered by `armDeadline`. For each due job it checks that the thread is
 * STILL parked at the matching interrupt (approval for an approval deadline, veto for a veto
 * deadline) and only then resumes it with a timeout payload:
 *   - approval_deadline -> { approved: false, by: "timeout" }  (in_loop never auto-approves)
 *   - veto_deadline     -> { approved: true,  by: "timeout" }  (a REQUEST to auto-approve; the
 *     vetoWindow node grants it only with an active heartbeat, an eligible action and a confirmed
 *     notification, and rejects otherwise)
 *
 * Safe to run repeatedly and after a crash: a job whose thread already moved on (decided by a
 * person, or resumed by an earlier run that died before marking the job) is marked "stale" and
 * never resumes anything. In production this would be driven by the real scheduler; here it is
 * called with an explicit clock.
 */
import { Command } from "@langchain/langgraph";

import type { CompiledGateGraph } from "./graph.js";
import type { DeadlineKind, SchedulerStub } from "./outbox.js";

export interface DeadlineFiring {
  readonly key: string;
  readonly threadId: string;
  readonly kind: DeadlineKind;
  readonly outcome: "resumed" | "stale";
}

const PROMPT_FOR: Record<DeadlineKind, "approval" | "veto"> = {
  approval_deadline: "approval",
  veto_deadline: "veto",
};

export function timeoutPayload(kind: DeadlineKind) {
  return { approved: kind === "veto_deadline", by: "timeout" as const, reason: "deadline reached" };
}

export async function fireDueDeadlines(
  graph: CompiledGateGraph,
  scheduler: SchedulerStub,
  nowMs: number,
): Promise<DeadlineFiring[]> {
  const firings: DeadlineFiring[] = [];
  for (const job of scheduler.due(nowMs)) {
    const config = { configurable: { thread_id: job.threadId } };
    const snapshot = await graph.getState(config);
    const waiting = snapshot.tasks.some((task) =>
      (task.interrupts ?? []).some((item) => {
        const value = item.value as { kind?: unknown; action?: { toolCallId?: unknown } } | undefined;
        return value?.kind === PROMPT_FOR[job.kind] && value.action?.toolCallId === job.toolCallId;
      }),
    );

    if (!waiting) {
      scheduler.markFired(job.key, "stale");
      firings.push({ key: job.key, threadId: job.threadId, kind: job.kind, outcome: "stale" });
      continue;
    }

    await graph.invoke(new Command({ resume: timeoutPayload(job.kind) }), config);
    scheduler.markFired(job.key, "resumed");
    firings.push({ key: job.key, threadId: job.threadId, kind: job.kind, outcome: "resumed" });
  }
  return firings;
}
