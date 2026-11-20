# golden-gate-appstore-slices

Checks App Store apps for missing Intel code. After each App Store install or update, it logs every Mach-O file of
the app that lacks an x86_64 slice when the app's main executable is universal (owner, 2026-11-14: "verify apps come
with both slices"). Such a file would leave a universal app partly arm64 on an Intel Mac: a framework, plug-in, XPC
service or helper that runs under macOS27Tool, or fails to load into the x86_64 main program. The recipe only reports. It
changes nothing in the App Store's identity or downloads.

## What it changes

| File on Data | Change |
|---|---|
| `usr/local/libexec/hao-slice-guard` (new) | x86_64 tool from the `appstore_slice_guard` input, ad hoc signed, root:wheel 0755. |
| `Library/LaunchDaemons/org.hao.slice-guard.plist` (new) | launchd runs the tool when `/Applications` changes (a new or replaced bundle) and every 10 minutes (an update in place), at background priority. It does not run at boot. |

The tool looks at each `/Applications/NAME.app` that has `Contents/_MASReceipt/receipt`. It scans the bundle when
the receipt, `Info.plist` or main executable (`CFBundleExecutable`) changed since its last record in
`/private/var/db/hao/slice-guard.state`. A scan reads the header of every regular file (no symlinks) and records the
architectures of each thin or fat Mach-O. Java class files, which share the fat magic, are skipped. A bundle with a
file written in the last 30 seconds is still being installed: it is retried for up to 2 minutes, then left for the
next run.

Each scan writes to `/Library/Logs/hao/slice-guard.log` and to the unified log (subsystem `org.hao.slice-guard`):

```
2026-11-14T16:02:11Z app=/Applications/X.app version=1.2(345) main=universal main_archs=x86_64,arm64 macho=4 missing_x86_64=1
2026-11-14T16:02:11Z app=/Applications/X.app missing_x86_64=Contents/Frameworks/Y.framework/Versions/A/Y archs=arm64
```

The summary line comes first. `main` is `universal`, `x86_64`, `arm64` or `none`. A `missing_x86_64=PATH` line follows
for each Mach-O without x86_64, but only when `main` is universal; those lines and their summary are logged at error
level. An arm64-only main executable (`main=arm64`) is listed without items. It means either an arm64-only app or a
thinned arm64 delivery of a universal one, and the summary alone cannot tell them apart.

`hao-slice-guard --all --state FILE --log FILE --settle 0 DIR` scans every `.app` in DIR, with or without a receipt,
and writes its own state and log.

## Build

`python3.14 -I patches/golden-gate-appstore-slices/build/make store/artifacts` builds
`appstore-slices-YYYYMMDD-<source sha256 12>/hao-slice-guard` and `build.json`.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/golden-gate-appstore-slices/tests` (declaration and job) and
  `... -s patches/golden-gate-appstore-slices/build/tests` (the tool on fixture bundles, built for the host).
- The build log prints the sha256 of both installed files.
- On the image: `launchctl print system/org.hao.slice-guard` shows the job. After an App Store install of a universal
  app, `/Library/Logs/hao/slice-guard.log` has its summary line within a minute, with `main=universal`.
  `log show --predicate 'subsystem == "org.hao.slice-guard"'` shows the same lines.

## Validation

NEEDS VALIDATION: not built or booted. The host tests pass. On the Apple-silicon host, the x86_64 artifact (run
under Rosetta) scanned the host's nine App Store apps (Amphetamine, AmorphousDiskMark, Speedtest, Office and
others: `main=universal`, `missing_x86_64=0`; Xcode `main=arm64`, thinned for that host) in 21 s, and the second run
logged nothing.

## Revisions
- r6 2026-11-17 Every path in apply.sh is quoted: an install runs recipe scripts from the installer volume, whose name can have a space ("HAO 1"), and an unquoted path splits there. No installed byte changes.
- r5 2026-11-17 Declares its scope (`requires`, docs/requires.md): a Data tool and LaunchDaemon that read Mach-O headers: no build or hardware dependence. Nothing it installs changes.
- r4 2026-11-17 Status diagnostic: it only reports, so release profiles leave it out.
- r3 2026-11-16 Declares its pack (os-extras): recipe layout format pass; nothing it installs changes.
- r2 2026-11-15 Row hash scope.
- r1 2026-11-14 New: hao-slice-guard and its LaunchDaemon log each Mach-O without x86_64 in a universal App Store app after install or update.
