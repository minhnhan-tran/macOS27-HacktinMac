"""The macpro71 image: target macpro71 (MacPro7,1 on an Intel PC: Polaris GPU Tahoe drives itself, no OCLP patch sets), its profile, set and assembly,
generated and planned.
python3.14 -I -B -m unittest discover -s pipeline/tests -k macpro71"""
import contextlib
import io
import json
from pathlib import Path
import runpy
import unittest

TOP = Path(__file__).resolve().parents[2]
P = runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))
TARGET = TOP / 'pipeline' / 'targets' / 'macpro71.json'
PROFILE = TOP / 'pipeline' / 'profiles' / 'macpro71.json'
SET = TOP / 'pipeline' / 'sets' / 'macpro71.txt'
ASSEMBLY = TOP / 'pipeline' / 'assemblies' / 'macpro71-26B131.json'
PLATFORM_KINDS = ('gpu', 'wifi', 'audio')


class Macpro71Target(unittest.TestCase):
    def test_a_pc_started_as_a_mac_pro(self):
        t = json.loads(TARGET.read_text())
        self.assertEqual(t['model'], 'MacPro7,1')
        self.assertEqual(t['board_id'], 'Mac-27AD2F918AE68F61')
        self.assertEqual((t['facts']['gpu'], t['facts']['firmware']), (['amd-polaris'], 'none'))
        for flag in ('SSE4.2', 'POPCNT', 'AVX1.0', 'AVX2'):
            self.assertIn(flag, t['facts']['cpu']['flags'])
        self.assertEqual(P['oclp_patchsets'](TARGET), set())       # an explicit empty list, not a missing profile

    def test_detects_only_the_os_layer(self):
        packs = P['load_packs']()
        found = P['detect_packs'](TARGET, packs)
        self.assertEqual(found, ['base'])
        self.assertEqual([n for n in found if packs[n].get('layer') != 'os'], [])

    def test_cpu_facts_come_from_the_target_without_an_oclp_profile(self):
        self.assertIn('sse4.2', P['target_facts'](str(TARGET))['cpu'])
        self.assertIn('avx2', P['target_facts'](str(TARGET))['cpu'])


class Macpro71Profile(unittest.TestCase):
    def setUp(self):
        self.packs = P['load_packs']()

    def test_generates_the_os_layer_and_the_model_pack_only(self):
        got = P['generate'](str(PROFILE))
        owner = {r: n for n, p in self.packs.items() for r in p.get('recipes', [])}
        self.assertTrue(set(self.packs['macpro71']['recipes']) <= set(got))
        self.assertTrue(self.packs['macpro71'].get('opt_in'), 'the macpro71 pack is selected by name only')
        for name in got:
            pack = self.packs[owner[name]]
            self.assertFalse(pack.get('layer') == 'platform' and pack.get('kind') in PLATFORM_KINDS, name)
            self.assertFalse(pack.get('kind') == 'model' and owner[name] != 'macpro71', name)
        # the OS layer exactly as the 9,2 alpha image has it
        alpha = P['generate'](str(TOP / 'pipeline' / 'profiles' / 'alpha-v0.1.0.json'))
        os_layer = [n for n in alpha if self.packs[owner[n]].get('layer') == 'os']
        self.assertEqual([n for n in got if owner[n] != 'macpro71'], os_layer)

    def test_the_set_is_the_generated_profile(self):
        got = P['generate'](str(PROFILE))
        self.assertEqual(got, P['names'](str(SET)))
        # the macpro71 model's OpenCore part right before macos27tool-opencore, as opencore-mbp92 is on the 9,2
        self.assertEqual(got.index('opencore-macpro71') + 1, got.index('macos27tool-opencore'))
        self.assertNotIn('opencore-mbp92', got)

    def test_plans_for_the_builds(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rows = P['plan'](str(SET), str(TARGET), '26B131/25G342', locks=None,
                             packs=P['detect_packs'](TARGET, self.packs), pack_defs=self.packs)
        names = [n for _, n, _ in rows]
        self.assertIn('darwin27-kernel', names)
        self.assertTrue(set(self.packs['macpro71']['recipes']) <= set(names))   # opt-in: named by the set

    def test_the_assembly(self):
        a = json.loads(ASSEMBLY.read_text())
        self.assertEqual((a['target'], a['patch_set'], a['repo']), ('macpro71', 'macpro71', 'store/images/macpro71-26B131'))
        self.assertEqual(Path(P['set_path'](a['patch_set'])).resolve(), PROFILE.resolve())   # generated: sets/macpro71.txt
        self.assertNotIn('hardware', a['inputs'])        # no OCLP patch sets: no hardware overlay (golden-run)
        self.assertEqual(a['golden_build'] + '/' + a['donor_build'], '26B131/25G342')

    def test_the_9_2_profiles_do_not_change(self):
        for prof in sorted((TOP / 'pipeline' / 'profiles').glob('*.json')):
            if json.loads(prof.read_text())['target'] != 'mbp92':
                continue
            self.assertEqual(P['generate'](str(prof)), P['names'](str(TOP / 'pipeline' / 'sets' / (prof.stem + '.txt'))),
                             prof.stem)


if __name__ == '__main__':
    unittest.main()
