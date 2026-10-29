/**
 * Helper for declaring a tool next to its tier (FR-1). The tier is required by the type, so a
 * normal build cannot ship an untiered tool; the runtime does NOT validate the tier, which is
 * what lets `npm run check:tiers` catch a bad definition (instead of it throwing at import and
 * hiding the real problem).
 */
import type { Tier } from "./types.js";

export interface ToolDefinitionSpec {
  readonly name: string;
  readonly tier: Tier;
  readonly reversible: boolean;
  readonly compensatingAction?: string;
  readonly touchesMoney?: boolean;
  /** Harnesses allowed to call the tool (documentation only in phase 1). */
  readonly harnesses?: readonly string[];
  readonly description?: string;
}

export interface ToolDefinition extends ToolDefinitionSpec {
  readonly __looper3Tool: true;
}

export function defineTool(spec: ToolDefinitionSpec): ToolDefinition {
  return Object.freeze({ __looper3Tool: true as const, ...spec });
}

export const TOOL_DEFINITION_MARKER = "__looper3Tool";
