# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] — 2026-11-27

### Added
- `patches/golden-gate-amd-metal-polaris` (r1, macpro71 pack, `requires.gpu` amd-polaris): installs Tahoe 25G342's Polaris Metal driver (`AMDMTLBronzeDriver`), `AMDShared` and its compiler plug-ins (`libSC`, `libAMDIL902`, `libGCNASM`) into the code-less AMD bundles, for the PC's lit-black-screen boot (WindowServer: `Failed to create MetalDevice`, 27 has no AMD Metal driver). New producer `make`, row `pipeline/inputs/25G342/amd_metal_polaris` (store artifact, never committed), `release-classes.json` row, `pipeline/order.txt` and `pipeline/sets/macpro71.txt` entries.

### Fixed
- `tools/cache-dylib` r10: symbol-table section numbers follow the section headers kept after `fit_header` drops one (Tahoe's `AMDMTLBronzeDriver` had five `__DATA` symbols in section 32 of 31, so llvm-objdump and `check.py` refused it); pinned outputs (CoreWiFi, 27's Metal, libKernelCollectionBuilder) are byte for byte r9's. `check.py --cpu haswell` counts, not fails, AVX2/BMI code for Haswell-only dylibs. Tests added.

## [0.3.4] — 2026-11-26

### Fixed
- `tools/macos27tool-runtime` (`src/runtime.c`, `src/aot_use.c`, `src/interpose.c`, new `include/m27/logd.h`, new unit test `tests/aot/logd_test.c`, `Makefile`): fix the boot stall at `Finished power state transition to sleep` (MSI B460M PC, boot-1907). Inside logd (arm64e, run by m27), `m27_diag_init` → `m27_refusal_log_init` set up native libsystem_trace before logd's `main` could call `os_log_set_client_type`. That setup asked logd for its logging preferences, so logd waited on itself forever, every daemon's first `os_log` waited on logd, and `opendirectoryd` never answered user lookups. In logd (`proc_pidpath` ends in `/usr/libexec/logd`) m27 now skips its native logging (refusal log, unified-log mirror, AOT use log) and applies the guest's `os_log_set_client_type` instead of dropping it.

## [0.3.3] — 2026-11-24

### Fixed
- `patches/opencore-macpro71` (`src/macpro71.py`, `tests/test_macpro71.py`): set `Booter.Quirks.ResizeAppleGpuBars = 0` (so `IOPCIFamily` still maps AMD GPU VRAM when Re-Size BAR is enabled in BIOS) and remove `AppleIntelCPUPowerManagement.kext` / `AppleIntelCPUPowerManagementClient.kext` from `EFI/OC/Kexts` and `Kernel.Add` (Comet Lake uses XCPM and freezes on those Ivy Bridge OCLP kexts).
- `patches/macos27tool-runtime` r91-r93 (and `macos27tool-runtime-fold` r11-r13, `macos27tool-runtime-fold-catalog` r2-r4) `src/kc-hook.sh`: stage `TMPDIR` on `$(dirname "$1")/preboot` (falling back to `$(dirname "$1")/data` root or `/private/var/tmp`), avoiding `M27`'s `noowners` rejection, Recovery's 2.5 GiB `/private/var/tmp` tmpfs running out of space on ~3.5 GiB AOT payloads, and the `/private` firmlink that caused `m27-install.py` `legacy_stubs(system)` to walk staged stubs under `data/private/var/tmp`.

## [0.3.2] — 2026-11-24

