import { Hono } from "hono";
import { serve, type ServerType } from "@hono/node-server";
import { randomUUID } from "node:crypto";
import { StudioProject } from "./project.js";
import { ControlHub } from "./control.js";
import { failure, PluginError } from "./stdio.js";

export class PluginServer {
  readonly ingress = randomUUID();
  readonly control = new ControlHub();
  readonly app = new Hono();
  server?: ServerType;
  origin = "";
  constructor(readonly project: StudioProject) {
    this.app.use(async (c, next) => {
      if (c.req.header("x-gewu-ingress") !== this.ingress) return c.text("Not found", 404);
      const supplied = c.req.header("x-gewu-app-origin");
      if (supplied) {
        const origin = new URL(supplied);
        if (origin.origin !== supplied || !["http:", "https:"].includes(origin.protocol))
          return c.text("Invalid origin", 403);
        if (!project.allowedHosts.includes(origin.host)) project.allowedHosts.push(origin.host);
      }
      if (c.req.method === "GET" || c.req.method === "HEAD") return next();
      if (c.req.path.startsWith("/__gewu/")) return next();
      return project.writes.run(async () => {
        await next();
        return c.res;
      });
    });
    this.app.onError((error, c) =>
      c.json({ error: failure(error) }, error instanceof PluginError ? 400 : 500),
    );
    this.app.get("/__gewu/control", (c) => {
      const view = c.req.query("viewId") ?? "",
        key = c.req.header("x-gewu-view-key") ?? "";
      return new Response(this.control.connect(view, key), {
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
      });
    });
    this.app.post("/__gewu/result", async (c) => {
      const body: unknown = await c.req.json();
      if (
        !body ||
        typeof body !== "object" ||
        !("viewId" in body) ||
        typeof body.viewId !== "string" ||
        !("id" in body) ||
        typeof body.id !== "string"
      )
        throw new PluginError("INVALID_ARGUMENT", "Invalid command result");
      this.control.result(
        body.viewId,
        c.req.header("x-gewu-view-key") ?? "",
        body.id,
        Reflect.get(body, "result"),
      );
      return c.json({ received: true });
    });
    // Studio probes these optional host features on load. GeWu owns the
    // project and views, so neither the desktop handoff nor local share exists.
    this.app.get("/api/open-in-desktop", (c) =>
      c.json({
        available: false,
        handoff: false,
        downloadUrl: null,
        os: "linux",
        installed: false,
      }),
    );
    this.app.post("/api/open-in-desktop", (c) =>
      c.json({ opened: false, reason: "handoff-unavailable", downloadUrl: null }),
    );
    this.app.all("/api/share/*", (c) =>
      c.json(
        {
          code: "unavailable",
          message: "Local sharing is unavailable in the GeWu Studio host.",
        },
        501,
      ),
    );
    this.app.use(async (c, next) => {
      const p = c.req.path;
      if (c.req.method === "POST" && p.endsWith("/render"))
        return c.json(
          {
            error:
              "Use the GeWu Export action so rendering has host-owned progress and cancellation",
          },
          409,
        );
      const projectPath = `/api/projects/${encodeURIComponent(project.id)}`;
      const allowed =
        p === "/" ||
        p === "/index.html" ||
        p === "/favicon.svg" ||
        p.startsWith("/assets/") ||
        p.startsWith("/icons/") ||
        p === "/api/projects" ||
        p === projectPath ||
        p.startsWith(projectPath + "/") ||
        p === "/api/events" ||
        p === "/api/runtime.js" ||
        p.startsWith("/api/vendor/") ||
        p.startsWith("/api/render/") ||
        p.startsWith("/api/fonts") ||
        p === "/api/registry/blocks" ||
        p === "/api/environment/ffmpeg" ||
        p === "/api/open-in-desktop" ||
        p.startsWith("/api/share/") ||
        p === "/__hyperframes_config";
      if (!allowed) return c.text("This endpoint is not part of the bound project", 404);
      await next();
    });
    this.app.route("/", project.server.app);
  }
  async start() {
    await new Promise<void>((resolve, reject) => {
      this.server = serve({ fetch: this.app.fetch, hostname: "127.0.0.1", port: 0 }, (info) => {
        this.origin = `http://127.0.0.1:${info.port}`;
        resolve();
      });
      this.server.on("error", reject);
    });
  }
  async close() {
    this.control.close();
    await this.project.close();
    const server = this.server;
    if (server)
      await new Promise<void>((resolve) => {
        server.close(() => resolve());
        if ("closeAllConnections" in server && typeof server.closeAllConnections === "function")
          server.closeAllConnections();
      });
  }
}
