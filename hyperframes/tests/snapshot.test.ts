import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { freezeProject } from "../src/snapshot";

it("copies actual source bytes into a stable input manifest and refuses symlink escapes", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-snapshot-")),
    src = join(root, "project"),
    out = join(root, "snapshot");
  mkdirSync(src);
  writeFileSync(join(src, "index.html"), "original");
  try {
    const snapshot = await freezeProject(src, out);
    writeFileSync(join(src, "index.html"), "edited while rendering");
    expect(readFileSync(join(out, "index.html"), "utf8")).toBe("original");
    expect(snapshot.files).toHaveLength(1);
    symlinkSync(root, join(src, "escape"));
    await expect(freezeProject(src, join(root, "other"))).rejects.toMatchObject({
      code: "INVALID_PATH",
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
