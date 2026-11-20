# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
