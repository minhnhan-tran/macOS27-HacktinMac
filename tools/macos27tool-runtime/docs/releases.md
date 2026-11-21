# Releases

`tools/m27_release.py` validates and materializes a versioned macOS27Tool release
(schema 1). A release is an assembly of components, each with its own revision
and a SHA-256-pinned manifest; the assembly pins the complete selection.

| Component | Contents | Depends on |
|---|---|---|
| runtime | `/usr/libexec/m27`, `libm27.dylib`, `libm27_identity.dylib` | |
| compatibility | interface units, trusted cdhash list, optional async provider | runtime |
| image plan | M27Exec, install helpers, embedding stubs and manifest, exact replacements | runtime, compatibility |
| AOT recipe | runtime-bound AOT caches and how to generate them | runtime, compatibility |
| build record | the original build record and logs | |

The runtime consumes interface v2 and AOT v8. An AOT v8 cache names the
runtime that generated it (its plain LC_UUID), the cdhash of the guest it was
translated from and the optional CPU features its code may use
(`include/m27/aot.h`, `include/m27/cpufeat.h`); the AOT recipe's
`format_version` is 8 and each guest binding records the header's `id_kind`,
`id_hash`, `code_id` and `cpu_features`. Caches install into a system cache
directory, which serves signed code only, so `check` rejects a binding whose
identity is not a cdhash. Release caches are generated with
`m27 --aot --cpu=baseline`: they may use only the features every supported Mac
has (the Ivy Bridge set: AES-NI, PCLMULQDQ, AVX, F16C, RDRAND), whatever the
build machine has, so they load on every Mac the image boots and their bytes
do not depend on the build machine. `check`, `m27-install.py` and
`m27-image-payload` refuse a cache needing any other feature. AOT v5 to v7
caches and recipes are not accepted: regenerate them with the selected
runtime. Native library UUIDs,
the native-cache identity, OS build, kernel input hash, ARM64e guest and x86_64
host are explicit. The initial CPU profile requires SSE2, CMPXCHG16B and
AVX-capable hardware; the runtime and translated code use at most x86-64-v2
(SSSE3, SSE4.1, SSE4.2, POPCNT), plus `vzeroupper` only where CPUID and XCR0
report AVX state, and `m27` refuses to start on a CPU without
the profile (AVX is not required under Rosetta, where the tests run). The target JSON passed to the tool is a declaration by
the builder, not a hardware probe.

This is a development interface, not an authenticated updater: hashes are not
publisher signatures, and schema acceptance is not behavioural acceptance.

## What is distributed

A release bundle contains m27-owned code, generated contract data, metadata and
recipes. It never contains Apple binaries, modified Apple binaries or
translated Apple code. Embedded executable stubs, the dyld fix and AOT
translations are `generate-locally` entries: their expected hashes and recipes
are in the bundle, their bytes are not. The validator rejects such entries
marked for shipping, and rejects extra objects.


## Payload manifest

`tools/m27-image-payload` writes `OUT/payload-manifest.json`. It is the
contract between m27-runtime and the image builder, checked by the m27-contract
`payload` check (vendored in `tools/m27-contract/`). It records:

- `version`: `OSBUILD-COMMIT12`;
- `commit`: the full commit;
- `clean`: no local changes in the checkout;
- `os_build` and `os_version`: from the root's `SystemVersion.plist`;
- `cache_uuid`: the x86_64 shared cache the units match;
- `units_version`: from `--units-version`, by default the units directory's
  name;
- `units_sha256`: the interface-unit set, as in `build.json`;
- `layout_version`: the install layout `m27-install.py` reads, now 1;
- `aot`: for a payload with AOT caches, their `format_version` (8), the `cpu`
  they were generated for (`baseline`), the union of the CPU profiles their
  headers record (`cpu_profile`, `include/m27/cpufeat.h` bits) and the number
  of `caches`; null without caches;
- `files`: the sha256 of every other file under `OUT`.

The builder declares the OS build and layout version it expects, and refuses
a manifest that is not clean, that differs from what it declares, or that
lists a missing or changed file. `tests/tools/aotpayload.sh` checks a built
payload's manifest with that checker, a changed file, and a dirty flag.

## Usage

```sh
python3.14 -B tools/m27_release.py check BUNDLE --target TARGET.json
python3.14 -B tools/m27_release.py generate BUNDLE --system OS_ROOT \
  --native-cache X86_CACHE --build M27_BUILD --out LOCAL_GENERATED
python3.14 -B tools/m27_release.py preflight BUNDLE --target TARGET.json \
  --system OS_ROOT --native-cache X86_CACHE --kernel-input BOOT_KC \
  --generated LOCAL_GENERATED
python3.14 -B tools/m27_release.py materialize BUNDLE --generated LOCAL_GENERATED \
  --out LOCAL_PAYLOAD
```

- `check` validates the bundle's manifests, dependencies, interface structure
  and AOT header bindings against the target.
- `generate` builds the Apple-derived outputs from the user's own System root
  with fixed macOS27Tool tools; it never runs a command from a manifest. It needs
  macOS, Python 3.14, `timeout`, Apple's compiler and signing tools and a macOS27Tool
  build matching the bundle's runtime (including `m27-stub` and
  `front/stub.o`). Every output must match its recorded hash. Repeat
  `--only PAYLOAD_PATH` to reproduce part of it.
- `preflight` reads the System without writing it and checks source or
  already-applied hashes, bundle resource identities, embedding results, AOT
  source bindings, path exclusions, and the native-cache and kernel-input
  identities.
- `materialize` copies the verified inputs into a new staging directory and
  renames it into place, in the layout `tools/m27-install.py` installs.

Generated and materialized outputs carry a local-only marker: they contain
Apple-derived code and must not be redistributed. None of these commands
modifies a System volume, kernel or EFI partition; installation, sealing and
kernel stamping are steps of the image build.

## Async provider

The compatibility component may contain
`root/usr/lib/m27/libm27_async_contract.dylib` and
`root/usr/lib/m27/async-contract.json`, always together. The descriptor is
schema 1, kind `m27-async-contract`, with `abi: 1`, `identifier`, `version`,
exact `os_build`, `guest_arch`, `host_arch`, `library_sha256` and
`library_uuid`. The validator checks the target, hash, UUID and x86_64 dylib
format without executing it. Such a package requires the runtime component to
declare `async_contract_package_abis: [1]` in its build recipe.

`tools/m27-image-payload --async-provider DIR` validates a provider and
descriptor against the input System's build and stages them beside
`libm27.dylib`. See the [async contract interface](async-contract.md).

## Interface units and the image

A unit is used only for a loaded library with its UUID. `m27-image-payload
--units` therefore fails if a unit names no image of the image's
`dyld_shared_cache_x86_64` (`tools/m27_ifunit.py match UNITDIR CACHE`, which
also checks an installed `/usr/lib/m27/if` copy against a cache). Units
generated for another build, or for `dyld_shared_cache_x86_64h`, are refused.

## Allowlist

`releases/runtime-files.txt` lists every file this repository may contain.
`python3.14 tools/check_distribution.py` rejects any other tracked path;
`--history` checks every commit reachable from the selected ref against the
allowlist that commit carried (`distribution/runtime-files.txt` before the
file moved to `releases/`).
