// Fixture for the CI gate: a tool definition that never declared a tier.
// `npm run check:tiers` must reject this file.
export const legacyTool = {
  name: "legacy_tool",
  reversible: true,
  description: "Untiered tool definition (negative test fixture).",
};
