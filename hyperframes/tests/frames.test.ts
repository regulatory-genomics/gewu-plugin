import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { StudioProject } from "../src/project";
import { JobRunner } from "../src/jobs";
import { VideoOperations } from "../src/video";

it("captures real revision-bound PNGs and exports a decodable movie", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-real-render-")),
    dir = join(root, "video");
  mkdirSync(dir);
  const project = new StudioProject({
    workspaceRoot: root,
    projectDir: dir,
    studioDir: join(root, "web"),
  });
  const cli = resolve(import.meta.dirname, "../../../packages/cli/dist/cli.js");
  const runner = new JobRunner(
    resolve(import.meta.dirname, "../../../packages/cli/dist/gewuWorker.js"),
    process.execPath,
    async () => {},
  );
  const video = new VideoOperations(project, cli);
  try {
    await project.create({ width: 640, height: 360, duration: 2 });
    const frames = await video.capture([0.1, 1.5], runner, new AbortController().signal);
    expect(frames.frames).toHaveLength(2);
    for (const frame of frames.frames) {
      expect(readFileSync(join(root, frame.read_file.file_path)).subarray(0, 8)).toEqual(
        Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
      );
    }
    const render = await video.render(
      { format: "mp4", quality: "draft", fps: 30 },
      runner,
      new AbortController().signal,
    );
    expect(render.probe.width).toBe(640);
    expect(render.probe.height).toBe(360);
    expect(render.probe.duration).toBeGreaterThan(1.9);
  } finally {
    await runner.close();
    await project.close();
    rmSync(root, { recursive: true, force: true });
  }
}, 300000);
