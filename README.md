# GeWu plugin sources

Canonical source for GeWu application plugin adapters, runtime recipes, and
installation repair instructions. Application engines remain in their own
repositories; this repository holds the integration layer GeWu actually loads.

## Plugin source map

| Plugin | Adapter and runtime source | Application or engine source |
| --- | --- | --- |
| GeWu Browser | [`browser/`](browser/) | Same repository: [`browser/plugin`](browser/plugin) |
| Paper | [`paper/`](paper/) | <https://github.com/regulatory-genomics/paper> |
| Manimble | [`manimble/`](manimble/) | [`manimble/engine`](manimble/engine) (snapshot; upstream <https://github.com/pachterlab/Manimble>) |
| Inkscape | [`inkscape/plugins/gewu`](inkscape/plugins/gewu) | <https://gitlab.com/inkscape/inkscape> (application); adapter is here |

Machine-readable source metadata is in [`sources.json`](sources.json). A
Marketplace release should pin this repository at the commit used to build the
package and set the fallback links to that commit:

Exact source snapshots for the older Marketplace releases are under
[`releases/paper/`](releases/paper/) (adapter 0.2.0, application 0.3.4) and
[`releases/inkscape/`](releases/inkscape/) (panel adapter 0.1.0). Each includes
`SOURCE.json` and a matching `INSTALL.md`; use the commit pinned by the release,
not the newer adapters in the top-level directories.

```text
application-source=<application source URL at the exact commit>
plugin-source=https://github.com/regulatory-genomics/gewu-plugin/tree/<commit>/<plugin path>
runtime-recipe=https://github.com/regulatory-genomics/gewu-plugin/tree/<commit>/<recipe path>
installation-guide=https://github.com/regulatory-genomics/gewu-plugin/blob/<commit>/<plugin path>/INSTALL.md
```

The exact plugin and recipe paths are listed in `sources.json`. Do not use a
moving `main` link in a release repair report. Manimble's package embeds the
engine snapshot under `manimble/engine`; `engine/SOURCE.json` records the
upstream base and file digests.

## Runtime recipes

- `browser/runtime/` and `browser/scripts/stage-bundled-runtime.sh` — relocatable
  Python/Playwright/Chromium runtime; `scripts/make-runtime-plan.py` builds the
  provider plan.
- `paper/runtime-plan.json` and `paper/make-runtime-plan.py` — Paper CLI provider.
- `manimble/runtime-plan.json` and `manimble/build-release.py` — external/managed
  Python environment and packed-runtime assembly.
- `inkscape/runtime-linux-x86_64.json` — native Inkscape/AppImage runtime
  metadata.

## Verify

Each plugin keeps its own tests. From a GeWu checkout with Python 3.11 and the
SDK available:

```sh
python -m unittest discover -s browser/tests -p 'test_*.py'
python -m unittest discover -s paper/tests -p 'test_*.py'
python -m unittest discover -s manimble/tests -p 'test_*.py'
```

GUI conformance tests require the corresponding application fixture and the GeWu
Playwright harness. See each plugin README and `release.json`.

## Scope and exclusions

This repository intentionally excludes packaged runtime archives, extracted
Inkscape test environments, caches, build outputs, and downloaded application
binaries. They are immutable release artifacts, not source. Do not commit API
keys or user configuration. The `.gewu-test` and `.preview` directories in a
developer checkout are ignored by this source snapshot.
