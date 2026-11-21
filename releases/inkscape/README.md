# Inkscape for GeWu

An editable SVG figure workspace backed by the real Inkscape renderer. A GeWu user and agent share one version-checked edit/undo history. The web editor and adapter use `gewu.plugin/v1`; no custom GeWu build or MCP service is required.

## Install locally

- Linux x86-64; tested with Python 3.11 and Inkscape 1.4.4 (official [AppImage release](https://inkscape.org/release/inkscape-1.4.4/gnulinux/appimage/), SHA-256 in `runtime-linux-x86_64.json`). On systems without FUSE, run `Inkscape-1.4.4.AppImage --appimage-extract` in a dedicated directory and use that directory's `squashfs-root/AppRun` as the executable.
- Start GeWu with the environment's Python on `PATH`. Install Python dependencies and stage against the SDK of the host you use:

```sh
export GEWU_ROOT=/path/to/GeWu
python3.11 -m venv --copies .runtime
.runtime/bin/python -m pip install -r requirements.txt
export PATH="$PWD/.runtime/bin:$PATH"
python3 plugins/gewu/package.py --sdk-root "$GEWU_ROOT/sdk/python" --out "$PWD/dist/inkscape"
gewu plugin install "$PWD/dist/inkscape"
```

Use GeWu config `plugins.inkscape.settings.inkscapeExecutable` if the binary is not on the GeWu server's `PATH` or beside its Python. Example local configuration fragment:

```json
{
  "plugins": {
    "inkscape": {
      "enabled": true,
      "settings": {"inkscapeExecutable": "/absolute/path/to/squashfs-root/AppRun"}
    }
  }
}
```

If you configure a fresh staged package directly, set `plugins.inkscape.package` to its directory. The release descriptor and external runtime provider configuration are under `plugins/gewu/`. No catalog registration is required for local installation.

## Use

1. **Preview → Plugins → Inkscape → Open…**. Enter an existing workspace SVG or a workspace directory. In a directory, **Create figure** creates `figure.svg` explicitly.
2. Import workspace-relative SVG/PNG panel paths, one per line. Shift-click to select multiple panels or drag an empty area to select with a box. Drag panels or use measured positions, dimensions and spacing in the inspector.
3. Ask the agent to align selected panels, replace a changed source, add A–F labels, or check the figure. Undo/redo applies to complete edits, regardless of actor. The agent reads selection and revision through the application's `plugin_call` path.
4. Export editable SVG, PDF, or PNG. Multipage PDF exports all pages; PNG exports the selected page. Native PDF page boxes are subject to Inkscape's point rounding. The browser view contains a native rendered preview, not active SVG markup.

The agent can also save/reapply layout rules and text presets, manage multiple pages, and edit explicitly identified simple text inside an imported panel. Source replacement keeps the panel's ID, top-left and visual width; a new aspect ratio can change its height. Layout checks are advisory. Assets are embedded as supported; static imports with unhandled content are rejected rather than silently rasterized.

**Paper / Manimble:** use **Export for Paper** or **Export for Manimble**, then insert the result through the receiving plugin. Both default to PNG; Paper's current PDF/HTML builds consume PNG directly. For a vector Paper PDF build use `handoff` with `format: "pdf"`; editable SVG is also available. `handoff` can place the versioned asset in an explicitly selected Paper `figures/` or Manimble `assets/` directory. Call `handoff_status` with the returned `manifestPath` to detect a newer figure revision. Manuscript captions and slide references remain the receiving plugin's responsibility.

## Desktop editing and live control

**Check out a copy** freezes embedded writes and creates a workspace SVG for desktop edits. Save and close the copy in Inkscape, then choose **Accept saved desktop changes** to add one embedded undo entry; **Cancel checkout** keeps the original.

For live cowork, configure `nativeSessionDirectory` (a private new directory on the machine that runs both the GeWu adapter and Inkscape). From that machine's graphical session, launch the checked-out SVG:

```sh
.runtime/bin/python plugins/gewu/live.py launch \
  --inkscape /absolute/path/to/squashfs-root/AppRun \
  --figure /workspace/path/to/checked-out/desktop.svg \
  --session /private/path/to/new-gewu-desktop-session
```

This starts a dedicated Inkscape instance with its own profile and D-Bus session. Keep only the checkout open in it. **Follow desktop** polls the live selection and revision about every three seconds while visible; preview rendering and remote snapshot transfer happen only when the document revision changes. The agent uses `live_state` → `live_edit` → `live_render`; Inkscape owns live state and native undo. If a write's outcome is unknown, inspect the native window before retrying.

For a desktop on another Linux host, copy the exact checkout SVG there and run the same launch command from the GUI session with the desktop paths. Install the plugin source or staged package and its Python dependencies on that host, and configure key-based OpenSSH from the GeWu server. Pin the desktop host key in the server's known-hosts file before connecting. In GeWu settings, replace `nativeSessionDirectory` with:

```json
{
  "nativeRemote": {
    "host": "my-desktop",
    "python": "/home/me/env/bin/python3",
    "script": "/home/me/inkscape/plugins/gewu/remote.py",
    "session": "/home/me/private/new-gewu-desktop-session"
  }
}
```

`sshConfig` is an optional absolute path to the SSH client configuration on the GeWu server. The remote helper verifies the checkout's original content hash and refuses to return bytes until native Inkscape has closed. Save and close on the desktop, then click **Accept saved desktop changes** in GeWu.

## Verify

Install `requirements-dev.txt`, install Chromium for the GeWu Playwright harness, and supply an existing GeWu binary and Xvfb. Run from the project with the runtime's Python on `PATH`:

```sh
export GEWU_ROOT=/path/to/GeWu
export GEWU_BIN="$GEWU_ROOT/target/debug/gewu"
export INKSCAPE_BIN=/absolute/path/to/squashfs-root/AppRun
export XVFB_BIN=/path/to/Xvfb
bash plugins/gewu/tests/verify.sh
```

The script exercises the native binary, native desktop undo and document binding, SDK JSONL, the stock host conformance runner, installation in a clean HOME and the real browser/agent execution path with a deterministic provider. Logs and screenshots are written under `.gewu-test/`.

`bash plugins/gewu/tests/verify_ssh.sh` runs both ends over isolated OpenSSH with separate checkout paths, including unsaved changes and saved check-in transfer. `verify_receivers.py --help` describes the real Paper and Manimble build check; `real-model.spec.mjs` uses the existing browser test harness with `GEWU_REAL_CONFIG` and optional `GEWU_REAL_MODEL`.
