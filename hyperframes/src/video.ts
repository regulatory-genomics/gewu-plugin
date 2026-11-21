import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { join, relative, resolve, sep } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { StudioProject } from "./project.js";
import { freezeProject } from "./snapshot.js";
import { JobRunner } from "./jobs.js";
import { PluginError } from "./stdio.js";
import { definition } from "./tools.js";

const exec = promisify(execFile);
export const videoDefinitions = [
  definition(
    "frame.capture",
    "Render saved source at up to eight exact times. Read each returned PNG with the provided read_file arguments to actually see it.",
    {
      type: "object",
      properties: {
        times: { type: "array", items: { type: "number", minimum: 0 }, minItems: 1, maxItems: 8 },
      },
      required: ["times"],
      additionalProperties: false,
    },
  ),
  definition(
    "project.check",
    "Run native lint and browser audits on a frozen project revision. Read checksPassed and the full report, not just invocation success.",
    { type: "object", properties: {}, additionalProperties: false },
  ),
  definition(
    "render",
    "Render a revision-frozen MP4/WebM. Use plugin_call async:true for progress, and plugin_cancel to stop the worker.",
    {
      type: "object",
      properties: {
        format: { enum: ["mp4", "webm"] },
        quality: { enum: ["draft", "looks", "delivery"] },
        fps: { type: "integer", minimum: 1, maximum: 120 },
        compositionPath: { type: "string" },
      },
      additionalProperties: false,
    },
    "write",
  ),
];
export class VideoOperations {
  private busy = false;
  constructor(
    readonly project: StudioProject,
    readonly cli: string,
  ) {}
  resource(path: string, type: string, mimeType: string, revision: string) {
    const rel = relative(this.project.options.workspaceRoot, path).split(sep).join("/");
    if (rel.startsWith("../"))
      throw new PluginError("INVALID_RESOURCE", "Artifact is outside workspace");
    return {
      uri: `workspace:///${rel.split("/").map(encodeURIComponent).join("/")}`,
      type,
      mimeType,
      revision,
    };
  }
  async freeze() {
    const root = join(this.project.artifactRoot, "jobs", randomUUID());
    const source = await this.project.writes.run(async () => {
      const project = await this.project.server.adapter.resolveProject(this.project.id);
      const history = project && (await this.project.server.adapter.history?.(project));
      await history?.flush();
      return freezeProject(this.project.options.projectDir, join(root, "input"));
    });
    return { root, source };
  }
  async capture(times: number[], runner: JobRunner, signal: AbortSignal) {
    if (
      !Array.isArray(times) ||
      times.length < 1 ||
      times.length > 8 ||
      times.some((t) => !Number.isFinite(t) || t < 0)
    )
      throw new PluginError("INVALID_ARGUMENT", "times must contain 1–8 nonnegative seconds");
    const { root, source } = await this.freeze();
    const output = join(root, "frames");
    const result = await runner.run(
      this.cli,
      [
        "snapshot",
        source.directory,
        "--at",
        times.join(","),
        "--no-end",
        "--keep",
        "--json",
        "--output",
        output,
      ],
      source.directory,
      signal,
    );
    await writeFile(join(root, "capture.log"), result.stderr);
    if (result.code !== 0)
      throw new PluginError(
        "CAPTURE_FAILED",
        result.stderr.slice(-2000) || result.stdout.slice(-2000),
      );
    const data: unknown = JSON.parse(result.stdout);
    if (!data || typeof data !== "object" || !("frames" in data) || !Array.isArray(data.frames))
      throw new PluginError("CAPTURE_FAILED", "Snapshot returned no frames");
    const frames = [];
    for (const frame of data.frames) {
      if (
        !frame ||
        typeof frame !== "object" ||
        typeof frame.path !== "string" ||
        typeof frame.time !== "number"
      )
        throw new PluginError("CAPTURE_FAILED", "Invalid frame identity");
      const path = resolve(frame.path);
      if (!path.startsWith(output + sep))
        throw new PluginError("CAPTURE_FAILED", "Unexpected frame location");
      const bytes = await readFile(path);
      if (bytes.length < 8 || bytes.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a")
        throw new PluginError("CAPTURE_FAILED", "Invalid PNG");
      frames.push({
        time: frame.time,
        sourceRevision: source.revision,
        resource: this.resource(path, "hyperframes.frame", "image/png", source.revision),
        read_file: { file_path: relative(this.project.options.workspaceRoot, path) },
      });
    }
    return { sourceRevision: source.revision, frames };
  }
  async check(runner: JobRunner, signal: AbortSignal) {
    const { root, source } = await this.freeze();
    const result = await runner.run(
      this.cli,
      ["check", source.directory, "--json"],
      source.directory,
      signal,
    );
    let report: unknown;
    try {
      report = JSON.parse(result.stdout);
    } catch {
      report = { stdout: result.stdout, stderr: result.stderr };
    }
    const path = join(root, "check-report.json");
    await writeFile(path, JSON.stringify(report, null, 2));
    return {
      checksPassed: result.code === 0,
      exitCode: result.code,
      sourceRevision: source.revision,
      report: this.resource(path, "hyperframes.report", "application/json", source.revision),
      summary: JSON.stringify(report).slice(0, 6000),
    };
  }
  async render(
    {
      format = "mp4",
      quality = "looks",
      fps = 30,
      compositionPath,
    }: { format?: string; quality?: string; fps?: number; compositionPath?: string },
    runner: JobRunner,
    signal: AbortSignal,
  ) {
    if (this.busy)
      throw new PluginError("RENDER_BUSY", "This project already has a render running", true);
    if (
      !["mp4", "webm"].includes(format) ||
      !["draft", "looks", "delivery"].includes(quality) ||
      !Number.isInteger(fps) ||
      fps < 1 ||
      fps > 120
    )
      throw new PluginError("INVALID_ARGUMENT", "Unsupported format, quality or fps");
    this.busy = true;
    try {
      if (compositionPath) this.project.path(compositionPath);
      const { root, source } = await this.freeze(),
        partial = join(root, `partial.${format}`);
      const result = await runner.run(
        this.cli,
        [
          "render",
          source.directory,
          "--output",
          partial,
          "--format",
          format,
          "--quality",
          quality,
          "--fps",
          String(fps),
          "--workers",
          "1",
          "--json",
          ...(compositionPath ? ["--composition", compositionPath] : []),
        ],
        source.directory,
        signal,
      );
      await writeFile(join(root, "render.log"), result.stdout + "\n" + result.stderr);
      if (result.code !== 0)
        throw new PluginError(
          "RENDER_FAILED",
          result.stderr.slice(-2000) || result.stdout.slice(-2000),
        );
      if (!(await stat(partial)).size)
        throw new PluginError("RENDER_FAILED", "Render output is empty");
      const probe = await probeVideo(partial, signal);
      if (
        probe.duration <= 0 ||
        probe.width <= 0 ||
        probe.height <= 0 ||
        Math.abs(probe.fps - fps) > 0.02
      )
        throw new PluginError("INVALID_RENDER", "Output metadata does not match request");
      const decoded = join(root, "decoded.png");
      await exec(
        "ffmpeg",
        [
          "-hide_banner",
          "-loglevel",
          "error",
          "-ss",
          String(probe.duration / 2),
          "-i",
          partial,
          "-frames:v",
          "1",
          decoded,
        ],
        { signal, timeout: 60000 },
      );
      const final = join(root, `video.${format}`);
      await rename(partial, final);
      await writeFile(
        join(root, "artifact.json"),
        JSON.stringify({ sourceRevision: source.revision, format, quality, fps, probe }, null, 2),
      );
      return {
        sourceRevision: source.revision,
        probe,
        resource: this.resource(final, "hyperframes.video", `video/${format}`, source.revision),
        decodedFrame: this.resource(decoded, "hyperframes.frame", "image/png", source.revision),
      };
    } finally {
      this.busy = false;
    }
  }
}
async function probeVideo(path: string, signal: AbortSignal) {
  const { stdout } = await exec(
    "ffprobe",
    ["-v", "error", "-show_streams", "-show_format", "-of", "json", path],
    { signal, timeout: 60000, maxBuffer: 2 * 1024 * 1024 },
  );
  const data = JSON.parse(stdout),
    video = data.streams?.find((s: { codec_type?: string }) => s.codec_type === "video");
  if (!video) throw new PluginError("INVALID_RENDER", "No decodable video stream");
  const [n = 0, d = 0] = String(video.avg_frame_rate).split("/").map(Number);
  return {
    width: Number(video.width),
    height: Number(video.height),
    duration: Number(data.format?.duration),
    fps: n / d,
    audio: data.streams.some((s: { codec_type?: string }) => s.codec_type === "audio"),
  };
}
