"""The producers run from a release archive, a folder with no .git: `git archive HEAD` of this checkout, with `store`
linked to this checkout's store. Four assets (asset-build) and two Tahoe donor sets (tools/tahoe-donors/make) built
there equal their rows, and each build record names the archive's commit.

  ASSET_BUILD_COMPILE=1 python3.14 -I -B -m unittest discover -s tools/asset-build/tests -p test_from_archive.py
  TAHOE_DONOR_INPUTS=DIR (the 25G342 minimal-26B131 inputs), TAHOE_DONOR_TREE=DIR (the full unpacked tree) and
  TAHOE_DONOR_CACHE=DIR (the x86_64h OS cache) add the donor sets."""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[3]
GIT = shutil.which('git') and (TOP/'.git').exists()
DONORS = tuple(os.environ.get('TAHOE_DONOR_' + name) for name in ('INPUTS', 'TREE', 'CACHE'))


def head():
    return subprocess.check_output(['git', '-C', str(TOP), 'rev-parse', 'HEAD'], text=True).strip()


@unittest.skipUnless(GIT and shutil.which('xcrun') and os.environ.get('ASSET_BUILD_COMPILE'),
                     'needs this checkout, Xcode and ASSET_BUILD_COMPILE=1')
class FromArchive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.src = Path(cls.tmp.name)/'m27-patcher'
        cls.src.mkdir()
        archive = subprocess.run(['git', '-C', str(TOP), 'archive', '--format=tar', 'HEAD'], capture_output=True,
                                 check=True).stdout
        subprocess.run(['tar', '-x', '-C', str(cls.src)], input=archive, check=True)
        if (TOP/'store').exists():
            (cls.src/'store').symlink_to((TOP/'store').resolve())
        cls.commit = head()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_tool(self, *args):
        r = subprocess.run([sys.executable, '-I', *map(str, args)], cwd=self.src, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return r.stdout

    def test_the_archive_has_no_git_and_names_its_commit(self):
        self.assertFalse((self.src/'.git').exists())
        self.assertEqual((self.src/'pipeline/SOURCE_COMMIT').read_text().strip(), self.commit)

    def test_assets_equal_their_rows(self):
        for row in ('display_state_shim', 'io80211_compat', 'fl', 'ripatch'):
            out = Path(self.tmp.name)/('asset-' + row)
            line = self.run_tool(self.src/'tools/asset-build/asset-build', row, out).split()
            self.assertEqual(line[1], row)   # asset-build exits 1 unless the sha256 is the row's

    @unittest.skipUnless(all(DONORS), 'set TAHOE_DONOR_INPUTS, TAHOE_DONOR_TREE and TAHOE_DONOR_CACHE')
    def test_donor_sets_equal_their_rows(self):
        inputs, tree, cache = map(Path, DONORS)
        common = ['--metadata', inputs/'lists/sys.tsv', '--links', inputs/'zip/AssetData/payloadv2/links.txt']
        runs = {'tahoe_native_donors': ['native', '--recipe', inputs/'recipe-cache-images-awdl.json',
                                        '--tree', tree, '--cache', cache, *common],
                'tahoe_extensions_donors': ['extensions', '--recipe', inputs/'recipe.json', '--tree', inputs/'tree',
                                            *common]}
        sys.path.insert(0, str(self.src/'pipeline'))
        import lock
        locks = str(self.src/'pipeline/inputs/golden/26B131')
        for row, args in runs.items():
            out = Path(self.tmp.name)/row
            self.run_tool(self.src/'tools/tahoe-donors/make', *args[:1], '--output', out, *args[1:])
            want = lock.rows(locks)[row][0]
            self.assertEqual(lock.row_hash(locks, row, str(out)), want, row)
            record = json.loads((out/'build.json').read_text())
            self.assertEqual((record['commit'], record['dirty']), (self.commit, False), row)


if __name__ == '__main__':
    unittest.main()
