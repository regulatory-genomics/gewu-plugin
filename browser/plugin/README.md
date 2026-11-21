# GeWu Browser package

Session-scoped GeWu adapter for a managed Chromium browser.

- `bin/browser-gewu` starts the adapter with the selected runtime.
- `lib/gewu_plugin.py` is the vendored GeWu stdio helper; packaging replaces it
  with the canonical `sdk/python/gewu_plugin.py`.
- `web/` is the Preview control panel.
- `skills/browser/SKILL.md` is the agent guidance.

The runtime supplies Python, Playwright and Chromium. Settings and the full
tool contract are documented in the repository README.
