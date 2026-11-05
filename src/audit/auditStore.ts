/**
 * M7 (FR-9): append-only gate decision audit table.
 *
 * SQLite here (paper mode, no network). The Postgres/Supabase version of the same table is
 * `migrations/postgres/001_gate_decisions.sql` (not applied by this project, NFR-4).
 *
 *  - One row per action: `idem_key` (sha256(thread_id + tool_call_id)) is the primary key, and a
 *    repeat insert is ignored, so a crash/resume never writes twice.
 *  - UPDATE and DELETE are rejected by triggers - even raw SQL cannot rewrite history.
 *  - A failed write throws; the gate treats that as "do not execute" (fail closed).
 */
import fs from "node:fs";
import path from "node:path";

import Database from "better-sqlite3";

import type { GateDecisionRecord } from "./decisionRecord.js";

export const GATE_DECISIONS_COLUMNS = [
  "idem_key",
  "thread_id",
  "tool",
  "tier",
  "effective_mode",
  "binding",
  "floor_source",
  "harness",
  "sme_id",
  "decision",
  "decided_by",
  "config_version",
  "latency_ms",
  "reject_reason",
  "created_at",
] as const;

const SCHEMA_SQL = `
create table if not exists gate_decisions (
  idem_key       text primary key,
  thread_id      text not null,
  tool           text not null,
  tier           integer not null check (tier between 1 and 4),
  effective_mode text not null check (effective_mode in ('in_loop','on_loop','autonomous')),
  binding        text not null,
  floor_source   text not null,
  harness        text not null,
  sme_id         text not null,
  decision       text not null check (decision in ('approved','rejected','vetoed','timeout')),
  decided_by     text not null check (decided_by in ('policy','human','timeout')),
  config_version text not null,
  latency_ms     integer not null check (latency_ms >= 0),
  reject_reason  text,
  created_at     text not null
);

create trigger if not exists gate_decisions_no_update
before update on gate_decisions
begin
  select raise(abort, 'gate_decisions is append-only: updates are rejected');
end;

create trigger if not exists gate_decisions_no_delete
before delete on gate_decisions
begin
  select raise(abort, 'gate_decisions is append-only: deletes are rejected');
end;
`;

export class GateAuditError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GateAuditError";
  }
}

export interface GateAuditStore {
  /** Inserts once per idem_key. Throws when the row cannot be written. */
  record(record: GateDecisionRecord): { inserted: boolean };
}

export function defaultAuditPath(env: NodeJS.ProcessEnv = process.env): string {
  const override = env["LOOPER3_AUDIT_DB"];
  return override !== undefined && override.trim() !== "" ? path.resolve(override) : path.resolve("data", "gate_audit.sqlite");
}

export function initializeAuditStore(dbPath: string): void {
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  const db = new Database(dbPath);
  try {
    db.exec(SCHEMA_SQL);
  } finally {
    db.close();
  }
}

export class SqliteGateAuditStore implements GateAuditStore {
  constructor(readonly dbPath: string) {
    initializeAuditStore(dbPath);
  }

  record(record: GateDecisionRecord): { inserted: boolean } {
    let db: InstanceType<typeof Database>;
    try {
      db = new Database(this.dbPath, { fileMustExist: true, timeout: 0 });
    } catch (error) {
      throw new GateAuditError(`cannot open gate audit db: ${error instanceof Error ? error.message : String(error)}`);
    }
    try {
      const info = db
        .prepare(
          `insert into gate_decisions (${GATE_DECISIONS_COLUMNS.join(", ")})
           values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           on conflict (idem_key) do nothing`,
        )
        .run(
          record.idemKey,
          record.threadId,
          record.tool,
          record.tier,
          record.effectiveMode,
          record.binding,
          record.floorSource,
          record.harness,
          record.smeId,
          record.decision,
          record.decidedBy,
          record.configVersion,
          Math.round(record.latencyMs),
          record.rejectReason,
          new Date(record.createdAtMs).toISOString(),
        );
      return { inserted: info.changes > 0 };
    } catch (error) {
      throw new GateAuditError(`cannot write gate decision: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      db.close();
    }
  }
}

/** Latest instant whose ISO string still sorts correctly as text (year 9999); later ones start with "+". */
const MAX_ISO_MS = 253_402_300_799_999;

/** Reads decisions with created_at in [fromMs, toMs). Throws when the table cannot be read. */
export function readGateDecisions(dbPath: string, fromMs = 0, toMs = Number.MAX_SAFE_INTEGER): GateDecisionRecord[] {
  let db: InstanceType<typeof Database>;
  try {
    db = new Database(dbPath, { readonly: true, fileMustExist: true, timeout: 0 });
  } catch (error) {
    throw new GateAuditError(`cannot open gate audit db: ${error instanceof Error ? error.message : String(error)}`);
  }
  try {
    const rows = db
      .prepare(`select ${GATE_DECISIONS_COLUMNS.join(", ")} from gate_decisions where created_at >= ? and created_at < ? order by created_at, idem_key`)
      .all(new Date(fromMs).toISOString(), new Date(Math.min(toMs, MAX_ISO_MS)).toISOString()) as Record<string, unknown>[];
    return rows.map((row) => ({
      idemKey: String(row["idem_key"]),
      threadId: String(row["thread_id"]),
      tool: String(row["tool"]),
      tier: Number(row["tier"]) as GateDecisionRecord["tier"],
      effectiveMode: row["effective_mode"] as GateDecisionRecord["effectiveMode"],
      binding: row["binding"] as GateDecisionRecord["binding"],
      floorSource: row["floor_source"] as GateDecisionRecord["floorSource"],
      harness: row["harness"] as GateDecisionRecord["harness"],
      smeId: row["sme_id"] as GateDecisionRecord["smeId"],
      decision: row["decision"] as GateDecisionRecord["decision"],
      decidedBy: row["decided_by"] as GateDecisionRecord["decidedBy"],
      configVersion: String(row["config_version"]),
      latencyMs: Number(row["latency_ms"]),
      rejectReason: row["reject_reason"] === null ? null : String(row["reject_reason"]),
      createdAtMs: Date.parse(String(row["created_at"])),
    }));
  } finally {
    db.close();
  }
}
