"""src/run.py: hao run builds the launcher's command line and replaces itself with it."""
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import console as CON      # noqa: E402
import run as RUN          # noqa: E402


class Pipe(io.StringIO):
    encoding = 'utf-8'

    def isatty(self):
        return False


def args(**kw):
    a = dict(verbose=0, interp=False, stats=False, allow_unsigned=False, program='/Applications/X.app/Contents/MacOS/X',
             args=['--flag', 'value'])
    a.update(kw)
    return types.SimpleNamespace(**a)


class Run(unittest.TestCase):
    def test_switches(self):
        argv, note = RUN.argv_for(args(verbose=3, stats=True, allow_unsigned=True), 501, '/usr/libexec/hao')
        self.assertEqual(argv, ['/usr/libexec/hao', '-vv', '--stats', '--allow-unsigned',
                                '/Applications/X.app/Contents/MacOS/X', '--flag', 'value'])
        self.assertIsNone(note)

    def test_sudo_drops_verbose(self):
        argv, note = RUN.argv_for(args(verbose=1, interp=True), 0, '/usr/libexec/hao')
        self.assertEqual(argv[:2], ['/usr/libexec/hao', '--interp'])
        self.assertIn('ignored under sudo', note)

    def test_execs_the_launcher(self):
        with tempfile.TemporaryDirectory() as d:
            launcher = Path(d) / 'hao'
            launcher.write_text('#!/bin/sh\n')
            launcher.chmod(0o755)
            seen = []
            c = CON.Console('steps', out=Pipe(), err=Pipe(), env={})
            RUN.run(args(), c, env={'HAO_LAUNCHER': str(launcher)}, euid=501, execv=lambda p, a: seen.append((p, a)))
        self.assertEqual(seen[0][0], str(launcher))
        self.assertEqual(seen[0][1][-2:], ['--flag', 'value'])

    def test_no_runtime_is_refused(self):
        c = CON.Console('steps', out=Pipe(), err=Pipe(), env={})
        with self.assertRaises(CON.Stopped) as x:
            RUN.run(args(), c, env={'HAO_LAUNCHER': '/nonexistent/hao'}, euid=501, execv=None)
        self.assertEqual(x.exception.code, 5)


if __name__ == '__main__':
    unittest.main()
