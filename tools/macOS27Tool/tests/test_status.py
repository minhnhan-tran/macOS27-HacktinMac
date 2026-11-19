"""src/status.py: what hao status reads, and how it shows it."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import console as CON      # noqa: E402
import status as ST        # noqa: E402


class Pipe(io.StringIO):
    encoding = 'utf-8'

    def isatty(self):
        return False


class Host:
    OUT = {('sw_vers', '-productVersion'): '26.7', ('sw_vers', '-buildVersion'): '25G332',
           ('sysctl', '-n', 'hw.memsize'): str(16 << 30), ('sysctl', '-n', 'hw.model'): 'MacBookPro9,2',
           ('sysctl', '-n', 'machdep.cpu.brand_string'): 'Intel(R) Core(TM) i7-3615QM',
           ('nvram', 'boot-args'): 'boot-args\tamfi=0x80 -v'}

    def run(self, argv, check=True, timeout=120):
        out = self.OUT.get(tuple(argv))
        return subprocess.CompletedProcess(argv, 0 if out is not None else 1, (out or '').encode(), b'')

    def csr_config(self):
        return 0x7f

    def euid(self):
        return 0


class Status(unittest.TestCase):
    def gather(self, d, **files):
        state, logs = Path(d) / 'state', Path(d) / 'logs'
        state.mkdir()
        logs.mkdir()
        for name, data in files.items():
            (state if name == 'record' else logs).joinpath(
                'install-record.json' if name == 'record' else name + '.json').write_text(json.dumps(data))
        return ST.gather(Host(), env={'HAO_STICK': '/Volumes/HAO', 'HAO_LOGS': str(logs)}, state=state,
                         folders=[logs], aot_dirs=[Path(d) / 'none'])

    def test_reads_this_mac(self):
        with tempfile.TemporaryDirectory() as d:
            got = self.gather(d)
        self.assertEqual(got['where'], 'installer')
        self.assertEqual(got['macos'], '26.7 (25G332)')
        self.assertEqual(got['mac'], 'MacBookPro9,2 · Intel(R) Core(TM) i7-3615QM · 16 GB')
        self.assertEqual(got['sip'], 'filesystem protection off (csr 0x7f)')
        self.assertEqual(got['boot_args'], 'amfi=0x80 -v')
        self.assertEqual(got['installed'], 'no install record')
        self.assertEqual(got['last_stop'], 'none')

    def test_install_record_and_last_stop(self):
        with tempfile.TemporaryDirectory() as d:
            got = self.gather(d, record={'date': '2026-11-16', 'release': 'v0.0.1', 'packs': ['base', 'mbp92']},
                              **{'install-20261116-1823': {'command': 'install', 'events': [
                                  {'ev': 'run.end', 'ok': False, 'what': 'the build onto /dev/disk0 failed'}]}})
        self.assertEqual(got['installed'], '2026-11-16 · v0.0.1 · base, mbp92')
        self.assertIn('install on ', got['last_stop'])
        self.assertTrue(got['last_stop'].endswith(': the build onto /dev/disk0 failed'))

    def test_plain_lines_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            got = self.gather(d)
        out = Pipe()
        ST.show(CON.Console('steps', out=out, env={}), got)
        self.assertIn('Runs on      the USB installer', out.getvalue())
        self.assertIn('SIP          filesystem protection off (csr 0x7f)', out.getvalue())
        out = Pipe()
        ST.show(CON.Console('json', out=out, env={}), got)
        ev = json.loads(out.getvalue())
        self.assertEqual((ev['ev'], ev['where']), ('status', 'installer'))


if __name__ == '__main__':
    unittest.main()
