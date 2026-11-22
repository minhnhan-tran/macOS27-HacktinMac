"""The macpro71 model's part of an image's OpenCore: a non-Apple Intel PC started as a MacPro7,1.

  macpro71.py ESP REFERENCE OCLP_SOURCE

ESP is the image's EFI partition (EFI/OC/config.plist as pipeline/stages/build-efi.py made it for the macpro71 target).
REFERENCE is the row qemu_reference_efi: the OpenCore tree tested on OVMF (QEMU's UEFI), the base of
tools/kernel-foundation vm_esp.py. This sets, in place:

- PlatformInfo: SMBIOS MacPro7,1 (a model Tahoe supports natively, so its donors and OCLP's base config fit it), created
  on firmware that has none (UpdateSMBIOSMode Create). Serial, board serial (MLB), system UUID and ROM are generated for
  this image the first time and kept after; they are random, not Apple-issued, so Apple services that check them
  (iMessage, FaceTime) need the user's own valid values.
- VirtualSMC.kext (Acidanthera 1.3.8, from the reference): non-Apple firmware has no SMC, and macOS needs one. It
  conflicts with a real Mac's SMC: this pack is never for Mac hardware.
- The reference's Booter, Kernel and UEFI quirks: each quirk the reference turns on is set to its value (only quirks
  this OpenCore's config knows).
- Lilu.kext replaced by OCLP's RELEASE payload (1.7.2, the pinned bytes), whatever Lilu the base ESP carries: both
  images load the same Lilu.
- CryptexFixup.kext (Acidanthera 1.0.5, OCLP's RELEASE payload from OCLP_SOURCE, the row oclp_source): without it APFS
  requires the image's modified OS cryptex's root hash and the boot stops at its graft (QEMU, bench 0726).
- boot-args gets `-crypt_force_avx`: CryptexFixup does nothing on a CPU with AVX2 (Haswell and newer, AMD Ryzen) unless
  it is set (its README; 1.0.5's code at 0x187d-0x1973), and most machines this pack is for have AVX2. A CPU without
  AVX2 never reads it.

macos27tool-opencore runs next: the Darwin 27 kernel repairs, IntelCommpage, M27Exec and boot-args, and the check that every
element of the finished config has a recorded reason (src/config-reasons.json there).
"""
import io
import plistlib
import secrets
import shutil
import sys
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path

PRODUCT = 'MacPro7,1'
SECTIONS = ('Booter', 'Kernel', 'UEFI')
SMC = 'VirtualSMC.kext'
ALNUM = '0123456789ABCDEFGHJKLMNPQRSTUVWXYZ'      # serial characters (no I, no O)


def on(value):
    return value is True or (isinstance(value, int) and not isinstance(value, bool) and value > 0)


def quirks(config, reference):
    """{section: [quirk, ...]} set from the reference: every quirk it turns on that this config also has."""
    changed = {}
    for section in SECTIONS:
        have, ref = config[section]['Quirks'], reference[section]['Quirks']
        for name, value in sorted(ref.items()):
            if on(value) and name in have and have[name] != value:
                have[name] = value
                changed.setdefault(section, []).append(name)
    return changed


def add_smc(config, esp, reference_root):
    """Copy VirtualSMC.kext from the reference into the ESP and load it right after Lilu."""
    add = config['Kernel']['Add']
    names = [k['BundlePath'] for k in add]
    if 'Lilu.kext' not in names:
        raise SystemExit('opencore-macpro71: the config loads no Lilu.kext; VirtualSMC needs it')
    entry, = [k for k in plistlib.loads((reference_root/'EFI/OC/config.plist').read_bytes())['Kernel']['Add']
              if k['BundlePath'] == SMC]
    dest = esp/'EFI/OC/Kexts'/SMC
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(reference_root/'EFI/OC/Kexts'/SMC, dest)
    add[:] = [k for k in add if k['BundlePath'] != SMC]
    add.insert([k['BundlePath'] for k in add].index('Lilu.kext') + 1, dict(entry, Enabled=True))


