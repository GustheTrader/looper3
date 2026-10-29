import { defineTool } from "../src/policy/toolDefinition.js";

/**
 * TRAP fixture required by the build brief (PRD P6): the name says "read" but the tool deletes.
 * A name-matching classifier would tier this 1. The static registry must keep it at 4.
 */
export const readAndDeleteTool = defineTool({
  name: "read_and_delete",
  tier: 4,
  reversible: false,
  harnesses: ["build", "business_process"],
  description: "Reads a file then deletes it. Destructive despite the misleading name.",
});
