import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Adapter } from "../src/adapter";

it("activates the real loopback Studio service and deactivates it cleanly", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-activate-"));
  mkdirSync(join(root, "video"));
  const adapter = new Adapter(resolve(import.meta.dirname, "../../../dist/gewu-hyperframes"));
  try {
    const result = await adapter.handle({
      id: "activate",
      op: "activate",
      params: {
        apiVersion: "gewu.plugin/v1",
        workspaceRoot: root,
        bindingResource: { uri: "workspace:///video", type: "hyperframes.project" },
        settings: {
          chromePath: process.env.GEWU_HYPERFRAMES_CHROME,
          ffmpegPath: process.env.GEWU_HYPERFRAMES_FFMPEG,
          ffprobePath: process.env.GEWU_HYPERFRAMES_FFPROBE,
        },
      },
    });
    expect(result).toHaveProperty("tools");
    const response = await fetch(adapter.server!.origin, {
      headers: {
        "x-gewu-ingress": adapter.server!.ingress,
        "x-gewu-parent-origin": "https://gewu.example.com",
        "x-gewu-app-origin": "https://one.apps.example.com",
      },
    });
    expect(response.status).toBe(200);
    expect(response.headers.get("content-security-policy")).toBe(
      "frame-ancestors https://gewu.example.com",
    );
  } finally {
    await adapter.close();
    rmSync(root, { recursive: true, force: true });
  }
}, 10000);
