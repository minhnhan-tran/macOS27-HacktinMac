"""src/amfi_dyld.py: hardened programs keep AMFI's dyld environment policy.
Synthetic bytes always; with HAO_OPENCORE_BOOT_KC=KC[:KC...] also real Boot KCs, through the binder as make.sh stages it."""
import os
from pathlib import Path
import plistlib
import runpy
import struct
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
AD = runpy.run_path(str(PATCH/'src/amfi_dyld.py'))
Refused = AD['Refused']
HARDENING = bytes.fromhex('554889e5833d') + b'\x10\x20\x00\x00' + bytes.fromhex('007404b0015dc35de9') + b'\0'*4
BASE, FILEOFF = 0xffffff8001000000, 0x1000


class Member:
    def __init__(self, size):
        self.segments = {'__TEXT': (BASE, size, FILEOFF, size)}


def site_bytes(rel):
    return (bytes.fromhex('4531e4b90000000085c0740be8') + struct.pack('<i', rel)
            + bytes.fromhex('0fb6c8c1e107b87fffffff230309c88903'))


def image(target_is_hardening=True, sites=1, outside=False):
    """Collection bytes: AMFI's __TEXT at FILEOFF holding hardeningEnabled() at +0x100 and the site at +0x400."""
    text = bytearray(0x1000)
    text[0x100:0x100+len(HARDENING)] = HARDENING if target_is_hardening else b'\x55\x48\x89\xe5\x31\xc0\x5d\xc3'
    call_end = 0x400+AD['CALL']+5
    text[0x400:0x400+len(site_bytes(0))] = site_bytes(0x100-call_end)
    if sites == 2:
        text[0x800:0x800+len(site_bytes(0))] = site_bytes(0x100-(0x800+AD['CALL']+5))
    data = bytearray(FILEOFF) + text
    if outside:
        data = bytearray(FILEOFF) + bytearray(0x1000) + text      # the site lies past AMFI's __TEXT
    return bytes(data)


class Site(unittest.TestCase):
    def test_the_call_answers_true_and_nothing_else_changes(self):
        offset, find, replace, target = AD['site'](image(), Member(0x1000))
        self.assertEqual(offset, FILEOFF+0x400)
        self.assertEqual(target, BASE+0x100)
        self.assertEqual(len(find), len(replace))
        call = AD['CALL']
        self.assertEqual(replace[call:call+5], bytes.fromhex('b0010f1f00'))     # mov al,1; 3-byte nop
        self.assertEqual(find[:call], replace[:call])
        self.assertEqual(find[call+5:], replace[call+5:])
        self.assertEqual(find[call+5:call+11], bytes.fromhex('0fb6c8c1e107'))   # movzx ecx,al; shl ecx,7 (bit 0x80)

    def test_refusals(self):
        for name, data, size in (('twice', image(sites=2), 0x1000), ('not hardeningEnabled', image(False), 0x1000),
                                 ('outside AMFI', image(outside=True), 0x1000), ('absent', bytes(0x3000), 0x1000)):
            with self.subTest(name), self.assertRaises(Refused):
                AD['site'](data, Member(size))

    def test_entry_is_limited_to_amfi_and_this_kernel(self):
        _, find, replace, _ = AD['site'](image(), Member(0x1000))
        e = AD['entry'](find, replace, '27.2.0')
        self.assertEqual((e['Identifier'], e['MinKernel'], e['MaxKernel'], e['Count'], e['Enabled']),
                         ('com.apple.driver.AppleMobileFileIntegrity', '27.2.0', '27.2.0', 1, True))
        self.assertEqual((e['Mask'], e['ReplaceMask'], e['Skip'], e['Limit']), (b'', b'', 0, 0))

    def test_install_replaces_its_own_entry_only(self):
        _, find, replace, _ = AD['site'](image(), Member(0x1000))
        e = AD['entry'](find, replace, '27.2.0')
        other = dict(e, Comment='other')
        config = AD['install']({'Kernel': {'Patch': [other, dict(e, Find=b'old')]}}, e)
        self.assertEqual(config['Kernel']['Patch'], [other, e])
        self.assertEqual(AD['install'](config, e), config)


class Wiring(unittest.TestCase):
    def test_apply_binds_it_before_the_reason_check(self):
        script = (PATCH/'apply.sh').read_text()
        call = script.index('"$P/src/amfi_dyld.py"')
        self.assertLess(script.index('"$P/src/repairs.py"'), call)
        self.assertLess(call, script.index('"$P/src/config_reasons.py" check'))
        self.assertIn('--config "$ESP/EFI/OC/config.plist"', script[call:call+400])

    def test_it_has_a_reason(self):
        reasons = runpy.run_path(str(PATCH/'src/config_reasons.py'))['load_reasons']()
        why = reasons['kernel-patch:'+AD['COMMENT']]
        self.assertFalse(why.startswith('UNTRACED'))
        self.assertIn('DYLD', why)


@unittest.skipUnless(os.environ.get('HAO_OPENCORE_BOOT_KC'), 'set HAO_OPENCORE_BOOT_KC=KC[:KC...] (real Boot KCs)')
class RealCollections(unittest.TestCase):
    def test_binds_once_in_amfi(self):
        stage_py = runpy.run_path(str(TOP/'tools/kc-binder/src/stage.py'))
        with tempfile.TemporaryDirectory() as d:
            stage = Path(d)/'stage'
            stage_py['stage'](stage)
            config = Path(d)/'config.plist'
            for kc in os.environ['HAO_OPENCORE_BOOT_KC'].split(':'):
                with self.subTest(kc=kc):
                    config.write_bytes(plistlib.dumps({'Kernel': {'Patch': []}}))
                    self.assertEqual(AD['main'](['--binder', str(stage), '--boot-kc', kc, '--config', str(config)]), 0)
                    patch, = plistlib.loads(config.read_bytes())['Kernel']['Patch']
                    self.assertEqual(patch['Identifier'], 'com.apple.driver.AppleMobileFileIntegrity')
                    self.assertEqual(Path(kc).read_bytes().count(patch['Find']), 1)


if __name__ == '__main__':
    unittest.main()
