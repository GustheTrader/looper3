import { defineTool } from "../src/policy/toolDefinition.js";

export const sendTelegramAlertTool = defineTool({
  name: "send_telegram_alert",
  tier: 3,
  reversible: false,
  // PRD open question 3: whether this is the right compensating action is undecided.
  compensatingAction: "send_correction_message",
  harnesses: ["monitoring", "analysis", "business_process"],
  description: "External notification delivery. Cannot be unsent; a correction message is the only compensation.",
});
