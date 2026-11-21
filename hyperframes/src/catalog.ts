import { existsSync } from "node:fs";
import { readFile, realpath, unlink } from "node:fs/promises";
import { join, sep, resolve, extname } from "node:path";
import type { RegistryItem } from "@hyperframes/core";
import {
  validRegistryManifest,
  validRegistryItem,
} from "../../../packages/cli/src/registry/validation.js";
import { StudioProject, revision } from "./project.js";
import { PluginError } from "./stdio.js";

const folder = {
  "hyperframes:block": "blocks",
  "hyperframes:component": "components",
  "hyperframes:example": "examples",
};
export class Catalog {
  constructor(readonly root: string) {}
  async list(): Promise<RegistryItem[]> {
    const value: unknown = JSON.parse(await readFile(join(this.root, "registry.json"), "utf8"));
    if (!validRegistryManifest(value))
      throw new PluginError("INVALID_CATALOG", "Pinned registry manifest is invalid");
    const items = [];
    for (const entry of value.items) {
      const data: unknown = JSON.parse(
        await readFile(
          join(this.root, folder[entry.type], entry.name, "registry-item.json"),
          "utf8",
        ),
      );
      if (!validRegistryItem(data, entry.name, entry.type))
        throw new PluginError("INVALID_CATALOG", `Invalid metadata for ${entry.name}`);
      items.push(data);
    }
    return items;
  }
  async search(query: string, cursor = 0, limit = 20) {
    if (
      !Number.isInteger(cursor) ||
      cursor < 0 ||
      !Number.isInteger(limit) ||
      limit < 1 ||
      limit > 100
    )
      throw new PluginError("INVALID_ARGUMENT", "cursor must be nonnegative; limit must be 1–100");
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const items = (await this.list()).filter((item) =>
      words.every((word) =>
        `${item.name} ${item.title} ${item.description} ${item.tags?.join(" ")}`
          .toLowerCase()
          .includes(word),
      ),
    );
    return {
      items: items
        .slice(cursor, cursor + limit)
        .map(({ name, title, type, description, tags }) => ({
          name,
          title,
          type,
          description,
          tags,
        })),
      total: items.length,
      cursor: cursor + limit < items.length ? cursor + limit : null,
    };
  }
  async add(project: StudioProject, name: string) {
    const catalog = await this.list();
    const item = catalog.find((item) => item.name === name);
    if (!item) throw new PluginError("NOT_FOUND", `No pinned catalog item named ${name}`);
    const ordered: RegistryItem[] = [],
      seen = new Set<string>(),
      visiting = new Set<string>();
    const visit = (entry: RegistryItem) => {
      if (seen.has(entry.name)) return;
      if (visiting.has(entry.name))
        throw new PluginError("INVALID_CATALOG", "Registry dependency cycle");
      visiting.add(entry.name);
      for (const dep of entry.registryDependencies ?? []) {
        const found = catalog.find((i) => i.name === dep);
        if (!found) throw new PluginError("INVALID_CATALOG", `Missing dependency ${dep}`);
        visit(found);
      }
      visiting.delete(entry.name);
      seen.add(entry.name);
      ordered.push(entry);
    };
    visit(item);
    const files: { path: string; content: string }[] = [];
    for (const entry of ordered)
      for (const file of entry.files) {
        if (
          file.url ||
          ![".html", ".css", ".js", ".mjs", ".json", ".svg", ".txt", ".md"].includes(
            extname(file.path),
          )
        )
          throw new PluginError(
            "TEMPLATE_ASSET_REQUIRED",
            `${entry.name} requires external/binary assets; import those explicitly before adapting its source`,
          );
        const base = await realpath(join(this.root, folder[entry.type], entry.name)),
          source = await realpath(resolve(base, file.path));
        if (!source.startsWith(base + sep))
          throw new PluginError("INVALID_PATH", "Catalog source escapes its package");
        project.path(file.target);
        files.push({ path: file.target, content: await readFile(source, "utf8") });
      }
    return project.writes.run(async () => {
      for (const file of files)
        if (existsSync(project.path(file.path)))
          throw new PluginError("ALREADY_EXISTS", `Catalog install would overwrite ${file.path}`);
      const written: { path: string; content: string }[] = [];
      try {
        for (const file of files) {
          await project.write(file.path, file.content, null, `Installed ${name}`);
          written.push(file);
        }
        return {
          files: written.map((f) => f.path),
          block: item,
          warnings: [
            "Source installed; check required parameters, media and dimensions before use.",
          ],
        };
      } catch (error) {
        for (const file of written) {
          if (revision(await readFile(project.path(file.path))) !== revision(file.content))
            throw new PluginError(
              "OUTCOME_UNKNOWN",
              `Catalog install interrupted; ${file.path} changed during rollback`,
            );
          await unlink(project.path(file.path));
        }
        throw error;
      }
    });
  }
}
