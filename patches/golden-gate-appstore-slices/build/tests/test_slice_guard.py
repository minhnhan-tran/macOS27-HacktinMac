"""m27-slice-guard on fixture bundles: built from build/slice_guard.c for the host's architecture (the logic does not
depend on it); Mach-O fixtures are headers only.
python3.14 -I -B -m unittest discover -s patches/golden-gate-appstore-slices/build/tests"""
import importlib.machinery
import importlib.util
import os
import pathlib
import platform
import plistlib
import struct
import subprocess
import tempfile
import time
import unittest

BUILD = pathlib.Path(__file__).resolve().parents[1]
X86_64, ARM64, I386 = 0x01000007, 0x0100000c, 7


def load_make():
    loader = importlib.machinery.SourceFileLoader('slice_make', str(BUILD / 'make'))
    spec = importlib.util.spec_from_loader('slice_make', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def thin(cputype):
    return struct.pack('<IIIIIII', 0xfeedfacf, cputype, 3, 2, 0, 0, 0) + bytes(64)


def fat(*cputypes):
    head = struct.pack('>II', 0xcafebabe, len(cputypes))
    for i, c in enumerate(cputypes):
        head += struct.pack('>IIIII', c, 3, 4096 * (i + 1), 64, 12)
    return head + bytes(64)


def app(root, name, main, files=(), receipt=True, executable=None, version='1.2', build='345'):
    bundle = root / f'{name}.app'
    exe = executable or name
    (bundle / 'Contents/MacOS').mkdir(parents=True)
    (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps(
        {'CFBundleExecutable': exe, 'CFBundleShortVersionString': version, 'CFBundleVersion': build}))
    (bundle / 'Contents/MacOS' / exe).write_bytes(main)
    for rel, data in files:
        path = bundle / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            path.symlink_to(data)
        else:
            path.write_bytes(data)
    if receipt:
        (bundle / 'Contents/_MASReceipt').mkdir()
        (bundle / 'Contents/_MASReceipt/receipt').write_bytes(b'receipt')
    return bundle


class SliceGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tool, _ = load_make().build(cls.tmp.name, platform.machine())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.dir.name) / 'Applications'
        self.root.mkdir()
        self.log = pathlib.Path(self.dir.name) / 'logs/m27/slice-guard.log'
        self.state = pathlib.Path(self.dir.name) / 'db/m27/slice-guard.state'

    def tearDown(self):
        self.dir.cleanup()

    def guard(self, *extra):
        out = subprocess.run([str(self.tool), '--state', str(self.state), '--log', str(self.log), '--settle', '0',
                              *extra, str(self.root)], check=True, capture_output=True, text=True)
        return out.stdout.splitlines()

    def test_universal_app_reports_each_mach_o_without_x86_64(self):
        app(self.root, 'Mixed', fat(X86_64, ARM64), [
            ('Contents/Frameworks/Good.framework/Versions/A/Good', fat(X86_64, ARM64)),
            ('Contents/Frameworks/Good.framework/Good', 'Versions/A/Good'),          # symlink: not counted twice
            ('Contents/Frameworks/Bad.framework/Versions/A/Bad', thin(ARM64)),
            ('Contents/PlugIns/P.appex/Contents/MacOS/P', fat(ARM64, I386)),
            ('Contents/Resources/A.class', bytes.fromhex('cafebabe00000041') + bytes(40)),   # Java, not Mach-O
            ('Contents/Resources/data.bin', b'\0' * 100)])
        lines = self.guard()
        bundle = self.root / 'Mixed.app'
        self.assertEqual(lines[0], f'app={bundle} version=1.2(345) main=universal main_archs=x86_64,arm64 macho=4 '
                                   'missing_x86_64=2')
        self.assertEqual(sorted(lines[1:]), [
            f'app={bundle} missing_x86_64=Contents/Frameworks/Bad.framework/Versions/A/Bad archs=arm64',
            f'app={bundle} missing_x86_64=Contents/PlugIns/P.appex/Contents/MacOS/P archs=arm64,other'])
        logged = self.log.read_text().splitlines()
        self.assertEqual([l.split(' ', 1)[1] for l in logged], lines)
        self.assertRegex(logged[0], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ app=')

    def test_complete_universal_app_has_a_summary_only(self):
        app(self.root, 'Good', fat(ARM64, X86_64), [('Contents/Helpers/h', thin(X86_64))])
        self.assertEqual(self.guard(), [f'app={self.root}/Good.app version=1.2(345) main=universal '
                                        'main_archs=arm64,x86_64 macho=2 missing_x86_64=0'])

    def test_thin_main_executables_are_summarised_not_itemised(self):
        app(self.root, 'Arm', thin(ARM64), [('Contents/Frameworks/F', thin(ARM64))])
        app(self.root, 'Intel', thin(X86_64), executable='Run Me', version='2.0 beta')
        self.assertEqual(sorted(self.guard()), [
            f'app={self.root}/Arm.app version=1.2(345) main=arm64 main_archs=arm64 macho=2 missing_x86_64=0',
            f'app={self.root}/Intel.app version=2.0_beta(345) main=x86_64 main_archs=x86_64 macho=1 '
            'missing_x86_64=0'])

    def test_only_app_store_apps_unless_all(self):
        app(self.root, 'Direct', fat(X86_64, ARM64), [('Contents/F', thin(ARM64))], receipt=False)
        self.assertEqual(self.guard(), [])
        self.assertEqual(len(self.guard('--all')), 2)

    def test_an_unchanged_app_is_scanned_once_and_an_update_again(self):
        bundle = app(self.root, 'Upd', fat(X86_64, ARM64), [('Contents/F', thin(ARM64))])
        self.assertEqual(len(self.guard()), 2)
        self.assertEqual(self.guard(), [])
        receipt = bundle / 'Contents/_MASReceipt/receipt'
        receipt.write_bytes(b'updated receipt')
        self.assertEqual(len(self.guard()), 2)
        (bundle / 'Contents/MacOS/Upd').write_bytes(fat(X86_64, ARM64) + b'longer')      # main executable replaced
        self.assertEqual(len(self.guard()), 2)
        self.assertIn(str(bundle), self.state.read_text())

    def test_a_bundle_being_written_waits_for_a_later_run(self):
        app(self.root, 'Busy', fat(X86_64, ARM64), [('Contents/F', thin(ARM64))])
        out = subprocess.run([str(self.tool), '--state', str(self.state), '--log', str(self.log), '--settle', '3600',
                              '--wait', '0', str(self.root)], check=True, capture_output=True, text=True)
        self.assertEqual(out.stdout.splitlines(), ['pending=1 (still being written; next run)'])
        self.assertFalse(self.state.exists())
        old = time.time() - 7200
        for dirpath, dirnames, filenames in os.walk(self.root):
            for n in dirnames + filenames:
                os.utime(os.path.join(dirpath, n), (old, old), follow_symlinks=False)
        self.assertEqual(len(self.guard('--settle', '60')), 2)

    def test_the_artifact_is_x86_64(self):
        with tempfile.TemporaryDirectory() as d:
            out, cmd = load_make().build(d)
            self.assertEqual(out.read_bytes()[:8], struct.pack('<II', 0xfeedfacf, X86_64))
            self.assertIn('x86_64', cmd)
            self.assertNotIn(d, ' '.join(cmd))


if __name__ == '__main__':
    unittest.main()
