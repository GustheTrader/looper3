import { existsSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import Database from "better-sqlite3";
import { afterAll, describe, expect, it } from "vitest";

import * as agentFlagsModule from "../src/flags/agentFlags.js";
import {
  AgentFlagsError,
  DEFAULT_GLOBAL_FLOOR,
  DEFAULT_ONCALL_HEARTBEAT,
  GLOBAL_FLOOR_KEY,
  ONCALL_HEARTBEAT_KEY,
  initializeAgentFlagsStore,
  lowerGlobalFloor,
  readFloorChanges,
  readGlobalFloor,
  readOncallHeartbeat,
  tryReadGlobalFloor,
  raiseGlobalFloor,
  setOncallHeartbeat,
} from "../src/flags/agentFlags.js";

const tmpRoot = process.env["TMPDIR"] ?? os.tmpdir();
const created: string[] = [];

function freshDir(): string {
  const dir = mkdtempSync(path.join(tmpRoot, "looper3-flags-"));
  created.push(dir);
  return dir;
}

function freshDbPath(): string {
  return path.join(freshDir(), "agent_flags.sqlite");
}

function seededDb(): string {
  const dbPath = freshDbPath();
  initializeAgentFlagsStore(dbPath);
  return dbPath;
}

function rawWrite(dbPath: string, key: string, value: string): void {
  const db = new Database(dbPath, { timeout: 2000 });
  try {
    db.prepare("update agent_flags set value = ? where key = ?").run(value, key);
  } finally {
    db.close();
  }
}

afterAll(() => {
  for (const dir of created) rmSync(dir, { recursive: true, force: true });
});

describe("global floor store bootstrap", () => {
  it("seeds global_floor=autonomous and oncall_heartbeat_until=0 with insert-on-conflict-do-nothing", () => {
    const dbPath = freshDbPath();
    const result = initializeAgentFlagsStore(dbPath);

    expect(result.created).toBe(true);
    expect([...result.seededKeys].sort()).toEqual([GLOBAL_FLOOR_KEY, ONCALL_HEARTBEAT_KEY].sort());
    expect(readGlobalFloor(dbPath)).toBe(DEFAULT_GLOBAL_FLOOR);
    expect(readGlobalFloor(dbPath)).toBe("autonomous");
    expect(readOncallHeartbeat(dbPath).active).toBe(false);
    expect(DEFAULT_ONCALL_HEARTBEAT).toBe("0");

    // Idempotent: a second initialise does not reseed and does not reset the value.
    raiseGlobalFloor(dbPath, "on_loop");
    const second = initializeAgentFlagsStore(dbPath);
    expect(second.created).toBe(false);
    expect(second.seededKeys).toEqual([]);
    expect(readGlobalFloor(dbPath)).toBe("on_loop");
  });

  it("does not create the database on a read", () => {
    const dbPath = path.join(freshDir(), "never-created.sqlite");
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
    expect(existsSync(dbPath)).toBe(false);
  });
});

describe("readGlobalFloor fails closed to in_loop", () => {
  it("missing database", () => {
    const dbPath = path.join(freshDir(), "missing.sqlite");
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
    expect(tryReadGlobalFloor(dbPath)).toMatchObject({ ok: false, floor: "in_loop" });
  });

  it("missing parent directory", () => {
    const dbPath = path.join(freshDir(), "no", "such", "dir", "agent_flags.sqlite");
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
  });

  it("corrupt (non-SQLite) file", () => {
    const dbPath = path.join(freshDir(), "corrupt.sqlite");
    writeFileSync(dbPath, "this is definitely not a SQLite database, it is prose", "utf8");
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
    expect(tryReadGlobalFloor(dbPath).ok).toBe(false);
  });

  it("valid SQLite file without the agent_flags table", () => {
    const dbPath = path.join(freshDir(), "empty.sqlite");
    const db = new Database(dbPath);
    db.exec("create table something_else (id integer primary key)");
    db.close();
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
  });

  it("missing global_floor row", () => {
    const dbPath = seededDb();
    const db = new Database(dbPath);
    db.prepare("delete from agent_flags where key = ?").run(GLOBAL_FLOOR_KEY);
    db.close();
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
  });

  it("garbage value", () => {
    const dbPath = seededDb();
    rawWrite(dbPath, GLOBAL_FLOOR_KEY, "sudo");
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
    const read = tryReadGlobalFloor(dbPath);
    expect(read.ok).toBe(false);
    expect(read.reason).toMatch(/not a known mode/);
  });

  it("empty value", () => {
    const dbPath = seededDb();
    rawWrite(dbPath, GLOBAL_FLOOR_KEY, "");
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
  });

  it("locked database", () => {
    const dbPath = seededDb();
    const holder = new Database(dbPath, { timeout: 0 });
    try {
      holder.exec("begin exclusive");
      expect(readGlobalFloor(dbPath)).toBe("in_loop");
      expect(tryReadGlobalFloor(dbPath).reason).toMatch(/cannot (open|read)/);
    } finally {
      holder.exec("rollback");
      holder.close();
    }
    // Proves the in_loop above came from the lock, not from the stored value.
    expect(readGlobalFloor(dbPath)).toBe("autonomous");
  });
});

describe("raiseGlobalFloor only tightens", () => {
  it("raises autonomous -> on_loop -> in_loop", () => {
    const dbPath = seededDb();

    const first = raiseGlobalFloor(dbPath, "on_loop");
    expect(first).toEqual({ changed: true, floor: "on_loop", from: "autonomous" });
    expect(readGlobalFloor(dbPath)).toBe("on_loop");

    const second = raiseGlobalFloor(dbPath, "in_loop");
    expect(second).toEqual({ changed: true, floor: "in_loop", from: "on_loop" });
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
  });

  it("is a no-op when asked to loosen", () => {
    const dbPath = seededDb();
    raiseGlobalFloor(dbPath, "in_loop");

    expect(raiseGlobalFloor(dbPath, "on_loop")).toEqual({ changed: false, floor: "in_loop", from: "in_loop" });
    expect(raiseGlobalFloor(dbPath, "autonomous")).toEqual({ changed: false, floor: "in_loop", from: "in_loop" });
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
  });

  it("is a no-op when the floor is already at the target", () => {
    const dbPath = seededDb();
    expect(raiseGlobalFloor(dbPath, "autonomous")).toEqual({ changed: false, floor: "autonomous", from: "autonomous" });
  });

  it("never writes when the floor is unreadable (already in_loop)", () => {
    const dbPath = seededDb();
    rawWrite(dbPath, GLOBAL_FLOOR_KEY, "sudo");

    expect(raiseGlobalFloor(dbPath, "on_loop")).toEqual({ changed: false, floor: "in_loop", from: "in_loop" });
    const db = new Database(dbPath, { readonly: true });
    try {
      const row = db.prepare("select value from agent_flags where key = ?").get(GLOBAL_FLOOR_KEY) as { value: string };
      expect(row.value).toBe("sudo");
    } finally {
      db.close();
    }
  });

  it("does not create a missing database", () => {
    const dbPath = path.join(freshDir(), "absent.sqlite");
    expect(raiseGlobalFloor(dbPath, "in_loop")).toEqual({ changed: false, floor: "in_loop", from: "in_loop" });
    expect(existsSync(dbPath)).toBe(false);
  });

  it("rejects a non-mode target", () => {
    const dbPath = seededDb();
    expect(() => raiseGlobalFloor(dbPath, "sudo" as never)).toThrow(AgentFlagsError);
  });
});

describe("lowerGlobalFloor is the only loosening path and it is logged", () => {
  it("requires an operator identity and a reason", () => {
    const dbPath = seededDb();
    raiseGlobalFloor(dbPath, "in_loop");

    expect(() => lowerGlobalFloor(dbPath, { to: "on_loop", operator: "", reason: "test" })).toThrow(/operator identity/);
    expect(() => lowerGlobalFloor(dbPath, { to: "on_loop", operator: "   ", reason: "test" })).toThrow(/operator identity/);
    expect(() => lowerGlobalFloor(dbPath, { to: "on_loop", operator: "jeffg", reason: "" })).toThrow(/reason/);
    expect(() => lowerGlobalFloor(dbPath, { to: "on_loop", operator: "jeffg", reason: "   " })).toThrow(/reason/);
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
    expect(readFloorChanges(dbPath)).toEqual([]);
  });

  it("lowers, logs, and is readable back", () => {
    const dbPath = seededDb();
    raiseGlobalFloor(dbPath, "in_loop");

    const result = lowerGlobalFloor(dbPath, {
      to: "on_loop",
      operator: "jeffg",
      reason: "PRD review 2026-10-29: manual loosening approved",
    });

    expect(result).toEqual({ changed: true, floor: "on_loop", from: "in_loop" });
    expect(readGlobalFloor(dbPath)).toBe("on_loop");

    const log = readFloorChanges(dbPath);
    expect(log.length).toBe(1);
    expect(log[0]).toMatchObject({
      scope: "global",
      from: "in_loop",
      to: "on_loop",
      operator: "jeffg",
      reason: "PRD review 2026-10-29: manual loosening approved",
    });
    expect(log[0]?.createdAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it("keeps an ordered append-only history across several manual changes", () => {
    const dbPath = seededDb();
    raiseGlobalFloor(dbPath, "in_loop");
    lowerGlobalFloor(dbPath, { to: "on_loop", operator: "jeffg", reason: "first loosening" });
    lowerGlobalFloor(dbPath, { to: "autonomous", operator: "jeffg", reason: "second loosening" });

    const log = readFloorChanges(dbPath);
    expect(log.map((row) => `${row.from}->${row.to}`)).toEqual(["in_loop->on_loop", "on_loop->autonomous"]);
    expect(log.map((row) => row.id)).toEqual([...log.map((row) => row.id)].sort((a, b) => a - b));
  });

  it("does not lower when the target is equal or stricter, and does not log", () => {
    const dbPath = seededDb();
    raiseGlobalFloor(dbPath, "on_loop");

    expect(lowerGlobalFloor(dbPath, { to: "on_loop", operator: "jeffg", reason: "noop" })).toEqual({
      changed: false,
      floor: "on_loop",
      from: "on_loop",
    });
    expect(lowerGlobalFloor(dbPath, { to: "in_loop", operator: "jeffg", reason: "stricter" })).toEqual({
      changed: false,
      floor: "on_loop",
      from: "on_loop",
    });
    expect(readFloorChanges(dbPath)).toEqual([]);
    expect(readGlobalFloor(dbPath)).toBe("on_loop");
  });

  it("refuses to lower when the current floor is unreadable", () => {
    const dbPath = seededDb();
    rawWrite(dbPath, GLOBAL_FLOOR_KEY, "sudo");
    expect(() => lowerGlobalFloor(dbPath, { to: "autonomous", operator: "jeffg", reason: "x" })).toThrow(
      /unreadable/,
    );
  });

  it("refuses to lower a database that does not exist", () => {
    const dbPath = path.join(freshDir(), "absent.sqlite");
    expect(() => lowerGlobalFloor(dbPath, { to: "autonomous", operator: "jeffg", reason: "x" })).toThrow(
      AgentFlagsError,
    );
  });

  it("rejects a non-mode target", () => {
    const dbPath = seededDb();
    raiseGlobalFloor(dbPath, "in_loop");
    expect(() => lowerGlobalFloor(dbPath, { to: "sudo" as never, operator: "jeffg", reason: "x" })).toThrow(AgentFlagsError);
  });
});

describe("no other code path can loosen the global floor", () => {
  it("rejects a raw SQL loosening that has no floor_changes row", () => {
    const dbPath = seededDb();
    raiseGlobalFloor(dbPath, "in_loop");

    const db = new Database(dbPath);
    try {
      expect(() => db.prepare("update agent_flags set value = 'autonomous' where key = ?").run(GLOBAL_FLOOR_KEY)).toThrow(
        /may only be loosened through lowerGlobalFloor/,
      );
      expect(() => db.prepare("update agent_flags set value = 'on_loop' where key = ?").run(GLOBAL_FLOOR_KEY)).toThrow(
        /may only be loosened through lowerGlobalFloor/,
      );
      // Tightening through raw SQL is allowed - it can only make things stricter.
      expect(() => db.prepare("update agent_flags set value = 'in_loop' where key = ?").run(GLOBAL_FLOOR_KEY)).not.toThrow();
    } finally {
      db.close();
    }
    expect(readGlobalFloor(dbPath)).toBe("in_loop");
  });

  it("makes floor_changes append-only (no UPDATE, no DELETE)", () => {
    const dbPath = seededDb();
    raiseGlobalFloor(dbPath, "in_loop");
    lowerGlobalFloor(dbPath, { to: "on_loop", operator: "jeffg", reason: "logged loosening" });

    const db = new Database(dbPath);
    try {
      expect(() => db.prepare("update floor_changes set reason = 'rewritten'").run()).toThrow(/append-only/);
      expect(() => db.prepare("delete from floor_changes").run()).toThrow(/append-only/);
    } finally {
      db.close();
    }
    expect(readFloorChanges(dbPath).length).toBe(1);
  });

  it("throws rather than returning an empty audit log when the db is unreadable", () => {
    const dbPath = path.join(freshDir(), "absent.sqlite");
    expect(() => readFloorChanges(dbPath)).toThrow(AgentFlagsError);
  });

  it("has no exported setter that writes the floor without the log", () => {
    const exported = Object.keys(agentFlagsModule);
    // Guard against a future convenience setter being added quietly. The only write paths are
    // raiseGlobalFloor (tighten), lowerGlobalFloor (logged) and setOncallHeartbeat (not a floor).
    expect(exported.filter((name) => /^set.*Floor|^write.*Floor/i.test(name))).toEqual([]);
  });
});

describe("on-call heartbeat fails closed", () => {
  const HOUR = 60 * 60 * 1000;

  it("is inactive by default (seeded 0 == never checked in)", () => {
    const dbPath = seededDb();
    const status = readOncallHeartbeat(dbPath);
    expect(status.active).toBe(false);
    expect(status.untilMs).toBeNull();
    expect(status.reason).toMatch(/never set/);
  });

  it("is active while the deadline is in the future", () => {
    const dbPath = seededDb();
    const now = Date.now();
    const status = setOncallHeartbeat(dbPath, now + 8 * HOUR, "jeffg", now);
    expect(status.active).toBe(true);
    expect(status.untilMs).toBe(now + 8 * HOUR);
  });

  it("is inactive once the deadline passes", () => {
    const dbPath = seededDb();
    const now = Date.now();
    setOncallHeartbeat(dbPath, now + 60_000, "jeffg", now);
    expect(readOncallHeartbeat(dbPath, now + 60_001).active).toBe(false);
    expect(readOncallHeartbeat(dbPath, now + 60_001).reason).toMatch(/expired/);
  });

  it("treats a garbage value as inactive", () => {
    const dbPath = seededDb();
    rawWrite(dbPath, ONCALL_HEARTBEAT_KEY, "tomorrow-ish");
    const status = readOncallHeartbeat(dbPath);
    expect(status.active).toBe(false);
    expect(status.reason).toMatch(/not a numeric epoch-ms deadline/);
  });

  it("treats a missing row, missing db, and corrupt db as inactive", () => {
    const noRow = seededDb();
    const db = new Database(noRow);
    db.prepare("delete from agent_flags where key = ?").run(ONCALL_HEARTBEAT_KEY);
    db.close();
    expect(readOncallHeartbeat(noRow).active).toBe(false);

    const missing = path.join(freshDir(), "missing.sqlite");
    expect(readOncallHeartbeat(missing).active).toBe(false);
    expect(existsSync(missing)).toBe(false);

    const corrupt = path.join(freshDir(), "corrupt.sqlite");
    writeFileSync(corrupt, "not sqlite", "utf8");
    expect(readOncallHeartbeat(corrupt).active).toBe(false);
  });

  it("treats an invalid 'now' as inactive", () => {
    const dbPath = seededDb();
    setOncallHeartbeat(dbPath, Date.now() + HOUR, "jeffg");
    expect(readOncallHeartbeat(dbPath, Number.NaN).active).toBe(false);
  });

  it("requires an operator and a positive deadline to write", () => {
    const dbPath = seededDb();
    expect(() => setOncallHeartbeat(dbPath, Date.now() + HOUR, "  ")).toThrow(/operator identity/);
    expect(() => setOncallHeartbeat(dbPath, 0, "jeffg")).toThrow(/positive epoch-ms deadline/);
    expect(() => setOncallHeartbeat(dbPath, Number.NaN, "jeffg")).toThrow(/positive epoch-ms deadline/);
  });
});

describe("store hygiene", () => {
  it("leaves no stray files next to a seeded database", () => {
    const dbPath = seededDb();
    const names = readdirSync(path.dirname(dbPath));
    expect(names).toEqual(["agent_flags.sqlite"]);
  });
});
