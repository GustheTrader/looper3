/**
 * In-memory stand-ins for the two side effects that must be safe to repeat (FR-3): the approval
 * notification and the scheduler registration. Both are keyed by sha256(thread_id + tool_call_id)
 * and are the reason `prepare` can be re-run after a crash without a second notification.
 *
 * NOTHING here talks to the network or a real scheduler. The notification channel is PRD open
 * question 1 and the scheduler is M4/M5 work; these are clearly marked stubs.
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

export interface ScheduleRequest {
  readonly key: string;
  readonly threadId: string;
  readonly toolCallId: string;
  readonly resumeAtMs: number;
  readonly kind: "veto_deadline";
}

export interface NotifyOutcome {
  /** True only for the delivery that actually happened; a repeat returns false. */
  readonly sent: boolean;
  readonly deliverable: boolean;
}

export class NotificationOutbox {
  private readonly byKey = new Map<string, PendingNotification>();
  /** Every call to notifyOnce, including idempotent repeats (proves dedupe, not silence). */
  private attempts = 0;
  private readonly sentLog: PendingNotification[] = [];

  notifyOnce(record: PendingNotification): NotifyOutcome {
    this.attempts += 1;
    if (this.byKey.has(record.key)) return { sent: false, deliverable: true };
    this.byKey.set(record.key, record);
    this.sentLog.push(record);
    return { sent: true, deliverable: true };
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

export class SchedulerStub {
  private readonly jobs = new Map<string, ScheduleRequest>();
  private registrations = 0;

  scheduleOnce(job: ScheduleRequest): { scheduled: boolean } {
    this.registrations += 1;
    if (this.jobs.has(job.key)) return { scheduled: false };
    this.jobs.set(job.key, job);
    return { scheduled: true };
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
