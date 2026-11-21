# dev-keepawake

A dev image never sleeps, from its first boot on: before and during Setup Assistant, at the login window and in
every session. A test laptop that sleeps at Setup cannot be reached over the network, and Wake-on-LAN does not
wake it. Dev sets only (`dev-bs-test`, `dev-metal27`; pack `dev`): it turns sleep off.

## What it installs (Data volume)
| Path | From |
|---|---|
| `Library/LaunchDaemons/dev.m27.keepawake.plist` | `src/dev.m27.keepawake.plist`: launchd keeps `caffeinate -dimsu` running from boot on |
| `Library/LaunchDaemons/dev.m27.power.plist` | `src/dev.m27.power.plist`: at every boot, `pmset -a sleep 0 displaysleep 0 disksleep 0 standby 0 autopoweroff 0 powernap 0 womp 1`, `pmset -a disablesleep 1`, and the login window's screensaver idle time 0 |

Both are system LaunchDaemons (no session type, no user), so they run before any login.

With the lid closed the panel stays an active display, so nothing more is needed for driving the screen.

## Verify
`python3.14 -I -B -m unittest discover -s patches/dev-keepawake/tests`

After a test boot, before Setup Assistant is finished: `pmset -g` shows `SleepDisabled 1` and `sleep 0`, and
`pmset -g assertions` shows caffeinate's PreventSystemSleep, PreventUserIdleDisplaySleep and
PreventUserIdleSystemSleep.

## Validation
NEEDS VALIDATION: r1 is host-tested only; not built into an image or booted yet.

## Revisions
- r3 2026-11-17 Declares its scope (`requires`, docs/requires.md): power settings and a launchd job: no build or hardware dependence. Nothing it installs changes.
- r2 2026-11-16 Declares its pack (dev): recipe layout format pass; nothing it installs changes.
- r1 2026-11-14 Two system LaunchDaemons: caffeinate -dimsu for good, and the power settings at every boot.
