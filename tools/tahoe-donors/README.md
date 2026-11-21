# tahoe-donors

Extracts the Tahoe (25G332) files a Golden Gate image needs on Intel: native
userspace files, stock kernel-extension resources, DriverKit, boot assets and
Recovery. Each role becomes one input row (`tahoe_native_donors`,
`tahoe_extensions_donors`, `tahoe_driverkit_donors`, `tahoe_boot_donors`,
`tahoe_recovery_donors`). Tahoe is not installed as the base system.

## Usage

```sh
python3.14 -I tools/tahoe-donors/make --help
```

Use `native --help`, `extensions --help`, `driverkit --help`, `boot --help`
and `recovery --help` for each role's input paths. The source is the Tahoe
25G332 installer unpacked by [`pipeline/unpack`](../../pipeline/unpack/README.md),
with its installer metadata. DriverKit also needs the generic x86_64 cache from
the SystemOS image. The
recipe is [recipes/25G332.json](recipes/25G332.json).

Outputs are new artifact directories with selected payload files and manifests
recording content hashes, modes, ownership, symlink targets and source identity.
No Apple payload is included in Git. The original inputs are read-only.

## Libraries from the donor's shared cache

Tahoe ships some libraries only inside its OS shared cache. The recipe's
`cache_images` names them (today CoreWiFi, from the x86_64h OS cache: Tahoe's
airportd needs the CoreWiFi it was built with). `native --cache DIR` cuts each
one out of the cache family in `DIR` (donor-inputs' `out/os-cache/`) with
the `cache-dylib` tool (r3, pinned in `cache-dylib/`; do not edit it there: its tree is the one
`cache-dylib.pin` names, checked by tests/test_pin.py, and tools/cache-dylib r9 cuts CoreWiFi differently), keeping its install name, signs it
ad hoc, verifies it with cache-dylib's `check.py` (bound GOT slots, resolvable
Swift references, no instruction Ivy Bridge lacks) and adds it to the `system`
role at its own path, with the recipe's owner, mode and flags. The extraction
records (every symbol bound per dependency, the Objective-C repairs) go to
`cache-images.json` beside the manifest.

Apple's extractor cannot do this. `dsc_extractor` (and `dyld_shared_cache_util
-extract`, which calls it), built from dyld-1378 source, writes files for
debuggers: it zeroes the rebase, bind and lazy-bind information and the export
trie, and keeps the cache's segment order. `ipsw dyld extract` (with or without
`--slide`/`--objc`) likewise writes no fixups. dyld refuses all of these
(`__DATA_CONST segment missing SG_READ_ONLY flag`, then `segment '__DATA_DIRTY'
vm address out of order`), and even past that nothing would rebind their
uniqued GOT slots.

The file keeps the original install name and path, so it can serve as a
library provider for a rebuilt shared cache as well as an on-disk file.

## Composition rules

- Copy Golden Gate System/Data first, then apply these donors.
- Overlay the KDK's Intel extensions onto the stock extension resource tree.
- Install complete Tahoe framework bases before OCLP's selected replacements.
- Supply generic x86_64 DriverKit; x86_64h alone does not support Ivy Bridge.
- Rebuild collections around Darwin 27; omit Tahoe's original kernel and KCs.
- Use Tahoe `kmutil` only as a maintenance build tool.

[Tahoe donors](../../docs/inputs.md#tahoe-donors) explains why the KDK and
Universal-Binaries cannot replace every installer input.

## Verification

```sh
python3.14 -I -B -m unittest discover -s tools/tahoe-donors/tests
```

Tests use authored filesystem fixtures for extraction, metadata, hardlinks,
exclusions and installer ordering. They do not mount or modify an OS image.

## Selection before extraction

`selection.py` checks the target's native code and cache image list before Tahoe
payload extraction. Target roots with Intel code are omitted from the extraction
list. Mixed-architecture, missing and resource-only roots remain candidates;
`keep_donor_roots` always takes precedence. The disk-image services remain paired
with Tahoe's IOHDIXController, as established by the 27-first laptop test.

`recipes/intel-donors.json` is the requirements file for `pipeline/unpack/donor-inputs`
(`--requirements`), for every donor build. It names its layout file relative to itself, so
it works from any checkout. The stored 26B131 donor rows (`minimal-*-25G342-26B131`) were
made from it: their run used a copy of this file as of 207d5635 with only `boot_layout`
rewritten to an absolute path (that copy's sha256 `83588560...` is the one the run's
`report.json` records, and donor-inputs' steps on it give the run's `recipe.json` byte for
byte, with the eight roots Golden Gate supplies itself left out). The file has since gained
only `os_cache`, `cache_images` and `cache_images_reason` (r11, Tahoe CoreWiFi), which
the native row's `-corewifi-r3` artifact was made with. No separate per-build requirements
file exists or is needed.

## Revisions
- r16 2026-11-16 The same rows on any host macOS: attributes the host adds to extracted files (com.apple.provenance, com.apple.quarantine, com.apple.macl) stay out of manifests and images, and a cache image's ad-hoc signature is pinned after codesign (`adhoc_signature.py`: codesign on macOS 26 reserves ~18 KB after the blobs, on 27 none). Rows tahoe_boot/recovery_donors (26B131) remade (payloads identical; equal to a fresh macOS 26 Mac's run); tahoe_native_donors' CoreWiFi and tahoe_kc_builder (pipeline/unpack/kc-builder pins the same way) are unchanged and now come out the same on macOS 26.
- r15 2026-11-16 Manifests carry no times at all (the installer listing's times differ between runs of donor-inputs; files get the install time) and no local paths: driverkit's `--source` folders join `tree/`, `unpack/`, `cache/`, and cache-images.json names the cache `cache`. Rows tahoe_extensions/driverkit/native_donors (26B131) remade; payloads identical; the same inputs on a fresh store give the same rows.
- r14 2026-11-16 Deterministic manifest: entries the installer's metadata does not describe record no mtime (it was the extraction time), and `source` names the input (`tree/`, `unpack/`, `cache/`) instead of this Mac's path, so a user's run of the same inputs writes the same manifest. cache-dylib r3 (m27-patcher 8727b717) is vendored in `cache-dylib/` and pinned: the native donors' CoreWiFi was cut with it, and later revisions cut it differently. Rows tahoe_boot/recovery/native_donors (26B131) remade; payloads identical.
- r13 2026-11-15 `recipes/intel-donors.json` adds DeviceToDeviceManager.framework/PlugIns/awdl_d2d.bundle to the system roots: 27.x ships the AWDL D2D plugin arm64e only, so the native x86_64 mDNSResponder could not load it and AirDrop never discovered peers over AWDL (diag#0044). Tahoe's copy carries x86_64 with the same plugin interface. Row tahoe_native_donors (26B131) remade with it; every other entry unchanged.
- r12 2026-11-11 cache-dylib is no longer kept in this repository: found next to it or through `CACHE_DYLIB`; a missing copy is an error before extracting.
- r11 2026-11-11 `native --cache`: the recipe's `cache_images` (Tahoe CoreWiFi from the x86_64h OS cache) are cut out with tools/cache-dylib, verified and added to the system role at their own path; extraction records in `cache-images.json`.
- r10 2026-11-11 Select positively identified native target roots before extracting the Tahoe payload; support fat64 Mach-O headers. DriverKit fixture metadata now follows the actual optional metadata argument.
