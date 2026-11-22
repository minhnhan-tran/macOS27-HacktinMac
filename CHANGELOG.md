# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
