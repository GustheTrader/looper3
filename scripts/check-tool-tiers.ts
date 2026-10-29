/**
 * CI gate for FR-1: every tool definition must declare a tier 1-4 and must match the static
 * registry. `npm run check:tiers` exits non-zero when any tool is untiered, invalid, unregistered
 * or drifted from the registry.
 *
 * The check imports the definitions (so it validates real values, not text), which is why it runs
 * under tsx rather than plain node.
 */
import { readdirSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { TOOL_DEFINITION_MARKER } from "../src/policy/toolDefinition.js";
import { TOOL_TIER, TOOL_TIER_REGISTRY_VERSION, type ToolTierRegistry } from "../src/policy/toolTiers.js";
import { isTier, type Tier } from "../src/policy/types.js";

export interface ToolCheckIssue {
  readonly severity: "error" | "warning";
  readonly file: string;
  readonly tool: string | null;
  readonly message: string;
}

export interface CheckedTool {
  readonly file: string;
  readonly name: string;
  readonly tier: Tier | null;
}

export interface ToolCheckResult {
  readonly ok: boolean;
  readonly toolsDir: string;
  readonly files: readonly string[];
  readonly tools: readonly CheckedTool[];
  readonly issues: readonly ToolCheckIssue[];
  readonly errors: readonly string[];
  readonly warnings: readonly string[];
}

interface Candidate extends CheckedTool {
  readonly exportName: string;
  readonly reversible: unknown;
  readonly compensatingAction: unknown;
  readonly touchesMoney: unknown;
  readonly marked: boolean;
}

/** Tool definition modules under `dir`. `_`-prefixed files are helper modules and are skipped. */
export function listToolFiles(dir: string): string[] {
  const files: string[] = [];
  const walk = (current: string): void => {
    const entries = readdirSync(current, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      if (entry.name.startsWith(".")) continue;
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (!entry.isFile() || !entry.name.endsWith(".ts")) continue;
      if (entry.name.endsWith(".d.ts") || entry.name.endsWith(".test.ts") || entry.name.endsWith(".spec.ts")) continue;
      if (entry.name.startsWith("_")) continue;
      files.push(full);
    }
  };
  walk(dir);
  return files;
}

function collectCandidates(namespace: Record<string, unknown>): Candidate[] {
  const candidates: Candidate[] = [];
  for (const [exportName, value] of Object.entries(namespace)) {
    if (typeof value !== "object" || value === null || Array.isArray(value)) continue;
    const record = value as Record<string, unknown>;
    const marked = record[TOOL_DEFINITION_MARKER] === true;
    const looksLikeTool = typeof record["name"] === "string" && ("tier" in record || "reversible" in record);
    if (!marked && !looksLikeTool) continue;
    const name = record["name"];
    const tier = record["tier"];
    candidates.push({
      exportName,
      file: "",
      name: typeof name === "string" ? name : `<unnamed export ${exportName}>`,
      tier: isTier(tier) ? tier : null,
      reversible: record["reversible"],
      compensatingAction: record["compensatingAction"],
      touchesMoney: record["touchesMoney"],
      marked,
    });
  }
  return candidates;
}

export interface CheckOptions {
  readonly toolsDir: string;
  readonly registry?: ToolTierRegistry;
}

export async function checkToolTiers(options: CheckOptions): Promise<ToolCheckResult> {
  const toolsDir = path.resolve(options.toolsDir);
  const registry = options.registry ?? TOOL_TIER;
  const issues: ToolCheckIssue[] = [];
  const tools: CheckedTool[] = [];

  let files: string[];
  try {
    files = listToolFiles(toolsDir);
  } catch (error) {
    const message = `cannot scan tools directory ${toolsDir}: ${error instanceof Error ? error.message : String(error)}`;
    return { ok: false, toolsDir, files: [], tools: [], issues: [{ severity: "error", file: toolsDir, tool: null, message }], errors: [message], warnings: [] };
  }

  const seen = new Set<string>();

  for (const file of files) {
    const relative = path.relative(process.cwd(), file) || file;

    let namespace: Record<string, unknown>;
    try {
      namespace = (await import(pathToFileURL(file).href)) as Record<string, unknown>;
    } catch (error) {
      issues.push({
        severity: "error",
        file: relative,
        tool: null,
        message: `failed to import tool module: ${error instanceof Error ? error.message : String(error)}`,
      });
      continue;
    }

    const candidates = collectCandidates(namespace);
    if (candidates.length === 0) {
      issues.push({
        severity: "error",
        file: relative,
        tool: null,
        message: "no tool definition exported (rename the file with a leading underscore if it is not a tool module)",
      });
      continue;
    }

    for (const candidate of candidates.sort((a, b) => a.name.localeCompare(b.name))) {
      const tool = candidate.name;
      tools.push({ file: relative, name: tool, tier: candidate.tier });
      seen.add(tool);

      if (!candidate.marked) {
        issues.push({
          severity: "warning",
          file: relative,
          tool,
          message: "definition does not use defineTool(); tier is still checked but the marker is missing",
        });
      }

      if (candidate.tier === null) {
        issues.push({
          severity: "error",
          file: relative,
          tool,
          message: "MISSING/INVALID TIER: every tool definition must declare tier 1-4",
        });
        continue;
      }

      const entry = registry[tool];
      if (entry === undefined) {
        issues.push({ severity: "error", file: relative, tool, message: `not present in TOOL_TIER registry (v${TOOL_TIER_REGISTRY_VERSION})` });
        continue;
      }

      if (entry.tier !== candidate.tier) {
        issues.push({
          severity: "error",
          file: relative,
          tool,
          message: `tier drift: definition declares ${candidate.tier}, registry says ${entry.tier}`,
        });
      }
      if (entry.reversible !== candidate.reversible) {
        issues.push({
          severity: "error",
          file: relative,
          tool,
          message: `reversible drift: definition ${String(candidate.reversible)}, registry ${String(entry.reversible)}`,
        });
      }
      if ((entry.compensatingAction ?? null) !== (candidate.compensatingAction ?? null)) {
        issues.push({
          severity: "error",
          file: relative,
          tool,
          message: `compensatingAction drift: definition ${String(candidate.compensatingAction)}, registry ${String(entry.compensatingAction)}`,
        });
      }
      if ((entry.touchesMoney ?? false) !== (candidate.touchesMoney ?? false)) {
        issues.push({
          severity: "error",
          file: relative,
          tool,
          message: `touchesMoney drift: definition ${String(candidate.touchesMoney)}, registry ${String(entry.touchesMoney)}`,
        });
      }
    }
  }

  for (const key of Object.keys(registry).sort()) {
    if (!seen.has(key)) {
      issues.push({
        severity: "warning",
        file: "-",
        tool: key,
        message: "registry entry has no tool definition in this tools directory",
      });
    }
  }

  const errors = issues.filter((issue) => issue.severity === "error").map((issue) => `${issue.file} ${issue.tool ?? ""}: ${issue.message}`);
  const warnings = issues.filter((issue) => issue.severity === "warning").map((issue) => `${issue.file} ${issue.tool ?? ""}: ${issue.message}`);

  return { ok: errors.length === 0, toolsDir, files, tools, issues, errors, warnings };
}

export function parseToolsDir(argv: readonly string[], cwd: string = process.cwd()): string {
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === undefined) continue;
    if (arg === "--tools") {
      const value = argv[index + 1];
      if (value === undefined || value.startsWith("--")) throw new Error("--tools requires a directory argument");
      return path.resolve(cwd, value);
    }
    if (arg.startsWith("--tools=")) return path.resolve(cwd, arg.slice("--tools=".length));
  }
  return path.resolve(cwd, "tools");
}

