---
name: figure-layout
description: Arrange and review editable SVG figure panels with the Inkscape GeWu plugin, including figure exports, updated plot sources and desktop handoff.
---

# Figure layout

## Native workspace (v0.2 default)

The default view is the real Inkscape desktop. `state` reads its live revision,
selection and save status; `inspect` includes nested groups, layers, text spans
and paths. Do not use `checkout` or direct file writes in native mode.

1. Read `state` and, when needed, the paged native `selection`.
2. Call `capabilities` for the exact schema of the operation you intend to use.
   The outer `edit` schema is intentionally compact; per-operation validation
   happens on the server. Native styles use `set_style`, not a guessed SVG patch.
3. Submit one `edit` with `expectedRevision`, a stable `requestId`, and operations.
   A human drag/composition returns `HUMAN_EDIT_ACTIVE`; a changed document returns
   `STALE_REVISION`. Re-read before forming a new batch. `OUTCOME_UNKNOWN` requires
   `request_status` and inspection, never blind replay with a new request ID.
4. Use native `undo`/`redo` for history. `save` publishes to the bound project SVG.
   Do not assume a successful edit already saved the user's project.
5. `render`, `export` and `handoff` consume the current live revision. Native
   geometry uses physical mm; raw path data and node coordinates are local SVG
   units. PNG/JPEG objects do not contain editable text or curve nodes.

Keep live path effects, masks, gradients, clone references and non-target text
spans intact. Select a specific tspan for mixed-format text edits. Plain path
node edits must not flatten an LPE. Large documents can exceed the current
extension commit deadline; an unknown result is not permission to retry.

The remainder describes the explicit `editorMode: "legacy"` panel workflow.

1. Bind the `inkscape` application to a workspace SVG or an existing figure directory. Resource applications are called through `plugin_describe` and `plugin_call` with the instance ID. Read `state` and the focused `selection` first; use `inspect` for explicit target IDs and fields.
2. Coordinates are millimetres, x right and y down. For multipage figures use the selected page's x/y origin in the document. Use one `edit` batch with the exact `expectedRevision` for a coherent layout change. Use explicit IDs; a different selection does not retarget an existing edit.
3. Import generated SVG/PNG as panels and preserve the contents. Use `align`, `distribute`, `equalize` and simple labels. `sources` reports source changes; `replace_panel` refreshes the source while retaining the top-left and visual width. An aspect-ratio change changes height: check the corresponding page and neighbouring panels.
4. Use `render` at the returned revision and examine the native image. Use `check_layout` for overlap, outside-page and raster-DPI warnings. Warnings are candidates for review; overlap can be intentional. Do not say the figure is correct just because an edit succeeded.
5. Use `undo` to roll back the last complete shared edit. On stale revision read the current state, compare the changes, then prepare an updated batch. On `OUTCOME_UNKNOWN` inspect the authoritative document/desktop before any retry.
6. `handoff` defaults to PNG for Paper and Manimble. Use `format: "pdf"` for a Paper PDF-only build that needs vector output. Pass `project` to publish into an existing recipient project's `figures/` or `assets/` directory. Use the receiving plugin's own edit/build commands for the reference, caption or slide. Use `handoff_status` on `manifestPath` to compare revisions later.
7. `checkout` or the desktop button suspends embedded edits and returns an editable SVG copy. For a configured live session use `live_state` to read the unsaved revision and selection, and `live_edit` for a short geometry/text batch. The desktop user can use native undo. Save and close the desktop copy before check-in so the workspace imports the intended saved revision.
