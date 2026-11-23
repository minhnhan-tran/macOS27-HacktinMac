# dev-capture

Every dev run collects what a failure needs, from its first boot on, with nothing to turn on. Dev sets only (pack
`dev`); an end-user image carries none of it.

## What it changes (Data volume)

| Path | Content | Why (S18) |
|---|---|---|
| `Library/Preferences/Logging/Subsystems/com.m27.plist` (root 644) | Info level enabled and persisted | m27's Info lines survive to the harvest; logd takes it (`log config --status --subsystem com.m27`: `INFO PERSIST_INFO`, laptop 2026-11-18). Owner 2026-11-18: "Every run should be collecting meta data and capturing crashes" |
| `private/var/db/m27-capture/` (root 755) | empty folder | where the Swift metadata recorder writes its per-process keys, for the harvest |
| `usr/local/libexec/m27-capture/{m27-capture,inventory.sh}` (root 755), `Library/LaunchDaemons/local.m27.capture.plist` (root 644) | the metadata recorder (rows `m27_capture_job`, `m27_capture_inventory`, `m27_capture_plist`; private, from the store) | every dev boot records each process's Swift generic metadata, 1200 s after load (past the ~885 s first-login settle a timed boot measures) and then every 30 min, into `<boot>/<UTC>.tgz` (DONE/FAILED lists, 1 GB cap); the harvest feeds the metadata catalog. It needs task-for-pid (dev-diagnostics' SIP 0x4); without it each run writes FAILED and exits 0 |

Showing m27's `<private>` values cannot be done here: macOS 27 ignores `Enable-Private-Data` in this file (the same test).
The runtime marks the values it needs as public in its own log calls.

`EFI/OC/config.plist` (after macos27tool-opencore, before dev-diagnostics): no `m27_crash=1` since r5 (it was macos27tool-runtime's crash lines in
`/private/var/log/m27`) and SIP bit 0x4 (task-for-pid, for the recorder's swift-inspect). dev-lite images have no
dev-diagnostics, so without these a refusal showed only in .ips frames (U0 image, 2026-11-18). dev-diagnostics adds the
same only if missing.

What the same decision needs is already in other recipes. This one does not repeat them:
- `/private/var/log/m27` at 755: macos27tool-command r12.
- launchd's job-exit lines: Apple logs them by default and persists them (`exited due to`), so nothing changes.
- Crash reports: ReportCrash writes every report to `/Library/Logs/DiagnosticReports`, with no throttling. A test
  image's log shows no cap or skip line, and the only cleanup is SubmitDiagInfo retiring analytics files it
  submitted (laptop, 2026-11-18). Nothing changes.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/dev-capture/tests`
- On a booted dev image: `log config --status --subsystem com.m27` shows `INFO PERSIST_INFO`;
  `ls -ld /private/var/db/m27-capture` is root 755.

## Validation

NEEDS VALIDATION: the logging file was tested live on a test laptop (2026-11-18): logd applied Info and persistence; the
folder and the image build are untested.

## Revisions
- r5 2026-11-22 m27_crash=1 left out: with it a dev image's first boot stalls before sandboxd (QEMU, ab855d51; without it the same image boots in 537 s); macos27tool-runtime's fix is parked until after beta.1.
- r4 2026-11-18 m27_crash=1 and SIP 0x4 in every dev image: dev-lite has no dev-diagnostics, so its images had neither. Listed before dev-diagnostics.
- r3 2026-11-18 Recorder payload 40309773dd00: its first run waits 1200 s instead of 300 s, so it does not load the timed first login.
- r2 2026-11-18 The metadata recorder: a root launchd job and its two scripts, from three private store rows.
- r1 2026-11-18 New: com.m27 Info lines persisted, and the capture folder (owner: always capture).
