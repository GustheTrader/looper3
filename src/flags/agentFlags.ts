/**
 * Global floor store (FR-6), backed by the agent_flags SQLite DB that sits next to the kill switch.
 *
 * Fail-closed contract:
 *  - readGlobalFloor() returns in_loop on ANY failure: missing db, locked db, corrupt file,
 *    missing table, missing row, unparsable or unknown value. Nothing is ever created on read.
 *  - raiseGlobalFloor() only ever moves the floor stricter; asking for a looser value is a no-op.
 *  - lowerGlobalFloor() is the ONLY loosening path: it demands an operator identity and a reason
 *    and appends to floor_changes in the same transaction.
 *  - M5: automatic raises by a named trigger go through raiseGlobalFloorFor(), which records
 *    them in floor_raises (also append-only) in the same transaction. floor_raises is a separate
 *    table on purpose: the anti-loosening trigger below trusts recent floor_changes rows, and an
 *    automatic raise must never be able to vouch for a later loosening.
 *  - floor_changes is append-only (SQLite triggers reject UPDATE/DELETE), and a trigger on
 *    agent_flags rejects a loosening write that has no matching floor_changes row, so a raw SQL
 *    UPDATE cannot bypass the manual path either.
 */
import fs from "node:fs";
import path from "node:path";

import Database from "better-sqlite3";

import { isMode, modeRank, strictest, type Mode } from "../policy/modes.js";

export const GLOBAL_FLOOR_KEY = "global_floor" as const;
export const ONCALL_HEARTBEAT_KEY = "oncall_heartbeat_until" as const;

/** Seeded default: a fresh store starts at the PRD default, not at in_loop. */
export const DEFAULT_GLOBAL_FLOOR: Mode = "autonomous";
/** Seeded default heartbeat: epoch-ms 0 == never checked in == inactive. */
export const DEFAULT_ONCALL_HEARTBEAT = "0";

export const AGENT_FLAGS_SCHEMA_VERSION = "1";

export class AgentFlagsError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AgentFlagsError";
  }
}

const SCHEMA_SQL = `
create table if not exists agent_flags (
  key   text primary key,
  value text not null
);

create table if not exists floor_changes (
  id         integer primary key autoincrement,
  created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  scope      text not null check (scope in ('global')),
  from_value text not null,
  to_value   text not null,
  operator   text not null check (length(trim(operator)) > 0),
  reason     text not null check (length(trim(reason)) > 0)
);

create trigger if not exists floor_changes_no_update
before update on floor_changes
begin
  select raise(abort, 'floor_changes is append-only: updates are rejected');
end;

create trigger if not exists floor_changes_no_delete
before delete on floor_changes
begin
  select raise(abort, 'floor_changes is append-only: deletes are rejected');
end;

create trigger if not exists agent_flags_global_floor_no_silent_loosen
before update of value on agent_flags
when old.key = 'global_floor'
 and new.value in ('in_loop', 'on_loop', 'autonomous')
 and (case new.value when 'in_loop' then 0 when 'on_loop' then 1 when 'autonomous' then 2 else 3 end)
   > (case old.value when 'in_loop' then 0 when 'on_loop' then 1 when 'autonomous' then 2 else 3 end)
 and not exists (
   select 1 from floor_changes
    where scope = 'global'
      and to_value = new.value
      and created_at >= strftime('%Y-%m-%dT%H:%M:%fZ','now','-10 seconds')
 )
begin
  select raise(abort, 'global_floor may only be loosened through lowerGlobalFloor (logged operator action)');
end;

create table if not exists floor_raises (
  id         integer primary key autoincrement,
  created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  from_value text not null,
  to_value   text not null,
  trigger    text not null check (length(trim(trigger)) > 0),
  detail     text not null
);

create trigger if not exists floor_raises_no_update
before update on floor_raises
begin
  select raise(abort, 'floor_raises is append-only: updates are rejected');
end;

create trigger if not exists floor_raises_no_delete
before delete on floor_raises
begin
  select raise(abort, 'floor_raises is append-only: deletes are rejected');
end;
`;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function openReadOnly(dbPath: string) {
  // fileMustExist + readonly: a read never creates the store, and a missing db throws.
  // timeout: 0 -> a locked db fails immediately instead of blocking the caller.
  return new Database(dbPath, { readonly: true, fileMustExist: true, timeout: 0 });
}

