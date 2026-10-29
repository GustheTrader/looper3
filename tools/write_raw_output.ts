import { defineTool } from "../src/policy/toolDefinition.js";

export const writeRawOutputTool = defineTool({
  name: "write_raw_output",
  tier: 2,
  reversible: true,
  harnesses: ["research", "analysis", "monitoring"],
  description: "Internal mutable write into raw/outputs/. Rate-limited and logged by the caller.",
});
