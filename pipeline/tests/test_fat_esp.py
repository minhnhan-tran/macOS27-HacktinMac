"""The assembly's copies onto and from the ESP, run on a real FAT32 volume (a 64 MB disk image made with hdiutil).

bench2's QEMU stick install stopped at boot-and-esp, 90 minutes in: `ditto --rsrc --extattr --acl` onto the FAT
ESP failed with "Attribute not found" on every folder. The stick's Recovery runs no doubleagentd, the daemon that
keeps a FAT volume's extended attributes in ._ files, and FAT has no ACLs at all. golden-image.py's FAT_COPY (and
macos27tool-opencore's apply.sh) copy data only. This test runs FAT_COPY over a tree that carries an extended attribute and
an ACL, on FAT32, with only the Recovery's commands on PATH, and checks that no attribute or ACL reached the volume
(com.apple.provenance excepted: this Mac's kernel adds it to every file a tracked process writes, which a Recovery
does not). The control copy with the old flags fails or carries the attribute, so the check can tell them apart.
With M27_TEST_KIT=KIT (a staged kit), the kit's own ESP tree is copied the same way.
No APFS: the test makes and attaches only a FAT disk image, never mounted in Finder (-nobrowse).
"""
import filecmp
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
SCRIPT = TOP / 'pipeline' / 'stages' / 'golden-image.py'
spec = importlib.util.spec_from_file_location('golden_image', SCRIPT)
golden = importlib.util.module_from_spec(spec); spec.loader.exec_module(golden)
LIST = TOP / 'pipeline' / 'stages' / 'recovery-commands-25G342.txt'
KERNEL_ADDED = {'com.apple.provenance'}


def attributes(path):
    r = subprocess.run(['/usr/bin/xattr', str(path)], capture_output=True, text=True)
    return set(r.stdout.split()) - KERNEL_ADDED


def acl(path):
    r = subprocess.run(['/bin/ls', '-lde', str(path)], capture_output=True, text=True)
    return [line for line in r.stdout.splitlines()[1:] if line.strip()]


@unittest.skipUnless(shutil.which('hdiutil') and os.path.exists('/usr/bin/ditto'), 'needs hdiutil and ditto')
class FatEsp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='fat-esp-test-')
        root = Path(cls.tmp.name)
        cls.image, cls.mount = root / 'esp.dmg', root / 'mnt'
        subprocess.run(['hdiutil', 'create', '-quiet', '-size', '64m', '-fs', 'MS-DOS FAT32', '-volname', 'M27TEST',
                        '-layout', 'NONE', str(cls.image)], check=True)
        cls.mount.mkdir()
        subprocess.run(['hdiutil', 'attach', '-quiet', '-nobrowse', '-mountpoint', str(cls.mount), str(cls.image)],
                       check=True, stderr=subprocess.DEVNULL)
        # PATH as in the stick's Recovery (golden-assemble.sh M27_RECOVERY_COMMANDS)
        cls.bin = root / 'recovery-only'; cls.bin.mkdir()
        for line in LIST.read_text().splitlines():
            if line.strip() and not line.startswith('#') and os.path.exists(line.strip()):
                (cls.bin / os.path.basename(line.strip())).symlink_to(line.strip())
        cls.source = root / 'esp'
        plist = cls.source / 'EFI/OC/Kexts/Fixture.kext/Contents/Info.plist'
        plist.parent.mkdir(parents=True)
        plist.write_text('<plist/>\n')
        (cls.source / 'EFI/OC/config.plist').write_text('<plist/>\n')
        subprocess.run(['/usr/bin/xattr', '-w', 'com.example.m27-test', 'carried', str(plist)], check=True)
        subprocess.run(['/bin/chmod', '+a', 'everyone deny delete', str(cls.source / 'EFI/OC')], check=True)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['hdiutil', 'detach', '-quiet', '-force', str(cls.mount)], stderr=subprocess.DEVNULL)
        subprocess.run(['/bin/chmod', '-N', str(cls.source / 'EFI/OC')])   # the fixture's ACL denies its removal
        cls.tmp.cleanup()

    def copy(self, flags, source, destination):
        return subprocess.run([*flags, str(source), str(destination)], capture_output=True, text=True,
                              env={'PATH': str(self.bin), 'HOME': self.tmp.name})

    def assert_data_only(self, tree):
        for folder, dirs, files in os.walk(tree):
            for name in dirs + files:
                path = Path(folder) / name
                if name.startswith('._'):
                    continue   # this Mac's doubleagentd file for com.apple.provenance
                self.assertEqual(set(), attributes(path), path)
                self.assertEqual([], acl(path), path)

    def test_fat_copy_onto_the_esp(self):
        self.assertIn('/usr/bin/' + golden.FAT_COPY[0], LIST.read_text().split())
        destination = self.mount / 'onto'
        r = self.copy(golden.FAT_COPY, self.source, destination)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual('', r.stderr)
        cmp = filecmp.dircmp(self.source, destination, ignore=[n for n in os.listdir(destination) if n.startswith('._')])
        self.assertEqual(([], []), (cmp.left_only, cmp.diff_files))
        self.assert_data_only(destination)

    def test_the_old_flags_carry_what_a_recovery_cannot_write(self):
        destination = self.mount / 'old'
        r = self.copy(('ditto', '--rsrc', '--extattr', '--acl'), self.source, destination)
        plist = destination / 'EFI/OC/Kexts/Fixture.kext/Contents/Info.plist'
        self.assertTrue(r.returncode != 0 or (plist.exists() and 'com.example.m27-test' in attributes(plist)),
                        'the control copy wrote no attribute: the check above proves nothing')

    def test_fat_copy_from_the_esp(self):
        # export_efi: the final ESP copied off the FAT volume
        onto = self.mount / 'export'
        self.assertEqual(0, self.copy(golden.FAT_COPY, self.source, onto).returncode)
        out = Path(self.tmp.name) / 'boot-efi' / 'payload'
        r = self.copy(golden.FAT_COPY, onto, out)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual((out / 'EFI/OC/config.plist').read_text(), '<plist/>\n')

    @unittest.skipUnless(os.environ.get('M27_TEST_KIT'), 'M27_TEST_KIT=KIT copies a staged kit\'s ESP tree')
    def test_the_kits_esp(self):
        source = Path(os.environ['M27_TEST_KIT']) / 'assembly-source' / 'esp'
        destination = self.mount / 'kit'
        r = self.copy(golden.FAT_COPY, source, destination)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual('', r.stderr)
        self.assert_data_only(destination)


if __name__ == '__main__':
    unittest.main()