### Added
- `patches/golden-gate-metal-gva` (r2): OCLP AppleGVA JPEG-decode-unavailable edit (`edit-gva.py`, `kc-hook.sh`, `edits/applegva.json`, tests); opt-in pack `metal-3802-extras`, listed in `pipeline/order.txt`.
- `tools/metal-llvm` (`make`, `arch-gate`, `NOTICE.md`), `pipeline/inputs/common/metal_llvm`, and the `metal_llvm` row in `pipeline/release-classes.json`: pinned LLVM 15.0.7 AIR tools plus the x86_64 legacy assembler, so a Mac without Xcode can make the golden-gate metallib rows (`cache/metallib/make --llvm-bin`, `toolchain.optimize_ir`).
- `tools/metal-argbuf`: C0 inline-buffer constant-pointer rewrite fix (`igil_rewrite.c`) with `test_constant_pointer_through_inline_buffer` and fixtures; `patches/golden-gate-metal-argbuf` r12.
- `pipeline/ci`: `tools/*/*/tests` suites are found; an `--only` selector that matches nothing now fails (exit 2); `--list` removes its worktree.
- `pipeline/candidate-cut`, `tools/acquire/acquire` and `tools/macOS27Tool/src/installer.py` (upstream `3e1ee849`): a candidate release exports `M27_BOOTSTRAP_FILES`, `acquire` never falls back to the unpublished candidate URLs (they are rewritten to the staged folder), and `create-installer` fetches from the staged cut; tests in `pipeline/tests/test_candidate_cut.py` and `tools/acquire/tests/test_acquire.py` use this fork's release URL prefix.
- `pipeline/make-on-mac`: runtime variants (`m27_runtime_fold`, `m27_runtime_fold_catalog`) complete their own payload from a per-row root view mounted without owners.

### Changed
- `tools/macOS27Tool` `frontdoor.where` treats a Rosetta-translated x86_64 process as a build host; `doctor` skips unpublished candidate-hosted URLs, accepts a frozen source tree and checks the pinned LLVM tools instead of Xcode.
- `patches/dev-capture` r5 and `patches/dev-diagnostics` r11: no `m27_crash=1` in dev images (first boot stalled before sandboxd under QEMU); `macos27tool-opencore` r86 documents SIP 0x807.
- `patches/golden-gate-font-registry` r11 and `golden-gate-icon-caches` r10: caches fontd/iconservicesd rewrite at first boot are `"content": "seed"`; `golden-gate-native-identities` r13 skips `programs.txt` lines whose libraries are not installed.
- `tools/recovery-spikes`: the host check is labelled `suite:macOS27Tool` (it runs the `macOS27Tool` suite); `test_cli.test_changelog_matches_version` skips inside the spike's `code.tar`, which ships `CODE` but not `CHANGELOG.md`.
- `pipeline/tests/fixtures/golden-image-commands.json` re-recorded for the `cache-build` / `cache-install` layers.

## [0.3.1] — 2026-11-23

### Added
- `patches/golden-gate-userspace` (r95) and `pipeline/stages/golden-image.py`: added `cryptex_cache.hole_map`, `allocate`, full-allocation `copy_file`, `compact_image` invocation inside `build_os_image`, and `allocate_cryptex_images` in `posts()` to prevent Preboot APFS graft blockmap errors on sparse holes (`#90`), plus `tests/test_compact_image.py`.
- `tools/ssdimg/generalize.py` and `tools/ssdimg/tests/test_generalize.py`: remove `private/var/db/.configureLocalKDC` alongside the System keychain and `KerberosKDC.plist` so generalized images generate a fresh local KDC realm on first boot.

### Fixed
- `tools/macOS27Tool/src/builder.py` (`#91`): `kit_ownership` now probes real file ownership under the resolved `assembly_local` work directory (`new_file_owner` + `mount_point`) instead of relying on `diskutil info`, runs `diskutil enableOwnership`, and stops before erasing if ownership still fails.
- `tools/macOS27Tool/src/verify.py`: skip checking file `mode` on `"content": "seed"` files rewritten by macOS (while keeping presence, type, owner, and flags checks).
- `tools/macOS27Tool/src/safety.py`: handle dangling symlinks (`FileNotFoundError`) in `others_can_change_tree`.
- `tools/macOS27Tool/src/stick.py` and `installer.py`: generate Apple-format year/week characters (`SERIAL_YEARS` / `SERIAL_WEEKS`) for generic USB installer serials, lock `M27` volume permissions via `close_tree`, and skip `sudo` (`own_device` / `as_admin`) when `--image` attaches a user-owned disk image.
- `tools/macOS27Tool/src/console.py`: pass `_environ=c.env` / `_environ=self.env` to Rich consoles so `Console` env overrides process `TERM`/`NO_COLOR`.
- `pipeline/stages/inputs.sh`: quote `$KIT_ON_DRIVE` and `"$(lock_of "$n")"` when staging inputs on drives with spaces.

