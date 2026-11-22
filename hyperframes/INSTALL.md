# Install HyperFrames 0.1.2 for GeWu

Prebuilt install (default) includes Node, Chromium/chrome-headless-shell,
FFmpeg/FFprobe, native modules and fonts. The rest is the **manual source
fallback**; it does not bypass Host compatibility or app-origin requirements.

## Prebuilt (recommended)

```sh
gewu plugin marketplace-install hyperframes --version 0.1.2 --channel beta --runtime bundled
gewu plugin check hyperframes --version 0.1.2 --probe \
  --workspace /absolute/path/to/workspace \
  --resource workspace:///my-video --resource-type hyperframes.project
```

Studio views also need an app origin configured by the administrator. Without
it, agent tools still work but opening the Studio view fails with
`APP_ORIGIN_NOT_CONFIGURED`; source installation cannot fix that.

## Source fallback: platform status

- Verified: Linux x86_64 (prebuilt runtime).
- This source path is **not verified** on any platform in this release. It needs
  Node ≥ 22, Bun (the adapter build script is a Bun script), Chromium, FFmpeg and
  FFprobe for the target architecture, plus a full HyperFrames monorepo checkout.

## Pinned sources

- Adapter: `d19405783c37ee5e2228b215236c5bfb334101f3` (or newer, only after the
  source-sync check below passes), directory `hyperframes/`
- Engine: the HyperFrames monorepo pinned by `hyperframes/SOURCE.json`
  (`repository`, `sourceCommit`, `pluginPath`). The upstream repository may be
  private or moved; use the engine snapshot referenced by `SOURCE.json` in the
  same pinned adapter revision. If it is not reachable, report the blocker.
- Adapter manifest check:

```sh
cd /path/to/gewu-plugin-checkout
git checkout d19405783c37ee5e2228b215236c5bfb334101f3
python3 - <<'PY'
import json
m = json.load(open('hyperframes/plugin.json'))
assert m['id'] == 'hyperframes' and m['version'] == '0.1.2', m
src = json.load(open('hyperframes/SOURCE.json'))
print('source manifest:', m['id'], m['version'], 'engine:', src['sourceCommit'])
PY
```

## Prepare the engine checkout

```sh
ENGINE="$HOME/gewu-hyperframes-0.1.2-engine"
git clone <repository from SOURCE.json> "$ENGINE"
git -C "$ENGINE" checkout <sourceCommit from SOURCE.json>
```

Sync this repository's adapter into the monorepo path declared by
`SOURCE.json` (`pluginPath`) and verify with the canonical check:

```sh
python3 /path/to/gewu-plugin-checkout/scripts/check-source-sync.py \
  --sources /path/to/gewu-plugin-checkout/sources.json \
  --hyperframes-root "$ENGINE" --check
```

Host dependencies, with absolute paths recorded for settings:

```sh
node --version            # must be >= 22
chromium --version        # or a chrome-headless-shell path
ffmpeg -version
ffprobe -version
bun --version             # required by plugins/gewu/build.ts
```

Missing native modules, Chromium or FFmpeg is a blocker; pip cannot supply them.

## Build and stage the package

```sh
cd "$ENGINE"
bun run plugins/gewu/build.ts
python3 /path/to/GeWu/scripts/plugin-release.py \
  --descriptor dist/gewu-hyperframes/release.json \
  --source-root dist/gewu-hyperframes \
  --out "$HOME/gewu-hyperframes-0.1.2-source/package"
gewu plugin install "$HOME/gewu-hyperframes-0.1.2-source/package"
```

Configure absolute host paths in the plugin settings:

```json
{
  "chromePath": "/absolute/path/to/chrome-headless-shell",
  "ffmpegPath": "/absolute/path/to/ffmpeg",
  "ffprobePath": "/absolute/path/to/ffprobe"
}
```

## Check and complete one real operation

```sh
gewu plugin check hyperframes --version 0.1.2 --probe \
  --workspace /absolute/path/to/workspace \
  --resource workspace:///my-video --resource-type hyperframes.project
```

Restart GeWu, create or open a project, render a short video with an audio track
and verify it with `ffprobe`. A lifecycle-only probe is not acceptance.

## Report

Record adapter and engine revisions, Node/Bun/Chromium/FFmpeg versions, the
source-sync result, probe output and the rendered video check. If the engine
snapshot or a native dependency is unavailable, report that blocker instead of
installing a newer adapter.
