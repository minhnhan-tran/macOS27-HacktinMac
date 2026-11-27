"""src/macpro71.py on a fixture ESP and the real qemu_reference_efi row when the store has it."""
from pathlib import Path
import io
import json
import os
import plistlib
import sys
import tarfile
import zipfile
import runpy
import shutil
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
G = runpy.run_path(str(PATCH/'src/macpro71.py'))
CR = runpy.run_path(str(TOP/'patches/macos27tool-opencore/src/config_reasons.py'))


def reference(root):
    """A reference tree like the row: config with quirks and a VirtualSMC entry, and the kext."""
    (root/'EFI/OC/Kexts/VirtualSMC.kext/Contents/MacOS').mkdir(parents=True)
    (root/'EFI/OC/Kexts/VirtualSMC.kext/Contents/MacOS/VirtualSMC').write_bytes(b'smc')
    (root/'EFI/OC/config.plist').write_bytes(plistlib.dumps({
        'ACPI': {'Add': [], 'Quirks': {'ResetLogoStatus': False}},
        'Booter': {'Quirks': {'SetupVirtualMap': True, 'ProvideMaxSlide': 0, 'OnlyInReference': True,
                              'DevirtualiseMmio': False, 'ProtectUefiServices': False, 'RebuildAppleMemoryMap': False,
                              'SyncRuntimePermissions': False, 'EnableWriteUnprotector': True,
                              'ResizeAppleGpuBars': -1}},
        'Kernel': {'Add': [{'BundlePath': 'VirtualSMC.kext', 'Enabled': True, 'Arch': 'x86_64',
                            'ExecutablePath': 'Contents/MacOS/VirtualSMC', 'PlistPath': 'Contents/Info.plist'}],
                   'Quirks': {'DisableIoMapper': True, 'SetApfsTrimTimeout': -1, 'ProvideCurrentCpuInfo': True,
                              'AppleCpuPmCfgLock': True}},
        'UEFI': {'Quirks': {'RequestBootVarRouting': True, 'ReleaseUsbOwnership': False}}}))


def hardware_inputs(folder):
    """The staged hardware rows: one fake RELEASE zip per HARDWARE row (the bundle layouts the real zips have)."""
    folder.mkdir(exist_ok=True)
    for row, bundles in G['HARDWARE']:
        with zipfile.ZipFile(folder/row, 'w') as z:
            for bundle, _ in bundles:
                exe = bundle[:-5]
                z.writestr('Release/%s/Contents/Info.plist' % bundle, plistlib.dumps({'CFBundleExecutable': exe}))
                z.writestr('Release/%s/Contents/MacOS/%s' % (bundle, exe), b'x')
                z.writestr('Debug/%s/Contents/MacOS/%s' % (bundle, exe), b'debug')
    return folder


def oclp_tar(path):
    """An OCLP source tar holding the CryptexFixup and Lilu RELEASE zips (fake kexts, as the payload lays them out)."""
    with tarfile.open(path, 'w') as tar:
        for zip_path, name, body in ((G['CRYPTEXFIXUP_ZIP'], 'CryptexFixup', b'fixup'), (G['LILU_ZIP'], 'Lilu', b'oclp-lilu')):
            z = io.BytesIO()
            with zipfile.ZipFile(z, 'w') as zf:
                zf.writestr(f'{name}.kext/Contents/Info.plist', b'plist')
                zf.writestr(f'{name}.kext/Contents/MacOS/{name}', body)
                zf.writestr(f'{name}.kext.dSYM/Contents/Info.plist', b'dsym')
            info = tarfile.TarInfo(zip_path); info.size = len(z.getvalue())
            tar.addfile(info, io.BytesIO(z.getvalue()))
    return path


