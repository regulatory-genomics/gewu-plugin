# Install Inkscape 0.2.2 for GeWu

Prebuilt core install (Web editor plus headless engine) is the default. The rest
is the **manual source fallback**; it does not bypass Host compatibility.

## Prebuilt (recommended)

```sh
gewu plugin marketplace-install inkscape --version 0.2.2 --channel beta --runtime auto
gewu plugin check inkscape --version 0.2.2 --probe \
  --workspace /absolute/path/to/workspace \
  --resource workspace:///figure --resource-type inkscape.document
```

The core runtime needs no Inkscape, Xvnc, window manager or display on the host.

## Source fallback: platform status

- Verified: Linux x86_64 (prebuilt core runtime).
- This source path is **not verified** on any platform in this release. It needs
  a native Inkscape build with a working command-line SVG engine, which is not a
  Python package and cannot be obtained with pip alone.

## Pinned sources

- Adapter: `1e2240ecb4c70316135d08e81a10e20d05a31507`, directory
  `inkscape/plugins/gewu`
- Adapter manifest check:

```sh
cd /path/to/gewu-plugin-checkout
git checkout 1e2240ecb4c70316135d08e81a10e20d05a31507
python3 - <<'PY'
import json
m = json.load(open('inkscape/plugins/gewu/plugin.json'))
assert m['id'] == 'inkscape' and m['version'] == '0.2.2', m
print('source manifest:', m['id'], m['version'])
PY
```

## Prerequisites

1. A native Inkscape binary on the target architecture that can run SVG
   operations from the command line without a display (the same interface the
   packaged engine uses). Record `inkscape --version`.
2. Python 3.11 for the adapter with `jsonschema`, `lxml`, `Pillow`,
   `cssselect2` and `tinycss2`.

```sh
PYTHON="$HOME/gewu-inkscape-0.2.2-python/bin/python"
python3 -m venv "$HOME/gewu-inkscape-0.2.2-python"
"$PYTHON" -m pip install --prefer-binary \
  jsonschema lxml Pillow cssselect2 tinycss2
"$PYTHON" -c 'import jsonschema, lxml, PIL, cssselect2, tinycss2; print("adapter deps OK")'
INKSCAPE=/absolute/path/to/inkscape
"$INKSCAPE" --version
```

Pin the exact versions you actually used in the report. If the platform has no
native Inkscape CLI build, stop: this guide cannot substitute Inkscape with pip.

## Stage and register

The headless core package needs the runtime layout with `bin/inkscape` and the
headless flag, both provided by the staging options below:

```sh
WORK="$HOME/gewu-inkscape-0.2.2-source"
python3 /path/to/gewu-plugin-checkout/inkscape/plugins/gewu/package.py \
  --sdk-root /path/to/GeWu/sdk/python --out "$WORK/package" \
  --headless --python "$PYTHON" --inkscape "$INKSCAPE"
gewu plugin install "$WORK/package"
```

The staged launcher preserves the absolute interpreter and native Inkscape path,
so it survives a GeWu restart with a minimal environment.

## Check and complete one real operation

```sh
gewu plugin check inkscape --version 0.2.2 --probe \
  --workspace /absolute/path/to/workspace \
  --resource workspace:///figure --resource-type inkscape.document
```

Restart GeWu, open a figure, edit it and export SVG or PNG. Confirm the output
opens and the edit persisted. The native desktop workspace (Xvnc) is a separate
legacy/preview path, not part of this core release.

## Report

Record adapter and Inkscape versions, Python dependency pins, executable paths,
probe output and the exported file. Missing native Inkscape support is the
blocker; do not claim ARM64 support from a Python-only install.
