"""src/aot.py: macOS27Tool aot drives the launcher's --aot, --check and the caches folder."""
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import console as CON      # noqa: E402
import aot as AOT          # noqa: E402

BUILD = '''echo "m27-aot: no FILE given: the 3 programs /usr/lib/m27/aot-list names, 4 at a time" >&2
echo "m27-aot: [1/3] /usr/bin/a: valid, skipped" >&2
echo "m27-aot: [2/3] /usr/bin/b" >&2
echo "m27-aot: [3/3] /usr/bin/c" >&2
echo "m27-aot: 2 programs cached, 1 already valid, 0 failed, 0 listed but not present" >&2
'''
CHECK = '''echo "m27-aot: [1/2] /usr/bin/a: valid" >&2
echo "m27-aot: [2/2] /usr/bin/b: stale" >&2
echo "m27-aot: 1 valid, 0 missing, 1 stale, 0 not checkable, 0 listed but not present" >&2
exit 1
'''


class Pipe(io.StringIO):
    encoding = 'utf-8'

    def isatty(self):
        return False


def args(**kw):
    return types.SimpleNamespace(**dict(dict(status=False, background=False, clear=False), **kw))


def launcher(d, body):
    p = Path(d) / 'm27'
    p.write_text('#!/bin/sh\ncase "$*" in *--check*) %s;; *) %s;; esac\n' % (CHECK.replace('\n', '; ').rstrip('; '),
                                                                            body.replace('\n', '; ').rstrip('; ')))
    p.chmod(0o755)
    return str(p)


class Aot(unittest.TestCase):
    def console(self):
        out = Pipe()
        return CON.Console('steps', out=out, err=out, env={}), out

    def test_build_counts(self):
        with tempfile.TemporaryDirectory() as d:
            c, out = self.console()
            no_job = lambda *a, **k: subprocess.CompletedProcess(a, 1, '', '')
            self.assertEqual(AOT.aot(args(), c, env={'M27_LAUNCHER': launcher(d, BUILD), 'M27_LOGS': d}, euid=0,
                                     run=no_job, caches=Path(d)), 0)
        text = out.getvalue()
        self.assertIn('build the caches', text)
        self.assertIn('2 built, 1 still valid, 0 failed', text)
        self.assertIn('2 caches built, 1 still valid', text)

    def test_status_reports_stale_and_the_job(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'auto-aot.done').write_text('libm27 00ff\n')
            c, out = self.console()
            no_job = lambda *a, **k: subprocess.CompletedProcess(a, 1, '', '')
            AOT.aot(args(status=True), c, env={'M27_LAUNCHER': launcher(d, BUILD), 'M27_LOGS': d}, euid=501,
                    run=no_job, caches=Path(d))
        text = out.getvalue()
        self.assertIn('1 valid, 0 missing, 1 stale: sudo macOS27Tool aot builds them', text)
        self.assertIn('auto job    done · libm27 00ff', text)

    def test_build_and_clear_need_root(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = self.console()
            for kw in ({}, {'clear': True}, {'background': True}):
                with self.assertRaises(CON.Stopped) as x:
                    AOT.aot(args(**kw), c, env={'M27_LAUNCHER': launcher(d, BUILD)}, euid=501, caches=Path(d))
                self.assertEqual(x.exception.code, 5)

    def test_clear_removes_caches_and_the_marker(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ('A.aot', 'B.aot', 'auto-aot.done', 'keep.txt'):
                (Path(d) / name).write_text('x')
            c, out = self.console()
            AOT.aot(args(clear=True), c, env={'M27_LAUNCHER': launcher(d, BUILD)}, euid=0, caches=Path(d))
            left = sorted(p.name for p in Path(d).iterdir())
        self.assertEqual(left, ['keep.txt', 'm27'])
        self.assertIn('removed 2 caches', out.getvalue())

    def test_summary_for_status(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'A.aot').write_text('x')
            running = lambda *a, **k: subprocess.CompletedProcess(a, 0, 'system/m27.aot = {\n\tstate = running\n}', '')
            self.assertEqual(AOT.summary(Path(d), running),
                             '1 caches · auto job running (building now, at background priority)')


if __name__ == '__main__':
    unittest.main()
