# rec-display-ethernet

Gives the Recovery an accelerated display at native resolution and the built-in Ethernet. Apple's Recovery has
framebuffers only for newer GPUs, so on the MacBookPro9,2 it runs on the firmware's boot framebuffer. OCLP restores
the Ivy Bridge framebuffer and HD 4000 kexts in the root-patched System; this patch brings them into the BaseSystem.
Stage `recovery`.

## How it works
- `apply.sh` copies `AppleIntelFramebufferCapri.kext` and `AppleIntelHD4000Graphics.kext` from the root-patched
  System into the BaseSystem's `/System/Library/Extensions`, where user space looks for them.
- `kexts.txt` lists `com.apple.driver.AppleIntelFramebufferCapri`, `com.apple.driver.AppleIntelHD4000Graphics` and
  `com.apple.iokit.AppleBCM5701Ethernet` (Broadcom 57765).

Every recovery patch may ship a `kexts.txt`. [pipeline/stages/recovery.sh](../../pipeline/stages/recovery.sh)
rebuilds `BaseSystemKernelExtensions.kc` with the System's own `kmutil` from the BaseSystem's kext list plus all
`kexts.txt` files; no kext is modified. `kmutil` leaves out kexts that the boot kernel collection already holds
(BCM5701 is one of them).

Declared in `patch.json` `files` (paths relative to the Recovery BaseSystem's root: the two kext bundles, whole, and the kernel collection in [`edits/kc.json`](edits/kc.json), a `kc.link` declaration); [`tests/test_edits.py`](tests/test_edits.py) checks it against `apply.sh` and `kexts.txt`. No byte edits, so no real-input test.

## Verify
In the Recovery, `kextstat | grep -E 'Capri|HD4000'` lists both drivers and the screen runs at native resolution.

Declaration test:
```sh
python3.14 -I -B -m unittest discover -s patches/rec-display-ethernet/tests
```

## Validation
NEEDS VALIDATION: every listed bundle ID exists in the 25G332 System and the kernel collection builds; the display has
not been checked in a booted Recovery built from this repository.

## Revisions
- r5 2026-11-17 Every path in apply.sh is quoted: an install runs recipe scripts from the installer volume, whose name can have a space ("M27 1"), and an unquoted path splits there. No installed byte changes.
- r4 2026-11-17 Declares its scope (`requires`, docs/requires.md): the HD 4000 framebuffer in the Tahoe Recovery. Nothing it installs changes.
- r3 2026-11-16 Declares its pack (recovery-mbp92): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r2 2026-11-12 Declare the two kext bundles and the kernel collection link in `patch.json` `files` and `edits/kc.json`; the recipe is unchanged.
- r1 2026-11-10 Initial public release.
