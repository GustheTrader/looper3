/**
 * M5 (FR-6) floor-trigger alerts.
 *
 * The PRD wants every trigger "covered by a test and a LangSmith alert". LangSmith is not
 * reachable from this project (no network, no keys), so alerts go through a small sink
 * interface. Each alert is shaped like a LangSmith event (name, tags, metadata), so the real
 * adapter (M7) only has to send it. The adapter below is a clearly marked stub that refuses to
 * run.
 *
 * Alerts are idempotent per key: a node re-run after a crash, or a monitor that evaluates the
 * same day twice, never alerts twice.
 */
import type { Mode } from "../policy/types.js";

export interface FloorAlert {
  /** Dedupe key: `run:<thread>:<trigger>` or `global:<utc day>:<trigger>`. */
  readonly key: string;
  readonly scope: "run" | "global";
  readonly trigger: string;
  readonly raiseTo: Mode;
  /** Floor after the raise was applied (global scope), or the run floor now in force. */
  readonly floorAfter: Mode;
  readonly detail: string;
  readonly threadId?: string;
  readonly configVersion: string;
}

export interface LangSmithStyleEvent {
  readonly name: "looper3.floor_raised";
  readonly tags: readonly string[];
  readonly metadata: Readonly<Record<string, string>>;
}

export function toLangSmithEvent(alert: FloorAlert): LangSmithStyleEvent {
  return {
    name: "looper3.floor_raised",
    tags: [`scope:${alert.scope}`, `trigger:${alert.trigger}`, `floor:${alert.floorAfter}`],
    metadata: {
      key: alert.key,
      raiseTo: alert.raiseTo,
      detail: alert.detail,
      configVersion: alert.configVersion,
      ...(alert.threadId === undefined ? {} : { threadId: alert.threadId }),
    },
  };
}

export interface FloorAlertSink {
  emitOnce(alert: FloorAlert): { sent: boolean };
}

export class InMemoryFloorAlertSink implements FloorAlertSink {
  private readonly byKey = new Map<string, FloorAlert>();
  private attempts = 0;

  emitOnce(alert: FloorAlert): { sent: boolean } {
    this.attempts += 1;
    if (this.byKey.has(alert.key)) return { sent: false };
    this.byKey.set(alert.key, alert);
    return { sent: true };
  }

  get alerts(): readonly FloorAlert[] {
    return [...this.byKey.values()];
  }

  get events(): readonly LangSmithStyleEvent[] {
    return this.alerts.map(toLangSmithEvent);
  }

  get attemptsCount(): number {
    return this.attempts;
  }
}

export const LANGSMITH_ALERT_STUB_STATUS =
  "STUB: LangSmith floor alerts are not wired (M7). No client, no API key, no network. Use InMemoryFloorAlertSink.";

/** Clearly marked stub. Constructing it throws so nobody mistakes it for a working adapter. */
export class LangSmithFloorAlertSinkStub implements FloorAlertSink {
  constructor() {
    throw new Error(LANGSMITH_ALERT_STUB_STATUS);
  }

  emitOnce(_alert: FloorAlert): { sent: boolean } {
    throw new Error(LANGSMITH_ALERT_STUB_STATUS);
  }
}
