import { expect, it } from "vitest";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { JobRunner } from "../src/jobs";

it("stops a worker's whole process group and reaps it", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-job-"));
  const entry = join(root, "tree.mjs");
  writeFileSync(
    entry,
    `
import {spawn} from "node:child_process";
const grandchild=spawn(process.execPath,["-e","setInterval(()=>{},1000)"],{stdio:"ignore"});
console.log("ready "+grandchild.pid);
setInterval(()=>{},1000);`,
  );
  const abort = new AbortController();
  let grandchildPid = 0;
  const runner = new JobRunner(
    resolve(import.meta.dirname, "../src/worker.ts"),
    process.execPath,
    (line) => {
      const match = /ready (\d+)/.exec(line);
      if (match) {
        grandchildPid = Number(match[1]);
        abort.abort();
      }
    },
  );
  try {
    await expect(runner.run(entry, [], root, abort.signal)).rejects.toMatchObject({
      code: "CANCELLED",
    });
    expect(grandchildPid).toBeGreaterThan(0);
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(() => process.kill(grandchildPid, 0)).toThrow();
  } finally {
    await runner.close();
    rmSync(root, { recursive: true, force: true });
  }
});
