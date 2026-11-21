"""patch.json "files" declares what apply.sh installs, and the job runs the installed tool on App Store changes.
python3.14 -I -B -m unittest discover -s patches/golden-gate-appstore-slices/tests"""
import json
import pathlib
import plistlib
import re
import unittest

PATCH = pathlib.Path(__file__).resolve().parents[1]
PJ = json.loads((PATCH / 'patch.json').read_text())
APPLY = (PATCH / 'apply.sh').read_text()
JOB = plistlib.loads((PATCH / 'src/org.m27.slice-guard.plist').read_bytes())


class Recipe(unittest.TestCase):
    def installs(self):
        return {m.group(3)[len('$DATA/'):]: (m.group(1), m.group(2))
                for m in re.finditer(r'^install -o root -g wheel -m (\d+) "(\S+)" "(\$DATA/\S+)"$', APPLY, re.M)}

    def test_every_installed_file_is_declared_from_its_source(self):
        installs = self.installs()
        declared = {f['path']: f['origin'] for f in PJ['files']}
        self.assertEqual(set(installs), set(declared))
        for path, (mode, source) in installs.items():
            origin = declared[path]
            self.assertEqual(origin['kind'], 'm27')
            self.assertIn(mode, origin['note'])
            if 'row' in origin:
                self.assertTrue(source.startswith('$P/inputs/%s/' % origin['row']), source)
            else:
                self.assertTrue((PATCH / source.replace('$P/', '')).is_file(), source)
        self.assertEqual({o['row'] for o in declared.values() if 'row' in o}, set(PJ['inputs']))

    def test_the_job_runs_the_installed_tool_on_app_changes(self):
        self.assertEqual(JOB['Label'], 'org.m27.slice-guard')
        self.assertEqual(JOB['ProgramArguments'][0], '/' + next(p for p in self.installs() if 'libexec' in p))
        self.assertEqual(JOB['WatchPaths'], ['/Applications'])
        self.assertLessEqual(JOB['StartInterval'], 3600)            # in-place updates do not change /Applications
        self.assertEqual(JOB['ProcessType'], 'Background')
        self.assertNotIn('RunAtLoad', JOB)                          # nothing at boot: boot speed


if __name__ == '__main__':
    unittest.main()
