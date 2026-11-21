"""pipeline/swaps.py: a path's newest live swap needs a resolution; m27 payload bytes resolve /usr/lib/m27 files."""
import hashlib, io, contextlib, os, runpy, subprocess, sys, tempfile, unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
S = runpy.run_path(str(TOP / 'pipeline/swaps.py'))
SHA = lambda b: hashlib.sha256(b).hexdigest()


class Swaps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.ledger = self.d / 'ledger.tsv'
        self.res = self.d / 'live-swaps.tsv'
        self.res.write_text('# header\n')

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, date, path, data):
        with self.ledger.open('a') as f:
            f.write('%s\t%s\t%s\t755\t/src/x\tdev\n' % (date, path, SHA(data)))

    def test_newest_swap_of_a_path_decides(self):
        self.put('2026-11-12 10:00:00', '/usr/libexec/airportd', b'v1')
        self.put('2026-11-12 11:00:00', '/usr/libexec/airportd', b'v2')
        rows = S['status'](S['ledger'](self.ledger), S['resolutions'](self.res))
        self.assertEqual([(r['sha256'], r['swaps'], r['resolution']) for r in rows], [(SHA(b'v2'), 2, None)])
        self.res.write_text('%s\t/usr/libexec/airportd\trow tahoe_native_donors\n' % SHA(b'v1'))
        self.assertEqual(len(S['unresolved'](self.ledger, self.res, payload=self.d)), 1)    # v1's line is not v2's
        self.res.write_text('%s\t/usr/libexec/airportd\trow tahoe_native_donors\n' % SHA(b'v2'))
        self.assertEqual(S['unresolved'](self.ledger, self.res, payload=self.d), [])

    def test_m27_files_resolve_by_payload_bytes(self):
        self.put('2026-11-12 10:00:00', '/usr/lib/m27/libm27.dylib', b'm27')
        payload = self.d / 'payload'
        (payload / 'root/usr/lib/m27').mkdir(parents=True)
        (payload / 'root/usr/lib/m27/libm27.dylib').write_bytes(b'other')
        self.assertEqual(len(S['unresolved'](self.ledger, self.res, payload=payload)), 1)
        (payload / 'root/usr/lib/m27/libm27.dylib').write_bytes(b'm27')
        self.assertEqual(S['unresolved'](self.ledger, self.res, payload=payload), [])

    def test_bad_resolution_lines_refused(self):
        for line in ('abc\t/x\trow y', '%s\tx\trow y' % SHA(b''), '%s\t/x\tfixed it' % SHA(b'')):
            self.res.write_text(line + '\n')
            with self.assertRaises(ValueError):
                S['resolutions'](self.res)
        self.res.write_text('%s\tESP disk0s1 EFI/OC/config.plist\tdropped: test\n' % SHA(b''))
        self.assertEqual(len(S['resolutions'](self.res)), 1)     # the ledger's name for a file on the EFI partition
        self.res.write_text('%s\t/x\tdropped: experiment only\n' % SHA(b''))
        self.assertEqual(S['resolutions'](self.res), {(SHA(b''), '/x'): 'dropped: experiment only'})

    def test_private_file_resolves_and_the_tracked_file_wins(self):
        priv = self.d / 'private.tsv'
        priv.write_text('# p\n%s\t/x\tdropped: one\n%s\t/y\tdropped: two\n' % (SHA(b'a'), SHA(b'b')))
        self.res.write_text('%s\t/y\trecipe r\n' % SHA(b'b'))
        self.assertEqual(S['resolutions'](self.res, priv), {(SHA(b'a'), '/x'): 'dropped: one', (SHA(b'b'), '/y'): 'recipe r'})
        self.assertEqual(S['resolutions'](self.res), {(SHA(b'b'), '/y'): 'recipe r'})
        self.assertEqual(S['resolutions'](self.res, False), {(SHA(b'b'), '/y'): 'recipe r'})
        self.put('2026-01-01 00:00:00', '/x', b'a')
        self.assertEqual([r['resolution'] for r in S['status'](S['ledger'](self.ledger), S['resolutions'](self.res, priv))],
                         ['dropped: one'])

    def test_no_ledger_is_clean(self):
        self.assertEqual(S['unresolved'](self.d / 'none.tsv', self.res, payload=self.d), [])

    def test_repository_resolution_file_parses(self):
        S['resolutions']()


