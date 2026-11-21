---
name: hyperframes
description: Author and edit a Hyperframes video project in GeWu — plan scenes, write seekable HTML/GSAP, edit text/timing/animation, inspect real frames, check, and render.
---

# Make a video in GeWu

You are editing one real project directory bound to a GeWu application instance.
The person can open the same project in the native Studio at any time.

## Discover the instance

```
plugin_open {pluginId: "hyperframes", bindingResource: {uri: "workspace:///my-video", type: "hyperframes.project"}}
plugin_describe {instanceId, tool: "element.edit"}
plugin_call {instanceId, tool: "project.inspect", args: {}}
```

Use the existing instance if it is already open (`plugin_list`). Never assume a
tool's arguments: read its schema first. Errors are typed (`REVISION_CONFLICT`,
`INVALID_PATH`, `VIEW_NOT_ATTACHED`, `UNSUPPORTED_EDIT`, `TEMPLATE_ASSET_REQUIRED`,
`CANCELLED`, `OUTCOME_UNKNOWN`).

## The production loop

1. **Look first.** `project.inspect` for source, duration and elements;
   `timeline.read` for clips and the exact source revisions.
2. **Write seekable motion.** A composition is HTML with `data-start`,
   `data-duration`, `data-track-index`, one paused GSAP root timeline per
   composition on `window.__timelines`, and no wall-clock or unseeded randomness.
   Keep assets local (`asset.import`), never a CDN in a project that must render
   offline.
3. **Prefer typed edits over hand-written HTML.** `element.edit` (text, style,
   attribute, timing, move, add/remove) and `animation.edit` (GSAP tweens and
   keyframes) validate before writing. Include `sourceFile`, the `expectedRevision`
   you just read, and explicit targets. Use `dryRun: true` when unsure.
4. **Timeline edits** go through `timeline.apply` with every `sourceVersions`
   entry from `timeline.read`; it is all-or-nothing and refuses overlaps.
5. **See the result.** `frame.capture {times: [...]}` returns PNG resources and a
   `read_file` argument for each. Read at least one frame, and read a second time
   when the edit is visual. A frame proves the saved revision, not an unsaved
   preview state.
6. **Check, then render.** `project.check` must pass (read `checksPassed` and the
   report; a skipped audit is not a pass). Then `render` with `plugin_call` and
   `async: true`, poll `plugin_call_status`, and cancel with `plugin_cancel` if the
   person asks. Renders are frozen to the source revision they started from.

## Working with the person

- The person edits in Studio. Before overwriting, re-read the file: their change
  makes your old revision stale and you get `REVISION_CONFLICT` (read again and
  re-apply your own intent, never force).
- `preview.context` and the `studio_*` tools act on the focused Studio view.
  `studio_select` then `studio_set_text` is the precise path for "change this
  one"; `VIEW_NOT_ATTACHED` means the person must open or focus the view.
- `history.undo` reverts one recorded operation and refuses when later edits
  conflict. Do not hand-edit a file back.
- `catalog.search` / `catalog.add` install pinned editable source. Items that
  need external or binary assets return `TEMPLATE_ASSET_REQUIRED`; import those
  explicitly before adapting the source.

## Do not

- Do not invent element ids or reuse a handle after a reload; re-read elements.
- Do not claim a render succeeded from tool success alone — verify the returned
  probe metadata and the decoded frame.
- Do not put credentials, absolute host paths or generated media into a project
  you will share.
