import { openComposition, type EditOp } from "@hyperframes/sdk";
import Ajv from "ajv";
import { StudioProject } from "./project.js";
import { PluginError } from "./stdio.js";
import { importAsset } from "./assets.js";
import { readTimeline, editTimeline } from "./timeline.js";
import { Catalog } from "./catalog.js";

const str = { type: "string", minLength: 1 },
  num = { type: "number", minimum: 0 };
const obj = (properties: Record<string, unknown>, required: string[] = []) => ({
  type: "object",
  properties,
  required,
  additionalProperties: false,
});
const op = (type: string, properties: Record<string, unknown>, required: string[]) =>
  obj({ type: { const: type }, ...properties }, ["type", ...required]);
const target = { oneOf: [str, { type: "array", items: str, minItems: 1, maxItems: 100 }] };
const editOps = {
  oneOf: [
    op("setText", { target, value: { type: "string" } }, ["target", "value"]),
    op(
      "setStyle",
      {
        target,
        styles: {
          type: "object",
          minProperties: 1,
          additionalProperties: { type: ["string", "null"] },
        },
      },
      ["target", "styles"],
    ),
    op("setAttribute", { target, name: str, value: { type: ["string", "null"] } }, [
      "target",
      "name",
      "value",
    ]),
    op(
      "setTiming",
      {
        target,
        start: num,
        duration: num,
        trackIndex: { type: "integer", minimum: 0 },
        linked: { type: "boolean" },
      },
      ["target"],
    ),
    op("moveElement", { target, x: { type: "number" }, y: { type: "number" } }, [
      "target",
      "x",
      "y",
    ]),
    op("removeElement", { target }, ["target"]),
    op(
      "addElement",
      { parent: { type: ["string", "null"] }, index: { type: "integer", minimum: 0 }, html: str },
      ["parent", "index", "html"],
    ),
    op("setCompositionMetadata", { width: num, height: num, duration: num }, []),
    op("addGsapTween", { target: str, tween: { type: "object" } }, ["target", "tween"]),
    op("setGsapTween", { animationId: str, properties: { type: "object" } }, [
      "animationId",
      "properties",
    ]),
    op("addGsapKeyframe", { animationId: str, position: num, value: { type: "object" } }, [
      "animationId",
      "position",
      "value",
    ]),
    op("removeGsapTween", { animationId: str }, ["animationId"]),
  ],
};
const page = {
  cursor: { type: "integer", minimum: 0 },
  limit: { type: "integer", minimum: 1, maximum: 200 },
};
const mutation = obj(
  {
    sourceFile: str,
    expectedRevision: str,
    operations: { type: "array", items: editOps, minItems: 1, maxItems: 100 },
    dryRun: { type: "boolean" },
  },
  ["sourceFile", "expectedRevision", "operations"],
);
export function definition(
  name: string,
  description: string,
  inputSchema: Record<string, unknown>,
  effect = "read",
  target = "backend",
) {
  return {
    name,
    description,
    inputSchema,
    outputSchema: { type: "object" },
    exposeToAgent: true,
    effect,
    target,
  };
}
export const authoringDefinitions = [
  definition(
    "project.create",
    "Initialize an empty bound directory with local, editable video source. Never overwrites an existing project.",
    obj({ width: num, height: num, fps: num, duration: num }),
    "write",
  ),
  definition(
    "project.inspect",
    "Read this project's source and editing capabilities.",
    obj({ sourceFile: str }),
  ),
  definition(
    "source.read",
    "Read project source and its strong revision before editing.",
    obj({ path: str }, ["path"]),
  ),
  definition(
    "source.write",
    "Save source with the exact revision read; null means create-only. Uses the native Studio history.",
    obj(
      {
        path: str,
        content: { type: "string", maxLength: 524288 },
        expectedRevision: { type: ["string", "null"] },
        label: str,
      },
      ["path", "content", "expectedRevision"],
    ),
    "write",
  ),
  definition(
    "element.inspect",
    "Read stable scoped element ids, text, timing and editable properties.",
    obj({ sourceFile: str, target: str, ...page }),
  ),
  definition(
    "element.edit",
    "Apply typed SDK operations to explicit targets. Read source revision first; dryRun never writes.",
    mutation,
    "write",
  ),
  definition(
    "animation.edit",
    "Edit GSAP tween/keyframes using the native SDK; unsupported animation patterns return an error.",
    mutation,
    "write",
  ),
  definition(
    "timeline.read",
    "Read the native project timeline with source file references and bounded clip rows.",
    obj(page),
  ),
  definition(
    "timeline.apply",
    "Apply native timeline edits atomically. Supply all sourceVersions from timeline.read; dryRun never writes.",
    obj(
      {
        operations: {
          type: "array",
          minItems: 1,
          maxItems: 100,
          items: obj(
            {
              verb: { enum: ["move", "trim", "split", "delete", "set", "duplicate"] },
              ref: str,
              time: str,
              start: str,
              end: str,
              duration: str,
              volume: str,
              rate: str,
              track: str,
              at: str,
              overwrite: { type: "boolean" },
            },
            ["verb", "ref"],
          ),
        },
        expectedVersions: { type: "object", additionalProperties: { type: "string" } },
        dryRun: { type: "boolean" },
      },
      ["operations", "expectedVersions"],
    ),
    "write",
  ),
  definition("history.list", "Read shared native Studio history.", obj(page)),
  definition(
    "history.undo",
    "Undo one recorded operation, preserving later conflicting edits.",
    obj({ operationId: str }, ["operationId"]),
    "write",
  ),
  definition("history.redo", "Step forward in native history.", obj({}), "write"),
  definition(
    "asset.import",
    "Copy a workspace-local media/font file into project assets without overwriting. Handles large files without base64.",
    obj({ source: str, destination: str }, ["source", "destination"]),
    "write",
  ),
  definition(
    "catalog.search",
    "Search the registry pinned into this plugin. Results are complete or paginated, never fetched from a floating registry.",
    obj({
      query: { type: "string" },
      cursor: { type: "integer", minimum: 0 },
      limit: { type: "integer", minimum: 1, maximum: 100 },
    }),
  ),
  definition(
    "catalog.add",
    "Install editable source for a pinned registry item and its dependencies without overwriting. Items needing external/binary assets return TEMPLATE_ASSET_REQUIRED.",
    obj({ name: str }, ["name"]),
    "write",
  ),
];
const ajv = new Ajv({ strict: false, allowUnionTypes: true });
const validateOps = ajv.compile<EditOp[]>({
  type: "array",
  items: editOps,
  minItems: 1,
  maxItems: 100,
});

