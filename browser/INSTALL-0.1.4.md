# Install GeWu Browser 0.1.4 from source (manual fallback)

Use this only when the prebuilt runtime cannot be installed, for example on a
platform without a published browser runtime. It is a manual, Agent-assisted
path; it is not an automatic fallback and does not bypass Host compatibility.

## Pinned revision

- Adapter revision: `97a4e985365ce5172895dc47d5ce0cf05ebade85`
- Adapter directory: `browser/plugin`
- Dependency version recorded in this release: **Playwright 1.59.0**
- Verified platform: Linux x86_64 (headless Chromium). Linux/macOS ARM64 is
  **unverified**: a matching Playwright Chromium build and system libraries must
  exist for that architecture.

Confirm the checked-out manifest before preparing dependencies:

```sh
cd /path/to/gewu-plugin-checkout
git checkout 97a4e985365ce5172895dc47d5ce0cf05ebade85
python3 - <<'PY'
import json
m = json.load(open('browser/plugin/plugin.json'))
assert m['id'] == 'gewu-browser' and m['version'] == '0.1.4', m
print('source manifest:', m['id'], m['version'])
PY
```

A newer `browser/` directory (0.1.5 or later) is **not** a substitute for this
release. Do not install a newer adapter under the 0.1.4 generation.

## Prepare a private Python environment

Reuse an existing compatible environment when it already launches headless
Chromium. Otherwise create one GeWu does not own:

```sh
RUNTIME="$HOME/gewu-browser-0.1.4-runtime"
python3 -m venv "$RUNTIME/venv"
"$RUNTIME/venv/bin/python" -m pip install --prefer-binary playwright==1.59.0
if [ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ]; then
  "$RUNTIME/venv/bin/python" -m playwright install chromium
else
  PLAYWRIGHT_BROWSERS_PATH="$RUNTIME/ms-playwright" \
    "$RUNTIME/venv/bin/python" -m playwright install chromium
fi
```

Chromium also needs the usual browser system libraries (fonts, libnss3,
libatk, libgbm and similar). The packaged runtime supplies these; a source
install uses the host's libraries. Missing libraries make the probe fail even
when pip succeeded.

Run the release's own compatibility probe. It launches headless Chromium,
renders an offline page and captures a PNG:

```sh
PYTHON="$RUNTIME/venv/bin/python"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$RUNTIME/ms-playwright}"
"$PYTHON" - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, timeout=10000,
                                args=["--disable-dev-shm-usage"])
    try:
        page = browser.new_page()
        page.set_content("<title>GeWu runtime probe</title><h1>Ready</h1>", timeout=3000)
        assert page.title() == "GeWu runtime probe"
        assert page.screenshot(timeout=3000).startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        browser.close()
print("Playwright Chromium OK")
PY
```

`pip install` alone, or an existing Node Playwright, is not sufficient.

## Stage and register the adapter

The launcher must keep the absolute interpreter and browser cache because GeWu
starts it with a minimal environment:

```sh
WORK="$HOME/gewu-browser-0.1.4-source"
"$PYTHON" /path/to/gewu-plugin-checkout/browser/scripts/stage.py \
  --out "$WORK/package" --python "$PYTHON" \
  --browsers "$PLAYWRIGHT_BROWSERS_PATH"
gewu plugin install "$WORK/package"
```

Keep `$RUNTIME` in place while the plugin is installed. The package is
host-specific; do not redistribute it as a release artifact.

## Check and complete one real operation

```sh
gewu plugin check gewu-browser --version 0.1.4 --probe \
  --workspace /absolute/path/to/workspace --session installation-check
```

Then restart a running GeWu host, open Browser, load a real HTTPS page, take a
screenshot and reconnect after a short network interruption. Verifying only the
adapter lifecycle is not acceptance.

Known limit of 0.1.4: reconnect/NSS failures on real HTTPS sites were reported;
the fix is in the unpublished 0.1.5 candidate. If those checks fail here, record
the failure rather than installing 0.1.5 under this version.

## Report

Record the pinned revision, Playwright/Chromium versions, selected interpreter,
probe result and the real page/screenshot result. If any pinned source is
missing or describes another version, report the blocker instead of substituting
newer code.
