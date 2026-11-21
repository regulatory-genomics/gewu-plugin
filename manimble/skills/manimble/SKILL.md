---
name: manimble
description: Edit, render and export presentations backed by Manimble slide source.
---

Open the selected deck folder as `manimble.deck`; GeWu shows the Manimble editor
in Preview: slide list, canvas, object inspector, builds, undo/redo and PPTX
export. The user can drag, scale and re-wrap objects there directly, so read the
deck before you change it and leave objects the user has already placed alone.

Use `state` for the ordered slides, build status, undo/redo and the document
`revision`, and `scene` for one slide's size, builds, object boxes and PNG
frames. Change the deck with the editor's own commands instead of rewriting
Python: `slide` adds, duplicates, deletes, reorders or hides a slide, `insert`
adds text, a shape or a table, and `undo`/`redo` walk the deck history.

`edit` is one command with the engine's own operations. The ones worth
remembering: `move` (dx, dy in frame pixels), `resize` (a ratio), `size`
(width/height for rectangles and ellipses), `wrap` (text box width, `side:
"left"` keeps the right edge), `font`, `color`, `style` (font family, weight,
slant), `align` (`left`/`center`/`right` paragraph alignment), `spacing`,
`rotate` (degrees), `order` (`front`/`forward`/`backward`/`back`), `group` and
`ungroup` with `oids`, `decorate` (underline), `highlight`, `link`,
`clear_format`, `table`, and `delete`. Multi-object commands take the first id as
`oid` and the whole selection as `oids`. Moving an object on an earlier build
sends `build: true` with `stop`.

Read one slide with `source` before editing it. `source` returns the class code
without rendering; `scene` adds size, builds and object boxes, and `render`
returns PNG frames. Ask for `include` when you only need part of a render.

Pass the `revision` from `state` as `expected` on editor commands that accept
it; a mismatch means the deck changed and the call is refused, so read it again
rather than overwriting someone else's edit.

Use `edit_source` only when the change needs real Python (animations, data
driven scenes). Call `source` first, then pass that text as `expectedText` and
your replacement as `text`. For a one-line change prefer `edits:
[{oldText, newText}]`, which patches only the matched region.
Export with `export` and `format: "pptx"` after the affected slides render.
