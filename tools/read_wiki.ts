import { defineTool } from "../src/policy/toolDefinition.js";

export const readWikiTool = defineTool({
  name: "read_wiki",
  tier: 1,
  reversible: true,
  harnesses: ["research", "analysis", "monitoring", "business_process", "build"],
  description: "Read-only fetch of a compiled wiki page.",
});
