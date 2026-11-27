"""golden-gate-amd-metal-polaris: the kc-hook installs exactly the files patch.json declares, from the row, and refuses a
System without the AMD bundles; only the macpro71 set holds the recipe (x86_64h code: AVX2/BMI, never Ivy Bridge).
python3 -m unittest discover -s patches/golden-gate-amd-metal-polaris/tests"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

UNIT = pathlib.Path(__file__).resolve().parents[1]
TOP = UNIT.parents[1]
NAME = UNIT.name
PATCH = json.loads((UNIT / 'patch.json').read_text())


def artifact():
    """The row's store folder (pipeline/inputs/25G342/amd_metal_polaris), present or not."""
    for line in (TOP / 'pipeline/inputs/25G342/amd_metal_polaris').read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            return TOP / line.split()[1]
    raise AssertionError('row amd_metal_polaris has no artifact line')


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        # The test runs unprivileged: chown and chflags only record their calls.
        tools = self.tmp / 'bin'
        tools.mkdir()
        for tool in ('chown', 'chflags'):
            (tools / tool).write_text('#!/bin/sh\necho "%s $*" >> "%s"\n' % (tool, self.tmp / 'calls'))
            os.chmod(tools / tool, 0o755)
        self.env = dict(os.environ, PATH=f'{tools}:/usr/bin:/bin:/usr/sbin:/sbin')
        self.hook = self.tmp / 'hook'
        (self.hook / 'inputs').mkdir(parents=True)
        shutil.copy2(UNIT / 'src/kc-hook.sh', self.hook / 'kc-hook.sh')
        self.system = self.tmp / 'mnt'
        self.system.mkdir()

    def run_hook(self):
        return subprocess.run(['sh', '-eu', str(self.hook / 'kc-hook.sh'), str(self.system)],
                              env=self.env, capture_output=True, text=True)

    def test_refuses_a_system_without_the_bundles(self):
        r = self.run_hook()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('extensions layer', r.stderr)

    def test_installs_exactly_the_declared_files(self):
        art = artifact()
        if not art.is_dir():
            self.skipTest(f'row amd_metal_polaris is not in the store ({art})')
        os.symlink(art, self.hook / 'inputs/amd_metal_polaris')
        for bundle in ('AMDMTLBronzeDriver.bundle', 'AMDShared.bundle'):
            contents = self.system / 'System/Library/Extensions' / bundle / 'Contents'
            contents.mkdir(parents=True)
            (contents / 'Info.plist').write_text('<plist/>')
        r = self.run_hook()
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = (self.tmp / 'calls').read_text().splitlines()
        for f in PATCH['files']:
            dest = self.system / f['path']
            self.assertEqual(dest.read_bytes(), (art / 'root' / f['path']).read_bytes(), f['path'])
            self.assertEqual(dest.stat().st_mode & 0o7777, int(f['install']['mode'], 8), f['path'])
            self.assertTrue(any(c.startswith(f'chown root:wheel {dest}.new.') for c in calls), f['path'])
            self.assertIn(f'chflags restricted {dest}', calls)
        installed = sorted(str(p.relative_to(self.system)) for p in self.system.rglob('*')
                           if p.is_file() and p.name != 'Info.plist')
        self.assertEqual(installed, sorted(f['path'] for f in PATCH['files']))


class Sets(unittest.TestCase):
    def test_only_the_macpro71_set_holds_the_recipe(self):
        holders = sorted(p.name for p in (TOP / 'pipeline/sets').glob('*.txt')
                         if any(line.split('#')[0].split() == [NAME] for line in p.read_text().splitlines()))
        self.assertEqual(holders, ['macpro71.txt'])
        self.assertEqual(PATCH['pack'], 'macpro71')
        self.assertEqual(PATCH['requires']['gpu'], ['amd-polaris'])


if __name__ == '__main__':
    unittest.main()
