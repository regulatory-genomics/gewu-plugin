# Inkscape for GeWu — native workspace v0.2

A real Inkscape window inside GeWu, with its native tools, layers, text cursor,
node editing, snapping and undo. The agent reads and edits the same unsaved SVG.
The browser receives the desktop display; the figure remains editable vectors.

**Status: technical preview.** The native workspace, structured editing, save,
import/export and bundled-runtime installation paths are implemented. The 50k-node
continuous-edit benchmark exposed an extension-bridge timeout; that performance
gate and user experience acceptance still block a stable release. See
[`plans/2026-11-21-inkscape-native-workspace-v02/findings.md`](plans/2026-11-21-inkscape-native-workspace-v02/findings.md).

## Requirements

- Linux x86-64, glibc 2.28 or newer; tested with Inkscape 1.4.4.
- A **WebSocket-enabled GeWu host** (the v0.2 companion branch identifies as
  GeWu 0.1.8). A version number alone does not enable the service proxy; the host
  must contain the companion changes. Older hosts get `HOST_UPGRADE_REQUIRED`.
- Native runtime: Python 3.11 with `requirements.txt`, TigerVNC `Xvnc`, Openbox
  or Metacity, `gdbus` and `dbus-run-session`. The packed runtime supplies these;
  no physical display or FUSE is required on the server.

The default Marketplace release 0.1.0 is still the old panel editor. v0.2 is not
promoted to stable until its remaining acceptance gates pass.

## Local development install

Build the companion GeWu checkout, then stage the plugin with that SDK:

```sh
export GEWU_ROOT=/path/to/websocket-enabled/GeWu
cargo build --manifest-path "$GEWU_ROOT/Cargo.toml" -p gewu
python3.11 -m venv --copies .runtime
.runtime/bin/python -m pip install -r requirements.txt
export PATH="$PWD/.runtime/bin:$PATH"
python plugins/gewu/package.py --sdk-root "$GEWU_ROOT/sdk/python" --out "$PWD/dist/inkscape"
"$GEWU_ROOT/target/debug/gewu" plugin install "$PWD/dist/inkscape"
```

If required, configure absolute executable paths in the GeWu server settings:

```json
{
  "plugins": {
    "inkscape": {
      "enabled": true,
      "settings": {
        "inkscapeExecutable": "/path/to/extracted-Inkscape/AppRun",
        "editorMode": "native"
      }
    }
  }
}
```

`xvncExecutable` and `windowManagerExecutable` are optional overrides. For a
development checkout with no installed generation, set `plugins.inkscape.package`
to the staged package. Restart GeWu after adding a package through the CLI.

## Use

1. **Preview → Plugins → Inkscape → Open…**: select a workspace SVG or an
   existing workspace directory. A directory uses `figure.svg`; a missing figure
   starts as an unsaved native document.
2. Use **Maximize** when the native window needs more room. Click it again to
   return to the conversation. Native menus, toolbox, canvas, palette and panels
   work directly. The browser retains some reserved shortcuts; use native menus
   for their equivalent actions.
3. Draw paths, drag nodes, edit text and use native layers. Chinese composition is
   committed as Unicode through the focused native text-input bridge. **Paste
   text → Send text** inserts text at the current native text cursor.
4. Select objects and ask the agent to work on them. For example:

   > Keep the selected curves in place. Set their stroke to blue, 0.5 mm wide.
   > Preserve the other objects and layers.

5. **Save** and native **Ctrl+S** write the working document back to the bound
   project SVG. Changes from another editor produce a conflict instead of an
   overwrite. Export SVG remains available to preserve a separate copy.
6. **Import…** chooses files from the browser's machine; dragging files onto the
   workspace also uploads them. SVG, PNG, JPEG and PDF are supported. For PDF,
   select the input page with **Page** and choose editable text or outlines with
   **PDF text**. A PNG/JPEG remains an image; editing its internal curves requires
   its original vector source.
