import { createHash, randomUUID } from "node:crypto";
import { existsSync, lstatSync, realpathSync } from "node:fs";
import { mkdir, writeFile, readFile, unlink } from "node:fs/promises";
import { basename, join, relative, resolve, sep } from "node:path";
import { AsyncLocalStorage } from "node:async_hooks";
import { createStudioServer } from "../../../packages/cli/src/server/studioServer.js";
import { PluginError } from "./stdio.js";
import { GSAP_CDN_VERSION } from "@hyperframes/core/gsap-cdn";
import { applyFileMutations } from "@hyperframes/studio-server";

export interface SourceChange {
  path: string;
  before: string;
  after: string;
  expectedRevision: string;
}

export function revision(content: string | Buffer) {
  return `"sha256:${createHash("sha256").update(content).digest("hex")}"`;
}

export class SerialWrites {
  private tail: Promise<unknown> = Promise.resolve();
  private context = new AsyncLocalStorage<boolean>();
  run<T>(work: () => Promise<T>): Promise<T> {
    if (this.context.getStore()) return work();
    const result = this.tail.then(() => this.context.run(true, work));
    this.tail = result.catch(() => {});
    return result;
  }
}

export interface ProjectOptions {
  workspaceRoot: string;
  projectDir: string;
  studioDir: string;
}
export class StudioProject {
  readonly id: string;
  readonly writes = new SerialWrites();
  readonly server: ReturnType<typeof createStudioServer>;
  readonly artifactRoot: string;
  readonly allowedHosts: string[] = [];
  constructor(readonly options: ProjectOptions) {
    const root = realpathSync(options.workspaceRoot),
      dir = realpathSync(options.projectDir);
    if (dir === root || !dir.startsWith(root + sep))
      throw new PluginError("INVALID_PATH", "Project must be a directory within the workspace");
    options.workspaceRoot = root;
    options.projectDir = dir;
    this.id = basename(dir);
    this.artifactRoot = join(
      root,
      ".gewu",
      "hyperframes",
      createHash("sha256").update(relative(root, dir)).digest("hex").slice(0, 24),
    );
    this.server = createStudioServer({
      projectDir: dir,
      projectName: this.id,
      historyRoot: join(this.artifactRoot, "history"),
      host: {
        studioDir: options.studioDir,
        allowedHosts: this.allowedHosts,
        parentOrigin: (request) => request.headers.get("x-gewu-parent-origin") ?? undefined,
      },
    });
  }
  path(path: string) {
    if (
      !path ||
      path.startsWith("/") ||
      path.includes("\\") ||
      path.includes("\0") ||
      path
        .split("/")
        .some(
          (p) =>
            !p ||
            p === "." ||
            p === ".." ||
            p.startsWith(".gewu") ||
            p === ".git" ||
            p === ".hyperframes",
        )
    )
      throw new PluginError(
        "INVALID_PATH",
        "Use a project-relative path without private or parent components",
      );
    let at = this.options.projectDir;
    for (const part of path.split("/")) {
      at = join(at, part);
      if (existsSync(at) && lstatSync(at).isSymbolicLink())
        throw new PluginError("INVALID_PATH", "Symlink paths are not supported");
    }
    return at;
  }
  request(path: string, init?: RequestInit) {
    const work = async () => this.server.app.request(`http://127.0.0.1${path}`, init);
    return !init?.method || ["GET", "HEAD"].includes(init.method) ? work() : this.writes.run(work);
  }
  async read(path: string) {
    this.path(path);
    const response = await this.request(
      `/api/projects/${encodeURIComponent(this.id)}/files/${encodeURIComponent(path)}`,
    );
    if (!response.ok)
      throw new PluginError(
        response.status === 404 ? "NOT_FOUND" : "READ_FAILED",
        `Cannot read ${path} (${response.status})`,
      );
    const data: unknown = await response.json();
    if (
      !data ||
      typeof data !== "object" ||
      !("content" in data) ||
      typeof data.content !== "string" ||
      !("version" in data) ||
      typeof data.version !== "string"
    )
      throw new PluginError("READ_FAILED", "Invalid file response");
    return { path, content: data.content, revision: data.version };
  }
  async write(
    path: string,
    content: string,
    expectedRevision: string | null,
    label = "Edited by agent",
  ) {
    this.path(path);
    if (Buffer.byteLength(content) > 512 * 1024)
      throw new PluginError("TOO_LARGE", "Source write exceeds 512 KiB");
    return this.writes.run(async () => {
      const project = await this.server.adapter.resolveProject(this.id);
      if (!project) throw new PluginError("NOT_FOUND", "Project is missing");
      const history = await this.server.adapter.history?.(project);
      if (!history) throw new PluginError("HISTORY_UNAVAILABLE", "Project history is unavailable");
      await history.flush();
      const token = randomUUID();
      const response = await this.request(
        `/api/projects/${encodeURIComponent(this.id)}/files/${encodeURIComponent(path)}`,
        {
          method: "PUT",
          headers: {
            "content-type": "text/plain",
            "X-Hyperframes-Write-Token": token,
            ...(expectedRevision === null
              ? { "If-None-Match": "*" }
              : { "If-Match": expectedRevision }),
          },
          body: content,
        },
      );
      if (response.status === 409)
        throw new PluginError(
          "REVISION_CONFLICT",
          `File ${path} changed; read its current version before editing`,
          true,
        );
      if (!response.ok)
        throw new PluginError(
          "WRITE_FAILED",
          `Save failed (${response.status}): ${(await response.text()).slice(0, 400)}`,
        );
      const receipt = (await response.json()) as { version: string };
      history.noteChange(path);
      const operation = await history.claim({ kind: "agent", name: "GeWu agent" }, label, [path], {
        overwrote: expectedRevision ? { [path]: expectedRevision } : undefined,
      });
      await history.flush();
      this.server.adapter.invalidateProjectSignature?.(this.options.projectDir);
      return { path, revision: receipt.version, operationId: operation?.id ?? null, saved: true };
    });
  }
  async commit(changes: SourceChange[], label: string) {
    return this.writes.run(async () => {
      const resolved = await this.server.adapter.resolveProject(this.id);
      const history = resolved && (await this.server.adapter.history?.(resolved));
      if (!history) throw new PluginError("HISTORY_UNAVAILABLE", "Native history is unavailable");
      await history.flush();
      const inputs = changes.map((c) => ({
        sourceFile: c.path,
        absPath: this.path(c.path),
        before: c.before,
        after: c.after,
        expectedVersion: c.expectedRevision,
      }));
      for (const input of inputs)
        if (revision(await readFile(input.absPath)) !== input.expectedVersion)
          throw new PluginError(
            "REVISION_CONFLICT",
            `${input.sourceFile} changed before commit`,
            true,
          );
      await mkdir(this.artifactRoot, { recursive: true });
      const journal = join(this.artifactRoot, "pending-transaction.json"),
        operationLabel = `${label} [${randomUUID()}]`;
      await writeFile(journal, JSON.stringify({ label: operationLabel, changes }), { flag: "wx" });
      try {
        const receipts = applyFileMutations(this.options.projectDir, inputs);
        for (const c of changes) history.noteChange(c.path);
        const entry = await history.claim(
          { kind: "agent", name: "GeWu agent" },
          operationLabel,
          changes.map((c) => c.path),
          { overwrote: Object.fromEntries(changes.map((c) => [c.path, c.expectedRevision])) },
        );
        await history.flush();
        await unlink(journal);
        this.server.adapter.invalidateProjectSignature?.(this.options.projectDir);
        return {
          saved: true,
          operationId: entry?.id ?? null,
          files: receipts.map((r) => ({
            path: r.sourceFile,
            revision: r.version,
            changed: r.changed,
          })),
        };
      } catch (error) {
        if (
          error instanceof Error &&
          error.message === "file changed since the timeline was read"
        ) {
          await unlink(journal);
          throw new PluginError("REVISION_CONFLICT", error.message, true);
        }
        throw new PluginError(
          "OUTCOME_UNKNOWN",
          `Transaction needs recovery: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
    });
  }
  async recover() {
    const journal = join(this.artifactRoot, "pending-transaction.json");
    if (!existsSync(journal)) return;
    await this.writes.run(async () => {
      const raw: unknown = JSON.parse(await readFile(journal, "utf8"));
      if (
        !raw ||
        typeof raw !== "object" ||
        !("changes" in raw) ||
        !Array.isArray(raw.changes) ||
        !("label" in raw) ||
        typeof raw.label !== "string"
      )
        throw new PluginError(
          "OUTCOME_UNKNOWN",
          "Malformed pending transaction; preserve the journal for recovery",
        );
      const changes: SourceChange[] = [];
      for (const item of raw.changes) {
        if (
          !item ||
          typeof item !== "object" ||
          typeof item.path !== "string" ||
          typeof item.before !== "string" ||
          typeof item.after !== "string" ||
          typeof item.expectedRevision !== "string"
        )
          throw new PluginError("OUTCOME_UNKNOWN", "Invalid pending transaction");
        this.path(item.path);
        changes.push(item);
      }
      const current = await Promise.all(changes.map((c) => readFile(this.path(c.path), "utf8")));
      if (current.some((text, i) => text !== changes[i]!.before && text !== changes[i]!.after))
        throw new PluginError(
          "OUTCOME_UNKNOWN",
          "External edits conflict with pending recovery; journal preserved",
        );
      if (current.every((text, i) => text === changes[i]!.after)) {
        const project = await this.server.adapter.resolveProject(this.id),
          history = project && (await this.server.adapter.history?.(project));
        if (!history)
          throw new PluginError("HISTORY_UNAVAILABLE", "Recovery requires native history");
        if (!history.list().some((entry) => entry.label === raw.label))
          await history.claim(
            { kind: "agent", name: "GeWu agent" },
            raw.label,
            changes.map((c) => c.path),
            { overwrote: Object.fromEntries(changes.map((c) => [c.path, c.expectedRevision])) },
          );
        await history.flush();
      } else {
        applyFileMutations(
          this.options.projectDir,
          changes
            .filter((c, i) => current[i] === c.after)
            .map((c) => ({
              sourceFile: c.path,
              absPath: this.path(c.path),
              before: c.after,
              after: c.before,
              expectedVersion: revision(c.after),
            })),
        );
      }
      await unlink(journal);
    });
  }
  async create({
    width = 1920,
    height = 1080,
    fps = 30,
    duration = 10,
  }: {
    width?: number;
    height?: number;
    fps?: number;
    duration?: number;
  }) {
    return this.writes.run(async () => {
      for (const [key, value, max] of [
        ["width", width, 7680],
        ["height", height, 7680],
        ["fps", fps, 120],
        ["duration", duration, 3600],
      ] as const)
        if (!Number.isFinite(value) || value <= 0 || value > max)
          throw new PluginError("INVALID_ARGUMENT", `${key} must be positive and at most ${max}`);
      if (
        existsSync(join(this.options.projectDir, "index.html")) ||
        existsSync(join(this.options.projectDir, "hyperframes.json"))
      )
        throw new PluginError(
          "ALREADY_EXISTS",
          "Project already has source; create never overwrites it",
        );
      await mkdir(join(this.options.projectDir, "assets"), { recursive: true });
      const html = `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;width:${width}px;height:${height}px;overflow:hidden;background:#132238;color:#fff;font-family:sans-serif}.clip{position:absolute}h1{left:${width / 16}px;top:${height / 3}px;font-size:${width / 20}px}</style><script src="assets/gsap.min.js"></script></head><body><div data-composition-id="main" data-width="${width}" data-height="${height}" data-fps="${fps}" data-duration="${duration}"><h1 id="title" data-hf-id="hf-title" class="clip" data-start="0" data-duration="${duration}" data-track-index="0">Your video</h1></div><script>const tl=gsap.timeline({paused:true});tl.fromTo('#title',{opacity:0,y:50},{opacity:1,y:0,duration:1});window.__timelines={main:tl};</script></body></html>`;
      const gsapResponse = await this.request(
        `/api/vendor/npm/gsap@${GSAP_CDN_VERSION}/dist/gsap.min.js`,
      );
      if (!gsapResponse.ok)
        throw new PluginError("DEPENDENCY_MISSING", "Bundled GSAP runtime is missing");
      const gsap = await gsapResponse.text();
      await this.write("assets/gsap.min.js", gsap, null, "Added local animation runtime");
      // Studio protects executable project config; only initialization creates it.
      await writeFile(
        join(this.options.projectDir, "hyperframes.json"),
        JSON.stringify({ name: this.id }, null, 2),
        { flag: "wx" },
      );
      return this.write("index.html", html, null, "Created video composition");
    });
  }
  async close() {
    await this.server.watcher.close();
    await this.server.shutdown();
  }
}