def esp(root, lilu=True):
    (root/'EFI/OC/Kexts').mkdir(parents=True)
    add = [{'BundlePath': 'Lilu.kext', 'Enabled': True}] if lilu else []
    for k in ('AppleIntelCPUPowerManagement.kext', 'AppleIntelCPUPowerManagementClient.kext'):
        (root/'EFI/OC/Kexts'/k/'Contents').mkdir(parents=True)
        add.append({'BundlePath': k, 'Enabled': True})
    add.append({'BundlePath': 'RestrictEvents.kext', 'Enabled': True})
    (root/'EFI/OC/config.plist').write_bytes(plistlib.dumps({
        'ACPI': {'Add': [], 'Quirks': {'ResetLogoStatus': False}},
        'Booter': {'Quirks': {'SetupVirtualMap': False, 'ProvideMaxSlide': 0, 'DevirtualiseMmio': False,
                              'ProtectUefiServices': False, 'RebuildAppleMemoryMap': False,
                              'SyncRuntimePermissions': False, 'EnableWriteUnprotector': True,
                              'ResizeAppleGpuBars': -1}},
        'Kernel': {'Add': add, 'Quirks': {'DisableIoMapper': False, 'SetApfsTrimTimeout': -1,
                                          'ProvideCurrentCpuInfo': True, 'AppleCpuPmCfgLock': True}},
        'UEFI': {'Quirks': {'RequestBootVarRouting': True, 'ReleaseUsbOwnership': False}},
        'PlatformInfo': {'Generic': {'SystemProductName': 'MacBookPro9,2'}}}))


