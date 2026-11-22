import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PluginServer } from "../src/studio-server";
import { StudioProject } from "../src/project";

it("answers Studio's optional desktop and share probes instead of 404", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-routes-"));
  const projectDir = join(root, "video");
  const studioDir = join(root, "web");
  mkdirSync(projectDir);
  mkdirSync(studioDir);
  writeFileSync(join(studioDir, "index.html"), "<!doctype html><title>Studio</title>");
  writeFileSync(join(projectDir, "index.html"), '<div data-composition-id="main"></div>');
  const project = new StudioProject({ workspaceRoot: root, projectDir, studioDir });
  const server = new PluginServer(project);
  await server.start();
  const headers = { "x-gewu-ingress": server.ingress };
  try {
    const desktop = await fetch(`${server.origin}/api/open-in-desktop`, { headers });
    expect(desktop.status).toBe(200);
    expect(await desktop.json()).toMatchObject({ available: false, handoff: false });

    const share = await fetch(`${server.origin}/api/share/bootstrap`, { headers });
    expect(share.status).toBe(501);
    expect(await share.json()).toMatchObject({ code: "unavailable" });
  } finally {
    await server.close();
    rmSync(root, { recursive: true, force: true });
  }
});
