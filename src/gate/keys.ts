/**
 * Idempotency keys (FR-3): sha256(thread_id + tool_call_id).
 *
 * A NUL separator is used so that ("ab","c") and ("a","bc") cannot collide.
 */
import { createHash } from "node:crypto";

export function idempotencyKey(threadId: string, toolCallId: string): string {
  if (typeof threadId !== "string" || threadId === "") {
    throw new Error("idempotencyKey requires a non-empty threadId");
  }
  if (typeof toolCallId !== "string" || toolCallId === "") {
    throw new Error("idempotencyKey requires a non-empty toolCallId");
  }
  return createHash("sha256").update(`${threadId}\u0000${toolCallId}`, "utf8").digest("hex");
}
