#!/usr/bin/env bun
/**
 * Builds the release package (no runtime is bundled).
 *
 * Usage:
 *   bun build-release.mjs --base-url https://plugin.example.cn --gewu <gewu-checkout> \
 *     [--allow-dirty] [--out dist/release]
 *
 * The package archive is produced by GeWu's deterministic packager; the runtime
 * plan declares an external provider only, and INSTALL.md tells the operator
 * which host dependencies to install.
 */
import { mkdir, readFile, writeFile, rm, cp, stat } from "node:fs/promises";
import { join, resolve } from "node:path";
import { execFileSync } from "node:child_process";

const args = process.argv.slice(2);
const value = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0
    ? args[i + 1]
    : (fallback ??
        (() => {
          throw Error(`Missing ${name}`);
        })());
};
const gewu = resolve(value("--gewu")),
  base = value("--base-url"),
  allowDirty = args.includes("--allow-dirty");
const out = resolve(value("--out", join(import.meta.dirname, "dist/release")));
const plugin = JSON.parse(await readFile(join(import.meta.dirname, "plugin.json"), "utf8"));
const staged = join(import.meta.dirname, "../../dist/gewu-hyperframes");
const run = (command, argv, cwd = import.meta.dirname) =>
  execFileSync(command, argv, { cwd, stdio: "inherit", encoding: "utf8" });
if (!(await stat(join(staged, "engine/cli/gewuAdapter.js")).catch(() => null)))
  throw Error("Run `bun run build` first");
await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
run(
  "python3",
  [
    join(gewu, "scripts/plugin-release.py"),
    "--descriptor",
    join(staged, "release.json"),
    "--source-root",
    staged,
    "--out",
    join(out, "package"),
    ...(allowDirty ? ["--allow-dirty"] : []),
  ],
  gewu,
);
run(process.execPath, [join(import.meta.dirname, "make-runtime-plan.mjs")]);
await cp(join(import.meta.dirname, "runtime-plan.json"), join(out, "runtime-plan.json"));
const packageMeta = JSON.parse(
  await readFile(join(out, "package", `${plugin.id}-${plugin.version}.package.json`), "utf8"),
);
const plan = JSON.parse(await readFile(join(out, "runtime-plan.json"), "utf8"));
const record = {
  plugin: plugin.id,
  version: plugin.version,
  package: packageMeta.package,
  runtimePlan: plan,
  policies: plan.policies,
  provenance: packageMeta.provenance,
  note: "No runtime is bundled: install Node >= 22, Chromium/chrome-headless-shell and FFmpeg/FFprobe on the host (see INSTALL.md).",
};
await writeFile(join(out, "release-record.json"), JSON.stringify(record, null, 2) + "\n");
console.log(JSON.stringify({ out, package: record.package, policies: record.policies }, null, 2));
