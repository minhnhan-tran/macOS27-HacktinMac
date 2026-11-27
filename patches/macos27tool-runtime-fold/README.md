# macos27tool-runtime-fold

[macos27tool-runtime](../macos27tool-runtime/README.md)'s installation for the shared-cache fold-in sets (`dev-bs-test-fold` and the
release fold sets), in macos27tool-runtime's place. The rebuilt cache family has its own cache UUID, and macOS27Tool's
generated data names the UUID it was made for, so these images take a payload built for that family (runtime builds
it from research's generated lock for the family). The row is separate because `m27_runtime` is the payload of every
other set (and `pipeline/latest`, `swaps.py` and release-cut read that row by name).

## Input

`m27_runtime_fold` (`pipeline/inputs/golden/26B131/`): m27-runtime-26B131-20261118-cabc1d5-fold, a payload folder as
`m27_runtime` (payload-manifest sha256 checked by the row), from macos27tool-runtime cabc1d5 for family B (cache 31C0215A,
row `rebuilt_cache`). Its records name that UUID only: the B+catalog family (row `rebuilt_cache_catalog`, set
`dev-bs-test-fold-catalog`) has another UUID and takes [macos27tool-runtime-fold-catalog](../macos27tool-runtime-fold-catalog/README.md).

## How it works

`src/kc-hook.sh` runs the payload's own `m27-install.py` exactly as macos27tool-runtime's hook does, on `inputs/m27_runtime_fold`.
What it installs is macos27tool-runtime's `files` list; it is not declared again here (the fold sets are dev images).

## Verify

- `python3.14 -I -B -m unittest discover -s patches/macos27tool-runtime-fold/tests`: the hook is macos27tool-runtime's on this row, and
  only the fold sets other than B+catalog use it, in macos27tool-runtime's place.
- On the image: `usr/lib/m27/install.json` and the payload's `build.json` name the fold family's cache UUID.

## Validation

NEEDS VALIDATION: r10 not built by golden-run. A QEMU boot of an aae0ab3 image with family 31C0215A and this payload
swapped in reached sshd, with loginwindow and WindowServer up. The payload: 963 embedded, 965 of 969 AOT caches with
none failed.

## Revisions
- r13 2026-11-25 `src/kc-hook.sh`: stage `TMPDIR` on `$(dirname "$1")/preboot` (or `$(dirname "$1")/data` root) rather than `data/private/var/tmp` (matches `macos27tool-runtime` r93; `/private` is firmlinked into `system`).
- r12 2026-11-25 `src/kc-hook.sh`: prefer `$(dirname "$1")/data/private/var/tmp` over `/private/var/tmp` for `TMPDIR` (matches `macos27tool-runtime` r92 so AOT staging fits off Recovery's 2.5 GiB tmpfs).
- r11 2026-11-24 `src/kc-hook.sh`: put `TMPDIR` on `/private/var/tmp` when writable (matches `macos27tool-runtime` r91 for `noowners` Recovery sticks).
- r10 2026-11-18 Row `m27_runtime_fold` -> macos27tool-runtime cabc1d5 payload for fold family B 31C0215A (manifest 8e45ee9e,
  libm27 b739adbd). The B+catalog set takes macos27tool-runtime-fold-catalog instead.
- r9 2026-11-17 Status product (was experiment): it ships in the release profiles. Declaration only; `validated` stays false until a boot proves the re-cut. Nothing it installs changes.
- r8 2026-11-17 Declares its scope (`requires`, docs/requires.md): the fold payload for the rebuilt 26B131 family; x86-64-v2 like macos27tool-runtime. Nothing it installs changes.
- r7 2026-11-17 Row `m27_runtime_fold` -> macos27tool-runtime bfce4a1 payload for fold family B 954aa5de (manifest cde83630,
  libm27 c1a948e9), after golden-gate-shared-cache r7 rebuilt that family.
- r6 2026-11-16 Declares its pack (base): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r5 2026-11-16 Row `m27_runtime_fold` -> macos27tool-runtime 1a290d7 payload (manifest 721ec2eb, libm27 d6b9b712).
- r4 2026-11-15 Declares its installed files (patch.json files). Installed bytes unchanged.
- r3 2026-11-15 Row `m27_runtime_fold`: the units set 2 payload (-B2: same macos27tool-runtime 96870dd and libm27; 16 more B records typed). No recipe change.
- r2 2026-11-15 Row `m27_runtime_fold`: m27-runtime-26B131-20261115-96870dd3f441-B (m27-runtime 96870dd, both fold families); status experiment.
- r1 2026-11-15 New: macos27tool-runtime's installation on row m27_runtime_fold (the payload for the fold-in family), for the two fold sets.
