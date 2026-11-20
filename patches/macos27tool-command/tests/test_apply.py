"""apply.sh into a scratch Data root: the launcher, the CPython and tools/macOS27Tool land where /usr/local/bin/hao expects."""
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
RECIPE = HERE.parent


class Apply(unittest.TestCase):
    def run_apply(self, d, prepare=None):
        """make.sh then apply.sh into D/data (chown left out: no root here); PREPARE(data) runs before apply.sh."""
        p = d / 'recipe'
        (p / 'inputs').mkdir(parents=True)
        (p / 'build').mkdir()
        py = d / 'python' / 'bin'
        py.mkdir(parents=True)
        (py / 'python3.12').write_text('#!/bin/sh\n')
        (py / 'python3.12').chmod(0o755)
        with tarfile.open(p / 'inputs' / 'python_x86_64', 'w:gz') as t:
            t.add(d / 'python', arcname='python')
        subprocess.run(['sh', str(RECIPE / 'make.sh'), str(p / 'build')], check=True)
        data = d / 'data'
        (data / 'usr/local/bin').mkdir(parents=True)
        if prepare:
            prepare(data)
        env = dict(os.environ, P=str(p), DATA=str(data))
        script = (RECIPE / 'apply.sh').read_text().replace('chown ', ': chown ').replace(' -o root -g wheel', '')
        return subprocess.run(['sh', '-eu', '-c', script], env=env, capture_output=True, text=True)

    def test_lays_out_the_command(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            data = d / 'data'
            # a symlink planted at the launcher's name is replaced, not written through
            victim = d / 'victim'
            victim.write_text('keep')
            r = self.run_apply(d, prepare=lambda data: (data / 'usr/local/bin/hao').symlink_to(victim))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(victim.read_text(), 'keep')
            self.assertFalse((data / 'usr/local/bin/hao').is_symlink())
            self.assertEqual(oct((data / 'usr/local/bin/hao').stat().st_mode & 0o777), '0o755')
            self.assertEqual([x.name for x in (data / 'usr/local/bin').iterdir()], ['hao'])
            launcher = (data / 'usr/local/bin/hao').read_text()
            self.assertIn('/usr/local/lib/hao/python/bin/python3.12 -I /usr/local/lib/hao/hao/tools/macOS27Tool/macOS27Tool', launcher)
            self.assertTrue((data / 'usr/local/lib/hao/python/bin/python3.12').is_file())
            self.assertTrue((data / 'usr/local/lib/hao/hao/tools/macOS27Tool/macOS27Tool').is_file())
            self.assertTrue((data / 'usr/local/lib/hao/hao/tools/macOS27Tool/vendor/rich/LICENSE').is_file())
            self.assertFalse((data / 'usr/local/lib/hao/hao/tools/macOS27Tool/tests').exists())
            # hao's log folder exists 755 before first boot, so launchd never creates it 744
            self.assertEqual(oct((data / 'private/var/log/hao').stat().st_mode & 0o777), '0o755')

    def test_an_existing_744_log_folder_is_opened(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            def prepare(data):
                (data / 'private/var/log/hao').mkdir(parents=True)
                (data / 'private/var/log/hao').chmod(0o744)
            r = self.run_apply(d, prepare=prepare)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(oct((d / 'data/private/var/log/hao').stat().st_mode & 0o777), '0o755')

    def test_a_launcher_link_to_a_folder_is_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            r = self.run_apply(d, prepare=lambda data: (d / 'elsewhere').mkdir() or
                               (data / 'usr/local/bin/hao').symlink_to(d / 'elsewhere'))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(list((d / 'elsewhere').iterdir()), [])
            self.assertTrue((d / 'data/usr/local/bin/hao').is_file())

    def test_refuses_a_symlinked_folder(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            p = d / 'recipe'
            (p / 'inputs').mkdir(parents=True)
            data = d / 'data'
            (data / 'usr').mkdir(parents=True)
            (d / 'elsewhere').mkdir()
            (data / 'usr/local').symlink_to(d / 'elsewhere')
            env = dict(os.environ, P=str(p), DATA=str(data))
            r = subprocess.run(['sh', '-eu', '-c', (RECIPE / 'apply.sh').read_text()], env=env, capture_output=True,
                               text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('is a symlink', r.stderr)
            self.assertEqual(list((d / 'elsewhere').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
