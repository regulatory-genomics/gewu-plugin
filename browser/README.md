# GeWu Browser

A session-scoped GeWu application plugin that lets an agent drive a managed
Chromium browser while the user watches the state and can take over.

```text
GeWu agent
   │  plugin tools (tabs.*, page.*)
   ▼
gewu-browser adapter ─── Playwright ─── Chromium
   │
   └── loopback Preview service ─── Browser webview
```

The adapter owns the browser and the tool contract. The Preview webview shows a
live JPEG frame of the active tab, the tab strip and the current URL, and
forwards the user's click, keyboard and wheel input. GeWu injects the private
Preview token on its service proxy; the webview never sees it.

## Install (recommended)

In GeWu Marketplace, choose **Install**. Or run this on the GeWu host as the
user running GeWu:

```sh
gewu plugin marketplace-install gewu-browser --version 0.1.2 --channel stable --runtime auto
gewu plugin doctor gewu-browser
gewu plugin check gewu-browser --version 0.1.2 --probe \
  --workspace /absolute/path/to/workspace --session installation-check
```

Auto follows the signed release's runtime plan. The current plan recipe prefers
an existing **Python** Playwright + Chromium environment, then the bundled runtime.
Older signed catalogs may still prefer bundled; `--runtime external` explicitly
selects reuse and `--runtime bundled` explicitly selects the bundle.

Detection checks `GEWU_BROWSER_PYTHON`, then `python3` on the host's PATH (including
an activated venv). It does not scan every conda/venv directory. The compatibility
probe launches headless Chromium, renders an offline page and captures a PNG,
with a 20-second outer timeout. Merely having Node Playwright or a Chromium file
is insufficient. When no usable environment is found, let Auto download the
bundle instead of manually creating another environment.

For an existing venv outside PATH:

```sh
gewu plugin marketplace-install gewu-browser --version 0.1.2 --channel stable \
  --runtime auto --executable /absolute/path/to/venv/bin/python
```

The selected environment must remain at that path for the installed plugin to
work. Reuse starts a private browser, not the user's existing tabs or profile.
After CLI installation, restart a running GeWu; Marketplace installation refreshes
the live plugin registry. Open Browser, open a page, and use `page.screenshot` to
confirm the resulting PNG is usable. `plugin check --probe` alone checks the
adapter lifecycle, not this user operation.

## Advanced: install the adapter from source

Check out the immutable revision from the release's source link, then run these
commands from its `browser/` directory. The adapter is Python source; Chromium
does not need to be compiled. `plugin/lib/gewu_plugin.py` supplies the vendored
GeWu SDK, so this local staging path needs no GeWu source checkout.

Select an existing compatible Python environment and verify it first:

```sh
PYTHON=/absolute/path/to/venv/bin/python
"$PYTHON" -c 'import json; m=json.load(open("plugin/plugin.json")); assert (m["id"], m["version"]) == ("gewu-browser", "0.1.2")'
"$PYTHON" -c 'import runpy; exec(runpy.run_path("scripts/make-runtime-plan.py")["PROBE"])'
```

Only if no environment is usable, create a new isolated one (the tested dependency
is `playwright==1.59.0`) and repeat the probe:

```sh
RUNTIME=$(mktemp -d "$HOME/gewu-browser-runtime.XXXXXX")
python3 -m venv "$RUNTIME/venv"
PYTHON="$RUNTIME/venv/bin/python"
"$PYTHON" -m pip install 'playwright==1.59.0'
"$PYTHON" -m playwright install chromium
"$PYTHON" -c 'import runpy; exec(runpy.run_path("scripts/make-runtime-plan.py")["PROBE"])'
```

Stage and register the adapter with the selected interpreter bound into its
launcher. Keep the selected environment; it is not copied into the package:

```sh
WORK=$(mktemp -d "$HOME/gewu-browser-source.XXXXXX")
"$PYTHON" scripts/stage.py --out "$WORK/package" --python "$PYTHON"
gewu plugin install "$WORK/package"
gewu plugin check gewu-browser --version 0.1.2 --probe \
  --workspace /absolute/path/to/workspace --session installation-check
```

For a non-default browser cache, set `PLAYWRIGHT_BROWSERS_PATH` during dependency
installation and probing, and pass the same absolute directory as `--browsers`
to `stage.py`. The launcher preserves it when GeWu starts with a minimal environment.
Finish with the restart/open/page/screenshot check above. A local staged package
is host-specific; use the release builder below for redistributable packages.

