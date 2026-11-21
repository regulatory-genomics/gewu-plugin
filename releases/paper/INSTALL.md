# Paper plugin 0.2.0: install from source

This snapshot contains the Paper 0.3.4 application and the **0.2.0** GeWu
adapter, from `02c4f789f87597e260c5ac09380819f158d9dfc4`. See `SOURCE.json`.
The top-level `paper/` directory in this repository is a newer adapter; do not
substitute it for this release.

Run on the GeWu host. Use an installed GHC/Cabal toolchain compatible with
`cabal.project.freeze`, plus Python 3.11. From this directory:

```sh
export GEWU_ROOT=/absolute/path/to/GeWu
export WORKSPACE=/absolute/path/to/workspace
mkdir -p .runtime/bin
cabal update
cabal install exe:paper --installdir="$PWD/.runtime/bin" --overwrite-policy=never
export PATH="$PWD/.runtime/bin:$PATH"
paper --version
paper describe --help
paper inspect --help
paper plan-edit --help
python3 plugins/gewu/package.py --sdk-root "$GEWU_ROOT/sdk/python" --out "$PWD/dist/paper"
gewu plugin install "$PWD/dist/paper"
```

For source installs, set `plugins.paper.settings.executable` to the absolute
`.runtime/bin/paper` path. The adapter also needs Python 3.11 on the GeWu
server's PATH. Keep existing plugin settings and documents when updating the
configuration. Restart the server after CLI installation.

Create a disposable manuscript with `paper init` or use an existing Paper
project. To check it, put the same `executable` setting in a JSON file and run:

```sh
gewu plugin check paper --version 0.2.0 --probe --workspace "$WORKSPACE" \
  --resource workspace:///manuscript --resource-type paper.project \
  --settings /absolute/path/to/paper-settings.json
```

The `manuscript` directory must exist inside that workspace. Open Paper in GeWu,
edit one paragraph and build HTML or DOCX. Check the output is readable.
PDF requires a separate Tectonic installation and is not part of this release's
advertised default acceptance. No bundled runtime download is needed above.
