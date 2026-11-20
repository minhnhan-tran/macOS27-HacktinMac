# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
