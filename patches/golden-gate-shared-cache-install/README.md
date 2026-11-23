# Build the shared cache during installation

This recipe gives installer users the verified rebuilt cache before their first boot, using `m27` and
`tools/macOS27Tool/src/cachebuild.py`.

The assembler runs this recipe in two phases (`M27_CACHE_PHASE`) after the provider recipes. The `build` phase reads the
target System, Data and read-only OS/App cryptex views, checks the source inventory, and builds from pinned offline
tools (`shared_cache_builder`) and the Swift catalog (`swift_metadata_catalog`). It refuses any output other than the
expected UUID (`31DA2287`) and all 36 hashes. A verified family survives a build retry on target Data
(`/private/var/db/m27/cache-install`). The `install` phase uses the existing fold recipe (`golden-gate-shared-cache`'s
`fold.py`) before the final System snapshot.

Stock profiles continue to use the stock cache. This recipe does not enable live cache activation.

## Verify

Run the recipe tests (`patches/golden-gate-shared-cache-install/tests/test_rebuild.py`), `tools/macOS27Tool/tests/test_cachebuild.py`,
and the pipeline assembler resume tests (`pipeline/tests/test_golden_image.py`). Image, Recovery and hardware proofs are separate.

## Validation

NEEDS VALIDATION: ported from upstream `lythka/hao-patcher` with `m27`/`macOS27Tool` renames. Host unit tests pass; not yet built or booted on target hardware.

Upstream reference evidence: Laptop 2026-11-22, a MacBookPro9,2 dev-beta image (26B131, r1) booted in QEMU with the
shared cache rebuilt in place and verified against the release (UUID 31DA2287, 36 files).

## Revisions
- r2 2026-11-22 Validated r1 upstream; ported to `m27` (`NEEDS VALIDATION` in fork until hardware/image proof).
- r1 2026-11-22 Build and verify the cache on the target before installing it and sealing System.
