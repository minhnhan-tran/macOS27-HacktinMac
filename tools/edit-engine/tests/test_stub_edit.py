"""stub-edit: a generated stub is edited only when its lineage holds; only its slice changes; its identity follows.
python3.14 -I -B -m unittest discover -s tools/edit-engine/tests -k stub_edit

Runs macOS codesign on copies of this Mac's universal /usr/bin/true (never executed) in a temporary folder: its
x86_64 slice, re-signed ad hoc, stands in for m27's stub next to Apple's arm64e slice."""
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import signing   # noqa: E402

E = runpy.run_path(str(HERE.parent / 'stub-edit'))
TRUE = Path('/usr/bin/true')
PROGRAM = 'System/Applications/X.app/Contents/MacOS/X'


def universal_true():
    if sys.platform != 'darwin' or not shutil.which('codesign') or not TRUE.is_file():
        return None
    data = TRUE.read_bytes()
    try:
        names = [n for _, _, _, n in signing.slices(data)]
    except signing.SignatureError:
        return None
    return data if len(names) > 1 and 'x86_64' in names and 'arm64e' in names else None


DATA = universal_true()


@unittest.skipUnless(DATA, 'needs macOS codesign and a universal /usr/bin/true (x86_64 + arm64e)')
class StubEdit(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        root = Path(self.d.name)
        self.system = root / 'system'
        program = self.system / PROGRAM
        program.parent.mkdir(parents=True)
        (self.system / 'usr/lib/m27').mkdir(parents=True)
        parts, wide = E['fat_slices'](DATA)
        stub = None
        for i, (cpu, sub, align, blob) in enumerate(parts):
            if cpu == E['CPU_X86_64']:
                thin = root / 'x86'
                thin.write_bytes(blob)
                subprocess.run(['codesign', '-f', '-s', '-', '-i', 'com.example.x', str(thin)], check=True,
                               capture_output=True)
                parts[i] = (cpu, sub, align, thin.read_bytes())
                stub = i
        program.write_bytes(E['fat_join'](parts, wide))
        self.before = program.read_bytes()
        cds = E['cdhashes'](self.before)
        self.cds = cds
        self.stubs = self.system / E['STUBS']
        self.stubs.write_text('# m27 stub identities v1\n%s  %s  arm64e  usr/bin/other\n%s  %s  arm64e  %s\n' % (
            'a' * 40, 'b' * 40, cds['x86_64'], cds['arm64e'], PROGRAM))
        self.spec = root / 'spec.json'
        self.spec.write_text(json.dumps({
            'format': 1, 'path': PROGRAM, 'arch': 'x86_64', 'origin': {'kind': 'm27'},
            'edits': [{'id': 'load-z', 'kind': 'op', 'op': 'macho.add-dylib', 'args': {'path': '/usr/lib/libz.1.dylib'},
                       'reason': 'test'}],
            'sign': {'mode': 'adhoc', 'preserve': ['identifier', 'flags']}}))

    def tearDown(self):
        self.d.cleanup()

    def test_only_the_stub_changes_and_its_identity_follows(self):
        r = E['stub_edit'](self.system, self.spec)
        after = (self.system / PROGRAM).read_bytes()
        old = {signing.slice_name(c, s): b for c, s, _, b in E['fat_slices'](self.before)[0]}
        new = {signing.slice_name(c, s): b for c, s, _, b in E['fat_slices'](after)[0]}
        self.assertEqual(sorted(old), sorted(new))
        for name in old:
            if name != 'x86_64':
                self.assertEqual(old[name], new[name], name)
        self.assertIn(b'/usr/lib/libz.1.dylib', new['x86_64'])
        cd = E['cdhashes'](after)['x86_64']
        self.assertNotEqual(cd, self.cds['x86_64'])
        self.assertEqual(r['after']['stub_cdhash'], cd)
        lines = self.stubs.read_text().splitlines()
        self.assertEqual(lines[2], '  '.join([cd, self.cds['arm64e'], 'arm64e', PROGRAM]))
        self.assertEqual(lines[:2], ['# m27 stub identities v1', '%s  %s  arm64e  usr/bin/other' % ('a' * 40, 'b' * 40)])
        out = subprocess.run(['codesign', '-dv', '--arch', 'x86_64', str(self.system / PROGRAM)], capture_output=True,
                             text=True)
        self.assertIn('Identifier=com.example.x', out.stderr)

    def test_when_target_skips_on_a_target_without_the_key(self):
        spec = json.loads(self.spec.read_text())
        self.spec.write_text(json.dumps(dict(spec, when_target='model_gates')))
        data = Path(self.d.name) / 'target-data.json'
        data.write_text('{}')
        stubs = self.stubs.read_text()
        r = E['stub_edit'](self.system, self.spec, target_data=data)
        self.assertEqual(r['skipped'], 'the target has no platform_data model_gates')
        self.assertEqual((self.system / PROGRAM).read_bytes(), self.before)
        self.assertEqual(self.stubs.read_text(), stubs)
        with self.assertRaises(ValueError):
            E['stub_edit'](self.system, self.spec)                         # no target known: not decided
        data.write_text(json.dumps({'model_gates': {}}))
        r = E['stub_edit'](self.system, self.spec, target_data=data)
        self.assertNotIn('skipped', r)
        self.assertNotEqual((self.system / PROGRAM).read_bytes(), self.before)

    def test_a_stub_from_elsewhere_is_refused_and_nothing_is_written(self):
        text = self.stubs.read_text().replace(self.cds['x86_64'], 'c' * 40)
        self.stubs.write_text(text)
        with self.assertRaisesRegex(E['Refused'], 'not the stub'):
            E['stub_edit'](self.system, self.spec)
        self.assertEqual((self.system / PROGRAM).read_bytes(), self.before)
        self.assertEqual(self.stubs.read_text(), text)

    def test_another_apple_slice_is_refused(self):
        self.stubs.write_text(self.stubs.read_text().replace(self.cds['arm64e'], 'd' * 40))
        with self.assertRaisesRegex(E['Refused'], "Apple"):
            E['stub_edit'](self.system, self.spec)
        self.assertEqual((self.system / PROGRAM).read_bytes(), self.before)

    def test_the_spec_must_name_a_m27_stub_or_an_apple_file(self):
        s = json.loads(self.spec.read_text())
        s['origin'] = {'kind': 'tahoe'}
        self.spec.write_text(json.dumps(s))
        with self.assertRaisesRegex(E['Refused'], 'origin kind m27'):
            E['stub_edit'](self.system, self.spec)


@unittest.skipUnless(DATA, 'needs macOS codesign and a universal /usr/bin/true (x86_64 + arm64e)')
class NativeEdit(unittest.TestCase):
    """origin kind apple: the input's sha256 must be a key of the spec's expect; stub-identities is not touched."""
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.system = Path(self.d.name) / 'system'
        self.program = self.system / 'usr/libexec/x'
        self.program.parent.mkdir(parents=True)
        self.program.write_bytes(DATA)
        self.spec = Path(self.d.name) / 'spec.json'

    def tearDown(self):
        self.d.cleanup()

    def write_spec(self, expect):
        self.spec.write_text(json.dumps({
            'format': 1, 'path': 'usr/libexec/x', 'arch': 'x86_64', 'origin': {'kind': 'apple'}, 'expect': expect,
            'edits': [{'id': 'load-z', 'kind': 'op', 'op': 'macho.add-dylib', 'args': {'path': '/usr/lib/libz.1.dylib'},
                       'reason': 'test'}],
            'sign': {'mode': 'adhoc', 'preserve': ['identifier', 'flags']}}))

    def test_an_expected_input_is_edited_on_its_x86_64_slice_only(self):
        import hashlib
        self.write_spec({hashlib.sha256(DATA).hexdigest(): '0' * 64})
        r = E['stub_edit'](self.system, self.spec)
        self.assertEqual(r['kind'], 'apple')
        self.assertFalse(r['as_expected'])          # reported, not refused (signature padding differs per Mac)
        old = {signing.slice_name(c, s): b for c, s, _, b in E['fat_slices'](DATA)[0]}
        new = {signing.slice_name(c, s): b for c, s, _, b in E['fat_slices'](self.program.read_bytes())[0]}
        self.assertEqual(old['arm64e'], new['arm64e'])
        self.assertIn(b'/usr/lib/libz.1.dylib', new['x86_64'])
        self.assertFalse((self.system / E['STUBS']).exists())

    def test_another_input_is_refused_untouched(self):
        self.write_spec({'1' * 64: '0' * 64})
        with self.assertRaisesRegex(E['Refused'], 'expect'):
            E['stub_edit'](self.system, self.spec)
        self.assertEqual(self.program.read_bytes(), DATA)


class Layout(unittest.TestCase):
    def test_join_keeps_order_and_alignment(self):
        parts = [(0x0100000c, 2, 14, b'\xcf\xfa\xed\xfe' + bytes(100)), (0x01000007, 3, 12, b'\xcf\xfa\xed\xfe' + bytes(7))]
        joined = E['fat_join'](parts, False)
        back, wide = E['fat_slices'](joined)
        self.assertFalse(wide)
        self.assertEqual([(c, s, a, b) for c, s, a, b in back], parts)
        self.assertEqual([st % (1 << a) for (_, st, _, _), (_, _, a, _) in zip(signing.slices(joined), parts)], [0, 0])


if __name__ == '__main__':
    unittest.main()