## [0.3.0] — 2026-11-23

### Added
- `tools/shared-cache`: ported upstream r90–r95 updates (pinned `x86_64` LLVM disassembler `llvm-disasm/`, `swift-demangle/`, `package-user-inputs`, `arch-gate`, portable `helpers/` / `consumer-helpers/` / `runtime-helpers/`, `src/dyld_rebuild/{artifacts,host_tools,x86,helper_build,helper_sign,source_identity}.py`, `inline-hook.h`, `prespecialization-adoption.py`, `swift-dependency-replay.cpp`, `swift-witness-*`) while preserving fork `m27` naming.
- `pipeline/cache-rebuild/` (`26B131.json`, `26B131-inputs.json`) and `pipeline/inputs/common/` (`shared_cache_builder`, `shared_cache_installer`, `shared_cache_user_inputs`, `llvm_disasm`, `swift_metadata_catalog`), plus `cache_rebuild` / `cache_rebuild_hook` support in `pipeline/release-classes.json`, `pipeline/release-cut`, and `pipeline/make-on-mac`.
- `patches/golden-gate-shared-cache-install`: two-phase (`M27_CACHE_PHASE=build|install`) post-install / pre-first-boot shared-cache rebuild recipe staged in `pipeline/order.txt` with `m27` paths (`/private/var/db/m27/cache-install`, `/private/var/db/m27/manifests`).
- `tools/macOS27Tool/src/cachebuild.py` and `m27 cache build` CLI command, integrated with `space.py` (55 GB scratch budget notice), `frontdoor.py`, `installer.py`, `builder.py` (`RESUMABLE`), `pipeline/golden-run`, and `pipeline/stages/golden-image.py` (`cache-build` / `cache-install` layers and durable `write_build_record` checkpoints).

### Changed
- `patches/golden-gate-userspace/src/cryptex.py` and `patches/golden-gate-shared-cache` (r11): added `compact_image` / `fitted_size` / `built_path` so folded OS cryptex images compact to used bytes before sealing.
- `patches/macos27tool-command` (r13): stages `tools/acquire/acquire` alongside `tools/macOS27Tool` for on-target `m27 cache build`.

## [0.2.7] — 2026-11-22

### Fixed
- `create-installer` wrote the kit's base OpenCore (MacBookPro9,2, 4 kexts, no SSDTs) to a `macpro71` stick, because that target has no `esp` row and the kit's own ESP was used silently. `--opencore` now also takes the `boot-efi.zip` of the image built for the target (its `payload/EFI` is repacked into an ESP tar; a zip without `payload/EFI/OC/config.plist`, or with a path that leaves `payload/`, is refused), and the fallback to the kit's base ESP now prints a warning. The stick written on 2026-11-22 was repaired with `stick.py rewrite-boot` from that zip and its EFI checked file by file against the image.

## [0.2.6] — 2026-11-22

