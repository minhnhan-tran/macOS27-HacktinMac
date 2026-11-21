"""patch.json "files" declares what src/install.py installs, and the row's two files are the bytes tools/live-text
produces from OCLP's service. install.py installs those prebuilt files whole, so both are origin entries: the load command (LC_LOAD_DYLIB, timestamp 0) and the ad-hoc re-signature are made
by tools/live-text/lib/compiler_service.py when the row is built, and the service is a thin x86_64 file.
python3.14 -I -B -m unittest discover -s patches/metal-compiler-compat/tests

Always: the declared paths and rows match install.py and patch.json. With real inputs it also runs
compiler_service.build on OCLP's service and compares the bytes with the row:
  METAL_COMPILER_COMPAT_ROW      the row's compiler-service/ folder (build.json and replace/)
  METAL_COMPILER_COMPAT_SOURCE   folder with OCLP's MTLCompilerService, Info.plist and CodeResources
  METAL_COMPILER_COMPAT_ADAPTER  the compiler-adapters/ folder holding compat.dylib and build.json
The third file, OCLP's GPUCompiler 31001 libLLVM.dylib, is edited on the System (edits/libllvm-31001.json): the hook's
llvm_helper_path.py and the edit engine write the same bytes, on a synthetic file always and on OCLP's file with
  METAL_COMPILER_COMPAT_LIBLLVM  OCLP's 31001 libLLVM.dylib (the hardware row's payload)"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = load('compat_install', PATCH/'src/install.py')
helper = load('llvm_helper_path', PATCH/'src/llvm_helper_path.py')
sys.path.insert(0, str(TOP/'tools/edit-engine/src'))
import engine   # noqa: E402
import spec as S   # noqa: E402
LLVM_SPEC = S.load(PATCH/'edits/libllvm-31001.json')
PATCH_JSON = json.loads((PATCH/'patch.json').read_text())
FILES = {f['path']: f for f in PATCH_JSON['files']}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Declaration(unittest.TestCase):
    def test_files_are_install_py_s_two_and_the_edited_libllvm(self):
        self.assertEqual(set(FILES), {install.SERVICE, install.LIBRARY, LLVM_SPEC['path']})
        self.assertEqual(FILES[LLVM_SPEC['path']], {'path': LLVM_SPEC['path'], 'edits': 'edits/libllvm-31001.json',
                                                     'install': {'mode': '0755', 'owner': 'root:wheel', 'flags': ['restricted']}})

    def test_every_installed_file_is_a_whole_file_from_the_patch_row(self):
        for f in (FILES[install.SERVICE], FILES[install.LIBRARY]):
            self.assertNotIn('edits', f)
            self.assertEqual(f['origin']['kind'], 'm27')
            self.assertIn(f['origin']['row'], PATCH_JSON['inputs'])
            self.assertTrue(f['origin'].get('note'))

    def test_hook_installs_through_install_py_and_signs_only_libllvm(self):
        hook = (PATCH/'src/kc-hook.sh').read_text()
        self.assertIn('install.py', hook)
        self.assertIn('llvm_helper_path.py', hook)
        self.assertEqual([l.split()[:5] for l in hook.splitlines() if l.startswith('codesign')],
                         [['codesign', '-f', '-s', '-', '-i']])
        self.assertIn('-i %s ' % LLVM_SPEC['sign']['identifier'], hook)

    def test_libllvm_spec_follows_the_format(self):
        self.assertEqual(S.validate(LLVM_SPEC), [])


def synthetic_libllvm():
    """A thin x86_64 MH_DYLIB whose second load command is OCLP's relative helper LC_LOAD_DYLIB (cmdsize 128, name at
    24, NUL padded), at the real file's offset 0x768 so the spec's `at` applies."""
    name = helper.OLD + b'\0' * 4
    helper_lc = struct.pack('<6I', 0xc, 128, 24, 2, 0, 0) + name
    first = 0x768 - 32
    filler = struct.pack('<2I', 0x2a, first) + b'\0' * (first - 8)   # LC_SOURCE_VERSION-sized placeholder
    cmds = filler + helper_lc
    head = struct.pack('<8I', 0xfeedfacf, 0x01000007, 3, 6, 2, len(cmds), 0, 0)
    return head + cmds + b'\0' * 64


class LibLLVM(unittest.TestCase):
    def test_same_bytes_script_and_engine_synthetic(self):
        data = synthetic_libllvm()
        script, at = helper.edit(data)
        self.assertEqual(at, 0x780)
        out, _ = engine.apply(LLVM_SPEC, data, repo=str(TOP))
        self.assertEqual(script, out)
        self.assertEqual(len(out), len(data))
        self.assertIn(b'\0/' + helper.OLD + b'\0', out)

    def test_refuses_an_edited_or_other_file(self):
        once, _ = helper.edit(synthetic_libllvm())
        with self.assertRaises(AssertionError):
            helper.edit(once)
        with self.assertRaises(AssertionError):
            helper.edit(synthetic_libllvm().replace(b'Versions/31001', b'Versions/31002'))

    @unittest.skipUnless(os.environ.get('METAL_COMPILER_COMPAT_LIBLLVM'), 'set METAL_COMPILER_COMPAT_LIBLLVM')
    def test_same_bytes_real_and_signed_as_expected(self):
        data = Path(os.environ['METAL_COMPILER_COMPAT_LIBLLVM']).read_bytes()
        self.assertIn(sha(data), LLVM_SPEC['expect'])
        script, _ = helper.edit(data)
        out, _ = engine.apply(LLVM_SPEC, data, repo=str(TOP))
        self.assertEqual(script, out)
        if shutil.which('codesign'):
            with tempfile.TemporaryDirectory() as tmp:
                f = Path(tmp)/'libLLVM.dylib'
                f.write_bytes(script)
                subprocess.run(['codesign', '-f', '-s', '-', '-i', LLVM_SPEC['sign']['identifier'], f], check=True,
                               capture_output=True)
                self.assertEqual(sha(f.read_bytes()), LLVM_SPEC['expect'][sha(data)])


@unittest.skipUnless(os.environ.get('METAL_COMPILER_COMPAT_ROW'), 'set METAL_COMPILER_COMPAT_ROW')
class RealRow(unittest.TestCase):
    def setUp(self):
        self.row = Path(os.environ['METAL_COMPILER_COMPAT_ROW'])
        self.build = json.loads((self.row/'build.json').read_text())

    def test_row_holds_exactly_the_declared_files(self):
        self.assertEqual(set(self.build['files']), set(FILES))
        for name, expected in self.build['files'].items():
            self.assertEqual(sha((self.row/'replace'/name).read_bytes()), expected)

    def test_service_has_the_appended_load_command(self):
        data = (self.row/'replace'/install.SERVICE).read_bytes()
        self.assertEqual(struct.unpack_from('<I', data, 0)[0], 0xfeedfacf, 'thin 64-bit Mach-O, no fat header')
        self.assertEqual(struct.unpack_from('<I', data, 4)[0], 0x01000007, 'x86_64')
        at, found = 32, []
        for _ in range(struct.unpack_from('<I', data, 16)[0]):
            cmd, size = struct.unpack_from('<II', data, at)
            if cmd == 0xc:
                offset, timestamp, current, compat = struct.unpack_from('<IIII', data, at + 8)
                found.append((data[at + offset:at + size].split(b'\0')[0].decode(), timestamp, current, compat))
            at += size
        self.assertEqual(found[-1], ('/' + install.LIBRARY, 0, 0, 0))

    @unittest.skipUnless(os.environ.get('METAL_COMPILER_COMPAT_SOURCE') and os.environ.get('METAL_COMPILER_COMPAT_ADAPTER'),
                         'set METAL_COMPILER_COMPAT_SOURCE and METAL_COMPILER_COMPAT_ADAPTER')
    def test_same_bytes_as_compiler_service_build(self):
        producer = load('compiler_service', TOP/'tools/live-text/lib/compiler_service.py')
        source = Path(os.environ['METAL_COMPILER_COMPAT_SOURCE'])
        self.assertEqual(sha((source/'MTLCompilerService').read_bytes()), self.build['source_service_sha256'])
        with tempfile.TemporaryDirectory() as tmp:
            record = producer.build(source, os.environ['METAL_COMPILER_COMPAT_ADAPTER'], Path(tmp)/'out')
            for name in FILES:
                self.assertEqual((Path(tmp)/'out/replace'/name).read_bytes(), (self.row/'replace'/name).read_bytes(), name)
            self.assertEqual(record['files'], self.build['files'])


if __name__ == '__main__':
    unittest.main()
