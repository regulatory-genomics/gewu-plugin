import { createHash } from "node:crypto";
import { createReadStream, constants } from "node:fs";
import { readdir, lstat, mkdir, copyFile, writeFile } from "node:fs/promises";
import { join, dirname } from "node:path";
import { PluginError } from "./stdio.js";

async function hash(path: string) {
  const digest = createHash("sha256");
  for await (const chunk of createReadStream(path)) digest.update(chunk);
  return digest.digest("hex");
}
export async function manifest(root: string) {
  const files: { path: string; sha256: string; bytes: number }[] = [];
  async function visit(dir: string) {
    for (const item of await readdir(join(root, dir), { withFileTypes: true })) {
      const path = dir ? `${dir}/${item.name}` : item.name;
      if (
        [".git", ".gewu", "node_modules", "renders"].includes(item.name) ||
        (path.startsWith(".hyperframes/") && path !== ".hyperframes/studio-manual-edits.json")
      )
        continue;
      const stat = await lstat(join(root, path));
      if (stat.isSymbolicLink())
        throw new PluginError("INVALID_PATH", `Source symlink is not snapshot-safe: ${path}`);
      if (stat.isDirectory()) await visit(path);
      else if (stat.isFile())
        files.push({ path, sha256: await hash(join(root, path)), bytes: stat.size });
      if (files.length > 10000)
        throw new PluginError("TOO_LARGE", "Project has over 10,000 source files");
    }
  }
  await visit("");
  return files.sort((a, b) => a.path.localeCompare(b.path));
}
export async function freezeProject(source: string, destination: string) {
  const files = await manifest(source);
  await mkdir(destination, { recursive: true });
  for (const file of files) {
    const target = join(destination, file.path);
    await mkdir(dirname(target), { recursive: true });
    await copyFile(join(source, file.path), target, constants.COPYFILE_FICLONE);
  }
  const copied = await manifest(destination),
    after = await manifest(source);
  if (
    JSON.stringify(files) !== JSON.stringify(after) ||
    JSON.stringify(files) !== JSON.stringify(copied)
  )
    throw new PluginError(
      "SOURCE_CHANGED",
      "Project changed while freezing inputs; retry from the current revision",
      true,
    );
  const revision = createHash("sha256").update(JSON.stringify(files)).digest("hex");
  await writeFile(
    join(dirname(destination), "source-manifest.json"),
    JSON.stringify({ revision, files }, null, 2),
  );
  return { revision, files, directory: destination };
}
