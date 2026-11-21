"""golden-gate-deep-sleep-off: the daemon apply.sh installs, run on a stand-in Data tree.
python3.14 -I -B -m unittest discover -s patches/golden-gate-deep-sleep-off/tests"""
import os
import pathlib
import plistlib
import shutil
import stat
import subprocess
import tempfile
import unittest

PATCH = pathlib.Path(__file__).resolve().parents[1]
REL = 'Library/LaunchDaemons/local.m27.deep-sleep-off.plist'


class Daemon(unittest.TestCase):
    def setUp(self):
        self.job = plistlib.loads((PATCH / 'src/local.m27.deep-sleep-off.plist').read_bytes())
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_runs_at_load_and_retries_until_pmset_succeeds(self):
        self.assertEqual(self.job['Label'], 'local.m27.deep-sleep-off')
        self.assertIs(self.job['RunAtLoad'], True)
        self.assertEqual(self.job['KeepAlive'], {'SuccessfulExit': False})
        self.assertEqual(self.job['ProgramArguments'][:2], ['/bin/sh', '-c'])

    def run_script(self, pmset_status):
        """The job's script with pmset and the marker path redirected into the scratch folder."""
        log, marker = self.tmp / 'pmset.log', self.tmp / 'marker'
        pmset = self.tmp / 'pmset'
        pmset.write_text('#!/bin/sh\necho "$@" >> "%s"\nexit %d\n' % (log, pmset_status))
        pmset.chmod(0o755)
        script = self.job['ProgramArguments'][2]
        self.assertIn('/usr/bin/pmset -a standby 0 autopoweroff 0 hibernatemode 0', script)
        script = script.replace('/usr/bin/pmset', str(pmset)).replace('/private/var/db/.m27-deep-sleep-off', str(marker))
        r = subprocess.run(['/bin/sh', '-c', script])
        return r.returncode, log.read_text().splitlines() if log.exists() else [], marker.exists()

    def test_sets_deep_sleep_off_once(self):
        self.assertEqual(self.run_script(0), (0, ['-a standby 0 autopoweroff 0 hibernatemode 0'], True))
        # later boots: the marker is there, the user's choice is kept
        self.assertEqual(self.run_script(0), (0, ['-a standby 0 autopoweroff 0 hibernatemode 0'], True))

    def test_a_failed_pmset_leaves_no_marker_and_exits_nonzero(self):
        code, calls, marked = self.run_script(1)
        self.assertNotEqual(code, 0)       # KeepAlive SuccessfulExit false: launchd runs it again
        self.assertEqual((calls, marked), (['-a standby 0 autopoweroff 0 hibernatemode 0'], False))


@unittest.skipUnless(shutil.which('plutil'), 'needs plutil (macOS)')
class Apply(unittest.TestCase):
    def test_installs_the_daemon_root_644(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / 'bin').mkdir()
        for tool in ('install',):   # not root here: install without owner flags
            (tmp / 'bin' / tool).write_text('#!/bin/sh\nwhile [ "$1" = -o ] || [ "$1" = -g ]; do shift 2; done\n'
                                            'exec /usr/bin/install "$@"\n')
            (tmp / 'bin' / tool).chmod(0o755)
        data = tmp / 'data'
        data.mkdir()
        env = dict(os.environ, PATH='%s:/usr/bin:/bin' % (tmp / 'bin'), DATA=str(data), P=str(PATCH))
        r = subprocess.run(['sh', '-eu', str(PATCH / 'apply.sh')], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((data / REL).read_bytes(), (PATCH / 'src/local.m27.deep-sleep-off.plist').read_bytes())
        self.assertEqual(stat.S_IMODE((data / REL).stat().st_mode), 0o644)


if __name__ == '__main__':
    unittest.main()
