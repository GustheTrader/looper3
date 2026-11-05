/**
 * In-memory stand-ins for the side effects that must be safe to repeat: the approval
 * notification (`prepare`), the deadline registration (`armDeadline`) and the timeout escalation
 * (`escalate`). All are keyed by sha256(thread_id + tool_call_id), which is why those nodes can be
 * re-run after a crash without a second message or a second job.
 *
 * NOTHING here talks to the network or a real scheduler. The notification / alert channel is PRD
 * open question 1; these are clearly marked stubs.
 */

export interface PendingNotification {
  readonly key: string;
  readonly threadId: string;
  readonly tool: string;
  readonly toolCallId: string;
  readonly kind: "gate_pending";
  readonly createdAtMs: number;
  readonly vetoExpiresAt: number;
}

export type DeadlineKind = "approval_deadline" | "veto_deadline";

export interface ScheduleRequest {
  readonly key: string;
  readonly threadId: string;
  readonly toolCallId: string;
  readonly resumeAtMs: number;
  readonly kind: DeadlineKind;
}

export interface NotifyOutcome {
  /** True only for the delivery that actually happened; a repeat returns false. */
  readonly sent: boolean;
  readonly deliverable: boolean;
}

export interface NotificationOutboxOptions {
  /**
   * Stand-in for a channel that acknowledges delivery. Default false: a notification counts as
   * delivered-and-confirmed only when `confirmDelivery()` is called (fail closed, FR-5).
   */
  readonly autoConfirm?: boolean;
}

export class NotificationOutbox {
  private readonly byKey = new Map<string, PendingNotification>();
  private readonly confirmed = new Set<string>();
  private readonly autoConfirm: boolean;
  /** Every call to notifyOnce, including idempotent repeats (proves dedupe, not silence). */
  private attempts = 0;
  private readonly sentLog: PendingNotification[] = [];

  constructor(options: NotificationOutboxOptions = {}) {
    this.autoConfirm = options.autoConfirm === true;
  }

  notifyOnce(record: PendingNotification): NotifyOutcome {
    this.attempts += 1;
    if (this.byKey.has(record.key)) return { sent: false, deliverable: true };
    this.byKey.set(record.key, record);
    this.sentLog.push(record);
    if (this.autoConfirm) this.confirmed.add(record.key);
    return { sent: true, deliverable: true };
  }

  /** The channel's delivery receipt. Only a notification that was actually sent can be confirmed. */
  confirmDelivery(key: string): boolean {
    if (!this.byKey.has(key)) return false;
    this.confirmed.add(key);
    return true;
  }

  /** FR-5: required (but not sufficient) for an on_loop auto-approve. Unknown key => false. */
  isDeliveryConfirmed(key: string | undefined): boolean {
    return typeof key === "string" && this.confirmed.has(key);
  }

  get attemptsCount(): number {
    return this.attempts;
  }

  /** Delivered notifications - must stay at one per key across crash/resume. */
  get deliveries(): readonly PendingNotification[] {
    return this.sentLog;
  }

  has(key: string): boolean {
    return this.byKey.has(key);
  }

  get(key: string): PendingNotification | undefined {
    return this.byKey.get(key);
  }
}

export type DeadlineOutcome = "resumed" | "stale";

export class SchedulerStub {
  private readonly jobs = new Map<string, ScheduleRequest>();
  private readonly fired = new Map<string, DeadlineOutcome>();
  private registrations = 0;

  scheduleOnce(job: ScheduleRequest): { scheduled: boolean } {
    this.registrations += 1;
    if (this.jobs.has(job.key)) return { scheduled: false };
    this.jobs.set(job.key, job);
    return { scheduled: true };
  }

  /** Jobs whose deadline has passed and that have not been fired yet, earliest first. */
  due(nowMs: number): readonly ScheduleRequest[] {
    return [...this.jobs.values()]
      .filter((job) => job.resumeAtMs <= nowMs && !this.fired.has(job.key))
      .sort((a, b) => a.resumeAtMs - b.resumeAtMs);
  }

  markFired(key: string, outcome: DeadlineOutcome): void {
    if (this.jobs.has(key)) this.fired.set(key, outcome);
  }

  firedOutcome(key: string): DeadlineOutcome | undefined {
    return this.fired.get(key);
  }

  get registrationCount(): number {
    return this.registrations;
  }

  get all(): readonly ScheduleRequest[] {
    return [...this.jobs.values()];
  }

  get(key: string): ScheduleRequest | undefined {
    return this.jobs.get(key);
  }
}

export type AlertSeverity = "high" | "critical";

export interface TimeoutAlert {
  readonly key: string;
  readonly threadId: string;
  readonly tool: string;
  readonly toolCallId: string;
  readonly kind: "gate_timeout";
  readonly severity: AlertSeverity;
  readonly approved: boolean;
  readonly reason: string;
}

/** FR-4 escalation channel stand-in. One alert per action key, however often `escalate` runs. */
export class AlertOutbox {
  private readonly byKey = new Map<string, TimeoutAlert>();
  private attempts = 0;

  escalateOnce(alert: TimeoutAlert): { sent: boolean } {
    this.attempts += 1;
    if (this.byKey.has(alert.key)) return { sent: false };
    this.byKey.set(alert.key, alert);
    return { sent: true };
  }

  get alerts(): readonly TimeoutAlert[] {
    return [...this.byKey.values()];
  }

  get attemptsCount(): number {
    return this.attempts;
  }

  get(key: string): TimeoutAlert | undefined {
    return this.byKey.get(key);
  }
}
