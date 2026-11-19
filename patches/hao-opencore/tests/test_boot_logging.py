import copy
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

APPLE = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
SCRIPT = (Path(__file__).resolve().parents[1]/'apply.sh').read_text().split("<<'PYCONFIG'\n",1)[1].split('\nPYCONFIG',1)[0]


class BootLoggingTests(unittest.TestCase):
    def apply(self, value, dev=None):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'config.plist'
            path.write_bytes(plistlib.dumps(value))
            env = {k: v for k, v in os.environ.items() if k != 'IMAGE_DEV_BUILD'}
            if dev is not None: env['IMAGE_DEV_BUILD'] = dev
            subprocess.run([sys.executable, '-c', SCRIPT, str(path)], check=True, env=env)
            return plistlib.loads(path.read_bytes())

    def fixture(self):
        return {'Kernel': {'Add': []}, 'NVRAM': {
            'Add': {APPLE: {'boot-args': 'keepsyms=1 -v startup_debug=1',
                            'bootercfg': 'log=1', 'bootercfg-once': 'log=1',
                            'csr-active-config': b'\x03\x08\x00\x00',
                            'efi-boot-device': 'netboot-fixture'},
                    'other-guid': {'untouched': 'yes'}},
            'Delete': {APPLE: ['boot-args'], 'other-guid': ['existing']}},
            'Misc': {'Debug': {'Target': 67, 'DisplayLevel': 0}}}

    def test_targeted_cleanup_preserves_unrelated_variables_and_route(self):
        original = self.fixture(); result = self.apply(copy.deepcopy(original))
        added = result['NVRAM']['Add'][APPLE]
        # bootercfg and bootercfg-once are no longer cleared (a boot-logging investigation's; audit 2026-11-17)
        self.assertEqual(result['NVRAM']['Delete'][APPLE], ['boot-args'])
        self.assertEqual(added['efi-boot-device'], original['NVRAM']['Add'][APPLE]['efi-boot-device'])
        self.assertEqual(added['csr-active-config'], original['NVRAM']['Add'][APPLE]['csr-active-config'])
        self.assertEqual(result['NVRAM']['Add']['other-guid'], original['NVRAM']['Add']['other-guid'])
        self.assertEqual(result['NVRAM']['Delete']['other-guid'], ['existing'])
        self.assertNotIn('hao_crash=1', added['boot-args'].split())     # dev images only (dev-diagnostics)
        self.assertNotIn('-v', added['boot-args'].split())
        self.assertEqual(result['Misc']['Debug']['Target'], 0)     # no OpenCore log in an end-user image

    def test_no_measurement_switches_whatever_the_environment(self):
        # hao_crash and hao-runtime's measurement switches are dev-diagnostics' (pack dev); hao-opencore writes the
        # same boot-args in every build, IMAGE_DEV_BUILD or not, and removes a hao_crash=1 it finds.
        release = '-lilubetaall amfi_get_out_of_my_way=1 amfi_force_policy=8 auxKCAtBoot=false'.split()
        for dev in (None, '0', '1'):
            with self.subTest(dev=dev):
                args = self.apply(self.fixture(), dev)['NVRAM']['Add'][APPLE]['boot-args'].split()
                self.assertEqual(['keepsyms=1'] + release, args)

    def test_an_existing_hao_crash_is_removed(self):
        value = self.fixture()
        value['NVRAM']['Add'][APPLE]['boot-args'] += ' hao_crash=1'
        self.assertNotIn('hao_crash=1', self.apply(value)['NVRAM']['Add'][APPLE]['boot-args'].split())

    def test_idempotent_and_the_leftover_settings_are_gone(self):
        value = self.fixture()
        value['NVRAM']['Add'][APPLE]['boot-args'] += ' -nokcmismatchpanic -pmap_pcid_disable'
        value['Kernel']['Add'] = [dict(BundlePath=b, Enabled=True) for b in
                                  ('Lilu.kext', 'CryptexFixup.kext', 'RSRHelper.kext', 'KDKlessWorkaround.kext',
                                   'CatalinaIntelI210Ethernet.kext')]
        result = self.apply(value)
        self.assertEqual(self.apply(result), result)
        args = result['NVRAM']['Add'][APPLE]['boot-args'].split()
        self.assertNotIn('-nokcmismatchpanic', args); self.assertNotIn('-pmap_pcid_disable', args)
        enabled = {k['BundlePath'] for k in result['Kernel']['Add'] if k['Enabled']}
        self.assertEqual(enabled, {'Lilu.kext', 'CryptexFixup.kext', 'HaoExec.kext'})


class DyldGate(unittest.TestCase):
    def test_apply_refuses_a_dyld_without_the_internal_install_edit(self):
        # the boot-arg is gone only because the image's dyld answers internalInstall() itself; apply.sh checks the
        # same bytes hao's live patcher reads (tools/macOS27Tool/src/livepatch.py DYLD_INTERNAL_EDITED)
        import runpy
        top = Path(__file__).resolve().parents[3]
        edited = runpy.run_path(str(top / 'tools/macOS27Tool/src/livepatch.py'))['DYLD_INTERNAL_EDITED']
        text = (Path(__file__).resolve().parents[1] / 'apply.sh').read_text()
        self.assertIn(edited.hex(), text)
        self.assertLess(text.index(edited.hex()), text.index("<<'PYCONFIG'"))
        self.assertNotIn("'ic_csr_internal=1',", SCRIPT)


if __name__ == '__main__': unittest.main()
