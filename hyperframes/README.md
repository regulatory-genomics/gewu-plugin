# GeWu video application (Hyperframes)

Editable HTML/GSAP video projects inside GeWu: the agent writes and edits the
project, a person reviews it in the native Hyperframes Studio, and both act on
the same versioned source and history.

## What it does

- **Native Studio in Preview.** The real Hyperframes Studio is served from its
  own origin and framed by GeWu. Playback, timeline, element selection, property
  edits, keyframes, undo/redo and export are the upstream UI, not a reimplementation.
- **Shared, version-checked edits.** Every agent write carries the revision it
  read. A stale write fails with `REVISION_CONFLICT` instead of overwriting the
  person's work. Both sides share the project's native history.
- **Agent tools.** Project creation, source read/write, typed element and
  animation edits, native timeline edits, history undo/redo, asset import,
  pinned catalog install, checks, frame capture and frozen-revision rendering.
- **Frames the model can see.** `frame.capture` writes bounded PNGs into the
  workspace and returns the exact `read_file` call; the agent reads the image
  before judging its own edit.
- **Reproducible renders.** Check/capture/render freeze the project into an
  immutable input, so editing during a render cannot change the output. Renders
  report progress and can be cancelled, with the worker process group reaped.

## Requirements

- GeWu with `webapp` views (this branch) and a browser-facing app origin.
- Node.js >= 22, Chromium (chrome-headless-shell preferred) and FFmpeg/FFprobe,
  installed on the GeWu host. Nothing is bundled; see `INSTALL.md`.
- System fonts for video text, or a project that ships its own fonts via
  `@font-face` (recommended for identical renders across machines).

## Layout

```text
plugin.json          manifest: resource scope, webapp Studio view, video view
bin/hyperframes-gewu adapter entry (uses the packed runtime when present)
engine/cli/          built adapter, CLI and Studio server (same commit)
web/                 built GeWu Studio host (native Studio + control bridge)
registry/            pinned blocks/components for catalog.search/add
tools.schema.json    the exact agent tool schemas
skills/hyperframes/  the agent skill that teaches the production loop
runtime-plan.json    external-only runtime provider (host dependencies)
release.json         package descriptor for scripts/plugin-release.py
```

## Development

```sh
bun run --cwd plugins/gewu test        # adapter, project, tools, timeline, jobs, catalog, render
bun run --cwd plugins/gewu typecheck
bun run --cwd plugins/gewu build       # stages dist/gewu-hyperframes
bun run --cwd plugins/gewu build:release -- --base-url <url> --gewu <gewu-checkout>
```

See `INSTALL.md` for installation, runtime providers and the exact acceptance
commands used to validate a release.
