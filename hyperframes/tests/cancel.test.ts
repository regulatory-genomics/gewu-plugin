import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, readdirSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { StudioProject } from "../src/project";
import { JobRunner } from "../src/jobs";
import { VideoOperations } from "../src/video";

it("cancels an in-flight render without publishing a video", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-cancel-")),
    dir = join(root, "video");
  mkdirSync(dir);
  const project = new StudioProject({
    workspaceRoot: root,
    projectDir: dir,
    studioDir: join(root, "web"),
  });
  const runner = new JobRunner(
    resolve(import.meta.dirname, "../../../packages/cli/dist/gewuWorker.js"),
    process.execPath,
  );
  const video = new VideoOperations(
    project,
    resolve(import.meta.dirname, "../../../packages/cli/dist/cli.js"),
  );
  const abort = new AbortController();
  try {
    await project.create({ width: 640, height: 360, duration: 3 });
    setTimeout(() => abort.abort(), 1500);
    const started = Date.now();
    await expect(
      video.render({ format: "mp4", quality: "delivery", fps: 30 }, runner, abort.signal),
    ).rejects.toSatisfy((error) => ["CANCELLED", "OUTCOME_UNKNOWN"].includes(error.code));
    expect(Date.now() - started).toBeLessThan(120000);
    const jobs = join(project.artifactRoot, "jobs");
    const found = existsSync(jobs) ? readdirSync(jobs, { recursive: true }).map(String) : [];
    expect(found.some((path) => path.endsWith("video.mp4"))).toBe(false);
    await expect(
      video.render(
        { format: "mp4", quality: "draft", fps: 30 },
        runner,
        new AbortController().signal,
      ),
    ).resolves.toMatchObject({ probe: { width: 640, height: 360 } });
  } finally {
    await runner.close();
    await project.close();
    rmSync(root, { recursive: true, force: true });
  }
}, 300000);
