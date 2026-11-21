# Paper for GeWu

Stage this plugin against the GeWu SDK and install the resulting directory:

```sh
python3 plugins/gewu/package.py --sdk-root "$GEWU/sdk/python" --out /path/to/staging/paper
gewu plugin install /path/to/staging/paper
gewu plugin check paper --probe --workspace /path/to/workspace --settings /path/to/settings.json
```

Set `executable` in settings to a Paper binary with `describe --json`,
`inspect --json` and `plan-edit --json` support. For PDFs, either put Tectonic
on `PATH`, set `tectonic` to its binary or directory, or add that directory to
`pathPrepend`. `timeoutSeconds` defaults to 900 and applies to each CLI call.

Run `diagnose` before a long build when the CLI or Tectonic is uncertain. It
reports the resolved executable, Paper version, Tectonic path and version,
workspace, output and log roots without building anything.

## Revisions

`describe` returns the ordered sources, a small metadata summary and the current
project revision; it no longer returns the full Pandoc metadata tree. `build`
accepts that revision, the literal `latest`, or no revision at all. A
`STALE_REVISION` error is retryable and its message contains the current
revision, so refresh `document`/`describe` and retry once. Source revision
covers metadata and ordered contents/supplement files; images and bibliography
dependencies are not part of it.

## Editing

Open a project folder to get the **Manuscript editor**. Plain paragraphs and
headings edit in place; blocks carrying citations, maths, tables, code or raw
LaTeX/HTML open as their exact Markdown source, so protected syntax is never
rebuilt from rendered output. Edits go through one revision-checked path shared
with the agent (`document`, `edit`, `undo`, `redo`), and are written to the
project's own files.

The editor is a block view, not a typesetting preview. Use **Build HTML** (or the
`build` tool) for the compiled manuscript; the native build remains the source
of truth for HTML, DOCX and PDF.

## Outputs and logs

Output files stay separate per call under `.gewu/outputs/<callId>`. Successful
builds update `.gewu/outputs/latest.json` and keep the newest ten output
directories. Full build output is written to `.gewu/logs/<callId>-build.log`;
`data.diagnostics` also contains structured `warnings` and a short summary. A
successful build does not imply every citation or cross-reference resolved.

Write manuscripts through the plugin, not with a shell redirect or whole-file
patch: the CLI validates each edit and the revision check only protects writes
that pass through it.