### Added
- `opencore-macpro71` r4: the board settings of the PC's own working EFI (`Hackintosh/i5 10400/EFI`) that the QEMU reference base cannot know: Booter quirks `DevirtualiseMmio`, `ProtectUefiServices`, `RebuildAppleMemoryMap`, `SyncRuntimePermissions` on and `EnableWriteUnprotector` off; `ProvideCurrentCpuInfo` and `AppleCpuPmCfgLock` off, `ReleaseUsbOwnership` and `ResetLogoStatus` on; its seven SSDTs (EC, PLUG, USBX, SBUS, MCHC, RTCAWAC, USB-Reset; kept as data in `patches/opencore-macpro71/src/ACPI`, each checked as a valid table by the test); the `XHCI-unsupported` injector for the 400-series xHCI (`user-supplied`); WhateverGreen 1.7.1 (new pinned row `whatevergreen_release`) with the UHD 630 `AAPL,ig-platform-id` `0300c89b`. Every new element has a recorded reason. Host tests only: not booted.
- Not ported (left to the user): AMFIPass and the `csr-active-config` it needs, the `itlwm_*` / `-itlawdl*` boot-args (they belong to AirportItlwm), NVMeFix, RestrictEvents, the SMC sensor kexts and the XHCI port-limit choices; `-no_compat_check` stays as the dev build has it.

## [0.2.5] — 2026-11-22

### Fixed
- `.gitignore`: unignore `patches/opencore-macpro71/src/UTBMap.kext/` so the codeless USB port-map `Info.plist` is tracked in git instead of being skipped by `*.kext/`.

## [0.2.4] — 2026-11-22

### Added
- `opencore-macpro71` r3: the PC's audio layout and USB port map, taken from its own working EFI (`Hackintosh/i5 10400/EFI`): AppleALC `layout-id` 1 on the ALC892 (`PciRoot(0x0)/Pci(0x1f,0x3)`), and the 9-port `UTBMap.kext` (kept as data in `patches/opencore-macpro71/src/UTBMap.kext`, provenance kind `user-supplied`) applied by USBToolBox 1.2.0 (new pinned row `usbtoolbox_release`). Host tests only: not booted.

## [0.2.3] — 2026-11-22

### Added
- `opencore-macpro71` r2 carries the MacPro7,1 PC's own kexts: RealtekRTL8111 (Ethernet), itlwm (AX210 Wi-Fi), IntelBluetoothFirmware, IntelBTPatcher and BlueToolFixup (AX210 Bluetooth), AppleALC (ALC892 audio, layout-id not chosen yet). Five new pinned input rows in `pipeline/inputs/common` (`appleac_release`, `rtl8111_release`, `itlwm_release`, `intelbt_release`, `bluetoolfixup_release`) with their `pipeline/release-classes.json` entries, provenance and reasons in `pipeline/targets/macpro71.json`. Host tests only: not booted. itlwm has no macOS 27 AirportItlwm build, so Wi-Fi joins through HeliPort.

## [0.2.2] — 2026-11-22

### Fixed
- `macpro71` set: it was copied from `generic.txt`, whose `golden-gate-store` recipe failed in the builder VM on this image, and it lacked the Golden Gate recipes. It is now the `golden-gate-26B131` set with `opencore-macpro71` instead of `opencore-mbp92`, less the recipes only the MacBook's packs bring (HD 4000, Metal 3802, Broadcom Wi-Fi). Built: `macpro71-26B131` committed (40 min); not booted.

## [0.2.1] — 2026-11-22

### Added
- `pipeline/flow`: one command from three inputs (macOS 27 version, macOS 26 version, Hardware Sniffer `Report.json`) to a built image: reads the report, refuses a machine the macpro71 pack cannot serve (not Intel, no AVX2, not a desktop, no discrete Polaris GPU) with the reason, checks both versions have input rows, writes the target, profile, OCLP hardware profile and assembly (`--write`), runs `patches.py plan`, and with `--build` hands the assembly to `pipeline/build`. Lists the devices the pack carries a kext for (Realtek Ethernet, AX210 Wi-Fi, Intel Bluetooth, ALC audio). Test and fixture: `pipeline/tests/test_flow.py`, `pipeline/tests/fixtures/report-msi-b460m-i5-10400.json`.

## [0.2.0] — 2026-11-22

