# asset-build

Builds a release asset of m27-patcher from this checkout with Xcode and compares it with its input row, so a user
can make every "ours" asset from the tagged source instead of downloading it.

```sh
python3.14 -I tools/asset-build/asset-build list
python3.14 -I tools/asset-build/asset-build ROW OUT [--m27-runtime CHECKOUT] [--oclp-upstream URL_OR_CLONE] [--live-text-inputs DIR]
```

`OUT` is a new folder (a file asset is written as `OUT/NAME`). The command prints `SHA256 ROW`, the sha256 as the
row defines it (`pipeline/lock.py`, so a `# hash:` line applies), and exits 1 when that is not the row's.
A row with made-on-Mac files (metal_argbuf, the live-text rows) prints each published file's sha256 and compares it
with the row's copy in the store; without a store it exits 3 (built, not compared), never 0.
`codeid_carrier` also needs a clean macos27tool-runtime checkout at the commit `asset-build` names.

| Row | Producer | Compared |
|---|---|---|
| display_state_shim | tools/display-state-shim | the dylib |
| io80211_compat, wifip2p_compat, corewifi_compat | patches/golden-gate-userspace/src/{io80211,wifip2p,corewifi}/make `build()` | the dylib |
| windowserver_filter_shim | patches/golden-gate-windowserver-filter/make | the dylib |
| corespotlight_compat_shim | patches/golden-gate-corespotlight-compat/make | the dylib |
| codeid_carrier | patches/golden-gate-userspace/src/codeid/make | carrier.o, libm27_codeid.a (row: `# hash: without build.json`) |
| appstore_slice_guard | patches/golden-gate-appstore-slices/build/make | m27-slice-guard (row: `# hash: release-files`) |
| fl | pipeline/unpack/pristine-tools/build-fl | the binary (ad hoc signed with ent.plist) |
| ripatch | pipeline/unpack/build-tools ripatch | the binary |
| oclp_source | pipeline/unpack/build-tools oclp-source-rebuild | oclp-source.tar |
| hd4000_planes | patches/golden-gate-hd4000-planes/build/make | libhd4000-planes.dylib (row: `# hash: release-files`) |
| libhelloblur | patches/golden-gate-hello/build/make.sh | the dylib |
| metalold_selectors | patches/metalold-selectors/build/make | libmetalold-selectors.dylib (row: `# hash: release-files`) |
| metal_framebuffer | patches/golden-gate-metal-framebuffer/build/metal-framebuffer build | libmetal-framebuffer.dylib (row: `# hash: release-files`) |
| metal_argbuf | tools/metal-argbuf/make, linked against an empty stub with Apple's iCBE install name | iCBE.dylib, libargbuf.dylib (with the store, each against the row's copy; iCBE-real.dylib is made on the Mac) |
| metal_compiler_compat, live_text_framework | tools/live-text/live-text build (needs `--live-text-inputs`, the folder `live-text inputs` makes from Apple's System and OCLP's compiler service) | the row's release files |

The compiled rows were made with Xcode 27.2 (27B52: Apple clang 21.0.0 clang-2100.4.1.1, ld-27106.1, SDK 27.2);
another Xcode can change their bytes. `oclp_source` clones upstream OCLP from GitHub (about 850 MB; `--oclp-upstream`
takes a local clone), applies tools/oclp-integration/target.patch to 3.0.0-rc.5 (e2107e05), checks the tree is the
pinned fd83bda09's, and archives it at fd83bda09's committer time with fd83bda09 named in the pax header; made with
git 2.54.0 (Apple Git-157), with no user or system git configuration.

The Wi-Fi libraries' coverage checks need the build's Apple files and are a maintainer gate, not part of the asset.
The kernel repairs (`kernel_repair_efi`, `kernel_repair_binding`, `intel_commpage_abi`) are not here yet: they
rebuild identically from tools/kernel-foundation and tools/kernel-fixes given their recorded inputs, some of which
are not yet rows a user's Mac can make.

Tests: `python3.14 -I -B -m unittest discover -s tools/asset-build/tests` (`ASSET_BUILD_COMPILE=1` compiles,
`ASSET_BUILD_OCLP=URL_OR_CLONE` rebuilds oclp_source). test_from_archive.py builds from `git archive HEAD`
(a folder with no .git) and checks the rows and build records there; its docstring names the env vars it needs.
