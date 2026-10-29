// Fixture for the CI gate: a non-tool module sitting inside a tools directory.
// The scan must fail loudly instead of silently skipping it.
export const HELPERS_VERSION = "1.0.0";

export function noop(): void {
  // intentionally empty
}
