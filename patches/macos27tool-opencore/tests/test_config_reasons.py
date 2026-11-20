"""PROTOCOL S18: every enabled element of the finished OpenCore config has a reason, and apply.sh refuses one without.
With HAO_OPENCORE_IMAGE_CONFIG (an image's EFI/OC/config.plist) the real config is checked too."""
import json
import os
from pathlib import Path
import plistlib
import runpy
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
CR = runpy.run_path(str(PATCH / 'src/config_reasons.py'))
APPLE = '7C436110-AB2A-4BBB-A880-FE41995C9F82'


def config(**changes):
    d = {'Kernel': {'Add': [{'BundlePath': 'Lilu.kext', 'Enabled': True}, {'BundlePath': 'Off.kext', 'Enabled': False}],
                    'Patch': [{'Comment': 'p', 'Enabled': True}], 'Block': [],
                    'Quirks': {'DisableIoMapper': True, 'SetApfsTrimTimeout': -1, 'Off': False}},
         'Booter': {'Quirks': {'SetupVirtualMap': True}}, 'UEFI': {'Quirks': {'ResizeGpuBars': -1}},
         'Misc': {'Security': {'Vault': 'Optional', 'PasswordHash': b'x'}},
         'NVRAM': {'Add': {APPLE: {'boot-args': '-v', 'csr-active-config': b'\x03\x08\x00\x00'}},
                   'Delete': {APPLE: ['boot-args']}, 'WriteFlash': True}}
    d.update(changes)
    return d