### Added
- `macpro71` target: an Intel PC started as a MacPro7,1 (first proof: MSI B460M PRO, Core i5-10400, Radeon RX 580): `pipeline/targets/macpro71.json`, `packs/macpro71.json`, `pipeline/profiles/macpro71.json`, `pipeline/sets/macpro71.txt`, assembly `macpro71-26B131`, OCLP hardware profile (no patch sets: Tahoe drives MacPro7,1 and Polaris itself) and the `opencore-macpro71` patch (SMBIOS MacPro7,1, board id `Mac-27AD2F918AE68F61`, VirtualSMC, CryptexFixup, the QEMU reference's firmware quirks). Host tests only: not built or booted yet. Not carried: Realtek 8168 Ethernet, AX210 Wi-Fi/Bluetooth, ALC892 audio (kexts this repository does not pin yet).

## [0.1.3] — 2026-11-22

### Fixed
- `create-installer`: when macOS 27 refuses the user's `diskutil mount` of the new drive's FAT32 EFI partition ("failed to mount"), mount it through the same `sudo -n` the erase already asked the password for, instead of stopping after the drive was erased.

## [0.1.2] — 2026-11-21

### Fixed
- `codeid_carrier` (golden-gate-userspace r94): `link` selects the `_hao_carrier_*` / `_hao_carrier_original_*` hook symbols when linking an archive pinned before the `hao` -> `m27` rename, instead of failing with `0 definitions in the subcache` after `load_objects` succeeded. Added a linking test for pre-rename hook names.
- `docs/MANIFEST.md`: regenerated after bumping `golden-gate-userspace` revision so `test_manifest_is_current` passes.

## [0.1.1] — 2026-11-21

### Fixed
- `codeid_carrier` loads a folder pinned before the `hao` -> `m27` rename (`build.json` key `hao_runtime`, archive `libhao_codeid.a`) as well as a new one; the rename had made `load_objects` raise `KeyError` on the tracked pin, which failed the `golden-gate-userspace` recipe in the guest. Test added.
- `kc-hook.sh` (darwin27-kernel r16): Tahoe's `libKernelCollectionBuilder` is loaded on every host, not only an Intel one (the arm64 builder VM on 27.2.1 refused x86_64), and Rosetta is installed when that VM lacks it (exit 32 if it cannot be). Removed an always-true `if`.
- `golden-image`: the ESP mounts through `diskutil` when `/sbin/mount_msdos` fails (rc 71 on the 27.2.1 guest).
- `release-cut`: a refused release leaves nothing in `--stage`; a harvested row with no producer commit is refused before its tar is written.
- `test_recovery_commands` allows the new `softwareupdate` call in `kc-hook.sh`.

## [0.1.0] — 2026-11-21

### Changed
- **Breaking:** renamed the remaining `hao` identifiers to `m27` across the tree (about 18,000 lines): `hao_*`/`HAO_*` -> `m27_*`/`M27_*`, `libhao` -> `libm27`, `/usr/libexec/hao` -> `/usr/libexec/m27`, `HaoExec.kext` -> `M27Exec.kext`, the `include/hao`, `tools/hao-*` and `kext/HaoExec` folders, the `local.hao.*` daemon labels and the `hao-contract` tool. Environment variables are now `M27_*`.
- Also renamed `hao-runtime*` (pinned artifact folders, lock rows `hao_runtime*` -> `m27_runtime*`, `qemu_esp_hao` -> `qemu_esp_m27`) and `hao-patcher`/`hao_patcher`. The tracked sha256 pins are unchanged, but the artifact folders under `store/artifacts/` must be renamed to match (`hao-runtime-*` -> `m27-runtime-*`). Third-party upstream URLs (`github.com/lythka/hao-*`) are left as they are: those repositories are not ours to rename.
- Kept as `hao-release`: the SSH signing namespace. It is a domain-separation string inside existing signatures, so changing it breaks verification of every signed release; the history in this changelog and the NOTICE/LICENSE files are also untouched.
- Re-locked `tools/macos27tool-runtime/generated.lock` (18 hashes; each changed file is byte-identical to its old self under the token swap) and re-pinned the vendored contract checker in `tools/macos27tool-runtime/tools/m27-contract/PIN`.
- `golden-gate-deep-sleep-off` is marked `NEEDS VALIDATION` again: its daemon label, plist name and marker changed, so the earlier on-laptop proof no longer covers the renamed bytes.

### Notes
- Not booted on Intel hardware. Runtime suite on the renamed tree matches the old tree test for test (the same failures, all guest tests that need a runnable x86 guest); the pipeline and tool suites pass apart from the known failures.
- Images built before this version install the old `hao` paths and kext ID; rebuild rather than mix.

## [0.0.16] — 2026-11-20

### Fixed
- `pipeline/vm/vmctl` `bridge_if`: pick the bridge whose `vmenet` member carries the `VIRTIO` flag (a Virtualization.framework VM) instead of the first bridge with any `vmenet`. With macOS Internet Sharing on, `bridge100` also has a `vmenet` and stays up with no VM running, so the agent server bound to the wrong bridge and a guest never reached it ("no agent after 420 s"). Falls back to the first `vmenet` bridge as before.

## [0.0.15] — 2026-11-20

### Fixed
- `pipeline/vm/clones.py` `provision`: create `<vm>.run/` before opening `provision.log` in it. `clean-vm make-base` provisions a bare base that has no `.run` folder yet (only clones get one), so it failed with `No such file or directory: …/base-golden.run/provision.log` right after the guest install.

## [0.0.14] — 2026-11-20

### Fixed
- `pipeline/stages/inputs.sh`: quote the command substitutions that take a path (`take`, `stage`, `where`, the staged-row links). zsh split them at a space, so a checkout whose path contains one (such as `~/Documents/Source Code/...`) failed with "is not a file" or a `where()` TypeError before reaching the real inputs.

## [0.0.13] — 2026-11-20

### Fixed
- `README.md` Build progress: the counts from `release-cut --dry-run` are 44 ready and 10 unrecorded (0.0.12 said 42 and 12). The download row now lists what its 21 rows are, not only Apple inputs, and the unrecorded `ours` rows (including `oclp_source`) say whether `lock.py path` confirms their pinned sha256.

## [0.0.12] — 2026-11-20

### Changed
- `README.md`: title and prose use this repository's name, and a new "Build progress" section tracks the macOS 27 Intel installer build — per-stage state and row counts read from `release-cut --dry-run` (64 rows: 42 ready, 10 absent, 12 unrecorded), with a checklist of what each blocker needs.

## [0.0.11] — 2026-11-20

### Changed
- `OTHER_HOSTS` (hao-contract) names this repository's runtime releases (`…/macOS27-HacktinMac/releases/download/runtime-*`) instead of `lythka/hao-runtime`; the `runtime-` tag prefix keeps them apart from kit releases, so ordinary assets are not mistaken for hosted ones. Its test uses the new host.
- `BUILD-ON-M-MAC.md`: the pinned runtime commit comes from a worktree of this repository, not a clone of `lythka/hao-runtime`; the "no releases" note no longer names the upstream repositories.
- Unchanged on purpose: the licence and `NOTICE.md` copyright lines, the donation link, the pinned `component.json` note, the vendored `tools/macos27tool-runtime/tools/hao-contract` copy (pinned by sha256 and checked by the runtime's tests), and this changelog's history.

## [0.0.10] — 2026-11-20

### Fixed
- `pipeline/release-classes.json`: the `hao_runtime` row no longer names a url. 0.0.9 pointed it at this repository's Releases, which made it both a "hosted elsewhere" asset (`release-cut` staged it in `STAGE/external/`) and an ordinary release asset (`check_stage` looked for it in `STAGE/`, and the contract refused a url that is neither in `OTHER_HOSTS` nor `ASSET_URL`). Published from this repository it is now an ordinary release asset, staged and checked with the rest.

## [0.0.9] — 2026-11-20

### Changed
- The runtime's clone step, security-report link, the `hao_runtime` row comments and the `release-classes.json` payload url now point at this repository instead of `lythka/hao-runtime`. The runtime source is already vendored under `tools/macos27tool-runtime`.
- Unchanged: the `OTHER_HOSTS` entry in `hao_contract.py` and its test (still accepts that host, and the vendored `hao-contract` copy is pinned), and the pinned payload artifact names.

## [0.0.8] — 2026-11-20

### Changed
- Releases are hosted on this repository's GitHub Releases, not `lythka/hao-assets` (which has no releases): `release-cut` `ASSET_URL`, the `hao_contract.py` `URL_PREFIX` and the contract schemas' default and `$id`, the release-check default owner/repositories, the installer's "hao" source test, and the tests that name them.
- `docs/versioning.md`: kit releases are tagged on this repository's GitHub Releases.
- `README.md`, `docs/install.md` and `docs/building.md` no longer `curl` a `release.json` that does not exist; new "Getting release.json" section explains generating it with `release-cut --write` and using it locally.

## [0.0.7] — 2026-11-20

### Changed
- Every `https://github.com/lythka/hao-patcher` URL (clone, milestones, issues, security advisory, docs and the release-schema `$id`) now points at `https://github.com/minhnhan-tran/macOS27-HacktinMac`. The clone steps drop `--branch v0.0.1`, because this repository has no tags.
- Unchanged: the pinned `patches/golden-gate-corespotlight-compat/build/component.json` note (its text is part of what the patch installs, so editing it invalidates the validation), the `hao-patcher` labels the pipeline uses for "this repository" (`release-cut`, `reproductions.json`, `release-classes.json`) and the `lythka/hao-assets` release download.

## [0.0.6] — 2026-11-20

### Added
- `BUILD-ON-M-MAC.md`: the guide for building the macOS 27 installer for an Intel Mac on an Apple silicon Mac with Xcode 27, linked from `README.md`.

## [0.0.5] — 2026-11-20

### Fixed
- `docs/inputs.md`: build the pinned `hao-runtime` commit in a worktree under `store/workspace/` (git-ignored, inside this repository) instead of a sibling folder outside it.

## [0.0.4] — 2026-11-20

### Added
- Vendor `lythka/hao-runtime` (main `0714743`, full history) under `tools/macos27tool-runtime`, so the pinned commits (`256dd30`, `273ba98`, `4b021c3`) resolve from this repository.

### Changed
- Docs link to the vendored folder instead of the external repository; `docs/inputs.md` builds the pinned commit from a worktree of this repo.
- Standalone `hao` in the vendored Markdown is now `macOS27Tool`. Identifiers (`hao_*`, `HAO_*`, `libhao`, `/usr/libexec/hao`) and pinned artifact names are unchanged.
- Allow-list the AOT policy test fixture (a placeholder `/Users/` path) in `contract.json` and `test_private_references.py`.

## [0.0.3] — 2026-11-20

### Changed
- Rename the five `patches/hao-*` patch folders and ids to lowercase `macos27tool-*` (runtime, runtime-fold, runtime-fold-catalog, opencore, command); update every internal reference, link and anchor, and regenerate the catalog, tracker and manifest.
- External names (`lythka/hao-*` repositories, the `hao-runtime-<build>` artifact folders and the on-device `hao` paths) are unchanged.

### Fixed
- Re-align the `install-files` console snapshot for the longer patch id.

## [0.0.2] — 2026-11-20

### Changed
- Replace standalone word 'hao' with 'macOS27Tool' across documentation and prose.
- Regenerate patch catalog and tracker documents.

## [0.0.1] — 2026-11-19

### Added
- Initial release of macOS27Tool and macOS 27 build pipeline.
