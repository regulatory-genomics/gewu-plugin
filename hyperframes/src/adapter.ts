import { spawnSync } from "node:child_process";
import { existsSync, realpathSync } from "node:fs";
import { join, resolve, relative, sep } from "node:path";
import Ajv from "ajv";
import { StudioProject } from "./project.js";
import { PluginServer } from "./studio-server.js";
import { AuthoringTools, authoringDefinitions, definition, text } from "./tools.js";
import { PluginError, failure, type Frame } from "./stdio.js";
import uiTools from "../ui-tools.json";
import { VideoOperations, videoDefinitions } from "./video.js";
import { JobRunner } from "./jobs.js";
import { Catalog } from "./catalog.js";

export const definitions = [
  ...authoringDefinitions,
  ...videoDefinitions,
  ...uiTools.map((t) => ({
    name: t.name,
    description: t.description,
    inputSchema: t.inputSchema,
    outputSchema: { type: "object" },
    exposeToAgent: true,
    effect: t.annotations?.readOnlyHint ? "read" : "write",
    target: "view",
  })),
  definition(
    "preview.context",
    "Read the current focused Studio selection and playhead.",
    { type: "object", properties: {}, additionalProperties: false },
    "read",
    "view",
  ),
  ...["view.attach", "view.detach"].map((name) => ({
    ...definition(name, "Bind a human Studio view", {
      type: "object",
      properties: {},
      additionalProperties: false,
    }),
    exposeToAgent: false,
  })),
];

export class Adapter {
  project?: StudioProject;
  server?: PluginServer;
  private authoring?: AuthoringTools;
  private video?: VideoOperations;
  private runners = new Set<JobRunner>();
  private active = new Map<string, AbortController>();
  private validators = new Map(
    definitions.map((d) => [
      d.name,
      new Ajv({ strict: false, allowUnionTypes: true }).compile(d.inputSchema),
    ]),
  );
  constructor(
    readonly packageRoot: string,
    readonly emit: (id: string, event: Record<string, unknown>) => void = () => {},
    readonly runtimeRoot?: string,
  ) {}
  private engine(name: string) {
    return join(this.runtimeRoot ?? this.packageRoot, "engine/cli", name);
  }
  private runner(requestId: string, callId: string) {
    const runner = new JobRunner(this.engine("gewuWorker.js"), process.execPath, (message) =>
      this.emit(requestId, { type: "progress", callId, message }),
    );
    this.runners.add(runner);
    return runner;
  }
  /**
   * New bundled releases pin executable paths. Legacy local packages can
   * still resolve explicit settings or executables from the host.
   */
  private configureDependencies(configured: (name: string) => string | undefined): void {
    const chrome = this.runtimeRoot
      ? join(this.runtimeRoot, "chrome/chrome-headless-shell")
      : configured("chromePath");
    const ffmpeg = this.runtimeRoot
      ? join(this.runtimeRoot, "bin/ffmpeg")
      : configured("ffmpegPath");
    const ffprobe = this.runtimeRoot
      ? join(this.runtimeRoot, "bin/ffprobe")
      : configured("ffprobePath");
    for (const [label, value] of [
      ["chromePath", chrome],
      ["ffmpegPath", ffmpeg],
      ["ffprobePath", ffprobe],
    ] as const)
      if (value && !existsSync(value))
        throw new PluginError("DEPENDENCY_MISSING", `${label} does not exist: ${value}`);
    if (chrome) {
      process.env.HYPERFRAMES_BROWSER_PATH = chrome;
      process.env.PRODUCER_HEADLESS_SHELL_PATH = chrome;
    }
    if (ffmpeg) process.env.HYPERFRAMES_FFMPEG_PATH = ffmpeg;
    if (ffprobe) process.env.HYPERFRAMES_FFPROBE_PATH = ffprobe;
    const nodeMajor = Number(process.versions.node.split(".")[0]);
    if (!Number.isFinite(nodeMajor) || nodeMajor < 22)
      throw new PluginError(
        "DEPENDENCY_MISSING",
        `Node.js >= 22 is required (running ${process.versions.node})`,
      );
    // GeWu cannot pass settings to the runtime probe, so the real dependency
    // check belongs here: the adapter sees both settings and the host PATH.
    if (!chrome)
      this.requireExecutable(
        "Chromium/chrome-headless-shell",
        "chromePath",
        ["chrome", "chromium", "chromium-browser", "google-chrome", "chrome-headless-shell"],
        "--version",
      );
    if (!ffmpeg) this.requireExecutable("FFmpeg", "ffmpegPath", ["ffmpeg"], "-version");
    if (!ffprobe) this.requireExecutable("FFprobe", "ffprobePath", ["ffprobe"], "-version");
  }

