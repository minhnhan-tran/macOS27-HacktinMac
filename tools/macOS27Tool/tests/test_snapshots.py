"""Snapshot tests of the console's output levels: the install fixture of test_progress rendered plain (a pipe, a fixed
clock) at --quiet, the default, -v and --log json, and the kit staging at the default level, compared with
tests/fixtures/console/*.txt. UPDATE_SNAPSHOTS=1 rewrites the files (review the diff before committing it).
--show-edits is not here: its lines follow the recipes' declarations, which change with every recipe."""
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
sys.path.insert(0, str(HERE))
import console as CON      # noqa: E402
import progress as PROG    # noqa: E402
from test_progress import ASSEMBLY, GROUPS, popen_of, Pipe   # noqa: E402

SNAPSHOTS = HERE / 'fixtures' / 'console'
STAGE = ('+ zsh /x/pipeline/stages/inputs.sh SHARE t\nINFO:root:- Adding Lilu.kext 1.7.2\n'
         '+ /k/py/bin/python3.12 -I /x/build-efi.py --out e\nKit staged: /k\n')


def render(level, text=ASSEMBLY, rc=0, argv=None, command='install'):
    out = Pipe()
    c = CON.Console(level, out=out, err=out, env={}, clock=lambda: 0.0)
    with tempfile.TemporaryDirectory() as d:
        r = PROG.Runner(c, command, groups=GROUPS, log=Path(d) / 'run.log', popen=popen_of(text, rc), env={})
        r(argv or ['sh', '/Volumes/M27/kit/assembly-source/golden-assemble.sh', '/nonexistent/kit', '/nonexistent/local'])
        if rc:
            c.stopped(r.explain(CON.Stopped('the build onto /dev/disk0 failed (exit %d) in step posts' % rc,
                                            why='the disk is erased and incomplete; it will not start',
                                            options=['run it again: /Volumes/M27/install'])))
        return re.sub(r'(/private)?/var/folders/\S+?/run\.log|/tmp\S*/run\.log|%s' % re.escape(str(Path(d))), '<LOG>',
                      out.getvalue()).replace(str(Path(d)), '<TMP>')


class Snapshots(unittest.TestCase):
    maxDiff = None

    def check(self, name, got):
        path = SNAPSHOTS / name
        if os.environ.get('UPDATE_SNAPSHOTS'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(got)
        self.assertEqual(got, path.read_text(), 'snapshot %s differs (UPDATE_SNAPSHOTS=1 to rewrite)' % name)

    def test_install_levels(self):
        for level in ('quiet', 'steps', 'files'):
            self.check('install-%s.txt' % level, render(level))

    def test_install_json(self):
        lines = [json.loads(l) for l in render('json').splitlines()]
        for e in lines:
            e.pop('log', None)
            e.pop('edits', None)        # counted from the recipes' declarations, which change with every recipe
        self.check('install-json.txt', '\n'.join(json.dumps(e, sort_keys=True) for e in lines) + '\n')

    def test_install_failure(self):
        text = ASSEMBLY.split('STEP post macos27tool-opencore')[0] + 'unmount of /Volumes/macOS refused: Resource busy\n'
        self.check('install-failure.txt', render('steps', text, rc=1))

    def test_kit_staging(self):
        self.check('kit-steps.txt', render('steps', STAGE, argv=['python3', '-I', 'golden-run', 'a.json', '--stage-kit'],
                                           command='create-installer'))


if __name__ == '__main__':
    unittest.main()
