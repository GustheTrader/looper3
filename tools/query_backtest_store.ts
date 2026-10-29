import { defineTool } from "../src/policy/toolDefinition.js";

export const queryBacktestStoreTool = defineTool({
  name: "query_backtest_store",
  tier: 1,
  reversible: true,
  harnesses: ["research", "analysis", "monitoring"],
  description: "Read-only query over stored backtest results.",
});
