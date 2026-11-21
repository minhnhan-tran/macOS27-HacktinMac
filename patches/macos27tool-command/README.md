# macos27tool-command

Installs the `m27` command on the Mac: `/usr/local/bin/m27`, which runs the release's `tools/macOS27Tool` with a bundled
x86_64 CPython 3.12. macOS 27 has no working `python3`, so `m27` brings its own (owner, 2026-11-16: "Bundle it.").

## What it installs (Data volume)

| Path | What |
|---|---|
| `usr/local/bin/m27` | A two-line launcher: `exec /usr/local/lib/m27/python/bin/python3.12 -I /usr/local/lib/m27/m27/tools/macOS27Tool/macOS27Tool "$@"` |
| `usr/local/lib/m27/python/` | Input row `python_x86_64` (python-build-standalone install_only), unpacked |
| `usr/local/lib/m27/m27/` | `tools/macOS27Tool` of this release without its tests, with `tools/macOS27Tool/vendor` (Rich, MIT) and the notices; `make.sh` stages it |

Everything is on the Data volume, root:wheel, not writable by others, so `m27 update` can replace it without
touching the sealed System.

## Verify

On the installed Mac: `m27`, `m27 status`, `m27 --help`.

## Validation

NEEDS VALIDATION: not yet run in an image build, a QEMU install or on a laptop.

## Revisions
- r12 2026-11-18 Makes macOS27Tool's log folder /private/var/log/m27 root:wheel 755 on Data. launchd created it 744 for the AOT job's log, so per-user macOS27Tool logs were lost.
- r11 2026-11-18 SHA256SUMS staged with release.json (an unsigned release, v0.0.1, is checked against it)
- r10 2026-11-17 a candidate build's tools/macOS27Tool/data/candidate-keys.pub is staged with macOS27Tool (never tracked; release-cut --candidate writes it)
- r9 2026-11-17 Status product (was experiment): it ships in the release profiles. Declaration only; `validated` stays false until a boot proves the re-cut. Nothing it installs changes.
- r8 2026-11-17 the new launcher is made by mktemp; every folder from usr to bin must be root's and loses its ACLs; a link at its name (to a file or a folder) is removed, a folder refused; release.json.sig staged with release.json
- r7 2026-11-17 the launcher is written to a new file and renamed into place; symlinked folders refused; make.sh stages committed bytes (git archive) into a fresh folder
- r6 2026-11-17 Declares its scope (`requires`, docs/requires.md): bundled CPython and tools/macOS27Tool: no build or hardware dependence. Nothing it installs changes.
- r5 2026-11-17 Declares its pack (base): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.

- r4 2026-11-16 no du in apply.sh (the Recovery has none); in the dev, release and metal27 sets too
- r3 2026-11-16 make.sh stages from a release archive too (no .git): files on disk, VERSION from pipeline/SOURCE_COMMIT
- r2 2026-11-16 make.sh writes tools/macOS27Tool/VERSION (the staged commit) for macOS27Tool's version on a dev image
- r1 2026-11-16 first recipe: CPython row and tools/macOS27Tool under /usr/local/lib/m27, launcher at /usr/local/bin/m27
