import { expect, it } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StudioProject } from "../src/project";
import { Catalog } from "../src/catalog";

it("installs pinned editable block source without overwriting project files", async () => {
  const root = mkdtempSync(join(tmpdir(), "hf-catalog-")),
    dir = join(root, "video"),
    registry = join(root, "registry");
  mkdirSync(dir);
  mkdirSync(join(registry, "blocks/chart"), { recursive: true });
  writeFileSync(
    join(registry, "registry.json"),
    JSON.stringify({
      name: "test",
      homepage: "https://example.com",
      items: [{ name: "chart", type: "hyperframes:block" }],
    }),
  );
  writeFileSync(
    join(registry, "blocks/chart/registry-item.json"),
    JSON.stringify({
      name: "chart",
      type: "hyperframes:block",
      title: "Chart",
      description: "Animated chart",
      dimensions: { width: 1920, height: 1080 },
      duration: 6,
      files: [
        { path: "chart.html", target: "compositions/chart.html", type: "hyperframes:composition" },
      ],
    }),
  );
  writeFileSync(
    join(registry, "blocks/chart/chart.html"),
    '<template><div data-composition-id="chart">Chart</div></template>',
  );
  const p = new StudioProject({
    workspaceRoot: root,
    projectDir: dir,
    studioDir: join(root, "web"),
  });
  try {
    await p.create({});
    const catalog = new Catalog(registry);
    expect((await catalog.search("chart")).items[0]?.name).toBe("chart");
    const result = await catalog.add(p, "chart");
    expect(result.files).toContain("compositions/chart.html");
    expect(readFileSync(join(dir, "compositions/chart.html"), "utf8")).toContain(
      'data-composition-id="chart"',
    );
    await expect(catalog.add(p, "chart")).rejects.toMatchObject({ code: "ALREADY_EXISTS" });
  } finally {
    await p.close();
    rmSync(root, { recursive: true, force: true });
  }
});
