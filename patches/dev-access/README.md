# dev-access

Root SSH into a bench image on the test laptop, so a test boot can be checked and the laptop sent back to NetBoot
with no one at the keyboard. Dev sets only (`dev-bs-test`): it puts the test host's private key into the
image, which is allowed only in dev images.

## What it installs (Data volume)
| Path | From |
|---|---|
| `private/var/root/.testssh/` (700) | |
| `.../ssh_host_ed25519_key` (600) | inputs row `test_host_key`: the test host key, `SHA256:a/MmAw/0nhj9lw+qzgSpisYAvFoPkXyINJyNASHtDi4` (the key the host's known_hosts already has for the laptop) |
| `.../authorized_keys` (600) | inputs row `test_authorized_key`: the host's `~/.ssh/mbp92_test.pub` |
| `.../sshd_config` | `src/sshd_config`: root by key only, no passwords, `UsePAM no` |
| `Library/LaunchDaemons/local.testssh.plist` | `src/local.testssh.plist`: launchd runs `sshd -i` per connection on port 22 |

Apple's `ssh.plist` and the Remote Login setting are not touched.

## Origin
The root-SSH-key part of the earlier Tahoe test tooling's dev-access data; the keys are the dev SSH key pair (kept
outside the repo). That tooling's testbench recorder, TCC rows and early capture are the `testbench` patch; its
IconServices logging profile is `dev-telemetry`.

Declared files: `patch.json` `files` lists the four files above with their origins (`tests/test_edits.py` checks them against `apply.sh`).

## Screen Sharing (r5)
Owner, 2026-11-14: Screen Sharing on the test laptops for the test account, on the LAN. `dev.m27.screensharing`
(`src/dev.m27.screensharing.plist`, a system LaunchDaemon run once at every boot) does what was done by hand on the
laptops:
- it creates the group `com.apple.access_screensharing` if it is missing and adds each account named in the row
  `screensharing_users` (installed as `/private/var/root/.testssh/screensharing-users`) by name, so the entry is in
  place before the account exists;
- it runs `launchctl enable system/com.apple.screensharing` and loads Apple's job if it is not loaded.

Only members of the group get view and control, with their own password: this is plain Screen Sharing, not Remote
Management. ARD's `kickstart` is not used, because its Perl bridge has only arm64e. screensharingd has an x86_64
slice; dscl, dseditgroup and launchctl run through m27. Log: `/private/var/log/dev-screensharing.log`.

## Verify
`python3.14 -I -B -m unittest discover -s patches/dev-access/tests`

After a test boot: `ssh -i ~/.ssh/mbp92_test root@<laptop> sw_vers` answers with the image's build. Screen Sharing:
port 5900 answers `RFB` from another Mac on the LAN, `dscl . -read /Groups/com.apple.access_screensharing
GroupMembership` lists the row's accounts, and a viewer signed in as one of them sees and controls the screen.


## Validation
NEEDS VALIDATION: r5 (Screen Sharing) is host-tested only and has not been built into an image yet. The same steps,
run by hand on both test laptops on 2026-11-14, brought port 5900 up for the test account. Before r5:
In use (FACT, from the build manifests in store/runs): r3 is in 12 image builds, first mbp92-25G332-dev-cache-layout-20261101-020101, latest mbp92-25G332-dev-next-20261102-115128,
including the benched dev images (store/runs/bench-mbp92-25G332-dev-*) and the owner's beta. No open item.

## Revisions
- r8 2026-11-17 Every path in apply.sh is quoted: the user's install runs posts from the installer volume ("/Volumes/M27 1"), and an unquoted path split at the space ("Read-only file system: '1'"). A test runs apply.sh from a folder whose name has a space.
- r7 2026-11-17 Declares its scope (`requires`, docs/requires.md): an SSH switch: no build, CPU, GPU or firmware dependence. Nothing it installs changes.
- r6 2026-11-16 Declares its pack (dev): recipe layout format pass; nothing it installs changes.
Newest first: revision, date, change (commit). `revision` in patch.json is the newest; bump it
and add a line here with every change to this patch or its input rows.
- r5 2026-11-14 Screen Sharing for the test account from the first boot (owner's decision): system LaunchDaemon dev.m27.screensharing adds the accounts of the new row `screensharing_users` to com.apple.access_screensharing by name and enables and loads com.apple.screensharing at every boot.
- r4 2026-11-12 Declare the four files it installs in patch.json `files`; the hook is unchanged.
- r3 2026-10-28 Lock rows one file each; working files in store/tmp; store-gc; STATUS edited only by the merging session (5a7d438)
- r2 2026-10-28 PLAN 3c dev patches: dev-telemetry, testbench; boot-kc-trim experiment (dev-kctrim set) (259a8f5)
- r1 2026-10-28 Patch mechanism (PLAN step 2) and the dev-access patch (badb51b)
