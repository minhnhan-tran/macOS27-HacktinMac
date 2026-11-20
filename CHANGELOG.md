# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
