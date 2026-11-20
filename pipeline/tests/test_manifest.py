"""python3.14 -I -B -m unittest discover -s pipeline/tests -p test_manifest.py"""
import json, os, runpy, tempfile, unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
M = runpy.run_path(str(TOP / 'pipeline' / 'manifest.py'))


class Manifest(unittest.TestCase):
    def test_manifest_is_current(self):
        # docs/MANIFEST.md is generated; regenerate with `python3.14 -I pipeline/patches.py manifest`
        self.assertEqual((TOP / 'docs/MANIFEST.md').read_text(), M['generate'](), 'docs/MANIFEST.md is stale')

    def test_records_name_no_store_path_or_who(self):
        for f in ('images.json', 'laptops.json'):
            text = (TOP / 'pipeline' / f).read_text()
            self.assertNotIn('/Users/', text, f)
            self.assertNotIn('"who"', text, f)
            self.assertNotIn('"source"', text, f)

    def test_every_recorded_laptop_image_is_recorded(self):
        images = json.loads((TOP / 'pipeline/images.json').read_text())
        for name, v in json.loads((TOP / 'pipeline/laptops.json').read_text()).items():
            self.assertIn(v['image'], images, name)

    def test_records_list_only_proven_or_resolved_swaps(self):
        resolved = runpy.run_path(str(TOP / 'pipeline' / 'swaps.py'))['resolutions']()
        for name, v in json.loads((TOP / 'pipeline/laptops.json').read_text()).items():
            for s in v['swaps']:
                self.assertTrue(M['listed'](s, resolved), (name, s['path']))

    def test_trial_swaps_are_counted_not_listed(self):
        a, b, c = 'a' * 64, 'b' * 64, 'c' * 64
        swaps = [{'path': '/x', 'sha256': a, 'proven': True, 'payload': False, 'now': 'there'},
                 {'path': '/y', 'sha256': b, 'proven': False, 'payload': False, 'now': 'there'},
                 {'path': '/z', 'sha256': c, 'proven': False, 'payload': False, 'now': 'gone'}]
        resolved = {(b, '/y'): 'dropped: trial'}
        on = [s for s in swaps if s['now'] != 'gone']
        self.assertEqual([s['path'] for s in on if M['listed'](s, resolved)], ['/x'])
        self.assertTrue(M['listed'](dict(swaps[1]), {(b, '/y'): 'recipe r'}))

    def test_revisions_from_the_build_manifest(self):
        text = 'oclp x\nset dev-bs-test: darwin27-kernel r10 macos27tool-runtime r12 dev-access r5\nfoundation golden-first\n'
        self.assertEqual(M['revisions_of'](text), {'darwin27-kernel': 10, 'macos27tool-runtime': 12, 'dev-access': 5})
        with self.assertRaises(ValueError):
            M['revisions_of']('oclp x\n')

    def test_scratch_folders_are_not_named(self):
        self.assertEqual(M['public_path']('/private/tmp/someone-trial-1/libx.dylib'), '/private/tmp/…/libx.dylib')
        self.assertEqual(M['public_path']('/usr/lib/hao/libhao.dylib'), '/usr/lib/hao/libhao.dylib')
        self.assertEqual(M['public_path']('/private/var/tmp/trace.d'), '/private/var/tmp/trace.d')
        swap = {'sha256': 'a' * 64, 'path': '/private/tmp/…/libx.dylib'}
        self.assertEqual(M['resolution_text'](swap, {('a' * 64, '/private/tmp/w-1/libx.dylib'): 'dropped: trial'}),
                         'dropped: trial')
        self.assertIsNone(M['resolution_text'](swap, {('b' * 64, '/private/tmp/w-1/libx.dylib'): 'dropped: trial'}))

    def test_laptop_swaps_are_the_newest_per_path_since_the_push(self):
        with tempfile.TemporaryDirectory() as d:
            led = Path(d, 'ledger.tsv')
            a, b, c = 'a' * 64, 'b' * 64, 'c' * 64
            led.write_text('\n'.join('\t'.join(f) for f in [
                ('2026-11-14 09:00:00', '/usr/lib/x.dylib', a, '755', 'src', 'w', 'laptop2'),    # before the push
                ('2026-11-14 11:00:00', '/usr/lib/x.dylib', b, '755', 'src', 'w', 'laptop2'),
                ('2026-11-14 12:00:00', '/usr/lib/x.dylib', c, '755', 'src', 'w', 'laptop2'),    # newest of the path
                ('2026-11-14 12:30:00', '/usr/lib/y.dylib', a, '755', 'src', 'w', 'laptop1'),    # other laptop
                ('2026-11-14 13:00:00', '/usr/lib/x.dylib', c, 'proven', 'owner: works', 'w', 'laptop2'),
            ]) + '\n')
            out = M['laptop_swaps']('laptop2', '2026-11-14 10:00:00', led)
        self.assertEqual([(s['path'], s['sha256'], s['proven']) for s in out], [('/usr/lib/x.dylib', c, True)])


if __name__ == '__main__':
    unittest.main()
