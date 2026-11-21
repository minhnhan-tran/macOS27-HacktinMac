"""pipeline/stages/inputs.sh with fake hdiutil and pkgutil: the KDK, OCLP source and CPython archives are checked
against their rows and extracted afresh each run (nothing left in a cache is used), and an OCLP archive is accepted
only when it is the oclp_source row's bytes, whatever commit it names. The run stops at python_site (no row here)."""
import hashlib
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
COMMIT = 'abcdef0123456789abcdef0123456789abcdef01'

FAKE_HDIUTIL = '''#!/bin/sh
echo "hdiutil $*" >> "$FAKE_LOG"
if [ "$1" = attach ]; then
  while [ $# -gt 1 ]; do [ "$1" = -mountpoint ] && m=$2; shift; done
  echo "$1" > "$m/KernelDebugKit.pkg"
fi
'''
FAKE_PKGUTIL = '''#!/bin/sh
echo "pkgutil $*" >> "$FAKE_LOG"
p=$3/KDK.pkg/Payload/System/Library
mkdir -p "$p/Extensions/Real.kext" "$p/CoreServices"
echo real > "$p/Extensions/Real.kext/Info.plist"; echo version > "$p/CoreServices/SystemVersion.plist"
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tar_bytes(files, comment=None, gz=False):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode='w:gz' if gz else 'w', format=tarfile.PAX_FORMAT,
                      pax_headers={'comment': comment} if comment else None) as t:
        for name, (data, mode) in files.items():
            i = tarfile.TarInfo(name); i.size, i.mode = len(data), mode
            t.addfile(i, io.BytesIO(data))
    return b.getvalue()


def macho():
    return struct.pack('<IiiIIIII', 0xfeedfacf, 0x01000007, 3, 2, 0, 0, 0, 0)


class InputsStage(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.d = Path(self.t.name)
        self.files = self.d / 'files'; self.files.mkdir()
        self.lock, self.common = self.d / 'lock', self.d / 'common'
        self.lock.mkdir(); self.common.mkdir()
        self.row(self.lock, 'kdk', 'Kernel_Debug_Kit_1.0_build_1A1.dmg', b'kdk image')
        self.row(self.common, 'fl', 'fl', b'fl tool')
        constants = b'patcher_support_pkg_version: str = "9.9.9"\n'
        self.oclp = tar_bytes({'opencore_legacy_patcher/constants.py': (constants, 0o644)}, comment=COMMIT)
        self.row(self.common, 'oclp_source', 'oclp-source.tar', self.oclp)
        self.row(self.common, 'psp_9.9.9', 'Universal-Binaries.dmg', b'psp')
        for arch, body in (('arm64', b'#!/bin/sh\necho archive arm64\n'), ('x86_64', macho())):
            self.row(self.common, 'python_' + arch, 'cpython-%s.tar.gz' % arch,
                     tar_bytes({'python/bin/python3.12': (body, 0o755)}, gz=True))
        self.fakes = self.d / 'bin'; self.fakes.mkdir()
        for name, text in (('hdiutil', FAKE_HDIUTIL), ('pkgutil', FAKE_PKGUTIL)):
            (self.fakes / name).write_text(text); (self.fakes / name).chmod(0o755)
        (self.fakes / 'python3').symlink_to(sys.executable)
        self.log = self.d / 'calls.log'

    def tearDown(self):
        self.t.cleanup()

    def row(self, lock, name, file, data):
        p = self.files / file; p.write_bytes(data)
        (lock / name).write_text('%s %s\n' % (sha(data), p))
        return p

    def run_stage(self, share='share', **env):
        e = dict(os.environ, PATH='%s:%s' % (self.fakes, os.environ['PATH']), FAKE_LOG=str(self.log),
                 OCLP_REPO=str(self.d / 'no-repo'), **env)
        share = self.d / share
        r = subprocess.run(['zsh', str(TOP / 'pipeline/stages/inputs.sh'), share, self.d / 'target.json', self.lock,
                            self.common, COMMIT[:9], self.d / 'set.txt', 'golden-first'],
                           capture_output=True, text=True, env=e)
        return r, share

    def test_archives_are_checked_and_extracted_fresh(self):
        r, share = self.run_stage()
        self.assertIn('has no row python_site', r.stderr)       # the planned stop, after the parts under test
        self.assertEqual((share / 'py/bin/python3.12').read_bytes(), b'#!/bin/sh\necho archive arm64\n')
        self.assertEqual((share / 'py-x86_64/bin/python3.12').read_bytes(), macho())
        kdk = share / 'kdk/KDK_1.0_1A1.kdk/System/Library'
        self.assertEqual(sorted(p.name for p in (kdk / 'Extensions').iterdir()), ['Real.kext'])
        self.assertEqual((share / 'oclp/Universal-Binaries.dmg').read_bytes(), b'psp')
        log = self.log.read_text()
        self.assertIn('pkgutil --expand-full', log)
        attached = log.split('hdiutil attach')[1].split()[-1]
        self.assertNotEqual(Path(attached).resolve(), (self.files / 'Kernel_Debug_Kit_1.0_build_1A1.dmg').resolve(),
                            'the KDK is mounted from its checked copy')
        self.assertEqual([p.name for p in self.d.iterdir() if p.name.startswith('.inputs.')], [],
                         'the checked copies are removed')
        # a second run extracts again: nothing kept between runs is read
        self.log.unlink()
        r, share2 = self.run_stage('share2')
        self.assertIn('pkgutil --expand-full', self.log.read_text())
        self.assertEqual((share2 / 'py/bin/python3.12').read_bytes(), b'#!/bin/sh\necho archive arm64\n')

    def test_a_changed_archive_stops_before_it_is_used(self):
        for row, file in (('kdk', 'Kernel_Debug_Kit_1.0_build_1A1.dmg'), ('python_arm64', 'cpython-arm64.tar.gz')):
            with self.subTest(row=row):
                p = self.files / file; good = p.read_bytes()
                p.write_bytes(good + b'x')
                try:
                    r, share = self.run_stage('share-' + row)
                finally:
                    p.write_bytes(good)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn('is not %s (%s)' % (sha(good), row), r.stderr)
                self.assertFalse((share / 'py/bin/python3.12').exists())

    def test_oclp_archive_must_be_the_rows_bytes(self):
        other = tar_bytes({'opencore_legacy_patcher/constants.py': (b'patcher_support_pkg_version: str = "9.9.9"\n'
                                                                      b'evil = 1\n', 0o644)}, comment=COMMIT)
        tar = self.d / 'override' / (COMMIT[:9] + '.tar'); tar.parent.mkdir(); tar.write_bytes(other)
        tar.with_suffix('.commit').write_text(COMMIT + '\n')
        r, share = self.run_stage(OCLP_TAR=str(tar))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('is not %s (oclp_source)' % sha(self.oclp), r.stderr)
        self.assertEqual(list((share / 'oclp').iterdir()), [], 'nothing of it is extracted')
        self.assertFalse(self.log.exists() and 'pkgutil' in self.log.read_text())
        # the row's own bytes under the override name are accepted
        tar.write_bytes(self.oclp)
        r, share = self.run_stage('share-ok', OCLP_TAR=str(tar))
        self.assertIn('has no row python_site', r.stderr)
        self.assertTrue((share / 'oclp/opencore_legacy_patcher/constants.py').is_file())

    def site_row(self, extra_line=''):
        site = self.d / 'store-site'
        (site / 'requests').mkdir(parents=True, exist_ok=True)
        (site / 'requests' / '__init__.py').write_text('ok = 1\n')
        (site / 'requests' / '__init__.py').chmod(0o644)
        (site / 'link.py').unlink(missing_ok=True); (site / 'link.py').symlink_to('requests/__init__.py')
        (site / 'extra.py').write_text('evil = 1\n')       # not in the row's hash (# hash: without extra.py)
        sys.path.insert(0, str(TOP / 'pipeline'))
        import lock
        want = lock.row_sha256(str(site), without=('extra.py',))
        (self.common / 'python_site').write_text('%s%s %s\n' % (extra_line, want, site))
        return site

    def test_python_site_is_a_copy_checked_as_a_tree(self):
        site = self.site_row('# hash: without extra.py\n')
        r, share = self.run_stage()
        self.assertNotIn('python_site', r.stderr)
        self.assertEqual((share / 'site/requests/__init__.py').read_text(), 'ok = 1\n')
        self.assertEqual(os.readlink(share / 'site/link.py'), 'requests/__init__.py')
        self.assertFalse((share / 'site/extra.py').exists(), 'bytes outside the checked tree never reach the share')
        (site / 'requests' / '__init__.py').write_text('ok = 2\n')      # changed after the run: the copy is not
        self.assertEqual((share / 'site/requests/__init__.py').read_text(), 'ok = 1\n')

    def test_a_changed_python_site_stops_and_leaves_nothing(self):
        site = self.site_row('# hash: without extra.py\n')
        (site / 'requests' / '__init__.py').write_text('evil = 1\n')
        r, share = self.run_stage()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('(python_site)', r.stderr)
        self.assertFalse((share / 'site').exists())

    def test_tools_and_psp_are_the_checked_bytes_even_when_swapped_after_a_check(self):
        """A cp on PATH that swaps fl and the PatcherSupportPkg image before copying them: what was checked by path
        is not what was copied. The share must hold the rows' bytes (copied, then checked as copied)."""
        swap = self.fakes / 'cp'
        swap.write_text('#!/bin/sh\nfor a; do case "$a" in */fl|*/Universal-Binaries.dmg) [ -f "$a" ] && '
                        'printf evil > "$a";; esac; done\nexec /bin/cp "$@"\n')
        swap.chmod(0o755)
        r, share = self.run_stage()
        self.assertIn('has no row python_site', r.stderr)
        self.assertEqual((share / 'pristine/fl').read_bytes(), b'fl tool')
        self.assertEqual((share / 'pristine/fl').stat().st_mode & 0o777, 0o755)
        self.assertEqual((share / 'oclp/Universal-Binaries.dmg').read_bytes(), b'psp')

    def test_a_changed_tool_stops_and_is_not_staged(self):
        (self.files / 'fl').write_bytes(b'evil')
        r, share = self.run_stage()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('(fl)', r.stderr)
        self.assertFalse((share / 'pristine/fl').exists())

    def patch_rows(self):
        """A set of two real hook recipes whose rows are fake folders here; the Golden Gate build's lock, donor lock
        and common as golden-first reads them. The run goes on past the patch inputs (the target needs a profile)."""
        import shutil
        site = self.site_row('# hash: without extra.py\n')
        golden = self.d / 'golden' / '26B131'; golden.mkdir(parents=True)
        donor = self.d / '25G342'; donor.mkdir()
        for f in self.lock.iterdir():
            shutil.copy(f, donor / f.name)
        sys.path.insert(0, str(TOP / 'pipeline'))
        import lock
        rows = {}
        for name in ('hd4000_planes', 'ids_service'):
            t = self.d / 'rows' / name; t.mkdir(parents=True)
            (t / 'payload.bin').write_bytes(name.encode())
            (golden / name).write_text('%s %s\n' % (lock.row_sha256(str(t)), t))
            rows[name] = t
        (self.d / 'set.txt').write_text('golden-gate-hd4000-planes\ngolden-gate-ids\n')
        return golden, donor, rows

    def run_golden(self, golden, donor, share='share', **env):
        e = dict(os.environ, PATH='%s:%s' % (self.fakes, os.environ['PATH']), FAKE_LOG=str(self.log),
                 OCLP_REPO=str(self.d / 'no-repo'), GOLDEN_LOCK=str(golden), **env)
        share = self.d / share
        r = subprocess.run(['zsh', str(TOP / 'pipeline/stages/inputs.sh'), share, TOP / 'pipeline/targets/mbp92.json',
                            donor, self.common, COMMIT[:9], self.d / 'set.txt', 'golden-first'],
                           capture_output=True, text=True, env=e)
        return r, share

    def test_patch_inputs_are_checked_copies(self):
        golden, donor, rows = self.patch_rows()
        r, share = self.run_golden(golden, donor)
        got = sorted(share.glob('hooks/*/inputs/*/payload.bin'))
        self.assertEqual([p.read_bytes() for p in got], [b'hd4000_planes', b'ids_service'], r.stderr)

    def test_a_changed_patch_input_stops_and_is_not_staged(self):
        golden, donor, rows = self.patch_rows()
        (rows['ids_service'] / 'payload.bin').write_bytes(b'evil')
        r, share = self.run_golden(golden, donor)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('(ids_service)', r.stderr)
        self.assertEqual(list(share.glob('hooks/*/inputs/ids_service')), [])

    def test_a_patch_input_on_the_usb_drive_is_linked_to_a_checked_copy_there(self):
        import shutil
        golden, donor, rows = self.patch_rows()
        drive = self.d / 'drive' / '.m27-build'; drive.mkdir(parents=True)
        moved = drive / 'hd4000_planes'
        shutil.move(rows['hd4000_planes'], moved)
        sys.path.insert(0, str(TOP / 'pipeline'))
        import lock
        (golden / 'hd4000_planes').write_text('%s %s\n' % (lock.row_sha256(str(moved)), moved))
        r, share = self.run_golden(golden, donor, KIT_ON_DRIVE=os.path.realpath(drive))
        link = next(share.glob('hooks/*/inputs/hd4000_planes'))
        self.assertTrue(link.is_symlink(), r.stderr)
        target = Path(os.path.realpath(link))
        self.assertNotEqual(target, Path(os.path.realpath(moved)), 'a checked copy, not the row\'s own folder')
        self.assertTrue(target.is_relative_to(os.path.realpath(drive)), 'on the drive, where the USB installer adopts it')
        (moved / 'payload.bin').write_bytes(b'evil')
        self.assertEqual((link / 'payload.bin').read_bytes(), b'hd4000_planes')

    def legacy_rows(self):
        sys.path.insert(0, str(TOP / 'pipeline'))
        import lock
        store = self.files / 'store'
        unpack = store / 'unpack'; (unpack / 'zip/AssetData/payloadv2').mkdir(parents=True)
        (unpack / 'payload.yaa').write_bytes(b'yaa')
        for n in ('links.txt', 'firmlinks_payload'):
            (unpack / 'zip/AssetData/payloadv2' / n).write_bytes(n.encode())
        lists = store / 'lists'; lists.mkdir()
        for n in ('sys', 'data', 'data-symlinks'):
            (lists / (n + '.tsv')).write_text('Library/x\t../y\n')
        for name, t in (('unpack', unpack), ('lists', lists)):
            (self.lock / name).write_text('%s %s\n' % (lock.row_sha256(str(t)), t))
        for t in ('autool', 'hmeta', 'fl-noent'):
            self.row(self.common, t, t, t.encode() + b' tool')
        return store

    def run_legacy(self, share='share', **env):
        e = dict(os.environ, PATH='%s:%s' % (self.fakes, os.environ['PATH']), FAKE_LOG=str(self.log),
                 OCLP_REPO=str(self.d / 'no-repo'), **env)
        share = self.d / share
        r = subprocess.run(['zsh', str(TOP / 'pipeline/stages/inputs.sh'), share, self.d / 'target.json', self.lock,
                            self.common, COMMIT[:9], self.d / 'set.txt', 'legacy'], capture_output=True, text=True, env=e)
        return r, share

    def test_legacy_pristine_inputs_are_the_checked_bytes_even_when_swapped_after_a_check(self):
        store = self.legacy_rows()
        swap = self.fakes / 'cp'                  # swaps any store file it is asked to copy, then copies
        swap.write_text('#!/bin/sh\nfor a; do case "$a" in %s/*) [ -f "$a" ] && printf evil > "$a";; esac; done\n'
                        'exec /bin/cp "$@"\n' % store)
        swap.chmod(0o755)
        r, share = self.run_legacy()
        self.assertIn('has no row python_site', r.stderr)
        p = share / 'pristine'
        self.assertEqual([(p / n).read_bytes() for n in ('payload.yaa', 'links.txt', 'data-symlinks.tsv')],
                         [b'yaa', b'links.txt', b'Library/x\t../y\n'])
        self.assertEqual([(p / t).read_bytes() for t in ('autool', 'hmeta', 'fl-noent')],
                         [b'autool tool', b'hmeta tool', b'fl-noent tool'])
        self.assertTrue(all((p / t).stat().st_mode & 0o777 == 0o755 for t in ('autool', 'hmeta', 'fl', 'fl-noent')))

    def test_a_changed_list_stops_before_anything_of_it_is_staged(self):
        store = self.legacy_rows()
        (store / 'lists' / 'data-symlinks.tsv').write_text('../../etc\tx\n')
        r, share = self.run_legacy()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('(lists)', r.stderr)
        self.assertFalse((share / 'pristine/data-symlinks.tsv').exists())


if __name__ == '__main__':
    unittest.main()