CRYPTEXFIXUP_ZIP = 'payloads/Kexts/Acidanthera/CryptexFixup-v1.0.5-RELEASE.zip'
CRYPTEXFIXUP_ENTRY = dict(Arch='x86_64', BundlePath='CryptexFixup.kext', Comment='CryptexFixup', Enabled=True,
                          ExecutablePath='Contents/MacOS/CryptexFixup', MaxKernel='', MinKernel='20.0.0',
                          PlistPath='Contents/Info.plist')            # OCLP's own entry


LILU_ZIP = 'payloads/Kexts/Acidanthera/Lilu-v1.7.2-RELEASE.zip'


def oclp_kext(esp, oclp_tar, zip_path, bundle):
    """Put BUNDLE from OCLP's RELEASE payload ZIP_PATH (in the pinned OCLP source) into EFI/OC/Kexts, replacing any
    copy there: both images then carry the same bytes, the ones kext-provenance pins."""
    with tarfile.open(oclp_tar) as tar:
        member = tar.extractfile(zip_path)
        if member is None:
            raise SystemExit('opencore-macpro71: %s is not in the OCLP source' % zip_path)
        data = member.read()
    dest = esp/'EFI/OC/Kexts'/bundle
    if dest.exists():
        shutil.rmtree(dest)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
        found = [p for p in Path(tmp).rglob(bundle) if p.is_dir() and 'dSYM' not in str(p)]
        if len(found) != 1:
            raise SystemExit('opencore-macpro71: expected one %s in the payload, found %d' % (bundle, len(found)))
        shutil.copytree(found[0], dest)


def add_cryptexfixup(config, esp, oclp_tar):
    """CryptexFixup.kext from OCLP's RELEASE payload in the pinned OCLP source, loaded right after Lilu."""
    oclp_kext(esp, oclp_tar, CRYPTEXFIXUP_ZIP, 'CryptexFixup.kext')
    add = config['Kernel']['Add']
    add[:] = [k for k in add if k['BundlePath'] != 'CryptexFixup.kext']
    add.insert([k['BundlePath'] for k in add].index('Lilu.kext') + 1, dict(CRYPTEXFIXUP_ENTRY))


# The PC's own hardware (MSI B460M PRO in the first proof): rows pinned in pipeline/inputs/common, each a RELEASE zip.
# (row, [(bundle, needs Lilu)]): loaded in this order after Lilu, VirtualSMC and CryptexFixup.
HARDWARE = (
    ('appleac_release', [('AppleALC.kext', True)]),                                   # Realtek ALC892 audio
    ('rtl8111_release', [('RealtekRTL8111.kext', False)]),                            # Realtek 8168 Ethernet
    ('itlwm_release', [('itlwm.kext', False)]),                                       # Intel AX210 Wi-Fi (with HeliPort)
    ('intelbt_release', [('IntelBluetoothFirmware.kext', False), ('IntelBTPatcher.kext', True)]),   # AX210 Bluetooth
    ('bluetoolfixup_release', [('BlueToolFixup.kext', True)]),                       # macOS 12+ third-party BT firmware
)


def zip_kext(esp, zip_path, bundle):
    """Put BUNDLE (found anywhere in the RELEASE zip, outside dSYM and PlugIns) into EFI/OC/Kexts; its Kernel.Add entry."""
    dest = esp/'EFI/OC/Kexts'/bundle
    if dest.exists():
        shutil.rmtree(dest)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)
        found = [p for p in Path(tmp).rglob(bundle) if p.is_dir() and 'dSYM' not in str(p) and 'PlugIns' not in p.parts
                 and 'Debug' not in p.parts and '__MACOSX' not in p.parts]       # RealtekRTL8111 ships Debug/ and Release/: the Release one
        if len(found) != 1:
            raise SystemExit('opencore-macpro71: expected one %s in %s, found %d' % (bundle, Path(zip_path).name, len(found)))
        shutil.copytree(found[0], dest)
    info = plistlib.loads((dest/'Contents/Info.plist').read_bytes())
    exe = info.get('CFBundleExecutable')
    return dict(Arch='x86_64', BundlePath=bundle, Comment=bundle[:-5], Enabled=True,
                ExecutablePath='Contents/MacOS/%s' % exe if exe else '', MaxKernel='', MinKernel='20.0.0',
                PlistPath='Contents/Info.plist')


