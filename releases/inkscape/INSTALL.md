# Inkscape panel plugin 0.1.0: install from source

This is the **0.1.0 panel editor**, exported from
`a854afd4ddd7b127f3673264b6ba0e72ada6992c`; see `SOURCE.json`.
It does not require a WebSocket-enabled host, VNC or a physical display.
The top-level `inkscape/` directory is the separate 0.2 native-workspace preview.

Run on a Linux x86-64 GeWu host with Python 3.11. Install Inkscape 1.4.4 from its
official distribution, or build that application revision using upstream's
instructions. The exact supported AppImage URL and SHA-256 are recorded in
`runtime-linux-x86_64.json`. Verify the checksum before extracting the AppImage
with `--appimage-extract`; its `squashfs-root/AppRun` works without FUSE.

From this directory:

```sh
export GEWU_ROOT=/absolute/path/to/GeWu
export WORKSPACE=/absolute/path/to/workspace
python3.11 -m venv --copies .runtime
.runtime/bin/python -m pip install -r requirements.txt
export PATH="$PWD/.runtime/bin:$PATH"
python plugins/gewu/package.py --sdk-root "$GEWU_ROOT/sdk/python" --out "$PWD/dist/inkscape"
gewu plugin install "$PWD/dist/inkscape"
```

Set `plugins.inkscape.settings.inkscapeExecutable` to the absolute Inkscape
binary or extracted AppRun. Put the same setting in a JSON file for the check
below. Start GeWu with this environment's Python on PATH and restart after CLI
installation. Preserve existing configuration entries and documents.

```sh
gewu plugin check inkscape --version 0.1.0 --probe --workspace "$WORKSPACE" \
  --resource workspace:///figure.svg --resource-type inkscape.document \
  --settings /absolute/path/to/inkscape-settings.json
```

Use an existing workspace SVG or disposable figure. Open the panel in GeWu,
perform an edit, export PNG or PDF, and check that the artifact is non-empty and
readable. Installing Python packages alone is insufficient: the adapter must
also resolve a working Inkscape 1.4.4 executable. No bundled runtime is needed
for this source-install route.