export class AuthoringTools {
  constructor(
    readonly project: StudioProject,
    readonly catalog?: Catalog,
  ) {}
  async call(name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const sourceFile = typeof args.sourceFile === "string" ? args.sourceFile : "index.html";
    if (name.startsWith("catalog.")) {
      if (!this.catalog)
        throw new PluginError("DEPENDENCY_MISSING", "Pinned registry is not installed");
      return name === "catalog.add"
        ? this.catalog.add(this.project, text(args, "name"))
        : this.catalog.search(
            typeof args.query === "string" ? args.query : "",
            typeof args.cursor === "number" ? args.cursor : 0,
            typeof args.limit === "number" ? args.limit : 20,
          );
    }
    if (name === "project.create") return this.project.create(args);
    if (name === "source.read") return this.project.read(text(args, "path"));
    if (name === "asset.import")
      return importAsset(this.project, text(args, "source"), text(args, "destination"));
    if (name === "source.write")
      return this.project.write(
        text(args, "path"),
        text(args, "content", true),
        args.expectedRevision === null ? null : text(args, "expectedRevision"),
        typeof args.label === "string" ? args.label : undefined,
      );
    if (name === "timeline.read") {
      const { timeline, sourceVersions } = await readTimeline(this.project);
      const rows = timeline.tracks.flatMap((track) => track.rows);
      return { duration: timeline.duration, sourceVersions, ...paginate(rows, args) };
    }
    if (name === "timeline.apply") return editTimeline(this.project, args);
    if (name.startsWith("history.")) {
      return this.project.writes.run(async () => {
        const project = await this.project.server.adapter.resolveProject(this.project.id);
        const history = project && (await this.project.server.adapter.history?.(project));
        if (!history) throw new PluginError("HISTORY_UNAVAILABLE", "Native history is unavailable");
        await history.checkpoint();
        if (name === "history.list") return paginate(history.list(), args);
        const who = { kind: "agent" as const, name: "GeWu agent" };
        const result =
          name === "history.undo"
            ? await history.undo(text(args, "operationId"), { who })
            : await history.step("forward", who);
        if (!result.ok)
          throw new PluginError(
            "REVISION_CONFLICT",
            `Later edits conflict in ${result.conflict.files.join(", ")}`,
            true,
          );
        return { saved: true, entry: result.entry };
      });
    }
    return this.project.writes.run(async () => {
      const source = await this.project.read(sourceFile);
      if (name === "element.edit" || name === "animation.edit") {
        if (source.revision !== args.expectedRevision)
          throw new PluginError("REVISION_CONFLICT", "Source changed; read before editing", true);
        if (!validateOps(args.operations))
          throw new PluginError("INVALID_ARGUMENT", ajv.errorsText(validateOps.errors));
      }
      const comp = await openComposition(source.content);
      try {
        if (name === "project.inspect")
          return {
            projectId: this.project.id,
            sourceFile,
            revision: source.revision,
            elementCount: comp.getElements().length,
            animationIds: [...comp.getAllAnimationIds()],
            capabilities: { studio: true, headlessEditing: true, formats: ["mp4", "webm"] },
          };
        if (name === "element.inspect") {
          const items =
            typeof args.target === "string"
              ? [comp.getElement(args.target)].filter(Boolean)
              : comp.getElements();
          return { sourceFile, revision: source.revision, ...paginate(items, args) };
        }
        if (name !== "element.edit" && name !== "animation.edit")
          throw new PluginError("UNKNOWN_TOOL", `Unknown tool ${name}`);
        if (!validateOps(args.operations))
          throw new PluginError("INVALID_ARGUMENT", ajv.errorsText(validateOps.errors));
        const operations = args.operations;
        comp.batch(() => {
          for (const operation of operations) {
            const can = comp.can(operation);
            if (!can.ok) throw new PluginError("UNSUPPORTED_EDIT", `${can.code}: ${can.message}`);
            comp.dispatch(operation);
          }
        });
        const content = comp.serialize();
        if (args.dryRun === true)
          return {
            dryRun: true,
            sourceFile,
            beforeRevision: source.revision,
            changed: content !== source.content,
          };
        return this.project.write(sourceFile, content, source.revision, `Agent ${name}`);
      } finally {
        comp.dispose();
      }
    });
  }
}

export function text(args: Record<string, unknown>, key: string, empty = false) {
  const value = args[key];
  if (typeof value !== "string" || (!empty && !value))
    throw new PluginError("INVALID_ARGUMENT", `${key} must be a string`);
  return value;
}
export function paginate<T>(items: T[], args: Record<string, unknown>) {
  const offset = typeof args.cursor === "number" ? args.cursor : 0,
    limit = typeof args.limit === "number" ? args.limit : 50;
  if (
    !Number.isInteger(offset) ||
    offset < 0 ||
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > 200
  )
    throw new PluginError("INVALID_ARGUMENT", "cursor must be nonnegative, limit must be 1–200");
  return {
    items: items.slice(offset, offset + limit),
    total: items.length,
    cursor: offset + limit < items.length ? offset + limit : null,
  };
}
