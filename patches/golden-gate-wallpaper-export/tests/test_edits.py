"""patch.json "files" declares the two files apply.sh installs (whole files from the row), and apply.sh run on a fixture
Data volume installs exactly those, refuses an export from another build, and needs golden-gate-user-seed first.
python3.14 -I -B -m unittest discover -s patches/golden-gate-wallpaper-export/tests
  WALLPAPER_EXPORT_ARTIFACT   the wallpaper_export_golden_gate artifact folder: SHA256SUMS must match its files."""
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PATCH = Path(__file__).resolve().parents[1]
ROW = 'wallpaper_export_golden_gate'
SEED = 'Library/Application Support/PerUserSeed'
PATCH_JSON = json.loads((PATCH / 'patch.json').read_text())
FILES = {f['path']: f for f in PATCH_JSON['files']}
SHIMS = {'install': '#!/bin/sh\nn=\nfor a do shift; case $n$a in -o|-g) n=1;; 1*) n=;; *) set -- "$@" "$a";; esac; done\n'
                    'exec /usr/bin/install "$@"\n'}


class Declaration(unittest.TestCase):
    def test_whole_files_from_the_row(self):
        self.assertEqual(sorted(FILES), [SEED + '/wallpaper/Metadata.plist', SEED + '/wallpaper/Wallpaper.png'])
        for path, f in FILES.items():
            self.assertEqual((f['origin']['kind'], f['origin']['row']), ('m27', ROW))
            self.assertEqual(f['source'], path[len(SEED) + 1:])
        self.assertEqual(PATCH_JSON['inputs'], [ROW])


class Hook(unittest.TestCase):
    def run_hook(self, export_build='26B131', seed=True):
        d = Path(self.tmp.name)
        shims = d / 'shims'
        shims.mkdir(exist_ok=True)
        for name, text in SHIMS.items():
            (shims / name).write_text(text)
            (shims / name).chmod(0o755)
        art = d / 'artifact/wallpaper'
        art.mkdir(parents=True, exist_ok=True)
        (art / 'Metadata.plist').write_bytes(plistlib.dumps({'BuildVersion': export_build, 'IsSystemDefault': True},
                                                            fmt=plistlib.FMT_BINARY))
        (art / 'Wallpaper.png').write_bytes(b'\x89PNG picture')
        sysroot = d / 'sys/System/Library/CoreServices'
        sysroot.mkdir(parents=True, exist_ok=True)
        (sysroot / 'SystemVersion.plist').write_bytes(plistlib.dumps({'ProductBuildVersion': '26B131'}))
        data = d / 'data'
        shutil.rmtree(data, ignore_errors=True)
        data.mkdir()
        if seed:
            (data / SEED).mkdir(parents=True)
        folder = d / 'hook'
        shutil.rmtree(folder, ignore_errors=True)
        shutil.copytree(PATCH, folder, ignore=shutil.ignore_patterns('tests'))
        (folder / 'inputs').mkdir()
        (folder / 'inputs' / ROW).symlink_to(d / 'artifact')
        env = dict(os.environ, PATH='%s:/usr/bin:/bin:/usr/sbin:/sbin' % shims, DATA=str(data), SYS=str(d / 'sys'),
                   P=str(folder))
        r = subprocess.run(['sh', '-eu', str(folder / 'apply.sh')], capture_output=True, text=True, env=env)
        return r, data, d / 'artifact'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_installs_exactly_the_declared_files(self):
        r, data, art = self.run_hook()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        installed = sorted(str(p.relative_to(data)) for p in data.rglob('*') if p.is_file())
        self.assertEqual(installed, sorted(FILES))
        for path, f in FILES.items():
            self.assertEqual((data / path).read_bytes(), (art / f['source']).read_bytes())
            self.assertEqual(oct((data / path).stat().st_mode & 0o777), '0o644')

    def test_other_build_refused(self):
        r, data, _ = self.run_hook(export_build='26B123')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('the export is from 26B123, System is 26B131', r.stderr)
        self.assertFalse((data / SEED / 'wallpaper').exists())

    def test_needs_user_seed_first(self):
        r, _, _ = self.run_hook(seed=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('after golden-gate-user-seed', r.stderr)


@unittest.skipUnless(os.environ.get('WALLPAPER_EXPORT_ARTIFACT'), 'set WALLPAPER_EXPORT_ARTIFACT')
class Real(unittest.TestCase):
    def test_artifact(self):
        a = Path(os.environ['WALLPAPER_EXPORT_ARTIFACT'])
        for line in (a / 'SHA256SUMS').read_text().splitlines():
            digest, rel = line.split(None, 1)
            self.assertEqual(hashlib.sha256((a / rel).read_bytes()).hexdigest(), digest, rel)
        meta = plistlib.loads((a / 'wallpaper/Metadata.plist').read_bytes())
        self.assertEqual((meta['BuildVersion'], meta['IsSystemDefault']), ('26B131', True))
        self.assertEqual(sorted(str(p.relative_to(a)) for p in (a / 'wallpaper').iterdir()),
                         sorted(f['source'] for f in FILES.values()))


if __name__ == '__main__':
    unittest.main()
