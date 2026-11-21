#!/usr/bin/env node
/**
 * Full GeWu journey gate: native protocol conformance, then the real browser
 * spec against a live GeWu server + GUI.
 *
 * Required env:
 *   GEWU_ROOT      GeWu checkout under test
 *   GEWU_PACKAGE   staged plugin package (dist/gewu-hyperframes)
 *   GEWU_RUNTIME   packed runtime root (Node, Chromium, FFmpeg)
 *   GEWU_FIXTURE   fixture project directory (video/)
 *   GEWU_EVIDENCE  directory for screenshots, the MP4 and probe JSON
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";

const need = (name) => {
  const value = process.env[name];
  if (!value) throw Error(`Set ${name}`);
  return value;
};
const gewu = need("GEWU_ROOT"),
  pkg = need("GEWU_PACKAGE"),
  runtime = need("GEWU_RUNTIME"),
  fixture = need("GEWU_FIXTURE"),
  evidence = need("GEWU_EVIDENCE");
const plugin = dirname(new URL(import.meta.url).pathname);
const workspace = mkdtempSync(join(tmpdir(), "hf-e2e-"));
const settings = join(workspace, "settings.json");
writeFileSync(settings, "{}\n");
try {
  execFileSync(
    process.env.PYTHON ?? "python3",
    [
      join(gewu, "scripts/plugin-conformance.py"),
      "--package",
      pkg,
      "--settings",
      settings,
      "--workspace",
      workspace,
      "--scenario",
      join(plugin, "..", "fixtures/contract.json"),
      "--schema-root",
      join(gewu, "gewu-protocol/schema"),
    ],
    { stdio: "inherit" },
  );
  execFileSync(process.execPath, [join(plugin, "run-browser-spec.mjs")], {
    stdio: "inherit",
    env: {
      ...process.env,
      GEWU_APP_PLUGIN: pkg,
      GEWU_APP_SPEC: join(plugin, "gui.spec.mjs"),
      GEWU_HYPERFRAMES_RUNTIME: runtime,
      GEWU_HYPERFRAMES_FIXTURE: fixture,
      GEWU_EVIDENCE: evidence,
      GEWU_BIN: process.env.GEWU_BIN ?? join(gewu, "target/debug/gewu"),
      GEWU_ROOT: gewu,
    },
  });
} finally {
  rmSync(workspace, { recursive: true, force: true });
}
