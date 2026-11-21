import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StudioProject } from "../src/project";
import { AuthoringTools } from "../src/tools";

it("edits an explicit element with the SDK, previews without writing, and undoes through native history", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-tools-")),
    dir = join(root, "video");
  mkdirSync(dir);
  const project = new StudioProject({
    workspaceRoot: root,
    projectDir: dir,
    studioDir: join(root, "web"),
  });
  const tools = new AuthoringTools(project);
  try {
    await project.create({ duration: 4 });
    const source = await project.read("index.html");
    const input = {
      sourceFile: "index.html",
      expectedRevision: source.revision,
      operations: [{ type: "setText", target: "hf-title", value: "Agent edited" }],
    };
    const preview = await tools.call("element.edit", { ...input, dryRun: true });
    expect(preview.dryRun).toBe(true);
    expect((await project.read("index.html")).revision).toBe(source.revision);
    const saved = await tools.call("element.edit", input);
    expect(saved.saved).toBe(true);
    expect((await project.read("index.html")).content).toContain("Agent edited");
    await expect(tools.call("element.edit", input)).rejects.toMatchObject({
      code: "REVISION_CONFLICT",
    });
    await tools.call("history.undo", { operationId: saved.operationId });
    expect((await project.read("index.html")).content).toContain("Your video");
    await expect(
      tools.call("element.edit", {
        ...input,
        operations: [{ type: "setText", target: "nonexistent", value: "bad" }],
      }),
    ).rejects.toBeDefined();
  } finally {
    await project.close();
    rmSync(root, { recursive: true, force: true });
  }
});
