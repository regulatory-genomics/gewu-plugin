# Install Paper 0.3.4 for GeWu

Prebuilt install (default) first; the rest is the **manual source fallback**.
Source installation does not bypass Host compatibility.

## Prebuilt (recommended)

Marketplace → Paper → **Install**. The signed runtime contains the Paper CLI and
its Python dependencies; the host needs nothing preinstalled.

```sh
gewu plugin marketplace-install paper --version 0.3.4 --channel beta --runtime auto
gewu plugin check paper --version 0.3.4 --probe \
  --workspace /absolute/path/to/workspace \
  --resource workspace:///manuscript --resource-type paper.project
```

The optional `pdf` profile additionally carries Tectonic. It is not required for
HTML/DOCX output.

## Source fallback: platform status

- Verified by the publisher: Linux x86_64 with glibc (prebuilt runtime).
- This source path is **not verified** on any platform in this release. It needs
  a GHC/Cabal toolchain whose package set satisfies the pinned freeze; expect a
  long build and do not attempt it as an automatic fallback.

## Pinned sources

- Application (Paper CLI): `https://github.com/regulatory-genomics/paper/tree/e0094d733910200d122f39faa5cc987b2c87983e`
- Adapter: `857cdea76649840375c2e43fd932c7857211f33e`, directory `paper/`
- Adapter manifest check:

```sh
cd /path/to/gewu-plugin-checkout
git checkout 857cdea76649840375c2e43fd932c7857211f33e
python3 - <<'PY'
import json
m = json.load(open('paper/plugin.json'))
assert m['id'] == 'paper' and m['version'] == '0.3.4', m
print('source manifest:', m['id'], m['version'])
PY
```

Do not substitute the top-level `paper/` directory of a newer commit for this
release. Release snapshots under `releases/paper/` describe older adapters.

## Build the Paper CLI

Use an installed GHC/Cabal compatible with the application repository's
`cabal.project.freeze`. This is not provided by GeWu.

```sh
APP="$HOME/gewu-paper-0.3.4-app"
git clone https://github.com/regulatory-genomics/paper "$APP"
git -C "$APP" checkout e0094d733910200d122f39faa5cc987b2c87983e
RUNTIME="$HOME/gewu-paper-0.3.4-runtime"
mkdir -p "$RUNTIME/bin"
cd "$APP"
cabal update
cabal install exe:paper --installdir="$RUNTIME/bin" --overwrite-policy=never
"$RUNTIME/bin/paper" --version
"$RUNTIME/bin/paper" describe --help
"$RUNTIME/bin/paper" inspect --help
"$RUNTIME/bin/paper" plan-edit --help
```

All three subcommands must exist; a Paper build without them is unsupported.

The adapter is standard-library Python only. Use any Python 3.11 interpreter; an
isolated virtual environment is still preferable so the interpreter stays stable
for the life of the installation:

```sh
python3 -m venv "$HOME/gewu-paper-0.3.4-python"
PYTHON="$HOME/gewu-paper-0.3.4-python/bin/python"
"$PYTHON" -c 'import sys; print(sys.version)'
```

## Stage and register

```sh
WORK="$HOME/gewu-paper-0.3.4-source"
python3 /path/to/gewu-plugin-checkout/paper/package.py \
  --sdk-root /path/to/GeWu/sdk/python --out "$WORK/package" --python "$PYTHON"
gewu plugin install "$WORK/package"
```

Because a source install uses an external CLI, keep the executable in settings.
Write `$HOME/paper-0.3.4-settings.json`:

```json
{"executable": "/home/YOU/gewu-paper-0.3.4-runtime/bin/paper"}
```

## Check and complete one real operation

```sh
gewu plugin check paper --version 0.3.4 --probe \
  --workspace /absolute/path/to/workspace \
  --resource workspace:///manuscript --resource-type paper.project \
  --settings /home/YOU/paper-0.3.4-settings.json
```

Restart GeWu, open the manuscript, edit a paragraph and build HTML or DOCX.
Confirm the output is readable. PDF needs Tectonic and is not part of this
release's default acceptance.

## Report

Record the app and adapter revisions, GHC/Cabal versions, executable path,
probe output and the built document. If the toolchain or pinned packages are
unavailable, report that blocker instead of installing a different adapter.
