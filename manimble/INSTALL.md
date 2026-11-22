# Install Manimble 0.2.3 for GeWu

Prebuilt install (default, no Python or Conda on the host) is described first.
The rest is the **manual source fallback** for platforms without a published
runtime. Source installation does not bypass Host compatibility.

## Prebuilt (recommended)

Marketplace → Manimble → **Install**. GeWu verifies the signed artifacts,
restores the pinned Pixi pack in its own directory and runs a probe that renders
a PNG and exports a PPTX. Nothing is installed system-wide.

```sh
gewu plugin marketplace-install manimble --version 0.2.3 --channel beta --runtime auto
gewu plugin check manimble --version 0.2.3 --probe \
  --workspace /absolute/path/to/workspace \
  --resource workspace:///deck --resource-type manimble.deck
```

## Source fallback: platform status

- Verified: Linux x86_64 with glibc.
- Unverified: Linux ARM64 and macOS ARM64. The pins below are known to have
  Conda/PyPI artifacts for common x86_64 and many ARM64 environments, but a
  native render/export check on the target is required. Do not describe ARM64 as
  supported until that check passes.

## Pinned revision

- Adapter, vendored engine and requirements: `f65ca95bdb98d0f442ab72588e979a96916ba6d3`
- Adapter directory: `manimble/`
- Engine directory in that revision: `manimble/engine/` (must contain `manimble/rpc.py`)

```sh
cd /path/to/gewu-plugin-checkout
git checkout f65ca95bdb98d0f442ab72588e979a96916ba6d3
python3 - <<'PY'
import json
m = json.load(open('manimble/plugin.json'))
assert m['id'] == 'manimble' and m['version'] == '0.2.3', m
print('source manifest:', m['id'], m['version'])
PY
```

## Prepare an isolated runtime

Do not modify the system Python or the Conda base. `pycairo` must come from
conda-forge; building it with pip needs Cairo development metadata that a normal
host does not have.

```sh
PREFIX="$HOME/gewu-manimble-0.2.3-runtime"
micromamba create --yes --prefix "$PREFIX" --channel conda-forge \
  python=3.11 cairo pango pkg-config manimpango=0.7.1 pycairo
micromamba run --prefix "$PREFIX" python -m pip install \
  --disable-pip-version-check --prefer-binary \
  manim==0.21.0 manim-slides==5.7.0 Pillow==12.3.0 matplotlib==3.11.2 \
  python-pptx==1.0.2 biocartoon==0.4.3 imageio-ffmpeg==0.6.0
micromamba run --prefix "$PREFIX" python -m pip check
micromamba run --prefix "$PREFIX" python -c \
  'import cairo, manim, manim_slides, PIL, pptx; print(cairo.version, manim.__version__)'
```

`GEWU_PIP_INDEX_URL` may point pip at an approved mirror. TeX is optional:
text, shapes and PPTX export work without it; TeX slides need a matching TeX
installation that the guide does not install for you.

## Stage and register

```sh
ENGINE=/path/to/gewu-plugin-checkout/manimble/engine
WORK="$HOME/gewu-manimble-0.2.3-source"
python3 /path/to/gewu-plugin-checkout/manimble/package.py \
  --sdk-root /path/to/GeWu/sdk/python --out "$WORK/package" \
  --python "$PREFIX/bin/python" --engine-root "$ENGINE"
gewu plugin install "$WORK/package"
```

The staged launcher binds the absolute interpreter and the vendored engine, so
it still works after a GeWu restart with a minimal environment. Keep `$PREFIX`
and `$ENGINE` in place.

## Check and complete one real operation

```sh
gewu plugin check manimble --version 0.2.3 --probe \
  --workspace /absolute/path/to/workspace \
  --resource workspace:///deck --resource-type manimble.deck
```

Create or reuse a deck with `deck/slides.py`, restart GeWu, open the deck, render
a slide and export PPTX. Confirm the PNG is a valid image and the PPTX opens with
the expected slide. A lifecycle-only probe is not acceptance.

## Report

Record the pinned revision, interpreter, Conda/PyPI pins, probe output and the
render/export result. If dependencies are unavailable for the target
architecture, report that as the blocker instead of changing the version or
installing an unreviewed newer adapter.
