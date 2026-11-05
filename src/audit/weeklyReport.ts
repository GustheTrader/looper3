/**
 * M7 (FR-9) weekly report: the share of gate decisions bound by each cap, by harness and by SME.
 *
 * Pure: takes audit records and a week start, returns numbers and a Markdown rendering. The
 * week is [weekStart, weekStart + 7 days) in UTC.
 */
import { BINDINGS, HARNESS_IDS, SME_IDS, type Binding } from "../policy/types.js";
import { DECISIONS, type Decision, type GateDecisionRecord } from "./decisionRecord.js";

export const WEEK_MS = 7 * 86_400_000;

export interface BindingBreakdown {
  readonly total: number;
  readonly byBinding: Readonly<Record<Binding, number>>;
  readonly byDecision: Readonly<Record<Decision, number>>;
}

export interface WeeklyReport {
  readonly weekStartMs: number;
  readonly weekEndMs: number;
  readonly total: number;
  readonly overall: BindingBreakdown;
  readonly byHarness: Readonly<Record<string, BindingBreakdown>>;
  readonly bySme: Readonly<Record<string, BindingBreakdown>>;
  readonly configVersions: readonly string[];
  readonly medianLatencyMs: number | null;
}

function emptyBreakdown(): { total: number; byBinding: Record<Binding, number>; byDecision: Record<Decision, number> } {
  return {
    total: 0,
    byBinding: Object.fromEntries(BINDINGS.map((binding) => [binding, 0])) as Record<Binding, number>,
    byDecision: Object.fromEntries(DECISIONS.map((decision) => [decision, 0])) as Record<Decision, number>,
  };
}

export function buildWeeklyReport(records: readonly GateDecisionRecord[], weekStartMs: number): WeeklyReport {
  const weekEndMs = weekStartMs + WEEK_MS;
  const inWeek = records.filter((record) => record.createdAtMs >= weekStartMs && record.createdAtMs < weekEndMs);

  const overall = emptyBreakdown();
  const byHarness: Record<string, ReturnType<typeof emptyBreakdown>> = Object.fromEntries(HARNESS_IDS.map((h) => [h, emptyBreakdown()]));
  const bySme: Record<string, ReturnType<typeof emptyBreakdown>> = Object.fromEntries(SME_IDS.map((s) => [s, emptyBreakdown()]));

  const add = (bucket: ReturnType<typeof emptyBreakdown>, record: GateDecisionRecord) => {
    bucket.total += 1;
    if (record.binding in bucket.byBinding) bucket.byBinding[record.binding] += 1;
    if (record.decision in bucket.byDecision) bucket.byDecision[record.decision] += 1;
  };
  for (const record of inWeek) {
    add(overall, record);
    add((byHarness[record.harness] ??= emptyBreakdown()), record);
    add((bySme[record.smeId] ??= emptyBreakdown()), record);
  }

  const latencies = inWeek.map((record) => record.latencyMs).sort((a, b) => a - b);
  const median = latencies.length === 0 ? null : (latencies[Math.floor((latencies.length - 1) / 2)] ?? null);

  return {
    weekStartMs,
    weekEndMs,
    total: inWeek.length,
    overall,
    byHarness,
    bySme,
    configVersions: [...new Set(inWeek.map((record) => record.configVersion))].sort(),
    medianLatencyMs: median,
  };
}

const pct = (count: number, total: number) => (total === 0 ? "-" : `${((count / total) * 100).toFixed(1)}%`);
const day = (ms: number) => new Date(ms).toISOString().slice(0, 10);

function table(title: string, rows: Readonly<Record<string, BindingBreakdown>>): string[] {
  const lines = [
    `## ${title}`,
    "",
    `| ${title.replace("By ", "")} | decisions | ${BINDINGS.join(" | ")} |`,
    `|---|---:|${BINDINGS.map(() => "---:").join("|")}|`,
  ];
  for (const [name, breakdown] of Object.entries(rows)) {
    lines.push(`| ${name} | ${breakdown.total} | ${BINDINGS.map((binding) => pct(breakdown.byBinding[binding], breakdown.total)).join(" | ")} |`);
  }
  lines.push("");
  return lines;
}

export function renderWeeklyReportMarkdown(report: WeeklyReport): string {
  const lines = [
    `# Looper³ gate - weekly binding report, ${day(report.weekStartMs)} to ${day(report.weekEndMs - 1)} (UTC)`,
    "",
    `Decisions: **${report.total}**. Config versions seen: ${report.configVersions.length === 0 ? "none" : report.configVersions.join(", ")}. ` +
      `Median latency (pending to decision): ${report.medianLatencyMs === null ? "-" : `${report.medianLatencyMs} ms`}.`,
    "",
    "Share of decisions bound by each cap. `wanted` = the caller asked for that strictness; `reversibility` / `oncall` = on_loop converted to in_loop (FR-5).",
    "",
    `Outcomes: ${DECISIONS.map((decision) => `${decision} ${report.overall.byDecision[decision]}`).join(", ")}.`,
    "",
    ...table("By harness", report.byHarness),
    ...table("By SME", report.bySme),
  ];
  return lines.join("\n");
}
