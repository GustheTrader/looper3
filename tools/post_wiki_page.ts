import { defineTool } from "../src/policy/toolDefinition.js";

export const postWikiPageTool = defineTool({
  name: "post_wiki_page",
  tier: 3,
  reversible: true,
  harnesses: ["research", "analysis", "business_process"],
  description: "External wiki write, revertible by the agent with revert_wiki_page.",
});
