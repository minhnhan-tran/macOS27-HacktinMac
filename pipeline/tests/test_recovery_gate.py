"""No image profile installs the end-user Recovery on the image's own Recovery volume (rec-install-volume) yet.

That Recovery boots through the image's OpenCore, so it gets hao-opencore's boot-args, amfi_force_policy=8 among them.
With it, entitled programs count as restricted and dyld ignores DYLD_INSERT_LIBRARIES, so the Recovery's helpers that
insert a library (rec-wifi-autojoin into airportd, rec-language-list's lcwrap) would silently do nothing. The installer
stick's Recovery is not affected: it boots OCLP's config with amfi=0x80 (tools/macOS27Tool/src/stick.py).

Until the helpers work there (their own boot-args, or no DYLD_INSERT_LIBRARIES) and a QEMU boot shows it, a profile
naming rec-install-volume fails here. The work is the patcher item "Image Recovery helpers under amfi_force_policy=8";
when it is done, this test is replaced by that check.

python3.14 -I -B -m unittest pipeline/tests/test_recovery_gate.py
"""
from pathlib import Path
import unittest

TOP = Path(__file__).resolve().parents[2]
GATED = 'rec-install-volume'


def set_recipes(path):
    return [line.split('#', 1)[0].strip() for line in path.read_text().splitlines() if line.split('#', 1)[0].strip()]


class RecoveryGate(unittest.TestCase):
    def test_no_profile_installs_the_image_recovery(self):
        profiles = sorted((TOP / 'pipeline/profiles').glob('*.json'))
        self.assertTrue(profiles)
        for profile in profiles:
            with self.subTest(profile=profile.stem):
                self.assertNotIn(GATED, profile.read_text(),
                                 '%s names %s: its Recovery helpers need their amfi_force_policy=8 fix first'
                                 % (profile.name, GATED))
                generated = TOP / 'pipeline/sets' / (profile.stem + '.txt')
                if generated.is_file():
                    self.assertNotIn(GATED, set_recipes(generated),
                                     '%s installs %s: its Recovery helpers need their amfi_force_policy=8 fix first'
                                     % (generated.name, GATED))

    def test_the_gate_names_a_real_recipe(self):
        # If the recipe is renamed, this test must follow it rather than pass vacuously.
        self.assertTrue((TOP / 'patches' / GATED / 'patch.json').is_file())


if __name__ == '__main__':
    unittest.main()
