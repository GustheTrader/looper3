import { defineTool } from "../../../src/policy/toolDefinition.js";

// Fixture for the CI gate: well-formed definition with no registry entry.
export const orphanTool = defineTool({
  name: "orphan_tool",
  tier: 1,
  reversible: true,
  description: "Unregistered tool (negative test fixture).",
});