export function defaultAgentFlagsPath(env: NodeJS.ProcessEnv = process.env): string {
  const override = env["LOOPER3_AGENT_FLAGS_DB"];
  if (typeof override === "string" && override.trim() !== "") return path.resolve(override);
  return path.resolve(process.cwd(), "data", "agent_flags.sqlite");
}

export interface InitializeResult {
  readonly dbPath: string;
  readonly created: boolean;
  readonly seededKeys: readonly string[];
}

/**
 * Creates the schema and seeds the PRD defaults with insert-on-conflict-do-nothing.
 * This is the only function that creates the store; reads never do.
 */
export function initializeAgentFlagsStore(dbPath: string = defaultAgentFlagsPath()): InitializeResult {
  const created = !fs.existsSync(dbPath);
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });

  let db: ReturnType<typeof openReadOnly>;
  try {
    db = new Database(dbPath);
  } catch (error) {
    throw new AgentFlagsError(`cannot open agent_flags db at ${dbPath}: ${errorMessage(error)}`);
  }

  const seededKeys: string[] = [];
  try {
    db.exec(SCHEMA_SQL);
    const insert = db.prepare("insert into agent_flags (key, value) values (?, ?) on conflict (key) do nothing");
    const defaults: readonly (readonly [string, string])[] = [
      [GLOBAL_FLOOR_KEY, DEFAULT_GLOBAL_FLOOR],
      [ONCALL_HEARTBEAT_KEY, DEFAULT_ONCALL_HEARTBEAT],
    ];
    for (const [key, value] of defaults) {
      const info = insert.run(key, value);
      if (info.changes > 0) seededKeys.push(key);
    }
  } catch (error) {
    throw new AgentFlagsError(`cannot initialise agent_flags db at ${dbPath}: ${errorMessage(error)}`);
  } finally {
    db.close();
  }

  return { dbPath, created, seededKeys };
}

export type FloorRead =
  | { readonly ok: true; readonly floor: Mode; readonly reason: null }
  | { readonly ok: false; readonly floor: Mode; readonly reason: string };

function failClosed(reason: string): FloorRead {
  return { ok: false, floor: "in_loop", reason };
}

/** Read the global floor with the failure reason preserved (for logs / telemetry). */
export function tryReadGlobalFloor(dbPath: string = defaultAgentFlagsPath()): FloorRead {
  let db: ReturnType<typeof openReadOnly>;
  try {
    db = openReadOnly(dbPath);
  } catch (error) {
    return failClosed(`cannot open agent_flags db: ${errorMessage(error)}`);
  }

  try {
    const row = db.prepare("select value from agent_flags where key = ?").get(GLOBAL_FLOOR_KEY) as
      | { value?: unknown }
      | undefined;
    if (row === undefined) return failClosed("agent_flags has no global_floor row");
    const value = row.value;
    if (typeof value !== "string") return failClosed("global_floor value is not text");
    if (!isMode(value)) return failClosed(`global_floor value ${JSON.stringify(value)} is not a known mode`);
    return { ok: true, floor: value, reason: null };
  } catch (error) {
    return failClosed(`cannot read global_floor: ${errorMessage(error)}`);
  } finally {
    db.close();
  }
}

/** Global floor for the gate. in_loop on ANY failure. */
export function readGlobalFloor(dbPath: string = defaultAgentFlagsPath()): Mode {
  return tryReadGlobalFloor(dbPath).floor;
}

export interface FloorChangeResult {
  readonly changed: boolean;
  readonly floor: Mode;
  readonly from: Mode;
}

/**
 * Automatic tightening (FR-6). Accepts a target mode and applies strictest(current, target).
 * Asking for a looser mode is a no-op. When the current floor cannot be read the floor already
 * counts as in_loop (the strictest possible), so nothing is written.
 */