7. Export SVG/PDF/PNG from the current live revision. **Page** selects the export
   page; **PNG DPI** controls raster output. The download is initiated by GeWu
   outside the sandbox. Agent exports can omit `page` for the complete document.

### Save and recovery

Inkscape owns the live document and undo stack. File-based edits cannot run in
parallel with native edits. An agent batch is one native undo step. Refreshing
the plugin view reconnects to the existing native process.

Idle checkpoints and an explicit session close preserve an unsaved recovery SVG.
On reopening, the plugin offers the recoverable state through the native session
status. A changed external project file is preserved, with conflicting recovery
bytes stored separately. Restarting the native process does **not** restore the
entire in-memory undo stack, and inputs newer than the last checkpoint can be lost
on a hard crash.

Use one controlling browser view per document. A second controller is refused.
Internal RFB and websockify traffic uses owner-only Unix sockets; only the
authenticated GeWu service endpoint is available over loopback HTTP. Native
children stay in the host-owned adapter process group for crash cleanup.
The dedicated Inkscape instance is bound to one document; native Open/Save As to
a different document pauses agent access until the binding is restored.

## Agent interface

- `state`, `inspect`, `selection`: revision-pinned, bounded native document data,
  including nested groups, text spans, styles and local path geometry.
- `capabilities`: operation names, or an exact per-operation schema.
- `edit`: one validated batch with `expectedRevision`, optional `requestId`, and
  `operations`. A repeated request ID cannot apply the batch twice.
- `select`, `undo`, `redo`, `save`, `request_status`: shared native state/history.
- `render`, `export`, `handoff`, `handoff_status`: live-revision artifacts and
  versioned Paper/Manimble delivery. Paper defaults to PNG with PDF/SVG optional;
  Manimble uses PNG.

Geometry operations use physical mm in document coordinates. Raw path strings
and `set_path_node` use SVG local units. Live path effects are preserved, not
silently flattened for node edits. Unsupported mutations return an explicit
error. During a drag or composition the human has priority; stale agent writes
are rejected.

The old [Figure layout interface](docs/legacy-v01.md), including checkout and SSH,
is available with `editorMode: "legacy"`. An unresolved v0.1 checkout must be
returned or cancelled there before native mode opens the document.

## Build the packed runtime

```sh
.runtime/bin/python runtime/build.py \
  --inkscape-root /path/to/extracted/squashfs-root --out /new/runtime-tree
bash plugins/gewu/tests/verify_clean_runtime.sh /new/runtime-tree
python "$GEWU_ROOT/scripts/plugin-runtime.py" \
  --root /new/runtime-tree --out /new/runtime-artifacts
```

The builder materializes symlinks, records executable/library provenance and
keeps third-party notices. The external provider is probed first, with a signed
bundled provider as the release fallback. The test runtime is about 1.2 GB
unpacked; the compressed artifact is about 570 MB. Exact release hashes are
recorded by the packer.

## Verification

```sh
export GEWU_ROOT=/path/to/websocket-enabled/GeWu
export GEWU_BIN="$GEWU_ROOT/target/debug/gewu"
export PATH="$PWD/.runtime/bin:/path/to/node/bin:/path/to/cargo/bin:$PATH"
export INKSCAPE_BIN=/path/to/extracted-Inkscape/AppRun
export XVFB_BIN=/path/to/Xvfb
bash plugins/gewu/tests/verify_v02.sh spike
bash plugins/gewu/tests/verify_v02.sh transport
bash plugins/gewu/tests/verify_v02.sh session
bash plugins/gewu/tests/verify_v02.sh agent
bash plugins/gewu/tests/verify_v02.sh assets
```

Additional gates are `interaction`, `install`, `reliability` (30-minute default),
`performance`, `acceptance` (requires `GEWU_REAL_CONFIG`) and `release`. Installation
tests require a signed fixture catalog and packed runtime. Artifacts are kept in
`.gewu-test/`; a passing probe or screenshot does not substitute for interaction,
file fidelity or user acceptance.
