"""make-on-mac runs a producer that comes in a downloaded archive (m27-runtime-on-mac) from a private copy of its folder
taken out of the archive the release and this checkout pin, read from the download cache. Fixtures only."""
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import tarfile
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
M = runpy.run_path(str(TOP / 'pipeline' / 'make-on-mac'))
take = M['archive_tool']
CLASSES = json.loads((TOP / 'pipeline/release-classes.json').read_text())['rows']


class ProducerFromArchive(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)
        root = Path(self.d.name)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as t:
            for name, data in (('top/tools/m27-runtime-on-mac', b'#!tool'), ('top/tools/helper.py', b'x = 1'),
                               ('top/payload/bin/m27', b'h')):
                info = tarfile.TarInfo(name); info.size = len(data); t.addfile(info, io.BytesIO(data))
        self.archive = buf.getvalue()
        self.sha = hashlib.sha256(self.archive).hexdigest()
        self.cache = root / 'cache'; (self.cache / 'sha256').mkdir(parents=True)
        (self.cache / 'sha256' / self.sha).write_bytes(self.archive)
        self.top = root / 'row'; (self.top / 'tools').mkdir(parents=True)
        self.tool = self.top / 'tools/m27-runtime-on-mac'; self.tool.write_bytes(b'#!tool')
        self.release = {'assets': [{'row': 'm27_runtime', 'sha256': self.sha, 'size': len(self.archive)}]}
        g = take.__globals__
        self.saved = g['TOP']
        fake = root / 'checkout'; (fake / 'pipeline').mkdir(parents=True)
        (fake / 'pipeline/release-classes.json').write_text(json.dumps(
            {'rows': {'m27_runtime': dict(CLASSES['m27_runtime'], source_sha256=self.sha)}}))
        g['TOP'] = fake
        self.addCleanup(lambda: g.__setitem__('TOP', self.saved))

    def call(self, release=None, cache='default'):
        return take(self.cache if cache == 'default' else cache, release or self.release, 'm27_runtime',
                    'tools/m27-runtime-on-mac')

    def test_the_archive_copy_runs_with_its_folder(self):
        tool, why = self.call()
        self.assertIsNone(why)
        self.assertEqual(tool.read_bytes(), b'#!tool')
        self.assertEqual((tool.parent / 'helper.py').read_bytes(), b'x = 1')     # the modules it imports
        self.assertFalse((tool.parent.parent / 'payload').exists())               # only its folder
        self.assertNotEqual(tool.parent, self.tool.parent)                         # never the placed copy
        self.assertEqual(oct(tool.parent.parent.stat().st_mode & 0o777), '0o711')

    def test_a_changed_placed_tool_does_not_matter(self):
        self.tool.write_bytes(b'#!other')
        tool, why = self.call()
        self.assertEqual(tool.read_bytes(), b'#!tool')

    def test_a_changed_cache_or_release_is_refused(self):
        other = {'assets': [{'row': 'm27_runtime', 'sha256': '1' * 64}]}
        self.assertIn('not the %s this checkout pins' % self.sha, self.call(other)[1])
        (self.cache / 'sha256' / self.sha).write_bytes(b'swapped')
        self.assertIn('not its pinned bytes', self.call()[1])

    def test_a_linked_cache_entry_is_refused(self):
        real = self.cache / 'sha256' / self.sha
        moved = Path(self.d.name) / 'moved'; real.rename(moved); os.symlink(moved, real)
        self.assertIn('not in the cache', self.call()[1])

    def test_the_archive_is_read_once_and_a_bad_one_leaves_nothing(self):
        reads = []
        real = take.__globals__['os'].fdopen
        def counting(fd, *a, **k):
            f = real(fd, *a, **k); reads.append(f); return f
        take.__globals__['os'].fdopen = counting
        try:
            tool, why = self.call()
        finally:
            take.__globals__['os'].fdopen = real
        self.assertIsNone(why)
        self.assertEqual(sum(1 for f in reads if 'b' in getattr(f, 'mode', '') and 'r' in f.mode), 1)
        data = bytearray(self.archive); data[-1] ^= 1                 # the hash fails only after extracting
        (self.cache / 'sha256' / self.sha).write_bytes(bytes(data))
        tool, why = self.call()
        self.assertIn('not its pinned bytes', why)
        self.assertIsNone(tool)

    def test_an_unreadable_archive_with_its_pinned_hash_is_refused(self):
        bad = b'not a tar at all'
        sha = hashlib.sha256(bad).hexdigest()
        (self.cache / 'sha256' / sha).write_bytes(bad)
        g = take.__globals__
        fake = g['TOP']
        (fake / 'pipeline/release-classes.json').write_text(json.dumps(
            {'rows': {'m27_runtime': dict(CLASSES['m27_runtime'], source_sha256=sha)}}))
        tool, why = self.call({'assets': [{'row': 'm27_runtime', 'sha256': sha}]})
        self.assertIsNone(tool)
        self.assertIn('could not be read', why)

    def test_an_archive_longer_than_its_pinned_size_is_not_read_past_it(self):
        (self.cache / 'sha256' / self.sha).write_bytes(self.archive + b'\0' * (1 << 20))
        tool, why = self.call()
        self.assertIsNone(tool)
        self.assertIn('not its pinned bytes', why)

    def test_no_cache_is_refused(self):
        self.assertIn('--cache', self.call(cache=None)[1])

if __name__ == '__main__':
    unittest.main()