export function raiseGlobalFloor(dbPath: string, to: Mode): FloorChangeResult {
  if (!isMode(to)) {
    throw new AgentFlagsError(`raiseGlobalFloor requires a known mode, received ${JSON.stringify(to)}`);
  }

  const current = tryReadGlobalFloor(dbPath);
  if (!current.ok) return { changed: false, floor: current.floor, from: current.floor };

  const next = strictest(current.floor, to);
  if (next === current.floor) return { changed: false, floor: current.floor, from: current.floor };

  initializeAgentFlagsStore(dbPath);
  let db: ReturnType<typeof openReadOnly>;
  try {
    db = new Database(dbPath, { fileMustExist: true, timeout: 0 });
  } catch (error) {
    throw new AgentFlagsError(`cannot open agent_flags db for raising the floor: ${errorMessage(error)}`);
  }
  try {
    db.prepare("update agent_flags set value = ? where key = ?").run(next, GLOBAL_FLOOR_KEY);
  } finally {
    db.close();
  }

  const after = readGlobalFloor(dbPath);
  if (after !== next) {
    throw new AgentFlagsError(`global floor readback mismatch: wrote ${next}, read ${after}`);
  }
  return { changed: true, floor: after, from: current.floor };
}

export interface FloorRaiseCause {
  /** The trigger that fired, e.g. "daily_loss" or "kill_switch". */
  readonly trigger: string;
  readonly detail: string;
}

/**
 * M5 (FR-6): an automatic raise with a named cause. Same rule as raiseGlobalFloor (only ever
 * stricter; a looser or equal target is a no-op), plus the change is recorded in floor_raises in
 * the same transaction as the write. Nothing is recorded when the floor did not move.
 */
export function raiseGlobalFloorFor(dbPath: string, to: Mode, cause: FloorRaiseCause): FloorChangeResult {
  if (!isMode(to)) {
    throw new AgentFlagsError(`raiseGlobalFloorFor requires a known mode, received ${JSON.stringify(to)}`);
  }
  const trigger = typeof cause?.trigger === "string" ? cause.trigger.trim() : "";
  if (trigger === "") throw new AgentFlagsError("raiseGlobalFloorFor requires a trigger name");

  const current = tryReadGlobalFloor(dbPath);
  if (!current.ok) return { changed: false, floor: current.floor, from: current.floor };
  const next = strictest(current.floor, to);
  if (next === current.floor) return { changed: false, floor: current.floor, from: current.floor };

  initializeAgentFlagsStore(dbPath);
  let db: ReturnType<typeof openReadOnly>;
  try {
    db = new Database(dbPath, { fileMustExist: true, timeout: 0 });
  } catch (error) {
    throw new AgentFlagsError(`cannot open agent_flags db for raising the floor: ${errorMessage(error)}`);
  }
  try {
    db.transaction(() => {
      db.prepare("insert into floor_raises (from_value, to_value, trigger, detail) values (?, ?, ?, ?)").run(
        current.floor,
        next,
        trigger,
        String(cause.detail ?? ""),
      );
      db.prepare("update agent_flags set value = ? where key = ?").run(next, GLOBAL_FLOOR_KEY);
    })();
  } catch (error) {
    throw new AgentFlagsError(`raising the global floor failed: ${errorMessage(error)}`);
  } finally {
    db.close();
  }

  const after = readGlobalFloor(dbPath);
  if (after !== next) throw new AgentFlagsError(`global floor readback mismatch: wrote ${next}, read ${after}`);
  return { changed: true, floor: after, from: current.floor };
}

export interface FloorRaiseRecord {
  readonly id: number;
  readonly createdAt: string;
  readonly from: string;
  readonly to: string;
  readonly trigger: string;
  readonly detail: string;
}

/** Audit read of automatic raises. Throws when the log cannot be read (never []). */
export function readFloorRaises(dbPath: string = defaultAgentFlagsPath()): readonly FloorRaiseRecord[] {
  let db: ReturnType<typeof openReadOnly>;
  try {
    db = openReadOnly(dbPath);
  } catch (error) {
    throw new AgentFlagsError(`cannot open agent_flags db for floor_raises: ${errorMessage(error)}`);
  }
  try {
    const rows = db
      .prepare("select id, created_at, from_value, to_value, trigger, detail from floor_raises order by id asc")
      .all() as { id: number; created_at: string; from_value: string; to_value: string; trigger: string; detail: string }[];
    return rows.map((row) => ({
      id: row.id,
      createdAt: row.created_at,
      from: row.from_value,
      to: row.to_value,
      trigger: row.trigger,
      detail: row.detail,
    }));
  } catch (error) {
    throw new AgentFlagsError(`cannot read floor_raises: ${errorMessage(error)}`);
  } finally {
    db.close();
  }
}

