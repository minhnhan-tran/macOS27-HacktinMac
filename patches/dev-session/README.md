# dev-session

Dev images only: the test account comes up logged in, with no lock screen, no password prompts and the GUI helper
already allowed to see and drive the screen. Built into the image, not set over SSH after each push.

## What it changes (Data volume)

- `usr/local/libexec/dev-session` and `Library/LaunchDaemons/dev.m27.session.plist`: at every boot, as root and
  idempotent:
  - creates the test account (uid 501, admin) if missing. Its random password is made on the Mac and kept in
    `/private/var/db/m27-dev/` (root, 0600), so no password is in the image or this repository;
  - turns on automatic login (`/etc/kcpassword`, `autoLoginUser`) and marks the per-user Setup panes seen;
  - turns the screen lock off for the account (`askForPassword` 0, idle time 0, `sysadminctl -screenLock off`);
  - grants Screen Recording, Accessibility, PostEvent and ListenEvent in the system TCC database to the GUI helper at
    its fixed path, by its signing identifier, so a rebuilt helper keeps them.
- `private/var/db/m27-dev/session.conf` (row `dev_session_config`, private): the account name and the GUI helper's path
  and identifier.
- `private/var/db/.AppleSetupDone`: the dev image skips Setup Assistant. An image built to test Setup leaves this
  recipe out of its set.

## Why (S18)

The owner, 2026-11-18: dev images should need no lock screen or password prompts. These steps were redone over SSH
after every push and reboot. Release profiles refuse dev recipes, so no user image has any of it.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/dev-session/tests`: the files and modes, the kcpassword encoding
  (byte for byte against the reference encoder), and no fixed password in the recipe.
- On a dev image: the first boot ends at the desktop as the test account without any password;
  `/private/var/log/dev-session.log` says done; the screen lock is off; the GUI helper needs no new grants.

## Validation

NEEDS VALIDATION: host tests only.

## Revisions
- r2 2026-11-18 The test account's Setup Assistant record is complete, so the per-user Setup Assistant does not stay
  pending (bookdatastored restarted every 10 s while it was).
- r1 2026-11-18 New (owner: dev images come up unlocked and drivable).
