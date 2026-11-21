# golden-gate-ids

Messages: iMessage sign-in and messaging on macOS 27.2.1 (26B131), MacBookPro9,2 (diag#0047).

Changes: adds `usr/lib/m27/libm27-imessage-wifi-test.dylib`; replaces
`System/Library/PrivateFrameworks/IDS.framework/identityservicesd.app/Contents/MacOS/identityservicesd` (row
`ids_service`); adds that file's line to `usr/lib/m27/stub-identities`. Stops unless the image's file is the one
macos27tool-runtime-fold's payload installs. Needs the macos27tool-runtime-fold payload's `libm27.dylib`.

Proof: these bytes on a test machine, owner 2026-11-15: "It fully works as far as I can tell".

## Verify

`patches.py check`; after a build, the two files have the row's sha256 and `stub-identities` has the row's line.

## Validation

NEEDS VALIDATION on an image built with this recipe.

## Revisions
- r3 2026-11-17 Declares its scope (`requires`, docs/requires.md): its row is for 26B131. Nothing it installs changes.
- r2 2026-11-16 Declares its pack (os-extras): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r1 2026-11-15 New.
