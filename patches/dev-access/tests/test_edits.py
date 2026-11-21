"""patch.json "files" declares what apply.sh installs: every declared path is installed by apply.sh from the row or
src file the declaration names, and apply.sh installs nothing else.
python3.14 -I -B -m unittest discover -s patches/dev-access/tests"""
import json
import plistlib
from pathlib import Path
import re
import unittest

PATCH = Path(__file__).resolve().parents[1]
PJ = json.loads((PATCH / 'patch.json').read_text())
APPLY = (PATCH / 'apply.sh').read_text()
SOURCES = {'private/var/root/.testssh/sshd_config': '$P/src/sshd_config',
           'Library/LaunchDaemons/local.testssh.plist': '$P/src/local.testssh.plist',
           'Library/LaunchDaemons/dev.m27.screensharing.plist': '$P/src/dev.m27.screensharing.plist'}


class Declaration(unittest.TestCase):
    def test_every_installed_file_is_declared_from_its_source(self):
        installs = {}
        for m in re.finditer(r'^install -o root -g wheel -m \d+ "(\S+)" "(\S+)"$', APPLY, re.M):
            dest = m.group(2).replace('$T', '$DATA/private/var/root/.testssh')
            self.assertTrue(dest.startswith('$DATA/'), dest)
            installs[dest[len('$DATA/'):]] = m.group(1)
        declared = {f['path']: f['origin'] for f in PJ['files']}
        self.assertEqual(set(installs), set(declared))
        for path, source in installs.items():
            origin = declared[path]
            self.assertEqual(origin['kind'], 'm27')
            if 'row' in origin:
                self.assertEqual(source, '$P/inputs/' + origin['row'])
                self.assertIn(origin['row'], PJ['inputs'])
            else:
                self.assertEqual(source, SOURCES[path])
                self.assertTrue((PATCH / source.replace('$P/', '')).is_file())
        self.assertEqual({o['row'] for o in declared.values() if 'row' in o}, set(PJ['inputs']))


class SpacesInPaths(unittest.TestCase):
    def test_apply_runs_from_a_volume_whose_name_has_a_space(self):
        # The user's install runs posts from the installer volume ("/Volumes/M27 1"): an unquoted path split there
        # (2026-11-17, laptop 2: "Read-only file system: '1'"). A stand-in install(1) takes the same arguments.
        import os, shutil, subprocess, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            top = Path(tmp) / 'M27 1'
            kit, data, bin_ = top / 'kit' / 'dev-access', top / 'Data volume', Path(tmp) / 'bin'
            shutil.copytree(PATCH / 'src', kit / 'src')
            (kit / 'inputs').mkdir(parents=True)
            for row in PJ['inputs']:
                (kit / 'inputs' / row).write_text(row)
            data.mkdir(); bin_.mkdir()
            stub = bin_ / 'install'
            stub.write_text('#!/bin/sh\n'
                            'd=0; while [ $# -gt 0 ]; do case "$1" in -d) d=1; shift;; -o|-g|-m) shift 2;; *) break;; esac; done\n'
                            'if [ $d = 1 ]; then [ $# -eq 1 ] || { echo "install -d: $# args" >&2; exit 1; }; mkdir -p "$1"\n'
                            'else [ $# -eq 2 ] || { echo "install: $# args" >&2; exit 1; }; cp "$1" "$2"; fi\n')
            stub.chmod(0o755)
            env = dict(os.environ, PATH='%s:/usr/bin:/bin' % bin_, P=str(kit), DATA=str(data))
            r = subprocess.run(['sh', '-eu', str(PATCH / 'apply.sh')], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            for f in PJ['files']:
                self.assertTrue((data / f['path']).is_file(), f['path'])


class ScreenSharing(unittest.TestCase):
    def test_a_system_job_at_every_boot_for_the_listed_accounts(self):
        job = plistlib.loads((PATCH / 'src/dev.m27.screensharing.plist').read_bytes())
        self.assertEqual(job['Label'], 'dev.m27.screensharing')
        self.assertTrue(job['RunAtLoad'])
        for key in ('LimitLoadToSessionType', 'UserName', 'KeepAlive'):
            self.assertNotIn(key, job)          # system domain, once per boot: before Setup Assistant and any login
        script = job['ProgramArguments'][2]
        self.assertEqual(job['ProgramArguments'][:2], ['/bin/sh', '-c'])
        self.assertIn('for u in $(/bin/cat $U)', script)
        self.assertIn('-append $G GroupMembership $u', script)   # by name: works before the account exists
        self.assertIn('U=/private/var/root/.testssh/screensharing-users', script)
        self.assertIn('G=/Groups/com.apple.access_screensharing', script)
        self.assertIn('launchctl enable system/com.apple.screensharing', script)
        self.assertIn('launchctl bootstrap system /System/Library/LaunchDaemons/com.apple.screensharing.plist', script)
        self.assertNotIn('kickstart', script)   # ARD's kickstart needs an arm64e-only Perl bridge; plain Screen Sharing


if __name__ == '__main__':
    unittest.main()