export interface FloorLowerOptions {
  readonly to: Mode;
  readonly operator: string;
  readonly reason: string;
}

/**
 * The ONLY loosening path (FR-6). Requires an explicit operator identity and a reason, and logs
 * the change to floor_changes in the same transaction as the write. Refuses to lower from an
 * unreadable floor (we will not guess what we are lowering from).
 */
export function lowerGlobalFloor(dbPath: string, options: FloorLowerOptions): FloorChangeResult {
  const to = options.to;
  const operator = typeof options.operator === "string" ? options.operator.trim() : "";
  const reason = typeof options.reason === "string" ? options.reason.trim() : "";

  if (!isMode(to)) throw new AgentFlagsError(`lowerGlobalFloor requires a known mode, received ${JSON.stringify(to)}`);
  if (operator === "") throw new AgentFlagsError("lowerGlobalFloor requires an explicit operator identity");
  if (reason === "") throw new AgentFlagsError("lowerGlobalFloor requires a reason");

  const current = tryReadGlobalFloor(dbPath);
  if (!current.ok) {
    throw new AgentFlagsError(`refusing to lower global floor: current floor is unreadable (${current.reason})`);
  }
  // modeRank: 0 == in_loop (strictest) .. 2 == autonomous (loosest), so loosening means a
  // HIGHER rank. Equal or stricter targets are not a loosening and are left to raise/ no-op.
  if (modeRank(to) <= modeRank(current.floor)) {
    return { changed: false, floor: current.floor, from: current.floor };
  }

  let db: ReturnType<typeof openReadOnly>;
  try {
    db = new Database(dbPath, { fileMustExist: true, timeout: 0 });
  } catch (error) {
    throw new AgentFlagsError(`cannot open agent_flags db for lowering the floor: ${errorMessage(error)}`);
  }
  try {
    db.transaction(() => {
      db.prepare(
        "insert into floor_changes (scope, from_value, to_value, operator, reason) values ('global', ?, ?, ?, ?)",
      ).run(current.floor, to, operator, reason);
      db.prepare("update agent_flags set value = ? where key = ?").run(to, GLOBAL_FLOOR_KEY);
    })();
  } catch (error) {
    throw new AgentFlagsError(`lowering the global floor failed: ${errorMessage(error)}`);
  } finally {
    db.close();
  }

  const after = readGlobalFloor(dbPath);
  if (after !== to) throw new AgentFlagsError(`global floor readback mismatch: wrote ${to}, read ${after}`);
  return { changed: true, floor: after, from: current.floor };
}

export interface FloorChangeRecord {
  readonly id: number;
  readonly createdAt: string;
  readonly scope: string;
  readonly from: Mode | string;
  readonly to: Mode | string;
  readonly operator: string;
  readonly reason: string;
}

/** Audit read of the manual floor-change log. Throws when the log cannot be read (never []). */
export function readFloorChanges(dbPath: string = defaultAgentFlagsPath()): readonly FloorChangeRecord[] {
  let db: ReturnType<typeof openReadOnly>;
  try {
    db = openReadOnly(dbPath);
  } catch (error) {
    throw new AgentFlagsError(`cannot open agent_flags db for floor_changes: ${errorMessage(error)}`);
  }
  try {
    const rows = db
      .prepare(
        "select id, created_at, scope, from_value, to_value, operator, reason from floor_changes order by id asc",
      )
      .all() as { id: number; created_at: string; scope: string; from_value: string; to_value: string; operator: string; reason: string }[];
    return rows.map((row) => ({
      id: row.id,
      createdAt: row.created_at,
      scope: row.scope,
      from: row.from_value,
      to: row.to_value,
      operator: row.operator,
      reason: row.reason,
    }));
  } catch (error) {
    throw new AgentFlagsError(`cannot read floor_changes: ${errorMessage(error)}`);
  } finally {
    db.close();
  }
}

export interface HeartbeatStatus {
  readonly active: boolean;
  readonly untilMs: number | null;
  readonly reason: string;
}

