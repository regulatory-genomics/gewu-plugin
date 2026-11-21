#!/usr/bin/env node
/**
 * Release gate: build the package + runtime, then install it from a signed local
 * catalog in a throwaway HOME and re-run the protocol scenario.
 *
 * Requires GEWU_ROOT, GEWU_EVIDENCE (the directory holding
 * marketplace-acceptance.sh), GEWU_PACKAGE, GEWU_RUNTIME and GEWU_FIXTURE.
 */
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

const need = (name) => {
  const value = process.env[name];
  if (!value) throw Error(`Set ${name}`);
  return value;
};
const evidence = need("GEWU_EVIDENCE"),
  gewu = need("GEWU_ROOT");
const script = join(evidence, "marketplace-acceptance.sh");
if (!existsSync(script)) throw Error(`Missing ${script}`);
execFileSync("bash", [script], { stdio: "inherit", env: { ...process.env, GEWU_ROOT: gewu } });
