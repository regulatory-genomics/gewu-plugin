import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StudioProject } from "../src/project";
import { importAsset } from "../src/assets";

it("imports workspace bytes without overwriting a target or reading a symlink outside the workspace", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-asset-")),
    dir = join(root, "video");
  mkdirSync(dir);
  const p = new StudioProject({
    workspaceRoot: root,
    projectDir: dir,
    studioDir: join(root, "web"),
  });
  try {
    await p.create({});
    writeFileSync(join(root, "input.dat"), Buffer.from([0, 1, 255]));
    await importAsset(p, "input.dat", "assets/data.bin");
    expect(readFileSync(join(dir, "assets/data.bin"))).toEqual(Buffer.from([0, 1, 255]));
    await expect(importAsset(p, "input.dat", "assets/data.bin")).rejects.toMatchObject({
      code: "ALREADY_EXISTS",
    });
    symlinkSync("/etc/passwd", join(root, "outside"));
    await expect(importAsset(p, "outside", "assets/passwords")).rejects.toMatchObject({
      code: "INVALID_PATH",
    });
  } finally {
    await p.close();
    rmSync(root, { recursive: true, force: true });
  }
});
