"""tools/macOS27Tool/macOS27Tool's sh part (owner 2026-11-17: "Root-owned Python only"): as root it runs only python.org's Python 3.14
when that Python, every folder above it and everything in its framework version are root's alone, and refuses (exit 5)
before any Python starts; as a user it runs python3.14 (else python3) from PATH, isolated."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
M27 = HERE.parent / 'macOS27Tool'


def sh_part():
    text = M27.read_text()
    return text[:text.index('":"""') + len('":"""')] + '\n'


class Launcher(unittest.TestCase):
    def run_as_root(self, root_py):
        """The sh part with `id` answering 0 and ROOT_PY pointing at ROOT_PY (a folder of the test's own)."""
        with tempfile.TemporaryDirectory() as b:
            (Path(b) / 'id').write_text('#!/bin/sh\necho 0\n')
            (Path(b) / 'id').chmod(0o755)
            script = sh_part().replace('ROOT_PY=$F/Versions/3.14', 'ROOT_PY=%s' % root_py) \
                .replace('/usr/bin/id -u', '%s/id -u' % b)          # an id answering 0, as under sudo
            (Path(b) / 'm27').write_text(script)
            return subprocess.run(['/bin/sh', str(Path(b) / 'm27'), 'help'], capture_output=True, text=True,
                                  env=dict(os.environ, PATH='%s:/usr/bin:/bin:/usr/sbin:/sbin' % b))

    def test_the_sh_part_is_a_python_string_and_has_no_backslashes(self):
        self.assertTrue(M27.read_text().startswith('#!/bin/sh\n""":"\n'))
        self.assertNotIn(chr(92), sh_part())

    def test_as_root_without_python_org_it_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_as_root(Path(d) / 'missing')
            self.assertEqual(r.returncode, 5, r.stderr)
            self.assertIn('not installed', r.stderr)

    def test_as_root_a_python_the_user_owns_is_refused_before_it_starts(self):
        with tempfile.TemporaryDirectory() as d:
            py = Path(d) / 'Versions' / '3.14'
            (py / 'bin').mkdir(parents=True)
            marker = Path(d) / 'ran'
            (py / 'bin' / 'python3.14').write_text('#!/bin/sh\ntouch %s\n' % marker)
            (py / 'bin' / 'python3.14').chmod(0o755)
            r = self.run_as_root(py)
            self.assertEqual(r.returncode, 5, r.stderr)
            self.assertIn("not root's alone", r.stderr)
            self.assertIn('sudo chown -R root:wheel', r.stderr)
            self.assertFalse(marker.exists())                   # never started

    def test_root_writes_no_bytecode_into_the_users_folder(self):
        self.assertIn('exec "$ROOT_PY/bin/python3.14" -I -S -B "$0" "$@"', sh_part())

    def test_as_a_user_it_runs_python_from_path(self):
        r = subprocess.run([str(M27), 'help'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('create-installer', r.stdout)


if __name__ == '__main__':
    unittest.main()