## Settings

```json
{
  "plugins": {
    "gewu-browser": {
      "enabled": true,
      "settings": {
        "mode": "managed",
        "headless": true,
        "maxTabs": 8,
        "timeoutMs": 15000
      }
    }
  }
}
```

| Setting | Values | Meaning |
| --- | --- | --- |
| `mode` | `managed`, `cdp` | Start a private Chromium, or attach to an explicit CDP endpoint. |
| `cdpEndpoint` | URL | Required for `cdp`, for example `http://127.0.0.1:9222`. |
| `headless` | boolean | Managed Chromium visibility. Default `true`. |
| `maxTabs` | 1–16 | Per-session tab limit. Default `8`. |
| `allowEvaluate` | boolean | Enables `page.evaluate`. Default `false`. |
| `adoptExistingTabs` | boolean | CDP only: list existing pages at startup. Default `false`. |
| `timeoutMs` | 1000–60000 | Per-operation timeout. Default `15000`. |

`cdp` never scans local ports. The operator or user supplies the endpoint. With
`adoptExistingTabs: false`, only tabs this session opens are controlled.

## Tools

| Tool | Effect | Purpose |
| --- | --- | --- |
| `tabs.list` | read | List session tabs without starting the browser. |
| `tabs.open` | write | Start Chromium if needed and open an http(s) URL. |
| `tabs.focus` | read | Choose the tab shown in Preview. |
| `tabs.close` | write | Close a session-owned tab. |
| `page.navigate` | write | Navigate an existing tab. |
| `page.snapshot` | read | Interactive elements with short refs and page text. |
| `page.click` | write | Click a ref from the latest snapshot. |
| `page.fill` | write | Replace editable text. |
| `page.select` | write | Select dropdown values. |
| `page.press` | write | Press a key or chord. |
| `page.wait` | read | Wait for load, literal text, or text to disappear. |
| `page.screenshot` | write | Save a PNG artifact under `.gewu-browser/`. |
| `page.evaluate` | write | JSON result from page JS; disabled by default. |
| `browser.state` | read | Connection, takeover, tabs, focus, active calls. |

Refs are short (`e1`…`eN`). They expire on navigation or the next snapshot.
Every write respects user takeover: agent calls fail with `PERMISSION_REQUIRED`
until the user chooses **Resume agent**. Calls made from the Preview itself are
still allowed so the user can keep working.

## Live Preview

The Preview is a live remote view, not a static screenshot. The adapter exposes
two host-proxied routes:

- `GET /preview/frame` returns a viewport JPEG of the active tab.
- `POST /preview/input` forwards click, type, key, scroll and navigate.

The webview polls frames at roughly 3 fps and maps a click from the displayed
image back to page coordinates. Frame capture shares the Playwright thread with
agent tools, so a long agent call can briefly delay the next frame. Use
`page.screenshot` for a durable PNG artifact, not for the live surface.

Mouse input supports Left / Right / Middle / Double modes, drag, hover, wheel,
and Ctrl/Cmd/Alt/Shift modifiers. Right-click is forwarded to the page; a remote
frame cannot display the browser's native context menu (for example "Open link
in new tab").

Some sites challenge automated browsers. Managed mode uses a normal Chrome UA,
locale and hides `navigator.webdriver`, but PubMed abstract pages still return a
reCAPTCHA challenge from this host; PubMed search and PMC article pages work.
Passing such challenges requires the user's own browser through CDP or the
extension connector.

## Security boundaries

- The adapter is a local executable, not an OS sandbox. Install only trusted
  packages, as with every GeWu application plugin.
- The Preview service binds `127.0.0.1`, mints a random token per activation,
  and GeWu injects it as `x-gewu-browser`. A local process that does not know
  the token cannot read frames or drive the Preview.
- Page text, screenshots, console data and `page.evaluate` values are untrusted.
  They never become instructions, filesystem paths or credentials.
- The webview cannot embed arbitrary sites: GeWu webviews are opaque-origin
  `sandbox="allow-scripts"` frames, and most sites forbid iframing. The Preview
  is a control surface, not the page itself.
- In `cdp` mode, the user's browser is not closed on deactivate; the adapter
  disconnects and leaves the user's profile untouched.
- Screenshots are capped at 8 MiB. Evaluate scripts are capped at 4000
  characters and are off unless `allowEvaluate` is enabled.

