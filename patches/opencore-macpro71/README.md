# opencore-macpro71

The macpro71 model's part of the image's OpenCore (pack `macpro71`): an **Intel PC started as a MacPro7,1** (first proof: MSI B460M PRO,
Core i5-10400, Radeon RX 580). Tahoe supports MacPro7,1 and Polaris natively, so it needs no GPU or model kexts and no OCLP patch
sets. **It is not for Mac hardware:** its VirtualSMC conflicts with a real Mac's SMC. A Mac uses its own model pack
(MacBookPro9,2: `mbp92`). Not carried yet: the PC's Realtek 8168 Ethernet, AX210 Wi-Fi and Bluetooth, ALC892 audio (use USB Ethernet).

## How it works

`apply.sh` runs `src/macpro71.py` on the EFI partition that `pipeline/stages/build-efi.py` made for the macpro71 target,
right before [macos27tool-opencore](../macos27tool-opencore/README.md). From the row `qemu_reference_efi` (the OpenCore tree tested on
OVMF, the base of `tools/kernel-foundation vm_esp.py`) it takes:

| Setting | Why |
|---|---|
| SMBIOS MacPro7,1, created (`UpdateSMBIOSMode` Create) | Non-Apple firmware has no Mac SMBIOS. MacPro7,1 is an Intel model Tahoe supports natively, so the Tahoe donors and OCLP's base config fit it and OCLP adds no model patches. |
| Serial, MLB, system UUID, ROM: generated per image | Made the first time the recipe runs, kept after. Random, not Apple-issued: Apple services that check them (iMessage, FaceTime) need the user's own valid values. |
| VirtualSMC.kext (Acidanthera 1.3.8), right after Lilu | macOS needs an SMC; non-Apple firmware has none. Byte-identical to the release (macos27tool-opencore `src/kext-provenance.json`). |
| Lilu.kext from OCLP's 1.7.2 RELEASE payload | Keeps the ESP's Lilu equal to macos27tool-opencore's provenance pin whatever the base ESP carries. VirtualSMC from the reference equals its pin (Acidanthera 1.3.8). |
| `-crypt_force_avx` in boot-args | CryptexFixup does nothing on a CPU with AVX2 (Haswell and newer, AMD Ryzen) unless it is set (CryptexFixup's README; 1.0.5's code at 0x187d-0x1973), and most VMs and PCs this pack is for have AVX2; without CryptexFixup's patch the OS cryptex does not graft. A CPU without AVX2 never reads it. macpro71 image only: the 9,2's boot-args are unchanged. |
| CryptexFixup.kext (Acidanthera 1.0.5), right after Lilu | Without it APFS requires the root hash of the image's modified OS cryptex, the graft fails (graftdmg 80) and the boot stops before userspace (QEMU, 2026-11-17: the 9,2 config without it failed there, with it passed). Taken from OCLP's RELEASE payload in the row `oclp_source`, byte-identical to macos27tool-opencore's pinned copy. |
| The reference's Booter, Kernel and UEFI quirks | What non-Apple firmware needs: runtime-services memory map, KASLR slide, write protection, Apple EFI image loading, CFG Lock, VT-d tables, power-state timeouts, CPU info on a hypervisor. Each one's reason is in macos27tool-opencore's `src/config-reasons.json`, marked "macpro71 image only". |

No GPU, Wi-Fi or model kexts are added. macos27tool-opencore then adds the Darwin 27 kernel repairs, IntelCommpage, M27Exec
and the boot-args. Its reasons check refuses any element without a reason, and any "macpro71 image only" element in
a config that isn't this one (SMBIOS MacPro7,1, created).

## Verify

- `python3.14 -I -B -m unittest discover -s patches/opencore-macpro71/tests` (with the store, the real reference's
  quirks: every element it adds has a reason).
- Build log: `opencore-macpro71: SMBIOS MacPro7,1 (new identity), VirtualSMC after Lilu, quirks set: ...`.
- Boot on the PC (or the builder's QEMU GEN2): the desktop; `sysctl hw.model` is MacPro7,1;
  `kextstat | grep -i smc` shows VirtualSMC.

## Validation

NEEDS VALIDATION: host tests only; no macpro71 image is built or booted yet, and the real PC's boot (agdpmod, USB map, audio, network) is untested.

## Revisions
- r1 2026-11-22 New: the macpro71 model's OpenCore: SMBIOS MacPro7,1 with a per-image identity (the board id Mac-27AD2F918AE68F61), VirtualSMC, CryptexFixup and Lilu from OCLP's pinned payload, and the QEMU reference's firmware quirks, for an Intel PC with a Polaris GPU (first proof: MSI B460M PRO, i5-10400, RX 580). Not carried yet: the PC's Realtek Ethernet, AX210 Wi-Fi/Bluetooth and ALC892 audio kexts.