export async function main(argv: readonly string[] = process.argv.slice(2)): Promise<number> {
  let toolsDir: string;
  try {
    toolsDir = parseToolsDir(argv);
  } catch (error) {
    console.error(`looper3 check:tiers - ${error instanceof Error ? error.message : String(error)}`);
    return 2;
  }

  const result = await checkToolTiers({ toolsDir });

  const tiered = result.tools.filter((tool) => tool.tier !== null).length;
  console.log("looper3 tool-tier check (FR-1)");
  console.log(`  tools dir : ${result.toolsDir}`);
  console.log(`  files     : ${result.files.length}`);
  console.log(`  tools     : ${result.tools.length} (${tiered} tiered, ${result.tools.length - tiered} untiered)`);
  console.log(`  registry  : v${TOOL_TIER_REGISTRY_VERSION}, ${Object.keys(TOOL_TIER).length} entries`);

  for (const warning of result.warnings) console.warn(`  [warn]  ${warning}`);
  for (const error of result.errors) console.error(`  [error] ${error}`);

  if (result.ok) {
    console.log("OK - every tool definition declares a tier and matches the registry.");
    return 0;
  }
  console.error(`FAILED - ${result.errors.length} error(s). Untiered or drifted tools cannot ship.`);
  return 1;
}

const entrypoint = process.argv[1];
if (entrypoint !== undefined && import.meta.url === pathToFileURL(path.resolve(entrypoint)).href) {
  const code = await main();
  process.exitCode = code;
}
