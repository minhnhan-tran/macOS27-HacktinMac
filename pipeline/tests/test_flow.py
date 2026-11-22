"""pipeline/flow: three inputs (macOS 27 version, macOS 26 version, hardware report) to a target, profiles and assembly."""
import importlib.machinery, importlib.util, json, os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures', 'report-msi-b460m-i5-10400.json')
_l = importlib.machinery.SourceFileLoader('flow', os.path.join(os.path.dirname(HERE), 'flow'))
flow = importlib.util.module_from_spec(importlib.util.spec_from_loader('flow', _l))
_l.exec_module(flow)


class Flow(unittest.TestCase):
    def report(self, **cpu):
        h = flow.read_report(FIX)
        h.update(cpu)
        return h

    def test_versions(self):
        self.assertEqual(flow.build_of('27.2.1-26B131', 'x'), '26B131')
        self.assertEqual(flow.build_of('25G342', 'x'), '25G342')
        self.assertEqual(flow.build_of('26.7.3', 'x'), '25G342')
        with self.assertRaises(flow.Refused):
            flow.build_of('27.9', 'x')

    def test_the_msi_report_is_a_macpro71(self):
        g = flow.judge(self.report())
        self.assertEqual(g['gpu_family'], 'amd-polaris')
        self.assertEqual(g['gpu_name'], 'Radeon RX 580 Series')

    def test_machines_it_refuses(self):
        for change in (dict(simd='SSE4.2, AVX'), dict(platform='Mobile'), dict(cpu_vendor='AMD'), dict(gpus={})):
            with self.assertRaises(flow.Refused, msg=change):
                flow.judge(self.report(**change))

    def test_a_missing_input_folder_is_named(self):
        with self.assertRaises(flow.Refused) as e:
            flow.need_inputs('26B999', '25G342')
        self.assertIn('26B999', str(e.exception))

    def test_files_follow_the_report_and_the_versions(self):
        h = self.report()
        out = flow.files('pc', h, flow.judge(h), '26B131', '25G342')
        t = out['pipeline/targets/pc.json']
        self.assertEqual((t['model'], t['facts']['gpu']), ('MacPro7,1', ['amd-polaris']))
        self.assertEqual(t['facts']['cpu']['class'], 'Comet Lake')
        self.assertIn('AVX2', t['facts']['cpu']['flags'])
        self.assertEqual(out['pipeline/profiles/pc.json']['target'], 'pc')
        a = out['pipeline/assemblies/pc-26B131.json']
        self.assertEqual((a['target'], a['golden_build'], a['donor_build'], a['repo']),
                         ('pc', '26B131', '25G342', 'store/images/pc-26B131'))

    def test_unpinned_kext_devices_are_listed(self):
        found = {flow.NEEDS_KEXT[i] for i in self.report()['ids'] if i in flow.NEEDS_KEXT}
        self.assertEqual(len(found), 4)


if __name__ == '__main__':
    unittest.main()
