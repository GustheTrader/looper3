import { defineTool } from "../src/policy/toolDefinition.js";

/**
 * STUB definition only. No broker/exchange adapter exists in this project and none may be added
 * in phase 1 - this exists solely so the execution harness has a money-touching tier-4 action.
 */
export const submitBrokerOrderStubTool = defineTool({
  name: "submit_broker_order_stub",
  tier: 4,
  reversible: false,
  touchesMoney: true,
  harnesses: ["execution"],
  description: "STUB: would submit a broker order. Not wired to any venue.",
});
