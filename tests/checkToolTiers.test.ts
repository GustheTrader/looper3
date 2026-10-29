import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { checkToolTiers, listToolFiles, main, parseToolsDir } from "../scripts/check-tool-tiers.js";
import { TOOL_TIER } from "../src/policy/toolTiers.js";

const root = fileURLToPath(new URL("../", import.meta.url));
const toolsDir = path.join(root, "tools");
const fixture = (name: string): string => path.join(root, "test", "fixtures", name);

async function silent<T>(work: () => Promise<T>): Promise<T> {
  const log = vi.spyOn(console, "log").mockImplementation(() => {});
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  try {
    return await work();
  } finally {
    log.mockRestore();
    error.mockRestore();
    warn.mockRestore();
  }
}

describe("check-tool-tiers on the real tools directory", () => {
  it("passes: every tool definition is tiered and matches the registry", async () => {
    const result = await checkToolTiers({ toolsDir });

    expect(result.errors).toEqual([]);
    expect(result.ok).toBe(true);
    expect(result.tools.length).toBe(Object.keys(TOOL_TIER).length);
    expect(result.tools.every((tool) => tool.tier !== null)).toBe(true);
    expect(result.warnings).toEqual([]);
  });

  it("scans every non-underscore .ts file in tools/", async () => {
    const files = listToolFiles(toolsDir);
    expect(files.length).toBe(Object.keys(TOOL_TIER).length);
    expect(files.every((file) => file.endsWith(".ts"))).toBe(true);
    expect(files.some((file) => path.basename(file).startsWith("_"))).toBe(false);
  });

  it("is wired to `npm run check:tiers`", () => {
    const pkg = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8")) as {
      scripts?: Record<string, string>;
    };
    expect(pkg.scripts?.["check:tiers"]).toContain("check-tool-tiers");
  });
});

describe("check-tool-tiers blocks bad definitions (CI fixture proofs)", () => {
  it("fails when a tool definition declares no tier", async () => {
    const result = await checkToolTiers({ toolsDir: fixture("tools-missing-tier") });

    expect(result.ok).toBe(false);
    expect(result.tools.map((tool) => tool.name)).toEqual(["legacy_tool"]);
    expect(result.tools[0]?.tier).toBeNull();
    expect(result.errors.join("\n")).toMatch(/legacy_tool: MISSING\/INVALID TIER/);
  });

  it("fails when a definition drifts from the registry", async () => {
    const result = await checkToolTiers({ toolsDir: fixture("tools-tier-drift") });

    expect(result.ok).toBe(false);
    expect(result.errors.join("\n")).toMatch(/tier drift: definition declares 3, registry says 2/);
  });

  it("fails when a well-formed definition has no registry entry", async () => {
    const result = await checkToolTiers({ toolsDir: fixture("tools-unregistered") });

    expect(result.ok).toBe(false);
    const errors = result.errors.join("\n");
    expect(errors).toMatch(/orphan_tool: not present in TOOL_TIER registry/);
    // `_helper.ts` is a helper module and must not be scanned as an untiered tool.
    expect(errors).not.toMatch(/helper_not_a_tool/);
    expect(result.files.length).toBe(1);
  });

  it("fails when the tools directory holds a non-tool module", async () => {
    const result = await checkToolTiers({ toolsDir: fixture("tools-no-definitions") });

    expect(result.ok).toBe(false);
    expect(result.errors.join("\n")).toMatch(/no tool definition exported/);
  });

  it("fails when the tools directory does not exist", async () => {
    const result = await checkToolTiers({ toolsDir: fixture("does-not-exist") });

    expect(result.ok).toBe(false);
    expect(result.errors.join("\n")).toMatch(/cannot scan tools directory/);
  });
});

describe("check-tool-tiers CLI", () => {
  it("exits 0 for the real tools directory", async () => {
    const code = await silent(() => main(["--tools", toolsDir]));
    expect(code).toBe(0);
  });

  it("exits 1 for an untiered tool", async () => {
    const code = await silent(() => main(["--tools", fixture("tools-missing-tier")]));
    expect(code).toBe(1);
  });

  it("exits 2 on a bad argument", async () => {
    const code = await silent(() => main(["--tools"]));
    expect(code).toBe(2);
  });

  it("resolves the tools directory from --tools, --tools= and the default", () => {
    expect(parseToolsDir(["--tools", "tools"], root)).toBe(path.join(root, "tools"));
    expect(parseToolsDir(["--tools=test/fixtures/tools-tier-drift"], root)).toBe(
      path.join(root, "test", "fixtures", "tools-tier-drift"),
    );
    expect(parseToolsDir([], root)).toBe(path.join(root, "tools"));
    expect(() => parseToolsDir(["--tools"], root)).toThrow(/requires a directory/);
  });
});
