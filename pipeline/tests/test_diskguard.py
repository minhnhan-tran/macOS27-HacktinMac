"""pipeline/diskguard.py: a job is refused before it writes when it would leave the disk under its floor."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
D = runpy.run_path(str(TOP / 'pipeline' / 'diskguard.py'))


class DiskGuard(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ.pop('M27_DISK_CHECK', None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def test_floor_against_free_space(self):
        free = D['free_gb']('/')
        os.environ['M27_DISK_FLOOR_GB'] = '0'
        D['check'](0, 't', 'small', '/', out=lambda m: None)
        with self.assertRaisesRegex(D['Refused'], 'under the floor'):
            D['check'](free + 10, 't', 'too big', '/', out=lambda m: None)
        os.environ['M27_DISK_FLOOR_GB'] = str(free + 1)
        with self.assertRaises(D['Refused']):
            D['check'](0, 't', 'floor above free', '/', out=lambda m: None)

    def test_a_host_command_decides_when_set(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = Path(d) / 'guard'
            cmd.write_text('#!/bin/sh\necho "$@" > "%s/args"\n[ "$3" -gt 100 ] && { echo REFUSED; exit 3; }\n'
                           'echo ok\n' % d)
            cmd.chmod(0o755)
            os.environ['M27_DISK_CHECK'] = str(cmd)
            said = []
            D['check'](86, 'golden-run', 'job', out=said.append)
            self.assertEqual(said, ['ok'])
            self.assertEqual((Path(d) / 'args').read_text().split(), ['check', '--need', '86', '--who', 'golden-run',
                                                                    '--what', 'job'])
            with self.assertRaisesRegex(D['Refused'], 'REFUSED'):
                D['check'](175, 'golden-run', 'job', out=said.append)

    def test_command_line_exit_code(self):
        r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/diskguard.py'), 'check', '--need', '1e12'],
                           capture_output=True, text=True, env={k: v for k, v in os.environ.items()
                                                                if k != 'M27_DISK_CHECK'})
        self.assertEqual(r.returncode, 3, r.stderr)

    def test_golden_run_refuses_before_it_writes(self):
        with tempfile.TemporaryDirectory() as d:
            a = json.loads((TOP / 'pipeline/assemblies/mbp92-26B131.json').read_text())
            a['disk_need_gb'] = 10 ** 12
            (Path(d) / 'a.json').write_text(json.dumps(a))
            ledger = Path(d) / 'ledger'; ledger.write_text('')
            r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/golden-run'), str(Path(d) / 'a.json'),
                                '--repo', str(Path(d) / 'repo'), '--work', str(Path(d) / 'work'),
                                '--record', str(Path(d) / 'record')], capture_output=True, text=True,
                               env=dict({k: v for k, v in os.environ.items() if k != 'M27_DISK_CHECK'},
                                        M27_SWAP_LEDGER=str(ledger)))     # this Mac's live swaps are not this test's
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('under the floor', r.stderr + r.stdout)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ['a.json', 'ledger'])


if __name__ == '__main__':
    unittest.main()
