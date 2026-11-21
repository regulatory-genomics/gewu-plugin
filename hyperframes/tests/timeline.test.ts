import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StudioProject } from "../src/project";
import { AuthoringTools } from "../src/tools";

it("applies native trim/move plans with the same versioned history as Studio", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-timeline-")),
    dir = join(root, "video");
  mkdirSync(dir);
  const p = new StudioProject({
      workspaceRoot: root,
      projectDir: dir,
      studioDir: join(root, "web"),
    }),
    tools = new AuthoringTools(p);
  try {
    await p.create({ duration: 4 });
    const timeline = await tools.call("timeline.read", {});
    const plan = {
      expectedVersions: timeline.sourceVersions,
      operations: [
        { verb: "trim", ref: "#title", duration: "2" },
        { verb: "move", ref: "#title", time: "1" },
      ],
    };
    const before = await p.read("index.html");
    await tools.call("timeline.apply", { ...plan, dryRun: true });
    expect((await p.read("index.html")).revision).toBe(before.revision);
    const saved = await tools.call("timeline.apply", plan);
    expect(saved.saved).toBe(true);
    const after = await p.read("index.html");
    expect(after.content).toContain('data-start="1"');
    expect(after.content).toContain('data-duration="2"');
    await tools.call("history.undo", { operationId: saved.operationId });
    expect((await p.read("index.html")).content).toBe(before.content);
  } finally {
    await p.close();
    rmSync(root, { recursive: true, force: true });
  }
});
