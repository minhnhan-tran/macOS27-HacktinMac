"""dev-capture: apply.sh installs what patch.json declares, and the logging profile persists com.m27's Info lines.
python3.14 -I -B -m unittest discover -s patches/dev-capture/tests"""
import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path

PATCH = Path(__file__).resolve().parents[1]
PJ = json.loads((PATCH / 'patch.json').read_text())


class Recipe(unittest.TestCase):
    def test_profile_persists_info_only(self):
        opts = plistlib.loads((PATCH / 'src/com.m27.plist').read_bytes())['DEFAULT-OPTIONS']
        self.assertEqual(opts, {'Level': {'Enable': 'Info', 'Persist': 'Info'}})   # 27 ignores Enable-Private-Data

    def test_dev_pack_only(self):
        self.assertEqual((PJ['pack'], PJ['status']), ('dev', 'dev'))

    def test_apply_installs_the_declared_files(self):
        text = (PATCH / 'apply.sh').read_text().replace('install -o root -g wheel ', 'install ') \
            .replace('install -d -o root -g wheel ', 'install -d ')
        with tempfile.TemporaryDirectory() as d:
            data, p = Path(d) / 'data', Path(d) / 'p'
            (p / 'inputs').mkdir(parents=True)
            (p / 'src').symlink_to(PATCH / 'src')
            for row in PJ['inputs']:
                (p / 'inputs' / row).write_bytes(row.encode())
            (p / 'inputs/m27_capture_plist').write_bytes(plistlib.dumps({'Label': 'local.m27.capture'}))
            env = dict(os.environ, DATA=str(data), P=str(p))
            subprocess.run(['/bin/sh', '-eu', '-c', text], env=env, check=True, capture_output=True)
            for f in PJ['files']:
                row = f['origin'].get('row')
                want = (p / 'inputs' / row) if row else (PATCH / 'src' / Path(f['path']).name)
                self.assertEqual((data / f['path']).read_bytes(), want.read_bytes(), f['path'])
            d = str(data)
            self.assertTrue((Path(d) / 'private/var/db/m27-capture').is_dir())


if __name__ == '__main__':
    unittest.main()
