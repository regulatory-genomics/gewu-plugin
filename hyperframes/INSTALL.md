# Install the GeWu Hyperframes plugin

## 1. Install from the marketplace

Use a GeWu host that supports `gewu.release/v3` and app origins, then select
Hyperframes in **Plugins → Marketplace → Install**. The signed release contains
the matching Node, Chromium/chrome-headless-shell, FFmpeg/FFprobe, native
modules and fonts. Do not install Node, Chromium or FFmpeg first; the host checks
the OS/architecture and disk budget before downloading.

```sh
gewu plugin marketplace-install hyperframes --channel beta --version <version> --runtime bundled
gewu plugin check hyperframes --probe --workspace <workspace> \
  --resource workspace:///my-video --resource-type hyperframes.project
```

The installed runtime probe captures a real frame, encodes a short video with an
audio track and validates it with FFprobe. A missing/foreign platform artifact
is rejected before installation.

## 2. Source / development installs only

A source checkout can use host dependencies. This path is not the user-facing
marketplace release.

| Requirement | Check |
| --- | --- |
| Node.js >= 22 | `node --version` |
| Chromium / chrome-headless-shell | `chromium --version` or set `chromePath` |
| FFmpeg + FFprobe | `ffmpeg -version`, `ffprobe -version` |

```sh
gewu plugin marketplace-install hyperframes --version <legacy-version> \
  --runtime external --executable "$(command -v node)"
```

## 3. Point GeWu at a browser-facing application origin

Studio runs from its own origin, framed by GeWu. Both origins must be
configured; without them, agent tools still work but opening the Studio view
fails with `APP_ORIGIN_NOT_CONFIGURED`.

```sh
gewu gui \
  --public-origin https://gewu.example.com \
  --plugin-app-origin-template 'https://{view}.apps.gewu.example.com'
```

- Local development: `--public-origin http://gui.gewu.localhost:8430 \
  --plugin-app-origin-template 'http://{view}.apps.gewu.localhost:8430'`.
- `{view}` must be the whole first hostname label. Remote deployments require
  HTTPS and same-site origins (`apps.<site>` under the GUI's site).
- Wildcard DNS/TLS must resolve `*.<label>.apps.<site>` to the GeWu GUI. Any
  tunnel or reverse proxy in front of GeWu must forward the browser `Host`
  unchanged and support streaming (SSE) and `Range`.

## 4. Optional settings

| Setting | Purpose |
| --- | --- |
| `chromePath` | Chromium/chrome-headless-shell used for checks, frames and rendering |
| `ffmpegPath` | FFmpeg binary |
| `ffprobePath` | FFprobe binary |

## 5. Use it

1. Create a project directory in the workspace and open it from **Plugins →
   Hyperframes → Open…**, or let the agent call `project.create`.
2. The Studio view opens in Preview. Edit by hand; ask the agent in the same
   conversation for changes. Both land in the same source and history.
3. Export from Studio's Export button (or the agent's `render`). The render
   status panel shows progress, cancels the worker group, and offers the finished
   video for playback and download.

## 6. Verify an installation

```sh
# full Studio journey against the installed generation and its own .gewu state
GEWU_APP_PLUGIN="$HOME/.gewu/plugins/hyperframes/generations/<id>/package" \
GEWU_INSTALLED_HOME="$HOME" \
GEWU_APP_SPEC=<plugin>/tests/gui.spec.mjs \
  npm --prefix <gewu>/gewu-gui/tests/browser test -- application-plugin.spec.mjs

# protocol: activate, create, inspect, invalid path, cancel, deactivate
python3 <gewu>/scripts/plugin-conformance.py \
  --package <package> --settings settings.json --workspace <ws> \
  --scenario <plugin>/fixtures/contract.json --schema-root <gewu>/gewu-protocol/schema

# native bridge: cross-origin frame, versions, Range, SSE, revocation
npm --prefix <gewu>/gewu-gui/tests/browser test -- plugin-webapp.spec.mjs

# full Studio journey (real Studio, agent tools, drag/undo, frame, render, download)
GEWU_APP_PLUGIN=<package> GEWU_APP_SPEC=<plugin>/tests/gui.spec.mjs \
GEWU_HYPERFRAMES_CHROME=/path/to/chrome-headless-shell \
GEWU_HYPERFRAMES_FFMPEG=/path/to/ffmpeg GEWU_HYPERFRAMES_FFPROBE=/path/to/ffprobe \
  npm --prefix <gewu>/gewu-gui/tests/browser test -- application-plugin.spec.mjs
```

## Known limits of this release

- Linux x86_64 glibc, desktop Chromium, 1920×1080 at 30 fps by default.
- MP4 (H.264/AAC) and WebM export are validated; other upstream formats are not exposed.
- One render per project at a time; no cloud rendering, TTS or generative media.
- Studio's Export routes through GeWu's render lifecycle; a direct POST to the
  native render route is refused so cancellation/ownership stay correct.
- Text rendering depends on host fonts unless the project embeds them.
