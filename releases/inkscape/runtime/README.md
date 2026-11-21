# Panel runtime 0.1.1

Reuse the already published Inkscape/Python payload, replacing only its manifest
and probe. The old panel needs Inkscape 1.4.4 and the pinned Python modules, not
the v0.2 WebSocket host or desktop services. This probe checks a real PNG export.
Unused desktop payload files remain to keep this a small, reproducible fix.

From `releases/inkscape/`, choose new output directories outside this checkout:

```sh
export GEWU_ROOT=/absolute/path/to/GeWu
export BUILD=/absolute/path/to/new-build-directory
mkdir -p "$BUILD"
curl --fail --location \
  https://plugin.regulogen.cn/releases/download/inkscape-0.1.0/87bdaac93d8f/inkscape-0.1.0.runtime.zip \
  --output "$BUILD/base.runtime.zip"
printf '%s  %s\n' 87bdaac93d8fc916de60d4c667eb5488fe3cbf56b0bbaeb26a905ab0f3358382 "$BUILD/base.runtime.zip" | sha256sum --check
mkdir "$BUILD/runtime-tree"
unzip -q "$BUILD/base.runtime.zip" -d "$BUILD/runtime-tree"
cp runtime/manifest.json "$BUILD/runtime-tree/runtime.json"
cp runtime/bin/probe "$BUILD/runtime-tree/bin/probe"
chmod +x "$BUILD/runtime-tree/bin/probe"
GEWU_RUNTIME_ROOT="$BUILD/runtime-tree" GEWU_PLUGIN_HOST_FEATURES= "$BUILD/runtime-tree/bin/probe"
python3 "$GEWU_ROOT/scripts/plugin-runtime.py" --root "$BUILD/runtime-tree" --out "$BUILD/runtime-release"
cp runtime/manifest.json "$BUILD/runtime-release/inkscape-0.1.1.runtime.manifest.json"
python3 "$GEWU_ROOT/scripts/plugin-release.py" \
  --descriptor plugins/gewu/release.json --source-root plugins/gewu \
  --sdk-root "$GEWU_ROOT/sdk/python" --out "$BUILD/package-release"
```

The runtime and package archives are new 0.1.1 assets. Do not overwrite the
published 0.1.0 ZIPs. `SOURCE.json` records the unchanged adapter's base commit;
only the package version changes. Source installation can instead follow
`../INSTALL.md` and does not download this base bundle.

Verify the probe on a prepared runtime tree, without a host capability marker:

```sh
INKSCAPE_TEST_RUNTIME="$BUILD/runtime-tree" python3 -m unittest discover -s runtime -p 'test_probe.py'
```