class Generic(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp)
        reference(self.tmp/'ref'); esp(self.tmp/'esp'); self.oclp = oclp_tar(self.tmp/'oclp.tar')

    def run_it(self):
        G['main'](['macpro71.py', str(self.tmp/'esp'), str(self.tmp/'ref'), str(self.oclp), str(hardware_inputs(self.tmp/'inputs'))])
        return plistlib.loads((self.tmp/'esp/EFI/OC/config.plist').read_bytes())

    def test_the_pcs_own_kexts_load_after_lilu(self):
        names = [k['BundlePath'] for k in self.run_it()['Kernel']['Add']]
        self.assertLess(names.index('Lilu.kext'), names.index('AppleALC.kext'))
        for bundle in ('AppleALC.kext', 'RealtekRTL8111.kext', 'itlwm.kext', 'IntelBluetoothFirmware.kext',
                       'IntelBTPatcher.kext', 'BlueToolFixup.kext'):
            self.assertEqual(names.count(bundle), 1, bundle)
            self.assertTrue((self.tmp/'esp/EFI/OC/Kexts'/bundle/'Contents/Info.plist').is_file(), bundle)
        # the Release build, not the Debug one
        self.assertEqual((self.tmp/'esp/EFI/OC/Kexts/AppleALC.kext/Contents/MacOS/AppleALC').read_bytes(), b'x')

    def test_board_quirks_ssdts_xhci_and_igpu(self):
        config = self.run_it()
        self.assertEqual({k: config['Booter']['Quirks'][k] for k in ('DevirtualiseMmio', 'RebuildAppleMemoryMap', 'EnableWriteUnprotector', 'ResizeAppleGpuBars')},
                         {'DevirtualiseMmio': True, 'RebuildAppleMemoryMap': True, 'EnableWriteUnprotector': False, 'ResizeAppleGpuBars': 0})
        self.assertEqual((config['Kernel']['Quirks']['ProvideCurrentCpuInfo'], config['UEFI']['Quirks']['ReleaseUsbOwnership'],
                          config['ACPI']['Quirks']['ResetLogoStatus']), (False, True, True))
        paths = [a['Path'] for a in config['ACPI']['Add']]
        self.assertEqual(sorted(paths), sorted(p.name for p in (G['ACPI_DIR']).glob('*.aml')))
        self.assertEqual(len(paths), 7)
        for path in paths:                                  # a valid ACPI table: length and checksum
            raw = (self.tmp/'esp/EFI/OC/ACPI'/path).read_bytes()
            self.assertEqual((raw[:4], len(raw), sum(raw) % 256), (b'SSDT', int.from_bytes(raw[4:8], 'little'), 0), path)
        self.assertEqual(config['DeviceProperties']['Add']['PciRoot(0x0)/Pci(0x2,0x0)']['AAPL,ig-platform-id'], bytes.fromhex('0300c89b'))
        names = [k['BundlePath'] for k in config['Kernel']['Add']]
        self.assertLess(names.index('Lilu.kext'), names.index('WhateverGreen.kext'))
        self.assertIn('XHCI-unsupported.kext', names)

    def test_audio_layout_and_usb_map(self):
        config = self.run_it()
        self.assertEqual(config['DeviceProperties']['Add']['PciRoot(0x0)/Pci(0x1f,0x3)']['layout-id'], b'\x01\x00\x00\x00')
        names = [k['BundlePath'] for k in config['Kernel']['Add']]
        self.assertLess(names.index('USBToolBox.kext'), names.index('UTBMap.kext'))
        info = plistlib.loads((self.tmp/'esp/EFI/OC/Kexts/UTBMap.kext/Contents/Info.plist').read_bytes())
        ports = info['IOKitPersonalities']['XHC']['IOProviderMergeProperties']['ports']
        self.assertEqual((len(ports), info['IOKitPersonalities']['XHC']['IOProviderMergeProperties']['port-count']),
                         (9, b'\x15\x00\x00\x00'))

    def test_force_avx_survives_m27_opencore(self):
        # CryptexFixup is inert on AVX2 CPUs without -crypt_force_avx; macos27tool-opencore (next) keeps existing args
        import subprocess
        c = self.run_it()
        args = c['NVRAM']['Add'][G['APPLE_NVRAM']]['boot-args'].split()
        self.assertEqual(args.count('-crypt_force_avx'), 1)
        self.assertEqual(self.run_it()['NVRAM']['Add'][G['APPLE_NVRAM']]['boot-args'].split().count('-crypt_force_avx'), 1)
        script = (TOP/'patches/macos27tool-opencore/apply.sh').read_text().split("<<'PYCONFIG'\n", 1)[1].split('\nPYCONFIG', 1)[0]
        cfg = self.tmp/'esp/EFI/OC/config.plist'
        c.setdefault('Misc', {}).setdefault('Debug', {})
        cfg.write_bytes(plistlib.dumps(c))
        subprocess.run([sys.executable, '-c', script, str(cfg)], check=True)
        after = plistlib.loads(cfg.read_bytes())['NVRAM']['Add'][G['APPLE_NVRAM']]['boot-args'].split()
        self.assertIn('-crypt_force_avx', after)

    def test_quirks_smc_and_smbios(self):
        c = self.run_it()
        self.assertTrue(c['Booter']['Quirks']['SetupVirtualMap'])
        self.assertTrue(c['Kernel']['Quirks']['DisableIoMapper'])
        self.assertNotIn('OnlyInReference', c['Booter']['Quirks'])         # only quirks this config knows
        self.assertEqual([k['BundlePath'] for k in c['Kernel']['Add']],
                         ['Lilu.kext', 'CryptexFixup.kext', 'VirtualSMC.kext', 'RestrictEvents.kext',
                          'AppleALC.kext', 'RealtekRTL8111.kext', 'itlwm.kext', 'IntelBluetoothFirmware.kext', 'IntelBTPatcher.kext',
                          'BlueToolFixup.kext', 'WhateverGreen.kext', 'USBToolBox.kext', 'UTBMap.kext', 'XHCI-unsupported.kext'])
        for dropped in ('AppleIntelCPUPowerManagement.kext', 'AppleIntelCPUPowerManagementClient.kext'):
            self.assertFalse((self.tmp/'esp/EFI/OC/Kexts'/dropped).exists(), dropped)
        self.assertEqual((self.tmp/'esp/EFI/OC/Kexts/CryptexFixup.kext/Contents/MacOS/CryptexFixup').read_bytes(), b'fixup')
        self.assertTrue((self.tmp/'esp/EFI/OC/Kexts/VirtualSMC.kext/Contents/MacOS/VirtualSMC').is_file())
        g = c['PlatformInfo']['Generic']
        self.assertEqual((g['SystemProductName'], c['PlatformInfo']['UpdateSMBIOSMode']), ('MacPro7,1', 'Create'))
        self.assertEqual((len(g['SystemSerialNumber']), len(g['MLB']), len(g['ROM'])), (12, 17, 6))
        self.assertTrue(CR['is_generic'](c, json.loads((TOP/'pipeline/targets/macpro71.json').read_text())['platform_data']))

    def test_identity_is_generated_once_and_kept(self):
        first = self.run_it()['PlatformInfo']['Generic']
        again = self.run_it()['PlatformInfo']['Generic']
        self.assertEqual(first, again)
        other = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, other)
        esp(other/'esp')
        G['main'](['macpro71.py', str(other/'esp'), str(self.tmp/'ref'), str(self.oclp), str(hardware_inputs(self.tmp/'inputs'))])
        third = plistlib.loads((other/'esp/EFI/OC/config.plist').read_bytes())['PlatformInfo']['Generic']
        self.assertNotEqual(first['SystemSerialNumber'], third['SystemSerialNumber'])   # one per image

    def test_refuses_without_lilu(self):
        shutil.rmtree(self.tmp/'esp'); esp(self.tmp/'esp', lilu=False)
        with self.assertRaises(SystemExit):
            self.run_it()

    def test_every_element_it_adds_has_a_generic_reason(self):
        before = set(CR['elements'](plistlib.loads((self.tmp/'esp/EFI/OC/config.plist').read_bytes())))
        reasons = CR['load_reasons'](data=json.loads((TOP/'pipeline/targets/macpro71.json').read_text())['platform_data'])
        rows = runpy.run_path(str(TOP/'pipeline/lock.py'))['rows'](str(TOP/'pipeline/inputs/25G342'))
        real = Path(rows['qemu_reference_efi'][1])
        if real.is_dir():                                                    # the row's real quirk set
            shutil.rmtree(self.tmp/'ref'); shutil.copytree(real, self.tmp/'ref')
            ref = plistlib.loads((real/'EFI/OC/config.plist').read_bytes())
            config = plistlib.loads((self.tmp/'esp/EFI/OC/config.plist').read_bytes())
            for s in G['SECTIONS']:
                config[s]['Quirks'] = {k: (False if isinstance(v, bool) else v) for k, v in ref[s]['Quirks'].items()}
            (self.tmp/'esp/EFI/OC/config.plist').write_bytes(plistlib.dumps(config))
        added = set(CR['elements'](self.run_it())) - before
        self.assertIn('kext:VirtualSMC.kext', added)
        for e in sorted(added):
            self.assertIn(e, reasons, e)

    @unittest.skipUnless((TOP/'store/artifacts/oclp-source-fd83bda09-9d111ed9/oclp-source.tar').is_file(), 'needs the OCLP source row')
    def test_real_oclp_kexts_are_the_pinned_payloads(self):
        import hashlib, json
        self.oclp = TOP/'store/artifacts/oclp-source-fd83bda09-9d111ed9/oclp-source.tar'
        self.run_it()
        provenance = json.loads((TOP/'patches/macos27tool-opencore/src/kext-provenance.json').read_text())
        for name in ('CryptexFixup.kext', 'Lilu.kext'):
            kext = self.tmp/'esp/EFI/OC/Kexts'/name
            have = {str(f.relative_to(kext)): hashlib.sha256(f.read_bytes()).hexdigest() for f in kext.rglob('*') if f.is_file()}
            self.assertEqual(have, provenance[name]['files'], name)

    def test_the_references_lilu_is_replaced(self):
        self.run_it()
        self.assertEqual((self.tmp/'esp/EFI/OC/Kexts/Lilu.kext/Contents/MacOS/Lilu').read_bytes(), b'oclp-lilu')

    def test_apply_runs_before_m27_opencore(self):
        order = [l.split('#')[0].strip() for l in (TOP/'pipeline/order.txt').read_text().splitlines()]
        self.assertLess(order.index('opencore-macpro71'), order.index('macos27tool-opencore'))


if __name__ == '__main__':
    unittest.main()
