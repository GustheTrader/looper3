/**
 * Fake side-effect executor with an idempotency ledger.
 *
 * FR-3: gated tier>=3 tool calls are non-replayable and carry the idempotency key
 * sha256(thread_id + tool_call_id) - applying the same key twice must run the side effect once.
 * Tier 1-2 calls are replayable by definition (read-only / internal mutable), so a repeat applies
 * again; that difference is what the tier registry buys us.
 *
 * No broker, exchange, network or file side effect exists here. This is the paper-mode stand-in.
 */
import type { Tier } from "../policy/types.js";

export interface ExecuteRequest {
  readonly key: string;
  readonly threadId: string;
  readonly toolCallId: string;
  readonly tool: string;
  readonly tier: Tier;
  readonly args?: Readonly<Record<string, unknown>>;
}

export interface ExecuteOutcome {
  readonly status: "applied" | "deduplicated";
  readonly key: string;
  readonly effects: number;
}

interface LedgerEntry {
  effects: number;
  tier: Tier;
  tool: string;
}

export class SideEffectLedger {
  private readonly applied = new Map<string, LedgerEntry>();
  private readonly attempts = new Map<string, number>();
  private total = 0;

  /**
   * Test-only chaos knob: the next execute() applies its effect and then throws, simulating a
   * process death between "side effect happened" and "checkpoint written".
   */
  failNextApply = false;

  execute(request: ExecuteRequest): ExecuteOutcome {
    const attempt = (this.attempts.get(request.key) ?? 0) + 1;
    this.attempts.set(request.key, attempt);

    const existing = this.applied.get(request.key);
    if (existing !== undefined) {
      if (request.tier >= 3) {
        return { status: "deduplicated", key: request.key, effects: existing.effects };
      }
      existing.effects += 1;
      this.total += 1;
      return { status: "applied", key: request.key, effects: existing.effects };
    }

    this.applied.set(request.key, { effects: 1, tier: request.tier, tool: request.tool });
    this.total += 1;

    if (this.failNextApply) {
      this.failNextApply = false;
      throw new Error("simulated crash after side effect, before checkpoint");
    }

    return { status: "applied", key: request.key, effects: 1 };
  }

  effectsFor(key: string): number {
    return this.applied.get(key)?.effects ?? 0;
  }

  attemptsFor(key: string): number {
    return this.attempts.get(key) ?? 0;
  }

  get totalEffects(): number {
    return this.total;
  }

  get appliedKeys(): readonly string[] {
    return [...this.applied.keys()];
  }
}