function heartbeatInactive(reason: string, untilMs: number | null = null): HeartbeatStatus {
  return { active: false, untilMs, reason };
}

/**
 * FR-5 on-call heartbeat. Value is an epoch-millisecond deadline; 0 means never set.
 * Any failure (missing db, missing row, garbage value) counts as INACTIVE.
 */
export function readOncallHeartbeat(dbPath: string = defaultAgentFlagsPath(), nowMs: number = Date.now()): HeartbeatStatus {
  if (!Number.isFinite(nowMs)) return heartbeatInactive("now is not a finite timestamp");

  let db: ReturnType<typeof openReadOnly>;
  try {
    db = openReadOnly(dbPath);
  } catch (error) {
    return heartbeatInactive(`cannot open agent_flags db: ${errorMessage(error)}`);
  }

  try {
    const row = db.prepare("select value from agent_flags where key = ?").get(ONCALL_HEARTBEAT_KEY) as
      | { value?: unknown }
      | undefined;
    if (row === undefined) return heartbeatInactive("agent_flags has no oncall_heartbeat_until row");
    if (typeof row.value !== "string") return heartbeatInactive("heartbeat value is not text");

    const untilMs = Number(row.value.trim());
    if (!Number.isFinite(untilMs)) {
      return heartbeatInactive(`heartbeat value ${JSON.stringify(row.value)} is not a numeric epoch-ms deadline`);
    }
    if (untilMs <= 0) return heartbeatInactive("heartbeat was never set (0)", null);
    if (untilMs <= nowMs) return heartbeatInactive("heartbeat expired", untilMs);
    return { active: true, untilMs, reason: "heartbeat active" };
  } catch (error) {
    return heartbeatInactive(`cannot read oncall heartbeat: ${errorMessage(error)}`);
  } finally {
    db.close();
  }
}

/**
 * Writes the heartbeat deadline (epoch ms). Write path is M4-owned in the real system; it lives
 * here so phase-1 tests can exercise the fail-closed read.
 */
export function setOncallHeartbeat(
  dbPath: string,
  untilMs: number,
  operator: string,
  nowMs: number = Date.now(),
): HeartbeatStatus {
  const who = typeof operator === "string" ? operator.trim() : "";
  if (who === "") throw new AgentFlagsError("setOncallHeartbeat requires an operator identity");
  if (!Number.isFinite(untilMs) || untilMs <= 0) {
    throw new AgentFlagsError(`setOncallHeartbeat requires a positive epoch-ms deadline, received ${JSON.stringify(untilMs)}`);
  }

  initializeAgentFlagsStore(dbPath);
  let db: ReturnType<typeof openReadOnly>;
  try {
    db = new Database(dbPath, { fileMustExist: true, timeout: 0 });
  } catch (error) {
    throw new AgentFlagsError(`cannot open agent_flags db for heartbeat write: ${errorMessage(error)}`);
  }
  try {
    db.prepare("update agent_flags set value = ? where key = ?").run(String(Math.trunc(untilMs)), ONCALL_HEARTBEAT_KEY);
  } catch (error) {
    throw new AgentFlagsError(`cannot write heartbeat: ${errorMessage(error)}`);
  } finally {
    db.close();
  }

  return readOncallHeartbeat(dbPath, nowMs);
}

/**
 * M4 (FR-5) operator check-in: on call for `hours` from now (default from policy, 8). This is the
 * mechanism only; how an operator triggers it (manual, calendar, both) is PRD open question 4.
 */
export function checkInOnCall(dbPath: string, operator: string, hours: number, nowMs: number = Date.now()): HeartbeatStatus {
  if (!Number.isFinite(hours) || hours <= 0) {
    throw new AgentFlagsError(`checkInOnCall requires a positive number of hours, received ${JSON.stringify(hours)}`);
  }
  return setOncallHeartbeat(dbPath, nowMs + hours * 3_600_000, operator, nowMs);
}

/** M4 (FR-5) operator check-out: the heartbeat expires immediately (deadline = now). */
export function checkOutOnCall(dbPath: string, operator: string, nowMs: number = Date.now()): HeartbeatStatus {
  return setOncallHeartbeat(dbPath, Math.max(1, nowMs), operator, nowMs);
}
