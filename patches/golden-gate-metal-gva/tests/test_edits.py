import hashlib, importlib.util, os, subprocess, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('edit_gva', HERE / 'src/edit-gva.py')
MOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MOD)


def fake_macho():
    hdr = MOD.MH_MAGIC_64.to_bytes(4, 'little') + b'\x07\x00\x00\x01' + b'\x00' * 24
    return hdr + b'\x90' * 64 + MOD.BEFORE + b'\x41\x55\x41\x54\xc3'


class EditGvaTests(unittest.TestCase):
    def test_patches_unique_prologue(self):
        out = MOD.edit_bytes(fake_macho())
        self.assertIn(MOD.AFTER, out)
        self.assertNotIn(MOD.BEFORE, out)
        self.assertEqual(len(out), len(fake_macho()))

    def test_refuses_duplicate_or_missing_prologue(self):
        with self.assertRaises(ValueError):
            MOD.edit_bytes(MOD.MH_MAGIC_64.to_bytes(4, 'little') + b'\x00' * 64)
        with self.assertRaises(ValueError):
            MOD.edit_bytes(fake_macho() + MOD.BEFORE)

    def test_refuses_wrong_input_hash(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / 'in', Path(d) / 'out'
            src.write_bytes(fake_macho())
            r = subprocess.run([os.sys.executable, str(HERE / 'src/edit-gva.py'), str(src), str(dst)],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('unexpected AppleGVA input sha256', r.stderr)

    @unittest.skipUnless(os.environ.get('GVA_OCLP_APPLEGVA'), 'set GVA_OCLP_APPLEGVA=PATH to OCLP AppleGVA')
    def test_real_oclp_applegva(self):
        src = Path(os.environ['GVA_OCLP_APPLEGVA'])
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d) / 'AppleGVA'
            subprocess.check_call([os.sys.executable, str(HERE / 'src/edit-gva.py'), str(src), str(dst), '--sign'])
            self.assertEqual(hashlib.sha256(dst.read_bytes()).hexdigest(), MOD.EXPECT_OUT)
            nm = subprocess.check_output(['nm', '-U', str(dst)], text=True)
            self.assertIn(MOD.SYMBOL, nm)


if __name__ == '__main__':
    unittest.main()