def add_hardware(config, esp, inputs):
    """The kexts of this PC's own devices, appended in HARDWARE order (Lilu plug-ins stay after Lilu)."""
    add = config['Kernel']['Add']
    names = [k['BundlePath'] for k in add]
    if 'Lilu.kext' not in names:
        raise SystemExit('opencore-macpro71: the config loads no Lilu.kext; the hardware kexts need it')
    loaded = []
    for row, bundles in HARDWARE:
        for bundle, _lilu in bundles:
            entry = zip_kext(esp, inputs/row, bundle)
            add[:] = [k for k in add if k['BundlePath'] != bundle]
            add.append(entry)
            loaded.append(bundle)
    return loaded


APPLE_NVRAM = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
FORCE_AVX = '-crypt_force_avx'


def force_avx(config):
    """Append -crypt_force_avx to boot-args (once): CryptexFixup is inert on AVX2 CPUs without it."""
    nvram = config.setdefault('NVRAM', {}).setdefault('Add', {}).setdefault(APPLE_NVRAM, {})
    args = nvram.get('boot-args', '').split()
    if FORCE_AVX not in args:
        args.append(FORCE_AVX)
    nvram['boot-args'] = ' '.join(args)


def serial(length):
    return ''.join(secrets.choice(ALNUM) for _ in range(length))


def platform(config):
    """SMBIOS MacPro7,1, created; identity values generated once per image and kept after."""
    info = config.setdefault('PlatformInfo', {})
    generic = info.setdefault('Generic', {})
    fresh = generic.get('SystemProductName') != PRODUCT or not generic.get('SystemSerialNumber')
    info.update(Automatic=True, UpdateDataHub=True, UpdateNVRAM=True, UpdateSMBIOS=True, UpdateSMBIOSMode='Create')
    generic.update(SystemProductName=PRODUCT, SpoofVendor=True)
    if fresh:
        generic.update(SystemSerialNumber=serial(12), MLB=serial(17),
                       SystemUUID=str(uuid.uuid4()).upper(), ROM=secrets.token_bytes(6))
    return fresh


def main(argv):
    esp, reference_root, oclp_tar, inputs = Path(argv[1]), Path(argv[2]), Path(argv[3]), Path(argv[4])
    path = esp/'EFI/OC/config.plist'
    config = plistlib.loads(path.read_bytes())
    reference = plistlib.loads((reference_root/'EFI/OC/config.plist').read_bytes())
    changed = quirks(config, reference)
    add_smc(config, esp, reference_root)
    oclp_kext(esp, oclp_tar, LILU_ZIP, 'Lilu.kext')     # the pinned Lilu, whatever the base ESP carries
    add_cryptexfixup(config, esp, oclp_tar)
    hardware = add_hardware(config, esp, inputs)
    force_avx(config)
    fresh = platform(config)
    path.write_bytes(plistlib.dumps(config))
    print('opencore-macpro71: SMBIOS %s (%s), VirtualSMC and CryptexFixup after Lilu, %s, kexts %s, quirks set: %s' % (
        PRODUCT, 'new identity' if fresh else 'identity kept', FORCE_AVX, ', '.join(hardware),
        '; '.join('%s %s' % (s, ', '.join(n)) for s, n in changed.items()) or 'none needed'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
