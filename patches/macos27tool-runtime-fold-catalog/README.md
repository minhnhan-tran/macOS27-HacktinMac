# macos27tool-runtime-fold-catalog

[macos27tool-runtime-fold](../macos27tool-runtime-fold/README.md)'s installation for the B+catalog image of the shared-cache fold-in
(`dev-bs-test-fold-catalog`), in macos27tool-runtime-fold's place. The B+catalog family has its own cache UUID, and macOS27Tool's
generated data names the UUID it was made for, so this image takes a payload built for that family. A set picks
recipes and a recipe names its rows, so the two families' payloads need two recipes.

## Input

`m27_runtime_fold_catalog` (`pipeline/inputs/golden/26B131/`): m27-runtime-26B131-20261118-3a6a5d9-catalog-d3bdb76e,
a payload folder as `m27_runtime` (payload-manifest sha256 checked by the row), from macos27tool-runtime 3a6a5d9 for the
B+catalog family (cache D3BDB76E, row `rebuilt_cache_catalog`).

## How it works

`src/kc-hook.sh` runs the payload's own `m27-install.py` exactly as macos27tool-runtime's hook does, on
`inputs/m27_runtime_fold_catalog`. Variant `cache-catalog` replaces macos27tool-runtime-fold, which replaces macos27tool-runtime.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/macos27tool-runtime-fold-catalog/tests`: the hook is macos27tool-runtime's on this
  row, and only the B+catalog set uses it, in place of both macos27tool-runtime and macos27tool-runtime-fold.
- On the image: `usr/lib/m27/install.json` and the payload's `build.json` name cache UUID D3BDB76E.

## Validation

NEEDS VALIDATION: not built by golden-run. A QEMU boot of an aae0ab3 image with the B+catalog family D3BDB76E and this
payload swapped in reached sshd in 559 s, with no panic and no launchd refusal.

## Revisions
- r4 2026-11-25 `src/kc-hook.sh`: stage `TMPDIR` on `$(dirname "$1")/preboot` (or `$(dirname "$1")/data` root) rather than `data/private/var/tmp` (matches `macos27tool-runtime` r93; `/private` is firmlinked into `system`).
- r3 2026-11-25 `src/kc-hook.sh`: prefer `$(dirname "$1")/data/private/var/tmp` over `/private/var/tmp` for `TMPDIR` (matches `macos27tool-runtime` r92 so AOT staging fits off Recovery's 2.5 GiB tmpfs).
- r2 2026-11-24 `src/kc-hook.sh`: put `TMPDIR` on `/private/var/tmp` when writable (matches `macos27tool-runtime` r91 for `noowners` Recovery sticks).
- r1 2026-11-18 New: the B+catalog set's runtime payload (row `m27_runtime_fold_catalog`, macos27tool-runtime 3a6a5d9 for
  D3BDB76E), in macos27tool-runtime-fold's place.
