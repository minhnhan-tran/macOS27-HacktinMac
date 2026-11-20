# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
