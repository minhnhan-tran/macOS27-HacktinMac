# golden-gate-store

App Store: Apple-silicon-only apps can be downloaded and updated on macOS 27.2.1 (26B131), MacBookPro9,2.

Changes: adds `usr/lib/m27/m27-appstore-policy.dylib` (row `store_service`'s `libm27-appstore-policy.dylib`, built from
`tools/appstore-policy/src/store_policy.m`); edits the x86_64 slice of `appstored` and `appstoreagent`
(`System/Library/PrivateFrameworks/AppStoreDaemon.framework/Support/`) and of the App Store app's macOS27Tool stub
(`App Store.app/Contents/MacOS/App Store`), as `edits/*.json` say, with `tools/edit-engine/stub-edit` (row
`edit_engine`); every other slice is kept. Each spec stops unless its input is the 26B131 file it names. Runs after
macos27tool-runtime-fold and before golden-gate-native-identities, which lists the two edited daemons.

Proof: the same edits as the App Store files the owner used on a test machine on 2026-11-15 ("These are proven live
fixes"), there with an embedded agent; this native agent has not run yet. Open: diag#0051.

## Verify

`patches.py check`; on the host, the three specs on Apple's 26B131 files give tools/appstore-policy prepare.py's files
slice for slice (only the x86_64 slice alignment differs: Apple's is kept).

## Validation

NEEDS VALIDATION on an image built with this recipe.

## Revisions
- r7 2026-11-18 The policy library is installed as usr/lib/m27/m27-appstore-policy.dylib, and the three specs load it from there. Under the old name, m27-install took the two edited daemons for macOS27Tool stubs and refused them. The library's bytes are unchanged.
- r6 2026-11-17 Declares its scope (`requires`, docs/requires.md): its row is for 26B131. Nothing it installs changes.
- r5 2026-11-16 Declares its pack (os-extras): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r4 2026-11-16 Row `store_service` carries its build record (hash without build.json). Installed bytes unchanged.
- r3 2026-11-16 Edit specs on the image's own files instead of copied files; the library is built from our source.
- r2 2026-11-15 Adds libm27-ios-coordinator-test.dylib, which the replaced appstoreagent loads.
- r1 2026-11-15 New.
