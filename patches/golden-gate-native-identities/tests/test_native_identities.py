"""native-identities generator and installer, on fixtures made from this Mac's Apple-signed /usr/bin/true and
/usr/bin/false (their x86_64 slices). Nothing is installed outside a temporary folder; only codesign and lipo run."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import struct
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE/'src'))
import native_identities as ni

TRUE, FALSE = Path('/usr/bin/true'), Path('/usr/bin/false')

def true_cdhash():
    try:
        return ni.x86_64(TRUE)[0]['cdhash']
    except (TypeError, KeyError):
        return '0' * 40


def entry(path, **changes):
    """The fixture's programs.txt entry for PATH (a re-signed /usr/bin/true): its own digests, TRUE's cdhash."""
    data = ni.x86_64(path)[1]
    profile = ni.signature_profile(data)
    one = dict(refs=true_cdhash(), digests=ni.content_digest(data), ents=profile['entitlements'],
               policies=profile['policy'])
    libs = changes.pop('libs', frozenset())
    one.update(changes)
    build = (one['refs'], one['digests'], one['ents'], one['policies'])
    return dict(recipe='test', libs=libs, builds=frozenset({build}),
                **{k: frozenset({v}) for k, v in one.items()})


def cdhash(path):
    out = subprocess.run(['codesign', '-dvvv', '-a', 'x86_64', str(path)], capture_output=True, text=True).stderr
    return next(l.split('=', 1)[1] for l in out.splitlines() if l.startswith('CDHash='))


ENTITLEMENTS = Path(tempfile.gettempdir())/'native-identities-test.entitlements'


def resign(path, identifier):
    """Ad hoc, with entitlements: the XML and DER blobs signature_profile requires, as codesign writes them."""
    ENTITLEMENTS.write_text('<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>'
                            '<key>com.example.flag</key><true/><key>com.example.count</key><integer>3</integer>'
                            '</dict></plist>')
    subprocess.run(['codesign', '-f', '-s', '-', '-i', identifier, '--entitlements', str(ENTITLEMENTS), str(path)],
                   check=True, capture_output=True)


class NativeIdentitiesTests(unittest.TestCase):
    def setUp(self):
        for program in (TRUE, FALSE):
            if 'x86_64' not in subprocess.run(['lipo', '-archs', str(program)], capture_output=True, text=True).stdout:
                self.skipTest(f'needs an Apple-signed x86_64 slice in {program}')
        self.tmp = tempfile.TemporaryDirectory(prefix='native-identities-')
        root = Path(self.tmp.name).resolve()
        self.system, self.source = root/'system', root/'source'
        for folder in (self.system/'usr/bin', self.system/'usr/lib/m27', self.source/'usr/bin'):
            folder.mkdir(parents=True)
        # re-signed ad hoc from the Apple original at the same path: a row
        shutil.copy(TRUE, self.source/'usr/bin/kept')
        shutil.copy(TRUE, self.system/'usr/bin/kept')
        resign(self.system/'usr/bin/kept', 'com.apple.true')
        # the identifier changed: not the same program
        shutil.copy(TRUE, self.source/'usr/bin/renamed')
        shutil.copy(TRUE, self.system/'usr/bin/renamed')
        resign(self.system/'usr/bin/renamed', 'com.example.renamed')
        # another program under the original's identifier at its path
        shutil.copy(TRUE, self.source/'usr/bin/other')
        (root/'other.c').write_text('static const char pad[16384] = {1};\n'
                                    'int main(int c, char **v) { return pad[c] + (v != 0); }\n')
        subprocess.run(['clang', '-arch', 'x86_64', '-O1', str(root/'other.c'), '-o', str(self.system/'usr/bin/other')],
                       check=True, capture_output=True)
        resign(self.system/'usr/bin/other', 'com.apple.true')
        # no original anywhere
        shutil.copy(FALSE, self.system/'usr/bin/orphan')
        resign(self.system/'usr/bin/orphan', 'com.apple.false')
        # still Apple-signed: not a candidate
        shutil.copy(TRUE, self.system/'usr/bin/apple')
        # listed by m27 as a stub
        shutil.copy(TRUE, self.source/'usr/bin/stub')
        shutil.copy(TRUE, self.system/'usr/bin/stub')
        resign(self.system/'usr/bin/stub', 'com.apple.stub')
        (self.system/ni.STUBS).write_text(f'# test\n{cdhash(self.system/"usr/bin/stub")}  {"0" * 40}  arm64e  x\n')
        self.kept = {'usr/bin/kept': entry(self.system/'usr/bin/kept')}

    def tearDown(self):
        self.tmp.cleanup()

    def test_cdhash_matches_codesign(self):
        for path in (TRUE, self.system/'usr/bin/kept'):
            sig, _ = ni.x86_64(path)
            self.assertEqual(sig['cdhash'], cdhash(path), path)
        self.assertFalse(ni.x86_64(TRUE)[0]['adhoc'])
        self.assertTrue(ni.x86_64(self.system/'usr/bin/kept')[0]['adhoc'])

    def test_scan(self):
        rows, skipped, refused = ni.scan(self.system, [self.source], self.kept)
        self.assertEqual(refused, {})
        self.assertEqual([(r['path'], r['observed'], r['reference']) for r in rows],
                         [('usr/bin/kept', cdhash(self.system/'usr/bin/kept'), cdhash(TRUE))])
        self.assertEqual(sorted(skipped), ['usr/bin/orphan', 'usr/bin/other', 'usr/bin/renamed', 'usr/bin/stub'])
        self.assertIn('identifier', skipped['usr/bin/renamed'])
        self.assertIn('another program', skipped['usr/bin/other'])
        self.assertIn('stub-identities', skipped['usr/bin/stub'])
        self.assertIn('no original', skipped['usr/bin/orphan'])

    def test_first_source_with_the_original_wins(self):
        empty = Path(self.tmp.name)/'empty'
        empty.mkdir()
        rows, _, _ = ni.scan(self.system, [empty, self.source], self.kept)
        self.assertEqual([r['source'] for r in rows], [str(self.source/'usr/bin/kept')])

    def test_install(self):
        owner = (os.getuid(), os.getgid())
        record = ni.install(self.system, [self.source], owner=owner, programs=self.kept)
        copy = self.system/ni.ORIGINALS/'usr/bin/kept'
        self.assertEqual(copy.read_bytes(), TRUE.read_bytes())
        self.assertEqual(copy.stat().st_mode & 0o7777, 0o644)
        self.assertEqual((self.system/ni.ORIGINALS).stat().st_mode & 0o7777, 0o755)
        listed = (self.system/ni.LIST).read_text().splitlines()
        rows = [line for line in listed if not line.startswith('#')]
        self.assertEqual(rows, [f'{cdhash(self.system/"usr/bin/kept")}  {cdhash(TRUE)}  x86_64  '
                                f'usr/lib/m27/originals/usr/bin/kept'])
        self.assertEqual((self.system/ni.LIST).stat().st_mode & 0o7777, 0o644)
        self.assertEqual(len(record['rows']), 1)
        # an installed original is not itself scanned, so a second run gives the same list
        before = (self.system/ni.LIST).read_bytes()
        ni.install(self.system, [self.source], owner=owner, programs=self.kept)
        self.assertEqual((self.system/ni.LIST).read_bytes(), before)

    def test_install_never_writes_through_a_link(self):
        elsewhere = Path(self.tmp.name)/'elsewhere'
        elsewhere.mkdir()
        (self.system/'usr/lib/m27/originals').symlink_to(elsewhere)
        with self.assertRaises(SystemExit) as stop:
            ni.install(self.system, [self.source], owner=(os.getuid(), os.getgid()), programs=self.kept)
        self.assertIn('not a directory', str(stop.exception))
        self.assertEqual(list(elsewhere.iterdir()), [])
        (self.system/'usr/lib/m27/originals').unlink()
        (self.system/ni.LIST).symlink_to(elsewhere/'list')
        with self.assertRaises(SystemExit):
            ni.install(self.system, [self.source], owner=(os.getuid(), os.getgid()), programs=self.kept)
        self.assertFalse((elsewhere/'list').exists())
        (self.system/ni.LIST).unlink()
        keep = elsewhere/'keep'
        keep.write_text('unchanged')
        os.link(keep, self.system/ni.LIST)                                          # a hard link: refused, not emptied
        with self.assertRaises(SystemExit):
            ni.install(self.system, [self.source], owner=(os.getuid(), os.getgid()), programs=self.kept)
        self.assertEqual(keep.read_text(), 'unchanged')

    def test_unlisted_program_is_refused(self):
        rows, _, refused = ni.scan(self.system, [self.source], {})
        self.assertEqual(rows, [])
        self.assertEqual(list(refused), ['usr/bin/kept'])
        self.assertIn('not in programs.txt', refused['usr/bin/kept'])
        with self.assertRaises(SystemExit) as stop:
            ni.install(self.system, [self.source], owner=(os.getuid(), os.getgid()), programs={})
        self.assertIn('usr/bin/kept', str(stop.exception))
        self.assertFalse((self.system/ni.LIST).exists())

    def test_libraries_must_be_the_listed_ones(self):
        wanted = {'usr/bin/kept': entry(self.system/'usr/bin/kept', libs=frozenset({'/usr/lib/libextra.dylib'}))}
        (self.system/'usr/lib').mkdir(parents=True, exist_ok=True)
        (self.system/'usr/lib/libextra.dylib').write_bytes(b'')   # its recipe ran: the line applies
        rows, _, refused = ni.scan(self.system, [self.source], wanted)
        self.assertEqual(rows, [])
        self.assertIn("adds none", refused['usr/bin/kept'])
        self.assertIn('/usr/lib/libextra.dylib', refused['usr/bin/kept'])

    def test_a_line_whose_libraries_are_not_installed_does_not_apply(self):
        # its recipe is not in this set: the line is skipped, and a re-signed program it would have covered is still refused
        wanted = {'usr/bin/kept': entry(self.system/'usr/bin/kept', libs=frozenset({'/usr/lib/libabsent.dylib'}))}
        rows, skipped, refused = ni.scan(self.system, [self.source], wanted)
        self.assertEqual(rows, [])
        self.assertIn('not in this set', skipped['usr/bin/kept'])
        self.assertIn('not in programs.txt', refused['usr/bin/kept'])

    def test_original_must_have_a_pinned_cdhash(self):
        for change, words in ((dict(refs='1' * 40), 'has cdhash'),
                              (dict(digests='2' * 64), 'is not a line programs.txt pins'),
                              (dict(ents='3' * 64), 'is not a line programs.txt pins'),
                              (dict(policies='4' * 64), 'is not a line programs.txt pins')):
            rows, _, refused = ni.scan(self.system, [self.source], {'usr/bin/kept': entry(self.system/'usr/bin/kept', **change)})
            self.assertEqual(rows, [], change)
            self.assertIn(words, refused['usr/bin/kept'])

    def test_content_digest_ignores_only_the_signature(self):
        data = ni.x86_64(self.system/'usr/bin/kept')[1]
        self.assertEqual(ni.content_digest(data), ni.content_digest(ni.x86_64(TRUE)[1]))   # re-signing alone
        changed = bytearray(data)
        at = 32 + struct.unpack_from('<I', data, 20)[0] + 64        # a byte after the load commands
        changed[at] ^= 0xff
        self.assertNotEqual(ni.content_digest(bytes(changed)), ni.content_digest(data))

    def test_listed_program_that_is_not_enrolled_is_refused(self):
        listed = dict(self.kept)
        listed['usr/bin/orphan'] = entry(self.system/'usr/bin/orphan')     # re-signed, no original anywhere
        _, _, refused = ni.scan(self.system, [self.source], listed)
        self.assertIn('listed but not enrolled', refused['usr/bin/orphan'])

    def test_der_and_xml_entitlements_must_agree(self):
        good = bytes([0x30, 0x0d, 0x02, 0x01, 0x01, 0xb0, 0x08, 0x30, 0x06, 0x0c, 0x01, 0x6b, 0x01, 0x01, 0xff])
        self.assertEqual(ni._der(good, 0, len(good))[0], ('a', [('i', 1), ('d', {'k': ('b', True)})]))
        self.assertNotEqual(ni._typed({'k': True}), ni._typed({'k': 1}))           # a boolean is not an integer
        for bad in (bytes([0x30, 0x05, 0x0c, 0x09]),                                  # runs past its container
                    bytes([0x01, 0x01, 0x01]),                                        # BOOLEAN 01
                    bytes([0x02, 0x02, 0x00, 0x05]),                                  # INTEGER not minimal
                    bytes([0x0c, 0x81, 0x01, 0x61]),                                  # length not minimal
                    bytes([0xb0, 0x0a, 0x30, 0x03, 0x0c, 0x01, 0x6b, 0x30, 0x03, 0x0c, 0x01, 0x6b]),  # pair not a pair
                    bytes([0x30, 0x80] * 40)):                                        # too deep / bad length
            with self.assertRaises(ValueError, msg=bad.hex()):
                ni._der(bad, 0, len(bad))

    def test_signature_profile_is_strict(self):
        data = bytearray(ni.x86_64(self.system/'usr/bin/kept')[1])
        profile = ni.signature_profile(bytes(data))
        self.assertEqual(profile['cdhash'], cdhash(self.system/'usr/bin/kept'))
        sig = next(at for c, at, l in ni._commands(data) if c == 0x1d)
        dataoff = struct.unpack_from('<I', data, sig + 8)[0]
        moved = bytearray(data)
        struct.pack_into('>I', moved, dataoff + 12, 2)                              # the CodeDirectory's slot -> 2
        with self.assertRaises(ValueError):
            ni.signature_profile(bytes(moved))
        cd_at = dataoff + struct.unpack_from('>I', data, dataoff + 16)[0]           # the CodeDirectory (first blob)
        hash_offset = struct.unpack_from('>I', data, cd_at + 16)[0]
        unbound = bytearray(data)
        unbound[cd_at + hash_offset - 32 * 5:cd_at + hash_offset - 32 * 4] = bytes(32)   # slot -5 (XML) emptied
        with self.assertRaisesRegex(ValueError, 'slot -5'):
            ni.signature_profile(bytes(unbound))
        longer = bytes(data) + b'\0' * 16                                           # bytes past the signature
        with self.assertRaises(ValueError):
            ni.signature_profile(longer)

    def test_dylibs(self):
        sig, data = ni.x86_64(TRUE)
        self.assertIn((0xc, '/usr/lib/libSystem.B.dylib'), ni.dylibs(data))

    def test_programs_file(self):
        programs = ni.load_programs()
        self.assertEqual(len(programs), 5)
        for path, want in programs.items():
            recipe, libs = want['recipe'], want['libs']
            self.assertTrue(want['refs'] and want['digests'] and want['ents'], path)
            self.assertTrue(path.startswith('System/') and recipe.startswith('golden-gate-'), path)
            self.assertTrue(all(lib.startswith('/') for lib in libs))
        bad = Path(self.tmp.name)/'bad.txt'
        spaced = Path(self.tmp.name)/'spaced.txt'
        line = 'System/A B.app/Contents/MacOS/A B  some-recipe  +/usr/lib/x.dylib  =%s  ~%s  %%%s  !%s\n'
        spaced.write_text(line % ('a' * 40, 'b' * 64, 'c' * 64, '4' * 64) +
                          line % ('d' * 40, 'e' * 64, 'f' * 64, '4' * 64))
        got = ni.load_programs(spaced)['System/A B.app/Contents/MacOS/A B']
        self.assertEqual((got['recipe'], got['libs'], len(got['builds'])), ('some-recipe', frozenset({'/usr/lib/x.dylib'}), 2))
        self.assertIn(('a' * 40, 'b' * 64, 'c' * 64, '4' * 64), got['builds'])
        self.assertNotIn(('a' * 40, 'e' * 64, 'c' * 64, '4' * 64), got['builds'])
        spaced.write_text('usr/bin/x  r  +/usr/lib/x.dylib\n')   # no pinned original
        with self.assertRaises(ValueError):
            ni.load_programs(spaced)
        bad.write_text('usr/bin/x  r  /no-plus\n')
        with self.assertRaises(ValueError):
            ni.load_programs(bad)

    def test_original_that_fails_anchor_apple_is_dropped(self):
        # a source whose slice carries a signature blob that is not Apple's: re-signed with a CMS-less but
        # non-adhoc-looking signature is not constructible here, so check the verifier directly.
        self.assertTrue(ni.verify(TRUE)[0])
        self.assertFalse(ni.verify(self.system/'usr/bin/kept')[0])


def whole_file(path):
    """x86_64_signature's result computed the way r1-r3 did, from the whole file's bytes."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if data[:4] not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        return None
    for cpu, offset, size in ni.slices(data):
        if cpu == ni.CPU_X86_64:
            return ni.signature(data, offset, size), offset, size
    return None