## Development

Playwright and Chromium are installed in the local Python environment used by
the tests:

```sh
/data/litian/micromamba/envs/py311/bin/python -m unittest discover -s tests -v
```

The real-browser test starts a local HTTP fixture, drives Chromium, and checks
snapshot/click/fill/wait/screenshot, stale refs, cancellation and cleanup.

Preview UI smoke (needs the GeWu GUI test dependencies and a GeWu binary):

```sh
python3 scripts/stage.py --out /tmp/gewu-browser-package \
  --python /data/litian/micromamba/envs/py311/bin/python \
  --browsers /data/litian/.cache/ms-playwright
GEWU_APP_SPEC="$PWD/tests/gui.spec.mjs" GEWU_APP_PLUGIN=/tmp/gewu-browser-package \
  npm --prefix /data3/litian/projects/GeWu/gewu-gui/tests/browser test -- application-plugin.spec.mjs
```

The GeWu fixture sets a temporary `HOME`, so the adapter cannot find the
development browser cache by default. `--python` and `--browsers` bind those
host-local paths into the staged launcher only; the release packer never uses them.

## Packaging

The package zip contains the adapter, Preview assets, tool schema, skill and the
vendored GeWu stdio helper:

```sh
scripts/build-release.sh /tmp/gewu-browser-release
```

or the equivalent explicit command:

```sh
find plugin -name __pycache__ -type d -prune -exec rm -rf {} +
python3 /data3/litian/projects/GeWu/scripts/plugin-release.py \
  --descriptor plugin/release.json \
  --source-root plugin \
  --sdk-root /data3/litian/projects/GeWu/sdk/python \
  --out /tmp/gewu-browser-release
```

The runtime zip contains Python, Playwright and Chromium. Build it on the target
platform, then pack it with `scripts/plugin-runtime.py`:

```sh
runtime/build-runtime.sh /tmp/gewu-browser-runtime
python3 /data3/litian/projects/GeWu/scripts/plugin-runtime.py \
  --root /tmp/gewu-browser-runtime \
  --out /tmp/gewu-browser-runtime-release
```

The runtime layout is:

```text
runtime.json
bin/python        (wrapper: bundled site-packages + browser path)
bin/launch
bin/probe
lib/python3.11/site-packages/playwright/
ms-playwright/    (Chromium)
```

`bin/probe` verifies the Python import and the Chromium executable without
opening a page.

`build-runtime.sh` currently wraps a host-compatible Python instead of bundling
one. That is enough for a same-host deployment, but a marketplace runtime that
must be relocatable should replace `bin/python` with a python-build-standalone
tree. The adapter and runtime.json do not change.

## Marketplace

The release metadata goes to `gewu-marketplace/catalog/releases/`, and the
package/runtime zips become immutable Gitea Release assets:

```sh
python3 /data3/litian/projects/gewu-marketplace/publisher/make_release.py \
  --package /tmp/gewu-browser-release/gewu-browser-0.1.1.package.json \
  --runtime /tmp/gewu-browser-runtime-release/gewu-browser-0.1.1.runtime.json \
  --base-url http://172.16.211.53:3000/gilbert/gewu-marketplace \
  --source ssh://git@172.16.211.53:2222/gilbert/gewu-browser.git \
  --created-at 2026-11-19T00:00:00Z \
  --notes "Managed Chromium browser with Preview takeover" \
  --capability browser --capability chromium \
  --out /data3/litian/projects/gewu-marketplace/catalog/releases/gewu-browser-0.1.0.json
```

Then upload the assets, rebuild `public/index.v1.json`, and sign it with the
protected marketplace key. Do not commit unsigned index files or binaries.

Install with:

```sh
gewu plugin search browser
gewu plugin marketplace-install gewu-browser --version 0.1.2 --channel stable --runtime auto
gewu plugin doctor
```

## Limits in 0.1.2

- One browser per session; no cross-session tab sharing.
- Frames and shadow DOM are not inspected yet; `page.snapshot` reads the main
  document only.
- The Preview is a remote JPEG frame (about 3 fps) with input forwarding, not a
  native GeWu browser pane. Frames and agent tools share one Playwright thread;
  a native pane would be a separate core/GUI feature.
- No Chrome Web Store extension yet. `cdp` covers an explicitly started Chrome;
  a normal-profile extension connector is a later phase.
