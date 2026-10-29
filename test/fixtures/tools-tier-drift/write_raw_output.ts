import { defineTool } from "../../../src/policy/toolDefinition.js";

// Fixture for the CI gate: valid definition, but it disagrees with the registry
// (registry says tier 2 for write_raw_output).
export const driftedWriteRawOutputTool = defineTool({
  name: "write_raw_output",
  tier: 3,
  reversible: true,
  description: "Drifted tier definition (negative test fixture).",
});
