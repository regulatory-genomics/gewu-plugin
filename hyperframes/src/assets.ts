import { constants } from "node:fs";
import { realpath, stat, mkdir, copyFile, link, unlink } from "node:fs/promises";
import { resolve, join, sep, dirname } from "node:path";
import { randomUUID, createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { StudioProject } from "./project.js";
import { PluginError } from "./stdio.js";

export async function importAsset(project: StudioProject, source: string, destination: string) {
  const workspace = project.options.workspaceRoot;
  const input = await realpath(resolve(workspace, source));
  if (!input.startsWith(workspace + sep))
    throw new PluginError("INVALID_PATH", "Asset source must resolve within this workspace");
  if (!destination.startsWith("assets/"))
    throw new PluginError("INVALID_PATH", "Import destinations must be under assets/");
  const target = project.path(destination),
    info = await stat(input);
  if (!info.isFile() || info.size > 2 * 1024 * 1024 * 1024)
    throw new PluginError("TOO_LARGE", "Asset must be a regular file of at most 2 GiB");
  return project.writes.run(async () => {
    const resolved = await project.server.adapter.resolveProject(project.id),
      history = resolved && (await project.server.adapter.history?.(resolved));
    if (!history) throw new PluginError("HISTORY_UNAVAILABLE", "Native history is unavailable");
    await history.flush();
    await mkdir(dirname(target), { recursive: true });
    const temporary = join(dirname(target), `.import-${randomUUID()}`);
    try {
      await copyFile(input, temporary, constants.COPYFILE_EXCL | constants.COPYFILE_FICLONE);
      try {
        await link(temporary, target);
      } catch (error) {
        if (error && typeof error === "object" && "code" in error && error.code === "EEXIST")
          throw new PluginError("ALREADY_EXISTS", `${destination} already exists`);
        throw error;
      }
      const digest = createHash("sha256");
      for await (const chunk of createReadStream(target)) digest.update(chunk);
      history.noteChange(destination);
      const entry = await history.claim({ kind: "agent", name: "GeWu agent" }, "Imported asset", [
        destination,
      ]);
      await history.flush();
      return {
        path: destination,
        bytes: info.size,
        sha256: digest.digest("hex"),
        operationId: entry?.id ?? null,
      };
    } finally {
      await unlink(temporary).catch(() => {});
    }
  });
}
