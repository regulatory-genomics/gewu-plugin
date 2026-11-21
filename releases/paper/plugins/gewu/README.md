# Paper for GeWu

Stage this plugin against the GeWu SDK and install the resulting directory:

```sh
python3 plugins/gewu/package.py --sdk-root "$GEWU/sdk/python" --out /path/to/staging/paper
gewu plugin install /path/to/staging/paper
gewu plugin check paper --probe --workspace /path/to/workspace --settings /path/to/settings.json
```

Set `executable` in settings to a Paper binary with `describe --json`,
`inspect --json` and `plan-edit --json` support. Tectonic must be available to
the adapter for PDFs.

## Editing

Open a project folder to get the **Manuscript editor**. Plain paragraphs and
headings edit in place; blocks carrying citations, maths, tables, code or raw
LaTeX/HTML open as their exact Markdown source, so protected syntax is never
rebuilt from rendered output. Edits go through one revision-checked path shared
with the agent (`document`, `edit`, `undo`, `redo`), and are written to the
project's own files.

The editor is a block view, not a typesetting preview. Use **Build HTML** (or the
`build` tool) for the compiled manuscript; the native build remains the source of
truth for HTML, DOCX and PDF.

Output files are separate per call under `.gewu/outputs`. The native CLI keeps
its bibliography cache behavior. Source revision is based on metadata and the
ordered contents and supplement files; image and bibliography dependencies are
not part of that revision. The build also returns native diagnostics; a
successful build does not imply every citation or cross-reference resolved.
