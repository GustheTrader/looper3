import { defineTool } from "../src/policy/toolDefinition.js";

export const mergePullRequestTool = defineTool({
  name: "merge_pull_request",
  tier: 4,
  reversible: false,
  harnesses: ["build"],
  description: "Irreversible repository mutation.",
});
