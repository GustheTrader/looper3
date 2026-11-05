/**
 * M6 CI gate: "all side-effecting tools in all harnesses gated".
 *
 * `npm run check:gating` fails when:
 *  1. a tool definition declares no harnesses, or an unknown one;
 *  2. the harness manifest drifts from the tool definitions' `harnesses` lists;
 *  3. a manifest tool has no tier in the registry (it would be tier 4, but a manifest should not
 *     list a tool nobody tiered);
 *  4. any source file other than the gate's execute node calls the side-effect executor - the
 *     gate must be the ONLY execution path.
 * It also prints the coverage table: harness x tool x tier x mode (on call / not on call).
 */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { gateDecisionFor } from "../src/gate/nodes.js";
import { HARNESS_MANIFEST, manifestFromDefinitions, type HarnessManifest } from "../src/harness/manifest.js";
import { activePolicy } from "../src/policy/activePolicy.js";
import { TOOL_DEFINITION_MARKER } from "../src/policy/toolDefinition.js";
import { TOOL_TIER, type ToolTierRegistry } from "../src/policy/toolTiers.js";
import { HARNESS_IDS, type HarnessId, type Mode } from "../src/policy/types.js";
import { listToolFiles } from "./check-tool-tiers.js";

/** The one file allowed to call the executor, and the call it is allowed to make. */
export const EXECUTION_PATH_ALLOWLIST: readonly string[] = ["src/gate/nodes.ts"];
const EXECUTOR_CALL = /\bledger\s*\.\s*execute\s*\(/;

export interface GatingCoverageRow {
  readonly harness: HarnessId;
  readonly tool: string;
  readonly tier: number;
  readonly modeOnCall: Mode;
  readonly modeOffCall: Mode;
}

export interface GatingCheckResult {
  readonly ok: boolean;
  readonly errors: readonly string[];
  readonly coverage: readonly GatingCoverageRow[];
}

export interface GatingCheckOptions {
  readonly toolsDir: string;
  readonly sourceDirs: readonly string[];
  readonly root: string;
  readonly manifest?: HarnessManifest;
  readonly registry?: ToolTierRegistry;
}

function listSourceFiles(dir: string): string[] {
  const out: string[] = [];
  const walk = (current: string) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile() && entry.name.endsWith(".ts") && !entry.name.endsWith(".d.ts")) out.push(full);
    }
  };
  walk(dir);
  return out;
}

export async function checkHarnessGating(options: GatingCheckOptions): Promise<GatingCheckResult> {
  const manifest = options.manifest ?? HARNESS_MANIFEST;
  const registry = options.registry ?? TOOL_TIER;
  const errors: string[] = [];

  // 1. definitions declare valid harnesses
  const definitions: { name: string; harnesses: string[] }[] = [];
  for (const file of listToolFiles(options.toolsDir)) {
    const namespace = (await import(pathToFileURL(file).href)) as Record<string, unknown>;
    for (const value of Object.values(namespace)) {
      if (typeof value !== "object" || value === null) continue;
      const record = value as Record<string, unknown>;
      if (record[TOOL_DEFINITION_MARKER] !== true || typeof record["name"] !== "string") continue;
      const name = record["name"];
      const harnesses = record["harnesses"];
      if (!Array.isArray(harnesses) || harnesses.length === 0) {
        errors.push(`tool ${JSON.stringify(name)} declares no harnesses (M6: every tool must say who may call it)`);
        continue;
      }
      const unknown = harnesses.filter((harness) => !(HARNESS_IDS as readonly unknown[]).includes(harness));
      if (unknown.length > 0) errors.push(`tool ${JSON.stringify(name)} names unknown harness(es) ${JSON.stringify(unknown)}`);
      definitions.push({ name, harnesses: harnesses.filter((harness): harness is string => typeof harness === "string") });
    }
  }

  // 2. manifest == definitions
  const expected = manifestFromDefinitions(definitions);
  for (const harness of HARNESS_IDS) {
    const have = [...(manifest[harness] ?? [])].sort();
    const want = expected[harness];
    const missing = want.filter((tool) => !have.includes(tool));
    const extra = have.filter((tool) => !want.includes(tool));
    if (missing.length > 0) errors.push(`manifest for ${harness} is missing ${JSON.stringify(missing)} (declared by the tool definitions)`);
    if (extra.length > 0) errors.push(`manifest for ${harness} lists ${JSON.stringify(extra)}, which no tool definition gives to ${harness}`);
  }

  // 3. every manifest tool is tiered
  for (const harness of HARNESS_IDS) {
    for (const tool of manifest[harness] ?? []) {
      if (!Object.prototype.hasOwnProperty.call(registry, tool)) {
        errors.push(`manifest for ${harness} lists ${JSON.stringify(tool)}, which has no tier in the registry`);
      }
    }
  }

  // 4. the gate is the only execution path
  for (const dir of options.sourceDirs) {
    for (const file of listSourceFiles(dir)) {
      const relative = path.relative(options.root, file).split(path.sep).join("/");
      if (EXECUTION_PATH_ALLOWLIST.includes(relative)) continue;
      const text = readFileSync(file, "utf8");
      if (EXECUTOR_CALL.test(text)) {
        errors.push(`${relative} calls the side-effect executor directly; only the gate's execute node may (ungated execution path)`);
      }
    }
  }

  // coverage table
  const policy = activePolicy();
  const coverage: GatingCoverageRow[] = [];
  for (const harness of HARNESS_IDS) {
    for (const tool of manifest[harness] ?? []) {
      const input = { tool, wantedMode: "autonomous" as const, harness, sme: "research", runFloor: "autonomous" as const, globalFloor: "autonomous" as const };
      coverage.push({
        harness,
        tool,
        tier: registry[tool]?.tier ?? 4,
        modeOnCall: gateDecisionFor({ ...input, oncallActive: true }, policy, registry).mode,
        modeOffCall: gateDecisionFor({ ...input, oncallActive: false }, policy, registry).mode,
      });
    }
  }

  return { ok: errors.length === 0, errors, coverage };
}

export async function main(root: string = process.cwd()): Promise<number> {
  const result = await checkHarnessGating({
    root,
    toolsDir: path.join(root, "tools"),
    sourceDirs: [path.join(root, "src"), path.join(root, "tools"), path.join(root, "scripts")],
  });
  console.log("looper3 harness gating check (M6)");
  console.log("  harness           tool                      tier  on call     not on call");
  for (const row of result.coverage) {
    console.log(`  ${row.harness.padEnd(17)} ${row.tool.padEnd(25)} ${String(row.tier).padEnd(5)} ${row.modeOnCall.padEnd(11)} ${row.modeOffCall}`);
  }
  console.log("  (best case: wanted autonomous, research SME, no floors; real caps can only be stricter)");
  for (const error of result.errors) console.error(`  [error] ${error}`);
  if (result.ok) {
    console.log(`OK - ${result.coverage.length} harness/tool pairs, every one routed through the gate; no ungated execution path.`);
    return 0;
  }
  console.error(`FAILED - ${result.errors.length} error(s).`);
  return 1;
}

const entrypoint = process.argv[1];
if (entrypoint !== undefined && import.meta.url === pathToFileURL(path.resolve(entrypoint)).href) {
  process.exitCode = await main();
}
