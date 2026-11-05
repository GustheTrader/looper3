/**
 * M6: which tools each harness may call.
 *
 * Hand-maintained next to the tier registry, like TOOL_TIER, and checked against the tool
 * definitions' `harnesses` field by `npm run check:gating`, so the two cannot drift. A tool a
 * harness does not list is refused by that harness's runner before the gate is even asked.
 */
import { HARNESS_IDS, type HarnessId } from "../policy/types.js";

export type HarnessManifest = Readonly<Record<HarnessId, readonly string[]>>;

export const HARNESS_MANIFEST_VERSION = "1.0.0";

const RAW: Record<HarnessId, readonly string[]> = {
  research: ["post_wiki_page", "query_backtest_store", "read_wiki", "write_raw_output"],
  analysis: ["post_wiki_page", "query_backtest_store", "read_wiki", "send_telegram_alert", "write_raw_output"],
  monitoring: ["query_backtest_store", "read_wiki", "send_telegram_alert", "write_raw_output"],
  business_process: ["post_wiki_page", "read_and_delete", "read_wiki", "send_telegram_alert"],
  build: ["merge_pull_request", "read_and_delete", "read_wiki"],
  execution: ["submit_broker_order_stub"],
};

export const HARNESS_MANIFEST: HarnessManifest = Object.freeze(
  Object.fromEntries(HARNESS_IDS.map((harness) => [harness, Object.freeze([...RAW[harness]].sort())])) as Record<
    HarnessId,
    readonly string[]
  >,
);

export function harnessAllows(manifest: HarnessManifest, harness: HarnessId, tool: string): boolean {
  const tools = manifest[harness];
  return Array.isArray(tools) && tools.includes(tool);
}

/** Builds a manifest from tool definitions' `harnesses` lists (used by the drift check). */
export function manifestFromDefinitions(
  definitions: readonly { readonly name: string; readonly harnesses?: readonly string[] }[],
): Record<HarnessId, string[]> {
  const manifest = Object.fromEntries(HARNESS_IDS.map((harness) => [harness, [] as string[]])) as Record<HarnessId, string[]>;
  for (const definition of definitions) {
    for (const harness of definition.harnesses ?? []) {
      if ((HARNESS_IDS as readonly string[]).includes(harness)) manifest[harness as HarnessId].push(definition.name);
    }
  }
  for (const harness of HARNESS_IDS) manifest[harness].sort();
  return manifest;
}
