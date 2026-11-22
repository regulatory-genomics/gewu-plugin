# Install Manimble for GeWu

Manimble runs the packaged engine against a deck directory. The default
Marketplace install creates a host-owned Python environment; it never modifies
the system Python or conda base.

## Requirements

- Linux x86_64 with glibc, or macOS aarch64
- GeWu 0.1.x
- About 1.5 GB free in `~/.gewu` for the managed environment and render cache
- `micromamba` 2.x on `PATH`, or at the absolute path GeWu reports
- A workspace containing the deck directory (for example `deck/slides.py`)

TeX rendering is optional. TeX slides also need the system TeX packages used by
Manimble; ordinary text, shapes and PPTX export do not.

## Default managed install (recommended)

In the GeWu website open **Marketplace**, select Manimble and click **Install**.
The signed runtime plan installs these conda-forge packages:

```text
python=3.11 cairo pango pkg-config manimpango=0.7.1 pycairo
```

`pycairo` is deliberately resolved from conda-forge. Do not remove it and do not
let pip build `pycairo` from source: the build needs host Cairo development
metadata such as `expat.pc`, which is not part of a normal GeWu host.

Then the plan installs the pinned Python packages:

```text
manim==0.21.0 manim-slides==5.7.0 Pillow==12.3.0 matplotlib==3.11.2
python-pptx==1.0.2 biocartoon==0.4.3 imageio-ffmpeg==0.6.0
```

The same install from a shell:

```sh
gewu plugin marketplace-install manimble --version 0.2.1 --channel stable --runtime managed
```

## Repair or reuse an external environment

Use this path when the managed install cannot run, or when a compatible
environment already exists. Keep it outside the GeWu source tree.

```sh
PREFIX="${HOME}/.gewu/runtimes/manimble-0.2.1-external/prefix"
MICROMAMBA="${MICROMAMBA:-micromamba}"

"$MICROMAMBA" create --yes --prefix "$PREFIX" --channel conda-forge \
  python=3.11 cairo pango pkg-config manimpango=0.7.1 pycairo
"$MICROMAMBA" run --prefix "$PREFIX" python -m pip install \
  --disable-pip-version-check --prefer-binary \
  manim==0.21.0 manim-slides==5.7.0 Pillow==12.3.0 matplotlib==3.11.2 \
  python-pptx==1.0.2 biocartoon==0.4.3 imageio-ffmpeg==0.6.0

"$MICROMAMBA" run --prefix "$PREFIX" python -m pip check
"$MICROMAMBA" run --prefix "$PREFIX" python -c \
  'import cairo, manim, manim_slides, PIL, pptx; print(cairo.version, manim.__version__)'
```

Probe and register that interpreter with the exact Marketplace release:

```sh
gewu plugin runtime probe manimble --version 0.2.1 --channel stable \
  --executable "$PREFIX/bin/python" --json

gewu plugin marketplace-install manimble --version 0.2.1 --channel stable \
  --runtime external --executable "$PREFIX/bin/python"
```

## Check and open

Run the adapter check with the deck selected as a resource:

```sh
gewu plugin check manimble --version 0.2.1 --probe \
  --workspace /absolute/path/to/workspace \
  --resource workspace:///deck --resource-type manimble.deck
```

Restart the GeWu host, open the workspace in the website, select the deck, and
open Manimble. Confirm that the editor lists the deck and displays a rendered
slide. Export a test deck to PPTX as the minimal application operation.

If the install report still says `ENGINE_UNAVAILABLE` or
`MANIM_UNAVAILABLE`, read the redacted attempt log under
`~/.gewu/install-attempts/<attempt-id>/install.log` and repeat the probe before
changing the environment.

## Upstream

- Application engine: <https://github.com/pachterlab/Manimble>
- Adapter, engine snapshot and this guide:
  <https://github.com/regulatory-genomics/gewu-plugin/tree/main/manimble>
- Engine snapshot manifest: [`engine/SOURCE.json`](engine/SOURCE.json)
# Installing a prebuilt Manimble release

On a supported GeWu host, choose the bundled release in Marketplace. GeWu
downloads the signed artifacts and restores the pinned Pixi pack locally.
The bundled release does not require a separately installed Python, Conda or
Pixi. A successful install checks both rendering and PowerPoint export.

Build inputs are fixed by `pixi.toml`, `pixi.lock` and the `v0.7.12` packer
and unpacker digests in `build-release.py`. For a Linux host, build with:

```sh
python3 manimble/build-release.py --target gewu-linux \
  --pixi-pack /path/to/pixi-pack --pixi-unpack /path/to/pixi-unpack \
  --gewu-scripts /path/to/GeWu/scripts --out /tmp/manimble-release
```

The following managed/external instructions apply to legacy releases and
development setups. They are not prerequisites for the new bundled release.
