import { defineTool } from "../../../src/policy/toolDefinition.js";

// Fixture for the M6 gating check: tiered and registered-looking, but no harness may call it.
export const lonelyTool = defineTool({
  name: "read_wiki",
  tier: 1,
  reversible: true,
  description: "Definition with no harnesses (negative test fixture).",
});
