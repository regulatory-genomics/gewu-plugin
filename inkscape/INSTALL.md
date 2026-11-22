# Inkscape core installation

The Linux core release is the **Web editor plus headless engine**. It does not
require Inkscape, Xvnc, a window manager or a display on the GeWu host.

Install it from the signed Marketplace, or with:

```sh
gewu plugin marketplace-install inkscape
gewu plugin check inkscape --probe --workspace /path/to/project --resource workspace:///figure --resource-type inkscape.document
```

The release opens the browser editor by default. User and agent edits share one
undo history; SVG, PNG and PDF exports use the packaged Inkscape engine.

The native Inkscape workspace remains a separate technical preview/legacy
installation. It is not part of this core release, and its advanced node, LPE
and extension UI is not claimed as migrated. Use the previous native generation
if that workflow is required; rollback does not modify project files.

The optional PDF profile of other applications is unrelated to Inkscape's PDF
export, which is included in this core runtime.
