# Manimble for GeWu

The adapter runs the existing Manimble engine against a selected deck directory.
Opening the deck shows the plugin's own slide editor in GeWu's Preview panel: a
slide list with thumbnails, a canvas where objects are dragged, scaled from the
corners, re-wrapped from a text side and snapped to edges and centres, marquee
and shift-click multi-select, group/stacking order, an inspector for text,
colour, alignment, spacing and rotation, build switching, undo/redo and PPTX
export.

The desktop front end is ~6k lines of macOS AppKit, so its UI cannot be reused in
a browser; what is reused is everything below it. The engine already owns the
deck, the source rewriting and the history, and `apps/windows/README.md` states
the intended architecture: a front end is "only UI" over the same JSON protocol.
The web editor is that second client, and every canvas gesture is one engine
`edit` operation.

Edits go through the engine's own commands, so the canvas, the source file and
the PowerPoint export all describe the same deck. Tool surface: `state`, `scene`,
`slide`, `insert`, `edit`, `undo`, `redo`, plus `describe`, `render`,
`edit_source` and `export`.

Stage and install a copy with the host's matching Python SDK:

```sh
python3 plugins/gewu/package.py --sdk-root "$GEWU/sdk/python" --out /path/to/staging/manimble
gewu plugin install /path/to/staging/manimble
gewu plugin check manimble --probe --workspace /path/to/workspace --settings /path/to/settings.json \
  --resource workspace:///deck --resource-type manimble.deck
```

Settings: `python` is the executable of a Python environment installed with
`engine/requirements.txt`; `engineRoot` is the directory containing `manimble/rpc.py`.
Select the workspace-relative deck directory with resource type `manimble.deck`.
Raw source editing uses revision checks. Render returns real PNG references;
export returns a new `.pptx` only after Manimble completes and the file is valid.
Ordinary text can be editable in PowerPoint; many shapes and effects are rendered
as images. TeX slides need the engine's TeX dependencies.

For a real engine test set `MANIMBLE_ENGINE_PYTHON`, `MANIMBLE_REQUIRE_REAL=1` and
`PYTHONPATH="$GEWU/sdk/python"`, then run `python3 -m unittest discover -s plugins/gewu/tests`.

### Marketplace installation and repair

See [INSTALL.md](INSTALL.md) for the managed install, external-runtime recovery,
probe, registration and minimal render checks. In short: use the
`runtime-plan.json` from the release revision, resolve `pycairo` and
`manimpango` from conda-forge in the isolated prefix before installing the pip
requirements, then run the versioned commands from **Copy repair info for
Agent**. This avoids compiling a native extension against the host's Cairo
headers. For the adapter check, select the deck with
`--resource workspace:///deck --resource-type manimble.deck`.
