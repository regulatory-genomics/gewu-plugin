---
name: paper
description: Read, edit and build a Pandoc Markdown manuscript project with Paper.
---

Open the project folder (a `paper.project`) before calling these tools. The
project's own Markdown files are the only stored copy; nothing is kept beside
them.

## Read before writing

`document` returns every block of every source file with `file`, `byteStart`,
`byteEnd`, `kind`, `editable`, `inline` and the file `revision`. Use it instead
of guessing addresses from rendered output. `describe` gives the project
revision, ordered sources, supplement and merged metadata.

## Edit one block at a time

`edit` replaces exactly one block and needs the block's current range, its
`fileRevision` and the new content:

- `inline: "plain"` (paragraph, heading): send the new plain text.
- `inline: "rich"` (citations, maths, links, emphasis, raw TeX): send Markdown
  for one block of the same kind. Protected syntax stays in the text verbatim.
- `editable: false` blocks (code, tables, display maths, footnotes, raw HTML)
  are preserved byte-for-byte; do not try to replace them.

A rejected edit writes nothing. `STALE_REVISION` or `STALE_BLOCK` means the
manuscript changed: call `document` again, re-locate the block and repeat. Never
retry with the old range. `undo` and `redo` take the project revision you
expect; they refuse rather than overwrite a newer document.

Do not rewrite a manuscript file with a shell redirect or a whole-file patch:
that bypasses validation and races the editor's revision checks.

## Build

`build` needs a `paper.project` reference carrying the current revision and at
least one of `html`, `docx`, `pdf`. TeX is always produced. Outputs land under
`.gewu/outputs/<callId>` and are returned as resources; open or download them
from there. Read `warnings` and the native diagnostic — a successful build does
not mean every citation or cross-reference resolved.

## Project conventions

Follow the project's own `AGENTS.md` (orders, metadata, authors, figures) before
inventing structure. New projects come from `init` with the native template; do
not hand-roll `metadata.yaml`.