class Elements(unittest.TestCase):
    def test_what_counts(self):
        self.assertEqual(CR['elements'](config()), sorted([
            'kext:Lilu.kext', 'kernel-patch:p', 'kernel-quirk:DisableIoMapper', 'booter-quirk:SetupVirtualMap',
            'security:Vault=Optional', f'nvram-add:{APPLE}:boot-args', f'nvram-add:{APPLE}:csr-active-config=03080000',
            f'nvram-delete:{APPLE}:boot-args', 'nvram:WriteFlash=True']))

    def test_a_changed_security_value_needs_its_own_reason(self):
        reasons = {e: 'r' for e in CR['elements'](config())}
        changed = config()
        changed['NVRAM']['Add'][APPLE]['csr-active-config'] = b'\xff\x0f\x00\x00'
        self.assertEqual(CR['missing'](changed, reasons), [f'nvram-add:{APPLE}:csr-active-config=ff0f0000'])

    def test_generic_only_elements_need_the_generic_config(self):
        cfg = config()
        cfg['Kernel']['Add'].append({'BundlePath': 'VirtualSMC.kext', 'Enabled': True})
        reasons = {e: 'r' for e in CR['elements'](cfg)}
        reasons['kext:VirtualSMC.kext'] = CR['GENERIC_ONLY'] + ': an SMC for non-Apple firmware'
        self.assertEqual(CR['missing'](cfg, reasons), ['kext:VirtualSMC.kext'])          # a Mac image: refused
        cfg['PlatformInfo'] = {'UpdateSMBIOSMode': 'Create', 'Generic': {'SystemProductName': 'Any1,1'}}
        self.assertEqual(CR['missing'](cfg, reasons), ['kext:VirtualSMC.kext'])          # no generic target: refused
        generic = {'opencore_generic': True}
        self.assertEqual(CR['missing'](cfg, reasons, generic), [])                       # the generic image
        cfg['PlatformInfo'] = {}
        self.assertEqual(CR['missing'](cfg, reasons, generic), ['kext:VirtualSMC.kext'])  # SMBIOS not created

    def test_the_os_layer_files_name_no_model(self):
        """S15: a model's reasons and generated kexts are its target's (platform_data), not this recipe's."""
        import re
        model = re.compile(r'(?:MacBook(?:Air|Pro)?|iMac(?:Pro)?|Macmini|MacPro)\d+,\d+|Mac-[0-9A-F]{16}')
        for name in ('src/config-reasons.json', 'src/kext-provenance.json', 'src/config_reasons.py'):
            self.assertIsNone(model.search((PATCH / name).read_text()), name)

    def test_the_targets_reasons_and_provenance_count(self):
        """A model's elements are explained in its target's platform_data (HAO_TARGET_DATA), not in this file."""
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            path, data = d / 'config.plist', d / 'target-data.json'
            cfg = config()
            cfg['Kernel']['Patch'] = []
            cfg['Kernel']['Add'] = [{'BundlePath': 'Model.kext', 'Enabled': True}]
            path.write_bytes(plistlib.dumps(cfg))
            (d / 'reasons.json').write_text(json.dumps({e: 'r' for e in CR['elements'](cfg) if e != 'kext:Model.kext'}))
            (d / 'Kexts/Model.kext/Contents').mkdir(parents=True)
            (d / 'Kexts/Model.kext/Contents/Info.plist').write_bytes(b'p')
            (d / 'prov.json').write_text('{}')
            env = {k: v for k, v in os.environ.items() if k != 'HAO_TARGET_DATA'}
            run = lambda *a, **more: subprocess.run(
                [sys.executable, '-I', str(PATCH / 'src/config_reasons.py'), 'check', str(path), '--reasons',
                 str(d / 'reasons.json'), '--kexts', str(d / 'Kexts'), '--provenance', str(d / 'prov.json'), *a],
                capture_output=True, text=True, env=dict(env, **more))
            self.assertEqual(run().returncode, 1)                                   # no reason for Model.kext
            data.write_text(json.dumps({'opencore_reasons': {'kext:Model.kext': 'the model needs it'}}))
            r = run('--target-data', str(data))
            self.assertEqual(r.returncode, 1)                                       # a reason, but no provenance
            self.assertIn('Model.kext: no provenance', r.stderr)
            data.write_text(json.dumps({'opencore_reasons': {'kext:Model.kext': 'the model needs it'},
                                        'kext_provenance': {'Model.kext': {'kind': 'oclp-generated', 'source': 'u',
                                            'files': {'Contents/Info.plist': hashlib.sha256(b'p').hexdigest()}}}}))
            self.assertEqual(run(HAO_TARGET_DATA=str(data)).returncode, 0)          # from the environment, as in a build

    def test_mbp92_target_explains_its_generated_kexts(self):
        data = json.loads((PATCH.parent.parent / 'pipeline/targets/mbp92.json').read_text())['platform_data']
        for name in ('ECM-Override.kext', 'USB-Map.kext', 'USB-Map-Tahoe.kext'):
            self.assertEqual(data['kext_provenance'][name]['kind'], 'oclp-generated', name)
            self.assertTrue(data['opencore_reasons']['kext:' + name], name)

    def test_reasons_file(self):
        reasons = CR['load_reasons']()
        self.assertGreater(len(reasons), 50)
        for element, why in reasons.items():
            if why.startswith('UNTRACED:'):
                self.assertIn('batch boot', why, element)            # says which test settles it

    def test_cli_refuses_an_unexplained_element(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'config.plist'
            path.write_bytes(plistlib.dumps(config()))
            r = subprocess.run([sys.executable, '-I', str(PATCH / 'src/config_reasons.py'), 'check', str(path)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn('kernel-patch:p', r.stderr)

    def test_apply_runs_the_check_on_the_finished_config(self):
        script = (PATCH / 'apply.sh').read_text()
        lint = script.index('plutil -lint "$ESP/EFI/OC/config.plist"')
        self.assertIn('"$PY" -I "$P/src/config_reasons.py" check "$ESP/EFI/OC/config.plist" --kexts "$ESP/EFI/OC/Kexts"',
                      script[lint:])

    def test_kext_provenance(self):
        provenance = {'Lilu.kext': {'kind': 'oclp-payload', 'source': 'u', 'files': {}},
                      'HaoExec.kext': {'kind': 'hao', 'source': 'row'}}
        cfg = {'Kernel': {'Add': [{'BundlePath': 'Lilu.kext', 'Enabled': True},
                                  {'BundlePath': 'HaoExec.kext', 'Enabled': True},
                                  {'BundlePath': 'Lilu.kext/Contents/PlugIns/X.kext', 'Enabled': True}]}}
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / 'Lilu.kext/Contents/MacOS/Lilu'
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b'lilu')
            import hashlib
            provenance['Lilu.kext']['files'] = {'Contents/MacOS/Lilu': hashlib.sha256(b'lilu').hexdigest()}
            self.assertEqual(CR['provenance_problems'](cfg, d, provenance), [])        # hao kext: checked elsewhere
            exe.write_bytes(b'other lilu')
            self.assertIn('Lilu.kext: files differ', CR['provenance_problems'](cfg, d, provenance)[0])
            (Path(d) / 'Extra.kext').mkdir()
            cfg['Kernel']['Add'].append({'BundlePath': 'Extra.kext', 'Enabled': True})
            self.assertTrue(any('Extra.kext: no provenance' in p for p in CR['provenance_problems'](cfg, d, provenance)))

    def test_provenance_file(self):
        provenance = json.loads((PATCH / 'src/kext-provenance.json').read_text())
        self.assertIn('generic image only', provenance['VirtualSMC.kext']['note'])     # never a Mac image's
        for name, entry in provenance.items():
            self.assertIn(entry['kind'], ('hao', 'oclp-payload', 'oclp-generated', 'acidanthera-release'), name)
            if entry['kind'] == 'acidanthera-release':
                self.assertTrue(entry['source'].startswith('https://github.com/acidanthera/'), name)
            if entry['kind'] == 'oclp-payload':
                self.assertTrue(entry['source'].startswith('https://github.com/dortania/OpenCore-Legacy-Patcher/blob/'), name)
                self.assertTrue(entry['files'], name)

    @unittest.skipUnless(os.environ.get('HAO_OPENCORE_IMAGE_ESP'), "set HAO_OPENCORE_IMAGE_ESP (an image's EFI/OC folder)")
    def test_real_esp(self):
        oc = Path(os.environ['HAO_OPENCORE_IMAGE_ESP'])
        r = subprocess.run([sys.executable, '-I', str(PATCH / 'src/config_reasons.py'), 'check', str(oc / 'config.plist'),
                            '--kexts', str(oc / 'Kexts')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(os.environ.get('HAO_OPENCORE_IMAGE_CONFIG'), 'set HAO_OPENCORE_IMAGE_CONFIG')
    def test_real_config(self):
        with open(os.environ['HAO_OPENCORE_IMAGE_CONFIG'], 'rb') as f:
            data = CR['target_data']()
            self.assertEqual(CR['missing'](plistlib.load(f), CR['load_reasons'](data=data), data), [])


if __name__ == '__main__':
    unittest.main()
