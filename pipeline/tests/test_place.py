"""pipeline/place: a made-on-Mac file that is a plain copy out of another row is copied once that row checks."""
from pathlib import Path
import os
import runpy
import tempfile
import types
import unittest

TOP = Path(__file__).resolve().parents[2]
P = runpy.run_path(str(TOP / 'pipeline' / 'place'))

COPY = {'path': 'iCBE-real.dylib', 'from_row': 'src', 'path_in_row': 'a/iCBE.dylib'}
MADE = {'path': 'stubs', 'from_row': 'src', 'producer': 'tools/m27-embed'}


class Place(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = Path(self.d.name)
        self.src, self.dest = self.root / 'src', self.root / 'dest'
        self.dest.mkdir()
        self.checked = []
        g = P['copy_made'].__globals__   # the functions' own globals (run_path returns a copy)
        self.saved = {k: g[k] for k in ('CLASSES', 'find_row', 'lock')}
        g['CLASSES'] = {'row': {'made_on_mac_files': [COPY, MADE]}}
        g['find_row'] = lambda folders, name: (self.root, 'x', self.src)
        g['lock'] = types.SimpleNamespace(path=lambda folder, name: self.checked.append(name))

    def tearDown(self):
        P['copy_made'].__globals__.update(self.saved)
        self.d.cleanup()

    def test_the_copy_waits_while_its_source_row_is_not_in_place(self):
        pending = P['copy_made']([], 'row', self.dest)
        self.assertEqual(pending, [COPY])
        self.assertFalse((self.dest / 'iCBE-real.dylib').exists())

    def test_the_copy_is_made_byte_for_byte_once_the_source_row_checks(self):
        (self.src / 'a').mkdir(parents=True)
        (self.src / 'a/iCBE.dylib').write_bytes(b'repaired')
        self.assertEqual(P['copy_made']([], 'row', self.dest), [])
        self.assertEqual((self.dest / 'iCBE-real.dylib').read_bytes(), b'repaired')
        self.assertEqual(self.checked, ['src'])
        self.assertFalse((self.dest / 'stubs').exists(), 'a producer file is never copied')

    def test_labels_name_the_producer_or_the_source_row(self):
        self.assertEqual(P['made_label'](MADE), 'tools/m27-embed')
        self.assertEqual(P['made_label'](COPY), 'a copy out of row src')


class Mode(unittest.TestCase):
    def test_a_mach_o_is_placed_executable_and_data_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            for name, data, mode in (('fat', b'\xca\xfe\xba\xbe' + bytes(8), 0o755), ('lib', b'\xcf\xfa\xed\xfe' + bytes(8), 0o755),
                                     ('dmg', b'koly' + bytes(8), 0o644)):
                src = Path(d) / ('src-' + name); src.write_bytes(data); src.chmod(0o644)
                dest = Path(d) / 'row' / name
                P['clone'](src, dest)
                self.assertEqual(dest.stat().st_mode & 0o777, mode, name)


class Unpack(unittest.TestCase):
    def test_an_archive_class_unpacks_its_payload_folder_as_the_row(self):
        import io, tarfile
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'rt.tar.gz'
            with tarfile.open(src, 'w:gz') as t:
                for name, data in (('top/GENERATE.md', b'g'), ('top/payload/bin/m27', b'h')):
                    info = tarfile.TarInfo(name); info.size = len(data); t.addfile(info, io.BytesIO(data))
            dest = Path(d) / 'row' / 'payload'
            P['unpack_archive'](src, dest, {'strip': 1, 'into': 'parent'})
            self.assertEqual((dest / 'bin/m27').read_bytes(), b'h')
            self.assertEqual((dest.parent / 'GENERATE.md').read_bytes(), b'g')
            self.assertFalse((dest.parent / 'top').exists())


class Verified(unittest.TestCase):
    """place checks each cached file on the open file it uses, never by reopening a path that may have changed."""

    def setUp(self):
        import hashlib, io, json, tarfile
        self.d = tempfile.TemporaryDirectory()
        self.root = Path(self.d.name)
        self.cache, self.store = self.root / 'cache', self.root / 'store'
        (self.cache / 'sha256').mkdir(parents=True)
        self.blob = b'pinned file'
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as t:
            info = tarfile.TarInfo('ok.txt'); info.size = 2; t.addfile(info, io.BytesIO(b'ok'))
        self.tar = buf.getvalue()
        sha = lambda b: hashlib.sha256(b).hexdigest()
        self.entries = {'blob': {'row': 'blob', 'size': len(self.blob), 'sha256': sha(self.blob)},
                        'shim': {'row': 'shim', 'size': len(self.tar), 'sha256': sha(self.tar), 'format': 'tar'}}
        for e, data in ((self.entries['blob'], self.blob), (self.entries['shim'], self.tar)):
            (self.cache / 'sha256' / e['sha256']).write_bytes(data)
        self.release = self.root / 'release.json'
        self.json = json
        self.write_release()
        g = P['place'].__globals__
        self.saved = {k: g[k] for k in ('CLASSES', 'find_row', 'lock', 'lock_folders', 'sha256_fd')}
        g['CLASSES'] = {}
        g['lock_folders'] = lambda release: [self.root]
        g['find_row'] = lambda folders, name: (self.root, 'x', self.store / name)
        g['lock'] = types.SimpleNamespace(path=lambda folder, name: None)

    def tearDown(self):
        P['place'].__globals__.update(self.saved)
        self.d.cleanup()

    def write_release(self, **over):
        e = {k: dict(v, **over.get(k, {})) for k, v in self.entries.items()}
        self.release.write_text(self.json.dumps({'downloads': [e['blob']], 'assets': [e['shim']]}))

    def place(self):
        return P['place'](self.release, self.cache, out=lambda line: None)

    def test_good_files_are_placed(self):
        placed, kept, problems, missing = self.place()
        self.assertEqual((placed, problems), (['blob', 'shim'], []))
        self.assertEqual((self.store / 'blob').read_bytes(), self.blob)
        self.assertEqual((self.store / 'shim' / 'ok.txt').read_bytes(), b'ok')
        self.assertEqual([p.name for p in self.store.iterdir() if p.name.startswith('.')], [])

    def test_an_archive_this_checkout_pins_is_placed_only_with_those_bytes(self):
        g = P['place'].__globals__
        g['CLASSES'] = {'shim': {'source_sha256': '0' * 64}}
        placed, kept, problems, missing = self.place()
        self.assertEqual(placed, ['blob'])
        self.assertEqual(len(problems), 1)
        self.assertIn('not the archive this checkout pins', problems[0])
        self.assertFalse((self.store / 'shim').exists())
        g['CLASSES'] = {'shim': {'source_sha256': self.entries['shim']['sha256']}}
        placed, kept, problems, missing = self.place()
        self.assertEqual((placed, problems), (['shim'], []))

    def test_a_cache_entry_changed_after_it_was_fetched_is_refused(self):
        for e in self.entries.values():
            (self.cache / 'sha256' / e['sha256']).write_bytes(b'swapped')
        placed, kept, problems, missing = self.place()
        self.assertEqual(placed, [])
        self.assertEqual(len(problems), 2)
        self.assertTrue(all('not placed' in p for p in problems), problems)
        self.assertEqual(sorted(p.name for p in self.store.iterdir()), [])

    def only_shim(self):
        self.entries = {'shim': self.entries['shim']}
        self.release.write_text(self.json.dumps({'assets': [self.entries['shim']]}))
        return self.cache / 'sha256' / self.entries['shim']['sha256']

    def test_a_cache_entry_swapped_after_the_check_is_not_what_is_unpacked(self):
        g = P['place'].__globals__
        real, path = g['private_copy'], self.only_shim()

        def swap_after(*a):
            f = real(*a)
            other = self.root / 'other'
            other.write_bytes(b'not a tar')
            os.replace(other, path)
            return f
        g['private_copy'] = swap_after
        try:
            placed, kept, problems, missing = self.place()
        finally:
            g['private_copy'] = real
        self.assertEqual((placed, problems), (['shim'], []))
        self.assertEqual((self.store / 'shim' / 'ok.txt').read_bytes(), b'ok')
        self.assertEqual(path.read_bytes(), b'not a tar')

    def test_a_cache_entry_rewritten_in_place_after_the_check_is_not_what_is_unpacked(self):
        """Whoever can write the cached file's inode (a user-owned cache) changes it after it was checked: the open
        file would read the new bytes; the private copy does not."""
        import io, tarfile
        g = P['place'].__globals__
        real, path = g['private_copy'], self.only_shim()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as t:
            info = tarfile.TarInfo('ok.txt'); info.size = 2; t.addfile(info, io.BytesIO(b'no'))
        evil = buf.getvalue()
        self.assertEqual(len(evil), len(self.tar))
        ino = path.stat().st_ino

        def rewrite_after(*a):
            f = real(*a)
            with open(path, 'r+b') as w:
                w.write(evil)
            return f
        g['private_copy'] = rewrite_after
        try:
            placed, kept, problems, missing = self.place()
        finally:
            g['private_copy'] = real
        self.assertEqual((path.stat().st_ino, path.read_bytes()), (ino, evil))
        self.assertEqual((placed, problems), (['shim'], []))
        self.assertEqual((self.store / 'shim' / 'ok.txt').read_bytes(), b'ok')

    def test_the_private_copy_is_unlinked_owned_and_closed_to_others(self):
        path = self.only_shim()
        with P['private_copy'](path, self.entries['shim'], self.root / 'near') as f:
            st = os.fstat(f.fileno())
            self.assertEqual((st.st_nlink, st.st_uid, st.st_mode & 0o077), (0, os.geteuid(), 0))
            self.assertEqual(f.read(), self.tar)
        self.assertEqual(list((self.root / 'near').iterdir()), [])

    def test_the_private_copy_refuses_changed_bytes_and_links(self):
        path = self.only_shim()
        with self.assertRaisesRegex(P['PlaceError'], 'sha256'):
            P['private_copy'](path, dict(self.entries['shim'], sha256='0' * 64), self.root)
        with self.assertRaisesRegex(P['PlaceError'], 'bytes'):
            P['private_copy'](path, dict(self.entries['shim'], size=1), self.root)
        link = self.root / 'link'
        link.symlink_to(path)
        with self.assertRaises(P['PlaceError']):
            P['private_copy'](link, self.entries['shim'], self.root)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['cache', 'link', 'release.json'])

    def test_a_cache_entry_that_is_a_link_is_refused(self):
        for e, data in ((self.entries['blob'], self.blob), (self.entries['shim'], self.tar)):
            p = self.cache / 'sha256' / e['sha256']
            elsewhere = self.root / ('elsewhere-' + e['row'])
            elsewhere.write_bytes(data)
            p.unlink()
            p.symlink_to(elsewhere)
        placed, kept, problems, missing = self.place()
        self.assertEqual(placed, [])
        self.assertEqual(len(problems), 2, problems)
        self.assertFalse(self.store.exists() and any(self.store.iterdir()))

    def test_a_digest_that_is_not_hex_is_refused_before_any_path_is_made(self):
        for bad in ('/' + 'a' * 63, '../' + 'a' * 61, 'A' * 64):
            self.write_release(blob={'sha256': bad}, shim={'row': '../x'})
            placed, kept, problems, missing = self.place()
            self.assertEqual(placed, [])
            self.assertTrue(any('not 64 lower-case hex' in p for p in problems), problems)
            self.assertTrue(any('not a plain name' in p for p in problems), problems)
            self.assertFalse(self.store.exists())


if __name__ == '__main__':
    unittest.main()
