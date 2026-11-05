/**
 * M7 (FR-9) weekly report from the gate audit table.
 *
 *   npm run report:weekly -- --week 2026-11-02 [--db data/gate_audit.sqlite] [--out reports/2026-11-02.md]
 *
 * The week runs 7 days from --week (UTC). Without --out the Markdown goes to stdout.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { defaultAuditPath, readGateDecisions } from "../src/audit/auditStore.js";
import { WEEK_MS, buildWeeklyReport, renderWeeklyReportMarkdown } from "../src/audit/weeklyReport.js";

function arg(argv: readonly string[], name: string): string | undefined {
  const index = argv.indexOf(name);
  if (index >= 0) return argv[index + 1];
  const inline = argv.find((item) => item.startsWith(`${name}=`));
  return inline?.slice(name.length + 1);
}

export function main(argv: readonly string[] = process.argv.slice(2)): number {
  const week = arg(argv, "--week");
  if (week === undefined || !/^\d{4}-\d{2}-\d{2}$/.test(week) || Number.isNaN(Date.parse(`${week}T00:00:00Z`))) {
    console.error("report:weekly - pass --week YYYY-MM-DD (UTC start of the week)");
    return 2;
  }
  const weekStartMs = Date.parse(`${week}T00:00:00Z`);
  const dbPath = path.resolve(arg(argv, "--db") ?? defaultAuditPath());
  const markdown = renderWeeklyReportMarkdown(buildWeeklyReport(readGateDecisions(dbPath, weekStartMs, weekStartMs + WEEK_MS), weekStartMs));

  const out = arg(argv, "--out");
  if (out === undefined) {
    console.log(markdown);
  } else {
    mkdirSync(path.dirname(path.resolve(out)), { recursive: true });
    writeFileSync(path.resolve(out), `${markdown}\n`, "utf8");
    console.log(`wrote ${path.resolve(out)}`);
  }
  return 0;
}

const entrypoint = process.argv[1];
if (entrypoint !== undefined && import.meta.url === pathToFileURL(path.resolve(entrypoint)).href) {
  process.exitCode = main();
}
