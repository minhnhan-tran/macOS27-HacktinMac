"""patch.json "files" declares what apply.sh installs, and the two LaunchDaemons are system jobs that run at load.
python3.14 -I -B -m unittest discover -s patches/dev-keepawake/tests"""
import json
from pathlib import Path
import plistlib
import re
import unittest

PATCH = Path(__file__).resolve().parents[1]
PJ = json.loads((PATCH / 'patch.json').read_text())
APPLY = (PATCH / 'apply.sh').read_text()


class Declaration(unittest.TestCase):
    def test_every_installed_file_is_declared_from_its_source(self):
        installs = {m.group(2)[len('$DATA/'):]: m.group(1)
                    for m in re.finditer(r'^install -o root -g wheel -m 644 "(\S+)" "(\S+)"$', APPLY, re.M)}
        self.assertEqual(set(installs), {f['path'] for f in PJ['files']})
        for path, source in installs.items():
            self.assertEqual(source, '$P/src/' + Path(path).name)
            self.assertTrue((PATCH / 'src' / Path(path).name).is_file())

    def test_daemons_run_at_load_without_a_user(self):
        for name in ('dev.m27.keepawake.plist', 'dev.m27.power.plist'):
            job = plistlib.loads((PATCH / 'src' / name).read_bytes())
            self.assertEqual(job['Label'] + '.plist', name)
            self.assertTrue(job['RunAtLoad'])
            self.assertNotIn('LimitLoadToSessionType', job)   # system domain: before Setup Assistant and any login
            self.assertNotIn('UserName', job)
        self.assertTrue(plistlib.loads((PATCH / 'src/dev.m27.keepawake.plist').read_bytes())['KeepAlive'])
        power = plistlib.loads((PATCH / 'src/dev.m27.power.plist').read_bytes())['ProgramArguments'][2]
        for setting in ('sleep 0', 'displaysleep 0', 'disksleep 0', 'standby 0', 'autopoweroff 0', 'powernap 0', 'womp 1',
                        'disablesleep 1', 'loginWindowIdleTime -int 0'):
            self.assertIn(setting, power)


if __name__ == '__main__':
    unittest.main()
