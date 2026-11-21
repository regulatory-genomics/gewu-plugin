import { join } from "node:path";
import { StudioProject, type SourceChange } from "./project.js";
import { PluginError } from "./stdio.js";
import { describeProject } from "../../../packages/cli/src/timeline/describeProject.js";
import { allRows } from "../../../packages/cli/src/timeline/a2Shared.js";
import { applyPlanEdits } from "../../../packages/cli/src/timeline/a2Commands.js";
import { resolveProjectOrThrow } from "../../../packages/cli/src/utils/project.js";
import { ensureDOMParser } from "../../../packages/cli/src/utils/dom.js";

export async function readTimeline(project: StudioProject) {
  ensureDOMParser();
  const timeline = await describeProject(join(project.options.projectDir, "index.html"));
  const sources = new Map<string, { path: string; content: string; revision: string }>();
  for (const file of new Set(["index.html", ...allRows(timeline).map((row) => row.file)]))
    sources.set(file, await project.read(file));
  return {
    timeline,
    sources,
    sourceVersions: Object.fromEntries(
      [...sources].map(([path, source]) => [path, source.revision]),
    ),
  };
}
export async function editTimeline(project: StudioProject, args: Record<string, unknown>) {
  if (
    !Array.isArray(args.operations) ||
    !args.expectedVersions ||
    typeof args.expectedVersions !== "object"
  )
    throw new PluginError("INVALID_ARGUMENT", "operations and expectedVersions are required");
  const operations = args.operations,
    expected = args.expectedVersions;
  return project.writes.run(async () => {
    const { timeline, sources, sourceVersions } = await readTimeline(project);
    for (const [path, version] of Object.entries(sourceVersions))
      if (Reflect.get(expected, path) !== version)
        throw new PluginError(
          "REVISION_CONFLICT",
          `Read the current timeline versions before editing ${path}`,
          true,
        );
    const updated = new Map([...sources].map(([path, source]) => [path, source.content]));
    const result = await applyPlanEdits(
      operations,
      timeline,
      resolveProjectOrThrow(project.options.projectDir),
      updated,
    );
    if (!result.ok)
      throw new PluginError("TIMELINE_EDIT_REFUSED", `${result.reason}. ${result.fix}`);
    const changes: SourceChange[] = [];
    for (const [path, after] of updated) {
      const before = sources.get(path);
      if (!before)
        throw new PluginError("INVALID_PATH", "Timeline introduced an unread source file");
      if (before.content !== after)
        changes.push({ path, before: before.content, after, expectedRevision: before.revision });
    }
    if (args.dryRun === true)
      return { dryRun: true, files: changes.map((c) => c.path), changed: changes.length > 0 };
    return project.commit(changes, "Edited timeline");
  });
}