  private requireExecutable(
    label: string,
    setting: string,
    commands: string[],
    flag: string,
  ): void {
    for (const command of commands)
      if (spawnSync(command, [flag], { stdio: "ignore", timeout: 15000 }).status === 0) return;
    throw new PluginError(
      "DEPENDENCY_MISSING",
      `${label} not found on PATH. Install it or set the "${setting}" plugin setting (see INSTALL.md).`,
    );
  }
  async handle(frame: Frame): Promise<unknown> {
    const p = frame.params;
    if (frame.op === "activate") {
      if (this.project) throw new PluginError("ALREADY_ACTIVE", "Adapter already active");
      if (p.apiVersion !== "gewu.plugin/v1")
        throw new PluginError("UNSUPPORTED_VERSION", "Expected gewu.plugin/v1");
      const workspaceRoot = realpathSync(text(p, "workspaceRoot"));
      const settings = p.settings && typeof p.settings === "object" ? p.settings : {};
      const configured = (name: string) =>
        name in settings && typeof Reflect.get(settings, name) === "string"
          ? String(Reflect.get(settings, name))
          : undefined;
      this.configureDependencies(configured);
      process.env.HYPERFRAMES_NO_TELEMETRY = "1";
      const binding = p.bindingResource;
      if (
        !binding ||
        typeof binding !== "object" ||
        !("uri" in binding) ||
        typeof binding.uri !== "string" ||
        !binding.uri.startsWith("workspace:///")
      )
        throw new PluginError("INVALID_RESOURCE", "Bind an existing workspace project directory");
      const projectDir = resolve(
        workspaceRoot,
        decodeURIComponent(binding.uri.slice("workspace:///".length)),
      );
      this.project = new StudioProject({
        workspaceRoot,
        projectDir,
        studioDir: join(this.packageRoot, "web"),
      });
      await this.project.recover();
      const catalog = new Catalog(join(this.packageRoot, "registry"));
      this.authoring = new AuthoringTools(this.project, catalog);
      this.project.server.adapter.listRegistryCatalog = () => catalog.list();
      this.project.server.adapter.installRegistryBlock = async ({ blockName }) => {
        const result = await catalog.add(this.project!, blockName);
        return { written: result.files, block: result.block };
      };
      this.server = new PluginServer(this.project);
      await this.server.start();
      this.video = new VideoOperations(this.project, this.engine("cli.js"));
      return {
        tools: definitions,
        serviceEndpoints: [
          {
            id: "studio",
            protocol: "http",
            baseUrl: this.server.origin,
            headers: { "x-gewu-ingress": this.server.ingress },
            routes: [
              { pathPrefix: "/", methods: ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] },
            ],
          },
        ],
      };
    }
    if (frame.op === "deactivate") {
      await this.close();
      return { stopped: true };
    }
    if (frame.op === "cancel") {
      const controller = this.active.get(text(p, "callId"));
      if (!controller) return { state: "not_found" };
      controller.abort();
      return { state: "requested" };
    }
    if (!this.project || !this.server || !this.authoring)
      throw new PluginError("NOT_ACTIVE", "Activate the adapter first");
    if (frame.op === "resolve") {
      const uri = text(p, "uri");
      if (!uri.startsWith("workspace:///"))
        throw new PluginError("INVALID_RESOURCE", "Only workspace artifacts are resolvable");
      const path = decodeURIComponent(uri.slice(13));
      const resolved = realpathSync(join(this.project.options.workspaceRoot, path));
      if (!resolved.startsWith(this.project.options.workspaceRoot + sep))
        throw new PluginError("INVALID_RESOURCE", "Resource escapes workspace");
      return {
        kind: "file",
        path: relative(this.project.options.workspaceRoot, resolved),
        mimeType: p.mimeType ?? "application/octet-stream",
        revision: p.revision ?? "current",
      };
    }
    if (frame.op !== "call")
      throw new PluginError("UNKNOWN_OPERATION", `Unsupported operation ${frame.op}`);
    const tool = text(p, "tool"),
      args = p.args,
      context = p.context;
    if (
      !args ||
      typeof args !== "object" ||
      Array.isArray(args) ||
      !context ||
      typeof context !== "object"
    )
      throw new PluginError("INVALID_ARGUMENT", "call requires args and context objects");
    const callId = Reflect.get(context, "callId");
    if (typeof callId !== "string") throw new PluginError("INVALID_ARGUMENT", "callId is required");
    const viewId = Reflect.get(context, "viewId"),
      view = typeof viewId === "string" ? viewId : undefined;
    const epoch = Reflect.get(context, "viewEpoch");
    const controller = new AbortController();
    this.active.set(callId, controller);
    try {
      const validate = this.validators.get(tool);
      if (!validate) throw new PluginError("UNKNOWN_TOOL", `Unknown tool ${tool}`);
      if (!validate(args))
        throw new PluginError("INVALID_ARGUMENT", new Ajv().errorsText(validate.errors));
      let data: unknown;
      const resources: unknown[] = [];
      if (tool === "view.attach") {
        if (!view || typeof epoch !== "number")
          throw new PluginError("INVALID_ARGUMENT", "View metadata required");
        data = { viewKey: this.server.control.attach(view, epoch), projectId: this.project.id };
      } else if (tool === "view.detach") {
        if (view) this.server.control.detach(view);
        data = { detached: true };
      } else if (tool === "frame.capture" || tool === "project.check" || tool === "render") {
        const runner = this.runner(frame.id, callId);
        try {
          if (tool === "frame.capture") {
            if (
              !("times" in args) ||
              !Array.isArray(args.times) ||
              !args.times.every((t): t is number => typeof t === "number")
            )
              throw new PluginError("INVALID_ARGUMENT", "times must be numbers");
            const captured = await this.video!.capture(args.times, runner, controller.signal);
            data = captured;
            resources.push(...captured.frames.map((f) => f.resource));
          } else if (tool === "project.check") {
            const checked = await this.video!.check(runner, controller.signal);
            data = checked;
            resources.push(checked.report);
          } else {
            const rendered = await this.video!.render(args, runner, controller.signal);
            data = rendered;
            resources.push(rendered.resource, rendered.decodedFrame);
          }
        } finally {
          await runner.close();
          this.runners.delete(runner);
        }
      } else if (tool.startsWith("studio_") || tool === "preview.context") {
        data = await this.server.control.call(
          view,
          tool === "preview.context" ? "studio_look" : tool,
          args,
          controller.signal,
        );
        if (
          tool === "studio_frame" &&
          data &&
          typeof data === "object" &&
          Reflect.get(data, "ok") === true
        ) {
          const time = Reflect.get(data, "time");
          if (typeof time !== "number")
            throw new PluginError("CAPTURE_FAILED", "Studio returned no frame time");
          const runner = this.runner(frame.id, callId);
          try {
            const captured = await this.video!.capture([time], runner, controller.signal);
            data = captured;
            resources.push(...captured.frames.map((f) => f.resource));
          } finally {
            await runner.close();
            this.runners.delete(runner);
          }
        }
      } else data = await this.authoring.call(tool, args);
      if (data && typeof data === "object" && Reflect.get(data, "ok") === false)
        throw new PluginError(
          "STUDIO_OPERATION_FAILED",
          String(Reflect.get(data, "reason") ?? "Studio did not apply the operation"),
        );
      return { ok: true, data, resources };
    } catch (error) {
      return { ok: false, error: failure(error), resources: [] };
    } finally {
      this.active.delete(callId);
    }
  }
  async close() {
    for (const c of this.active.values()) c.abort();
    await Promise.allSettled([...this.runners].map((r) => r.close()));
    if (this.server) await this.server.close();
    this.server = undefined;
    this.project = undefined;
  }
}