class ProvenSwaps(unittest.TestCase):
    """A swap proven on the laptop (bench pass or the owner's word) blocks every candidate build until it is resolved
    (owner, 2026-11-14: "I don't want to have to revisit this down the road")."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.ledger = self.d / 'ledger.tsv'
        self.res = self.d / 'live-swaps.tsv'; self.res.write_text('')
        self.path = '/System/Library/Extensions/Fixture.kext/Contents/MacOS/Fixture'
        for date, data in (('2026-11-14 01:00:00', b'v1'), ('2026-11-14 02:00:00', b'v2')):
            with self.ledger.open('a') as f:
                f.write('%s\t%s\t%s\t755\t/src/fix\tpatcher\tlaptop1\n' % (date, self.path, SHA(data)))

    def gate(self):
        return S['gate'](self.ledger, self.res, payload=self.d)

    def test_unproven_swaps_do_not_block(self):
        self.assertEqual([], self.gate())

    def test_proven_swap_blocks_until_resolved(self):
        line = S['prove'](self.ledger, self.path, 'bench queue/bench/20261114-0100 pass', who='bench')
        self.assertEqual(line.split('\t')[2:6], [SHA(b'v2'), 'proven', 'bench queue/bench/20261114-0100 pass', 'bench'])
        self.assertEqual('laptop1', line.split('\t')[6])
        lines = self.gate()
        self.assertIn('1 proven live swap is in no recipe or row', lines[0])
        self.assertIn(self.path, lines[1]); self.assertIn('by patcher', lines[1]); self.assertIn('by bench', lines[1])
        self.assertIn('20261114-0100', lines[1])
        self.assertEqual(2, len(S['ledger'](self.ledger)))     # the proof is no swap
        self.res.write_text('%s\t%s\trecipe fixture-recipe\n' % (SHA(b'v2'), self.path))
        self.assertEqual([], self.gate())

    def test_an_older_proven_swap_needs_its_own_resolution(self):
        S['prove'](self.ledger, self.path, 'owner: "that works, keep it"', sha=SHA(b'v1'), who='bench')
        self.res.write_text('%s\t%s\tdropped: test arm\n' % (SHA(b'v2'), self.path))   # the newer swap is resolved
        lines = self.gate()
        self.assertEqual(3, len(lines))    # the count, the swap, the hint
        self.assertIn(SHA(b'v1')[:12], lines[1])
        self.res.write_text(self.res.read_text() + '%s\t%s\tdropped: superseded by v2, which ships in recipe x\n'
                            % (SHA(b'v1'), self.path))
        self.assertEqual([], self.gate())

    def test_proof_needs_evidence_and_a_recorded_swap(self):
        for evidence in ('works', 'bench', 'owner:'):
            with self.assertRaises(ValueError):
                S['prove'](self.ledger, self.path, evidence)
        with self.assertRaises(ValueError):
            S['prove'](self.ledger, '/not/swapped', 'bench 20261114-0100')
        with self.assertRaises(ValueError):
            S['prove'](self.ledger, self.path, 'bench 20261114-0100', sha=SHA(b'v3'))

    def run_tool(self, *argv):
        env = dict(os.environ, M27_SWAP_LEDGER=str(self.ledger))
        return subprocess.run([sys.executable, '-I', *map(str, argv)], capture_output=True, text=True, env=env,
                              timeout=120)

    def test_cli_records_shows_and_gates(self):
        r = self.run_tool(TOP / 'pipeline/swaps.py', 'proven', self.path, '--evidence', 'owner: "lock it in"',
                          '--who', 'bench')
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn('no resolution yet', r.stdout)
        r = self.run_tool(TOP / 'pipeline/swaps.py', 'status')
        self.assertIn('PROVEN-OPEN', r.stdout); self.assertIn('lock it in', r.stdout)
        r = self.run_tool(TOP / 'pipeline/swaps.py', 'gate')
        self.assertEqual(1, r.returncode); self.assertIn(self.path, r.stderr)

    def test_golden_run_and_release_cut_refuse_to_start(self):
        S['prove'](self.ledger, self.path, 'bench 20261114-0100 pass', who='bench')
        for argv in ((TOP / 'pipeline/golden-run', TOP / 'pipeline/assemblies/mbp92-26B131.json', '--repo', self.d / 'repo'),
                     (TOP / 'pipeline/golden-run', TOP / 'pipeline/assemblies/mbp92-26B131.json', '--stage-kit',
                      '--work', self.d / 'stage'),
                     (TOP / 'pipeline/release-cut', TOP / 'pipeline/assemblies/mbp92-26B131.json', '--write',
                      self.d / 'release.json', '--release', 'x', '--channel', 'dev', '--version', 'v0.0.0')):
            r = self.run_tool(*argv)
            self.assertEqual(3, r.returncode, (argv, r.stdout[-500:], r.stderr[-500:]))
            self.assertIn('refused: 1 proven live swap', r.stderr)
            self.assertIn(self.path, r.stderr)
        self.assertFalse((self.d / 'repo').exists()); self.assertFalse((self.d / 'stage').exists())
        self.assertFalse((self.d / 'release.json').exists())


if __name__ == '__main__':
    unittest.main()
