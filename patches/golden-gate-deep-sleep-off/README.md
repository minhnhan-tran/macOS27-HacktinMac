# golden-gate-deep-sleep-off

Deep sleep (standby, autopoweroff and hibernation) is off by default in v0.0.1. Ordinary sleep and wake, from the menu
and the lid, stay on. A user can turn deep sleep back on with `pmset`, and the choice is kept.

## What it changes (Data volume)

- `Library/LaunchDaemons/local.m27.deep-sleep-off.plist` (root 644): at boot it runs
  `pmset -a standby 0 autopoweroff 0 hibernatemode 0`, for battery and AC. It retries every 30 s until pmset succeeds,
  then writes `/private/var/db/.m27-deep-sleep-off` and does nothing on later boots.

## Why (S18)

- diag#79: a wake panic (the USB 2.0 controller EHC1 waits forever at power state Off). It was shown on hardware
  2026-11-17 (a MacBookPro9,2 installed from the v0.0.1 USB installer).
- Plain sleep is safe: 5 sleep/wake cycles (menu and lid) woke without a panic, and EHC1 went to Sleep, not Off.
- The likely path to Off is a standby or hibernate resume. That resume could not yet be tested, so deep sleep
  is off until it is fixed (owner decision 2026-11-18, "v0.0.1 ships with known issues"; the root fix is for v0.0.2).
- powerd keeps these three settings only in `/Library/Preferences/com.apple.PowerManagement.<hardware UUID>.plist`
  (read on a MacBookPro9,2 running 26B131). The shared `com.apple.PowerManagement.plist` holds only the timers and wake settings, so
  the image cannot ship the values for an unknown Mac. Hence the one-time pmset on the Mac.
- Removal test: when #79's resume path is fixed and a hibernatemode 25 resume passes on hardware, drop this recipe.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/golden-gate-deep-sleep-off/tests`: the daemon file, its command,
  the marker and the retry.
- On the Mac: `pmset -g custom` shows standby 0, autopoweroff 0 and hibernatemode 0 on both power sources; the marker
  exists; after a reboot the values are the same; a sleep/wake cycle has no panic.

## Validation

NEEDS VALIDATION: the m27 -> m27 rename changed the daemon label, the plist file name and the marker (`local.m27.deep-sleep-off` -> `local.m27.deep-sleep-off`, `.m27-deep-sleep-off` -> `.m27-deep-sleep-off`), so the proof below was taken on the old bytes and the renamed recipe has not been run on a laptop.

Laptop 2026-11-18 (a MacBookPro9,2 running the v0.0.1 candidate C2, the recipe's file installed live): after two
boots, standby 0, autopoweroff 0 and hibernatemode 0 on both power sources, the marker present and the job's last exit
0; three software sleep/wake cycles with no hibernate or standby entry and no panic; a user's hibernatemode 3 kept
across a reboot. Lid-close and battery cycles were not run (out of v0.0.1's scope).

## Revisions
- r2 2026-11-18 Validated r1 on hardware (Validation); nothing it installs changes.
- r1 2026-11-18 New: deep sleep off by default (diag#79), owner decision for v0.0.1.
