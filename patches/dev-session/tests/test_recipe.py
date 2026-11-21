"""dev-session: apply.sh layout, the kcpassword encoding, and no fixed secret.
python3.14 -I -B -m unittest discover -s patches/dev-session/tests"""
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

PATCH = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = (PATCH / 'src/dev-session.zsh').read_text()


def reference_kcpassword(pw):
    """the reference encoder: XOR with the loginwindow key, NUL-padded to a multiple of 12, at least one NUL."""
    key = bytes([0x7D, 0x89, 0x52, 0x23, 0xD2, 0xBC, 0xDD, 0xEA, 0xA3, 0xB9, 0x1F])
    n = len(pw) + (12 - len(pw) % 12 if len(pw) % 12 else 12)
    pw = pw.ljust(n, b'\0')
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(pw))


class Recipe(unittest.TestCase):
    def test_kcpassword_matches_reference(self):
        perl = re.search(r"perl -e '(.*?)' \\\n", SCRIPT, re.S).group(1)
        for pw in (b'a', b'abcdefghijk', b'abcdefghijkl', b'A1b2C3d4E5f6G7h8I9j0'):
            out = subprocess.run(['perl', '-e', perl], input=pw, capture_output=True, check=True).stdout
            self.assertEqual(out, reference_kcpassword(pw), pw)

    def test_no_fixed_password(self):
        self.assertIn('/dev/urandom', SCRIPT)
        self.assertNotRegex(SCRIPT, r'-password "[^$]')        # every -password takes the generated file

    def test_apply_layout(self):
        tmp = pathlib.Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp)
        data = tmp / 'data'; data.mkdir(); (tmp / 'bin').mkdir()
        for tool in ('install', 'chown'):
            body = ('#!/bin/sh\nwhile [ "$1" = -o ] || [ "$1" = -g ]; do shift 2; done\nexec /usr/bin/install "$@"\n'
                    if tool == 'install' else '#!/bin/sh\n:\n')
            (tmp / 'bin' / tool).write_text(body); (tmp / 'bin' / tool).chmod(0o755)
        p = tmp / 'recipe'; shutil.copytree(PATCH, p); (p / 'inputs').mkdir(exist_ok=True)
        (p / 'inputs/dev_session_config').write_text('ACCOUNT=x\nGUI_HELPER=/x\nGUI_HELPER_ID=x\n')
        env = dict(os.environ, PATH='%s:/usr/bin:/bin' % (tmp / 'bin'), DATA=str(data), P=str(p))
        r = subprocess.run(['sh', '-eu', str(p / 'apply.sh')], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        mode = lambda p: stat.S_IMODE((data / p).stat().st_mode)
        self.assertEqual(mode('usr/local/libexec/dev-session'), 0o755)
        self.assertEqual(mode('Library/LaunchDaemons/dev.m27.session.plist'), 0o644)
        self.assertTrue((data / 'private/var/db/.AppleSetupDone').is_file())
        self.assertEqual(mode('private/var/db/m27-dev/session.conf'), 0o600)

    def test_dev_pack_only(self):
        import json
        self.assertEqual(json.loads((PATCH / 'patch.json').read_text())['pack'], 'dev')


if __name__ == '__main__':
    unittest.main()
