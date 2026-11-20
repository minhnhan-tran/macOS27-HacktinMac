# golden-gate-update-policy

Background Security Improvements (Rapid Security Responses, "splats" in Apple's code) and macOS updates do not
download or install on their own. A splat replaces the OS and App cryptex in Preboot (`cryptex1/current`), and this
image modifies that cryptex: the x86_64 dyld cache that hao-runtime's caches are bound to, the cryptex graft that
boots only because its root-hash check is lifted, and rows keyed by build 26B131. A splat that installs either
undoes those changes or stops the boot at the graft. macOS27Tool
releases carry Apple's fixes instead. A macOS update replaces the system the same way (owner decision,
2026-11-18). Release notes: "Background Security Improvements are off: Apple's security
responses replace parts of the system macOS27Tool modifies; macOS27Tool releases carry them instead."

Scan, XProtect and system data files (`AutomaticCheckEnabled`, `AutomaticDownload`, `ConfigDataInstall`,
`CriticalUpdateInstall`) keep Apple's defaults. They do not control splats (below).

## What it changes (Data volume)
| Path | Change |
|---|---|
| `Library/Preferences/com.apple.SoftwareUpdate.plist` | `SplatEnabled` false, `AutomaticallyInstallMacOSUpdates` false; `AutoInstallProductKeys` and `FirstInstallTonightDateDictionary` removed (no update queued for Install Tonight); other keys kept; created if missing; root:wheel 0644 |

The file is a seed: softwareupdated adds its state after install, and the user can turn the setting back on in
System Settings > Privacy & Security > Background Security Improvements.

## Why (S18)

**Evidence.** A MacBookPro9,2 test machine on 27.2.1 (26B131) had `AutomaticCheckEnabled`, `AutomaticDownload`,
`AutomaticallyInstallMacOSUpdates`, `CriticalUpdateInstall` and `ConfigDataInstall` all 0 in
`/Library/Preferences/com.apple.SoftwareUpdate.plist`, and `SplatEnabled = 1`. softwareupdated still offered
`MSU_UPDATE_26B131_full_27.2.1_rsr` ("Security Response 27.2.1 26B131 (Customer)(SPLAT)"), and
SUOSUInstallTonightManager queued it (`FirstInstallTonightDateDictionary` set). Nothing in the image set these keys.

**Trace** (26B131 arm64e dyld cache, OSUpdate.framework and SoftwareUpdate.framework; the x86_64 cache has the same
code):
- `-[SUSharedPrefs splatEnabled]` (0x1b515f1ec) returns the MDM (DDM) global setting
  `automaticallyInstallSystemAndSecurityUpdates` when one is set. Otherwise it returns
  `objectForAnyUserPreference:@"SplatEnabled" usingManaged:NO`, and YES when the key is missing.
  `objectForAnyUserPreference:usingManaged:` (0x1b516417c) is
  `CFPreferencesCopyValue(key, "com.apple.SoftwareUpdate", kCFPreferencesAnyUser, kCFPreferencesCurrentHost)`, which
  is `/Library/Preferences/com.apple.SoftwareUpdate.plist`.
- The Background Security Improvements switch in System Settings reads the same value.
  `-[SUOSUShimController isAutomaticallyInstallBackgroundSecurityImprovementsPreferenceEnabled]` (0x1bf65d040) calls
  `[SUSharedPrefs splatEnabled]`.
- Background download: `-[SUOSUServiceDaemon doBackgroundActionsWithCompletion:]` block (0x1bf61ed6c) builds the
  evaluator from `splatEnabled`. `-[SUOSUBackgroundDownloadEvaluator _isProductEligibleForBackgroundDownload:]`
  refuses a splat when `splatEnabled` is NO ("Splat is disabled": 0x1bf682974, 0x1bf682ba4). For a splat it skips
  `backgroundDownloadsEnabled` (`AutomaticDownload`) at 0x1bf682984. For this reason `AutomaticDownload = 0` did not
  stop it.
- Install Tonight: after a splat downloads, the completion block (0x1bf61fa98) always queues it
  (`_setMobileSoftwareUpdateDescriptorToInstallLater:` 0x1bf61fbf8, `armObserverWithMode:` 0x1bf61fc0c, log "Auto
  updates enabled, queuing splat for tonight"). `-[SUOSUAutoUpdatePolicy isProductEligibleForAutoUpdate:]` gates a
  splat on `splatEnabled` (0x1bf673290) and other updates on `doesMacOSAutoUpdate` (0x1bf6732c0), whose value is
  the `AutomaticallyInstallMacOSUpdates` key (`-[SUPreferenceManager automaticallyInstallMacOSUpdates]`, 0x1b5141da4;
  System Settings' switch is `-[SUOSUShimController isAutomaticallyInstallMacOSUpdatesPreferenceEnabled]`, 0x1bf65cf88).
- Both checks first accept a product key that is already in `AutoInstallProductKeys`: `containsObject:` at 0x1bf673050
  and `_enabledForAutoInstallTonight:` at 0x1bf682850. They do this before they look at `splatEnabled`. For this
  reason the queue is cleared.
- `-[SUPreferenceManager migratePreferencesAfterUpdate]` (0x1b51430dc) sets `SplatEnabled` from the critical-update
  setting only when the key is missing. An explicit false is kept.
- Not used: the update stack does not link ManagedConfiguration. `allowRapidSecurityResponseInstallation` occurs only
  in ManagedConfiguration, so a restriction profile has no effect. Splats are hidden from the scan
  (`splatUpdatesHiddenByManagedSetting`, 0x1b515f2d4) only by an MDM-delivered DDM declaration
  (`SUCoreDDMDeclarationGlobalSettings.enableRapidSecurityResponse`), which an image cannot carry without MDM.

**Limit.** A scan still lists the splat (`softwareupdate --list`), and a user can install it by hand. It is not
downloaded, notified, queued or installed automatically.

## Verify

`python3.14 -I -B -m unittest discover -s patches/golden-gate-update-policy/tests` runs apply.sh on a stand-in Data
tree, with and without an existing file.

On the image, after `softwareupdate --list` (a forced scan): `defaults read /Library/Preferences/com.apple.SoftwareUpdate`
shows `SplatEnabled = 0` and `AutomaticallyInstallMacOSUpdates = 0`, and no `AutoInstallProductKeys` or
`FirstInstallTonightDateDictionary` entry for the `_rsr` product. `/var/log/install.log` has "Splat is disabled" or
"is not eligible for auto update because automatic splat updates are disabled". It has no "queuing splat for
tonight" line. The same is true after a reboot.

## Validation

Laptop 2026-11-18, a MacBookPro9,2 with a fresh install of the v0.0.1 candidate C2 (26B131): SplatEnabled and
AutomaticallyInstallMacOSUpdates were off with no hand edits; softwareupdated logged "Splat is disabled" on both boots,
queued nothing and set no product keys; the settings held across a reboot, and Preboot's cryptex was unchanged.

## Revisions
- r3 2026-11-18 Validated r2 on hardware (Validation); nothing it installs changes.
- r2 2026-11-18 AutomaticallyInstallMacOSUpdates off too (owner), so no macOS update installs on its own either.
- r1 2026-11-18 New: SplatEnabled off and the Install Tonight queue cleared, so no Background Security Improvement installs on its own.
