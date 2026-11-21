#!/usr/bin/env bun
/**
 * Writes the external-only runtime plan: GeWu checks the host's Node, Chromium
 * and FFmpeg instead of downloading a packed runtime. Nothing is bundled.
 *
 * Usage: bun make-runtime-plan.mjs
 */
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

const plugin = JSON.parse(await readFile(join(import.meta.dirname, "plugin.json"), "utf8"));
// The probe runs with the selected executable (node) and the host PATH.
const probe = [
  "const major=Number(process.versions.node.split('.')[0]);",
  "if(!Number.isFinite(major)||major<22){console.error('Node.js >= 22 is required, found '+process.versions.node);process.exit(1);}",
  "console.log('hyperframes runtime: node '+process.versions.node);",
].join("");
const plan = {
  schemaVersion: "gewu.runtime-plan/v1",
  policies: ["external"],
  providers: [
    {
      kind: "external",
      id: "hyperframes-node",
      target: { os: "linux", arch: "x86_64", libc: "glibc" },
      executable: { env: "GEWU_HYPERFRAMES_NODE", commands: ["node", "nodejs"] },
      probe: { args: ["-e", probe], timeoutSeconds: 120 },
      binaries: { node: "{executable}" },
    },
  ],
};
const out = join(import.meta.dirname, "runtime-plan.json");
await writeFile(out, JSON.stringify(plan, null, 2) + "\n");
console.log(JSON.stringify({ out, policies: plan.policies, plugin: plugin.id }, null, 2));
