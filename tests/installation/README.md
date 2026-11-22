# Test the installed plugin, not the build tree

Build the release ZIPs. In the marketplace repository,
`deploy/stage-acceptance.py --inputs inputs.json --host-version <version> --out <test-dir>`
creates the release records, catalog, digest paths, and disposable public key.
Input entries have `package` and `runtime` metadata JSON paths, `fixture`,
`installedBytesUpperBound`, and any `resource` / `resourceType` binding.
The catalog used by the offline runner is copied as `index.v1.json` and
`index.v1.json.sig` into the test cache; its signed content can be v3.

```sh
python3 "$GEWU/scripts/check-plugin-clean-install.py" \
  --gewu "$GEWU_BIN" --mirror "$SIGNED_MIRROR" --manifest "$TEST_MANIFEST" \
  --public-key "$TEST_PUBLIC_KEY_B64" --out "$EVIDENCE"
```

Manifest entries carry `id`, `version`, `fixture` (directory relative to the
manifest), `packageSha256`, `runtimeSha256`, optional `channel` (defaults to
`beta`), and, for resource-scoped plugins, `resource` and `resourceType`.
Use one entry per declared plugin, and `--plugin` for individual iterations.
Create inputs with `inputs.example.json` and the staged manifest with
`deploy/stage-acceptance.py`; `manifest.example.json` shows the runner shape.
The controller needs Bubblewrap on Linux. It uses a new HOME, a workspace with
spaces and non-ASCII characters, launcher decoys, and a private network namespace.
It tests after download, with no external package mirrors. Run again in actual
clean OS images/VMs to prove the system-library baseline. The output directory
contains the install/activation result and the host's functional probe logs.

To test an extracted clean rootfs while keeping the controller's signed Range
mirror available, pass `--linux-rootfs` together with `--scenario cold` (or
`--download-first`). The target shares the controller's loopback-only network
namespace, gets a fresh HOME/cache and a private `/tmp`, and the mirror is
stopped before the functional check. `--rounds N` writes independent
`round-1` … `round-N` evidence directories; `--require-plugins a,b` rejects a
manifest that omits any required plugin.
