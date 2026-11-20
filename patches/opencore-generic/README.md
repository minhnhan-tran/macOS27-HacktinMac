# opencore-generic

The generic model's part of the image's OpenCore (pack `generic`): the image for **virtual machines and non-Apple PCs**,
with graphics in software, a baseline others can use for their own testing. **It is not for Mac hardware:** its
VirtualSMC conflicts with a real Mac's SMC. A Mac uses its own model pack (MacBookPro9,2: `mbp92`).

## How it works

`apply.sh` runs `src/generic.py` on the EFI partition that `pipeline/stages/build-efi.py` made for the generic target,
right before [macos27tool-opencore](../macos27tool-opencore/README.md). From the row `qemu_reference_efi` (the OpenCore tree tested on
OVMF, the base of `tools/kernel-foundation vm_esp.py`) it takes:

| Setting | Why |
|---|---|
| SMBIOS iMac20,1, created (`UpdateSMBIOSMode` Create) | Non-Apple firmware has no Mac SMBIOS. iMac20,1 is an Intel model Tahoe supports natively, so the Tahoe donors and OCLP's base config fit it and OCLP adds no model patches. |
| Serial, MLB, system UUID, ROM: generated per image | Made the first time the recipe runs, kept after. Random, not Apple-issued: Apple services that check them (iMessage, FaceTime) need the user's own valid values. |
| VirtualSMC.kext (Acidanthera 1.3.8), right after Lilu | macOS needs an SMC; non-Apple firmware has none. Byte-identical to the release (macos27tool-opencore `src/kext-provenance.json`). |
| Lilu.kext from OCLP's 1.7.2 RELEASE payload | Keeps the ESP's Lilu equal to macos27tool-opencore's provenance pin whatever the base ESP carries. VirtualSMC from the reference equals its pin (Acidanthera 1.3.8). |
| `-crypt_force_avx` in boot-args | CryptexFixup does nothing on a CPU with AVX2 (Haswell and newer, AMD Ryzen) unless it is set (CryptexFixup's README; 1.0.5's code at 0x187d-0x1973), and most VMs and PCs this pack is for have AVX2; without CryptexFixup's patch the OS cryptex does not graft. A CPU without AVX2 never reads it. Generic image only: the 9,2's boot-args are unchanged. |
| CryptexFixup.kext (Acidanthera 1.0.5), right after Lilu | Without it APFS requires the root hash of the image's modified OS cryptex, the graft fails (graftdmg 80) and the boot stops before userspace (QEMU, 2026-11-17: the 9,2 config without it failed there, with it passed). Taken from OCLP's RELEASE payload in the row `oclp_source`, byte-identical to macos27tool-opencore's pinned copy. |
| The reference's Booter, Kernel and UEFI quirks | What non-Apple firmware needs: runtime-services memory map, KASLR slide, write protection, Apple EFI image loading, CFG Lock, VT-d tables, power-state timeouts, CPU info on a hypervisor. Each one's reason is in macos27tool-opencore's `src/config-reasons.json`, marked "generic image only". |

No GPU, Wi-Fi or model kexts are added. macos27tool-opencore then adds the Darwin 27 kernel repairs, IntelCommpage, HaoExec
and the boot-args. Its reasons check refuses any element without a reason, and any "generic image only" element in
a config that isn't this one (SMBIOS iMac20,1, created).

## Verify

- `python3.14 -I -B -m unittest discover -s patches/opencore-generic/tests` (with the store, the real reference's
  quirks: every element it adds has a reason).
- Build log: `opencore-generic: SMBIOS iMac20,1 (new identity), VirtualSMC after Lilu, quirks set: ...`.
- QEMU boot of the generic image (builder's GEN2): the desktop in software graphics; `sysctl hw.model` is iMac20,1;
  `kextstat | grep -i smc` shows VirtualSMC.

## Validation

NEEDS VALIDATION: host tests only; no generic image is built or booted yet.

## Revisions
- r6 2026-11-18 Test only: macos27tool-opencore knows the generic config from the generic target's `platform_data` (`opencore_generic`), not from its SMBIOS model. Nothing it installs changes.
- r5 2026-11-17 Row qemu_reference_efi is now made by pipeline/unpack/qemu-reference from its downloads: the same tree, with OCLP's Lilu 1.7.2 and a config without a machine identity. What this recipe writes is unchanged.
- r4 2026-11-17 boot-args gets `-crypt_force_avx`: CryptexFixup is inert on AVX2 CPUs without it, and most machines this pack is for have AVX2.
- r3 2026-11-17 Lilu is OCLP's pinned 1.7.2 RELEASE (from row oclp_source), not the reference's build: the provenance check refused the reference's.
- r2 2026-11-17 Adds CryptexFixup (from OCLP's payload in row oclp_source, the pinned bytes): the modified OS cryptex does not graft without it.
- r1 2026-11-17 New (GEN1): SMBIOS iMac20,1 with a per-image identity, VirtualSMC, and the QEMU reference's firmware quirks, for VMs and non-Apple PCs.
