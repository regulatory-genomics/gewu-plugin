import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StudioProject, revision } from "../src/project";

it("refuses an entire multi-file edit when a human changed either file", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-transaction-")),
    dir = join(root, "video");
  mkdirSync(dir);
  const p = new StudioProject({
    workspaceRoot: root,
    projectDir: dir,
    studioDir: join(root, "web"),
  });
  try {
    await p.create({});
    await p.write("scene.html", "<h1>before</h1>", null);
    const a = await p.read("index.html"),
      b = await p.read("scene.html");
    await p.write("scene.html", "<h1>human</h1>", b.revision);
    await expect(
      p.commit(
        [
          {
            path: a.path,
            before: a.content,
            after: a.content + "<!-- agent -->",
            expectedRevision: a.revision,
          },
          {
            path: b.path,
            before: b.content,
            after: "<h1>agent</h1>",
            expectedRevision: b.revision,
          },
        ],
        "timeline edit",
      ),
    ).rejects.toMatchObject({ code: "REVISION_CONFLICT" });
    expect(readFileSync(join(dir, "index.html"), "utf8")).toBe(a.content);
    expect(readFileSync(join(dir, "scene.html"), "utf8")).toBe("<h1>human</h1>");
  } finally {
    await p.close();
    rmSync(root, { recursive: true, force: true });
  }
});

it("recovers an interrupted multi-file transaction without replacing unrelated bytes", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-recover-")),
    dir = join(root, "video");
  mkdirSync(dir);
  const p = new StudioProject({
    workspaceRoot: root,
    projectDir: dir,
    studioDir: join(root, "web"),
  });
  try {
    await p.create({});
    await p.write("a.html", "original a", null);
    await p.write("b.html", "original b", null);
    mkdirSync(p.artifactRoot, { recursive: true });
    writeFileSync(
      join(p.artifactRoot, "pending-transaction.json"),
      JSON.stringify({
        label: "Interrupted edit",
        changes: [
          {
            path: "a.html",
            before: "original a",
            after: "new a",
            expectedRevision: revision("original a"),
          },
          {
            path: "b.html",
            before: "original b",
            after: "new b",
            expectedRevision: revision("original b"),
          },
        ],
      }),
    );
    writeFileSync(join(dir, "a.html"), "new a");
    await p.recover();
    expect(readFileSync(join(dir, "a.html"), "utf8")).toBe("original a");
    expect(readFileSync(join(dir, "b.html"), "utf8")).toBe("original b");
    expect(existsSync(join(p.artifactRoot, "pending-transaction.json"))).toBe(false);
  } finally {
    await p.close();
    rmSync(root, { recursive: true, force: true });
  }
});