def whole_scan(system, sources, programs=None):
    """scan() reading every candidate whole, as before the header-only read."""
    with mock.patch.object(ni, 'x86_64_signature', whole_file), \
            mock.patch.object(ni, 'slice_bytes', lambda path, found: Path(path).read_bytes()[found[1]:found[1] + found[2]]):
        return ni.scan(system, sources, programs)


class HeaderOnlyRead(unittest.TestCase):
    """The scan reads each executable's headers and code signature only; its results are those of reading the whole
    file. With NATIVE_IDENTITIES_SYSTEM=ROOT and NATIVE_IDENTITIES_SOURCES=ROOT:ROOT (a built image's System mounted
    read-only and the sources its record names: the pristine 27.x System, the native donors' payload/system), the
    whole scan of a real System gives the same rows and skips both ways."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='native-identities-read-')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_signature_as_the_whole_file(self):
        thin = self.root/'thin'
        subprocess.run(['lipo', str(TRUE), '-thin', 'x86_64', '-output', str(thin)], check=True)
        adhoc = self.root/'adhoc'
        shutil.copy(TRUE, adhoc)
        resign(adhoc, 'com.example.adhoc')
        files = [TRUE, FALSE, thin, adhoc]
        data = thin.read_bytes()
        for n, cut in enumerate((16, 31, 32, 200, len(data) // 2, len(data) - 1)):
            (self.root/f'cut{n}').write_bytes(data[:cut])          # truncated: header, load commands, signature
            files.append(self.root/f'cut{n}')
        (self.root/'text').write_bytes(b'#!/bin/sh\n' * 1000)
        files.append(self.root/'text')
        for path in files:
            with self.subTest(path=path.name):
                try:
                    want = whole_file(path)
                except Exception:                   # malformed: the header-only read says "not readable" (None)
                    self.assertIsNone(ni.x86_64_signature(path))
                    continue
                self.assertEqual(ni.x86_64_signature(path), want)
                if want:
                    self.assertEqual(ni.x86_64(path)[1], path.read_bytes()[want[1]:want[1] + want[2]])

    def test_reads_headers_and_signature_only(self):
        big = self.root/'big'
        (self.root/'big.c').write_text('static const char pad[4 << 20] = {1};\n'
                                       'int main(int c, char **v) { return pad[c] + (v != 0); }\n')
        subprocess.run(['clang', '-arch', 'x86_64', '-O1', str(self.root/'big.c'), '-o', str(big)],
                       check=True, capture_output=True)
        resign(big, 'com.example.big')
        data = big.read_bytes()
        self.assertGreater(len(data), 4 << 20)
        sig = ni.x86_64_signature(big)[0]
        read = []
        real = open

        def counting(path, mode='r', *args, **kwargs):
            stream = real(path, mode, *args, **kwargs)
            original = stream.read
            stream.read = lambda n=-1: read.append(len(chunk := original(n))) or chunk
            return stream
        with mock.patch('builtins.open', counting):
            self.assertEqual(ni.x86_64_signature(big)[0], sig)
        # 4 KiB of headers, the signature blob (32 bytes of hashes per 4 KiB page, so under 1% of the file)
        self.assertLess(sum(read), 4096 + 4096 + len(data) // 100)

    def test_scan_matches_the_whole_file_scan(self):
        system, source = self.root/'system', self.root/'source'
        for folder in (system/'usr/bin', source/'usr/bin'):
            folder.mkdir(parents=True)
        shutil.copy(TRUE, source/'usr/bin/kept')
        shutil.copy(TRUE, system/'usr/bin/kept')
        resign(system/'usr/bin/kept', 'com.apple.true')
        shutil.copy(FALSE, system/'usr/bin/orphan')
        resign(system/'usr/bin/orphan', 'com.apple.false')
        (system/'usr/bin/link').symlink_to('kept')
        kept = {'usr/bin/kept': entry(system/'usr/bin/kept')}
        self.assertEqual(ni.scan(system, [source], kept), whole_scan(system, [source], kept))
        self.assertEqual(len(ni.scan(system, [source], kept)[0]), 1)

    @unittest.skipUnless(os.environ.get('NATIVE_IDENTITIES_SYSTEM') and os.environ.get('NATIVE_IDENTITIES_SOURCES'),
                         'set NATIVE_IDENTITIES_SYSTEM and NATIVE_IDENTITIES_SOURCES (a built image, read-only)')
    def test_real_system(self):
        system = Path(os.environ['NATIVE_IDENTITIES_SYSTEM'])
        sources = [Path(p) for p in os.environ['NATIVE_IDENTITIES_SOURCES'].split(':')]
        programs = ni.load_programs()
        rows, skipped, refused = ni.scan(system, sources, programs)
        self.assertEqual((rows, skipped, refused), whole_scan(system, sources, programs))
        self.assertEqual(refused, {})
        self.assertTrue(rows)


if __name__ == '__main__':
    unittest.main()
