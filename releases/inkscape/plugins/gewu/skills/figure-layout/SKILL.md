---
name: figure-layout
description: Arrange and review editable SVG figure panels with the Inkscape GeWu plugin, including figure exports, updated plot sources and desktop handoff.
---

# Figure layout

1. Bind the `inkscape` application to a workspace SVG or an existing figure directory. Resource applications are called through `plugin_describe` and `plugin_call` with the instance ID. Read `state` and the focused `selection` first; use `inspect` for explicit target IDs and fields.
2. Coordinates are millimetres, x right and y down. For multipage figures use the selected page's x/y origin in the document. Use one `edit` batch with the exact `expectedRevision` for a coherent layout change. Use explicit IDs; a different selection does not retarget an existing edit.
3. Import generated SVG/PNG as panels and preserve the contents. Use `align`, `distribute`, `equalize` and simple labels. `sources` reports source changes; `replace_panel` refreshes the source while retaining the top-left and visual width. An aspect-ratio change changes height: check the corresponding page and neighbouring panels.
4. Use `render` at the returned revision and examine the native image. Use `check_layout` for overlap, outside-page and raster-DPI warnings. Warnings are candidates for review; overlap can be intentional. Do not say the figure is correct just because an edit succeeded.
5. Use `undo` to roll back the last complete shared edit. On stale revision read the current state, compare the changes, then prepare an updated batch. On `OUTCOME_UNKNOWN` inspect the authoritative document/desktop before any retry.
6. `handoff` defaults to PNG for Paper and Manimble. Use `format: "pdf"` for a Paper PDF-only build that needs vector output. Pass `project` to publish into an existing recipient project's `figures/` or `assets/` directory. Use the receiving plugin's own edit/build commands for the reference, caption or slide. Use `handoff_status` on `manifestPath` to compare revisions later.
7. `checkout` or the desktop button suspends embedded edits and returns an editable SVG copy. For a configured live session use `live_state` to read the unsaved revision and selection, and `live_edit` for a short geometry/text batch. The desktop user can use native undo. Save and close the desktop copy before check-in so the workspace imports the intended saved revision.
