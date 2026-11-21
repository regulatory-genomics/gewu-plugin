import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StudioProject } from "../src/project";

it("shares version-checked writes with Studio and preserves human changes on stale agent saves", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-gewu-")),
    dir = join(root, "video");
  mkdirSync(dir);
  const p = new StudioProject({
    workspaceRoot: root,
    projectDir: dir,
    studioDir: join(root, "web"),
  });
  try {
    await p.create({ duration: 4 });
    const before = await p.read("index.html");
    const human = await p.request("/api/projects/video/files/index.html", {
      method: "PUT",
      headers: { "content-type": "text/plain", "If-Match": before.revision },
      body: before.content.replace("Your video", "Human title"),
    });
    expect(human.status).toBe(200);
    await expect(
      p.write("index.html", before.content + "<!-- stale -->", before.revision),
    ).rejects.toMatchObject({ code: "REVISION_CONFLICT" });
    expect((await p.read("index.html")).content).toContain("Human title");
    const fresh = await p.read("index.html");
    const receipt = await p.write(
      "index.html",
      fresh.content.replace("Human title", "Agent title"),
      fresh.revision,
    );
    expect(receipt.revision).not.toBe(before.revision);
    expect(readFileSync(join(dir, "index.html"), "utf8")).toContain("Agent title");
    await expect(p.read("../outside")).rejects.toMatchObject({ code: "INVALID_PATH" });
    await expect(p.create({})).rejects.toMatchObject({ code: "ALREADY_EXISTS" });
  } finally {
    await p.close();
    rmSync(root, { recursive: true, force: true });
  }
});
