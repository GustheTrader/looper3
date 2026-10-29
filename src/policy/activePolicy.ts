/**
 * Process-wide active policy config.
 *
 * Loaded lazily on first use and validated once. A failure here throws (fail closed) - there is
 * no looser fallback. Tests reset it with resetActivePolicy().
 */
import { assertPaperOnly, defaultPolicyPath, loadPolicyFromFile, type PolicyConfig } from "./config.js";

let cached: PolicyConfig | null = null;

export function activePolicy(): PolicyConfig {
  if (cached === null) {
    assertPaperOnly();
    cached = loadPolicyFromFile(defaultPolicyPath());
  }
  return cached;
}

/** Loads an explicit config (used by tests / future harness bootstrapping). */
export function setActivePolicy(config: PolicyConfig): void {
  cached = config;
}

export function resetActivePolicy(): void {
  cached = null;
}
