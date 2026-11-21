#!/usr/bin/env node
/** Runs the GeWu browser suite with this plugin's registered spec. */
import { execFileSync } from "node:child_process";
import { join } from "node:path";

const gewu = process.env.GEWU_ROOT;
if (!gewu) throw Error("Set GEWU_ROOT");
execFileSync(
  "npm",
  ["--prefix", join(gewu, "gewu-gui/tests/browser"), "test", "--", "application-plugin.spec.mjs"],
  { stdio: "inherit", env: process.env },
);
