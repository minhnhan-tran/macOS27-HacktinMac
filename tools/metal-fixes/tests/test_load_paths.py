"""Every Mach-O of the HD 4000 Metal stack the release image installs links its libraries by a path dyld follows in a
restricted, hardened program (MTLCompilerService runs with CS_RESTRICT|CS_RUNTIME, and macos27tool-opencore's AMFI dyld policy
makes dyld refuse relative load paths there).
python3.14 -I -B -m unittest discover -s tools/metal-fixes/tests -p test_load_paths.py

The files are the installed bytes of the release assembly (mbp92-26B131-alpha-v0.1.0, set alpha-v0.1.0): OCLP's
hardware overlay row (oclp_hardware, its install map) under the Metal, MTLCompiler and GPUCompiler frameworks (the
compiler service included) and the HD 4000 driver bundles, with every path a recipe of the set declares there made by
pipeline/materialize.py (whole files from rows, edit specs applied). Each LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB,
LC_REEXPORT_DYLIB, LC_LAZY_LOAD_DYLIB and LC_LOAD_UPWARD_DYLIB path must start with /, @loader_path/ or
@executable_path/; @rpath paths are listed with the file's LC_RPATHs. Skips when the overlay row's payload is not in
the store (CI has no store)."""
import os
from pathlib import Path
import runpy
import struct
import sys
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[3]
ASSEMBLY = {'set': 'alpha-v0.1.0', 'target': 'mbp92', 'golden': '26B131', 'donor': '25G342'}
SCOPE = (
    'System/Library/Frameworks/Metal.framework/',
    'System/Library/PrivateFrameworks/MTLCompiler.framework/',
    'System/Library/PrivateFrameworks/GPUCompiler.framework/',
    'System/Library/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/',
    'System/Library/Extensions/AppleIntelHD4000GraphicsGLDriver.bundle/',
    'System/Library/Extensions/AppleIntelHD4000GraphicsVADriver.bundle/',
    'System/Library/Extensions/AppleIntelIVBVA.bundle/',
    'System/Library/Extensions/AppleIntelGraphicsShared.bundle/',
)
LINKS = {0xc: 'LC_LOAD_DYLIB', 0x80000018: 'LC_LOAD_WEAK_DYLIB', 0x8000001f: 'LC_REEXPORT_DYLIB',
         0x20: 'LC_LAZY_LOAD_DYLIB', 0x80000023: 'LC_LOAD_UPWARD_DYLIB'}
LC_RPATH = 0x8000001c
FOLLOWED = ('/', '@loader_path/', '@executable_path/')


def in_scope(path):
    return path.startswith(SCOPE)


def slices(data):
    """File offsets of the 64-bit Mach-O slices of DATA (thin or universal); [] for anything else."""
    magic = struct.unpack_from('<I', data, 0)[0] if len(data) >= 4 else 0
    if magic == 0xfeedfacf:
        return [0]
    if data[:4] in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        wide = data[3] == 0xbf
        n = struct.unpack_from('>I', data, 4)[0]
        out = []
        for i in range(n):
            if wide:
                off = struct.unpack_from('>Q', data, 8 + i * 32 + 8)[0]
            else:
                off = struct.unpack_from('>I', data, 8 + i * 20 + 8)[0]
            if struct.unpack_from('<I', data, off)[0] == 0xfeedfacf:
                out.append(off)
        return out
    return []


def links(data):
    """[(slice offset, command name, path)] and {slice offset: [rpath]} of every 64-bit slice of DATA."""
    found, rpaths = [], {}
    for base in slices(data):
        ncmds = struct.unpack_from('<I', data, base + 16)[0]
        at = base + 32
        rpaths[base] = []
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', data, at)
            if cmd in LINKS or cmd == LC_RPATH:
                name_at = at + struct.unpack_from('<I', data, at + 8)[0]
                name = data[name_at:at + size].split(b'\0')[0].decode('utf-8', 'replace')
                if cmd == LC_RPATH:
                    rpaths[base].append(name)
                else:
                    found.append((base, LINKS[cmd], name))
            at += size
    return found, rpaths


def problems(files):
    """(relative: [line], rpath: [line]) for FILES {installed path: bytes}."""
    relative, rpath = [], []
    for path in sorted(files):
        found, rpaths = links(files[path])
        for base, cmd, name in found:
            where = '%s (slice at %#x) %s %s' % (path, base, cmd, name)
            if name.startswith('@rpath/'):
                rpath.append('%s; LC_RPATH %s' % (where, rpaths[base] or 'none'))
            elif not name.startswith(FOLLOWED):
                relative.append(where)
    return relative, rpath


def installed_files():
    """{installed path: bytes} of the Metal-stack files of the release assembly, or None without the store."""
    materialize = runpy.run_path(str(TOP/'pipeline/materialize.py'))
    rows = materialize['repo_rows'](ASSEMBLY['donor'], ASSEMBLY['golden'])
    art = rows.get('oclp_hardware')
    if not art or not (Path(art)/'install-map.json').is_file() or not (Path(art)/'payload').is_dir():
        return None
    sources = materialize['Sources'].from_repo(rows)
    files = {}
    for dest, _, rel in sources.oclp_map:              # map order: a later entry overwrites an earlier one
        root = Path(sources.oclp_payload, rel)
        if not (in_scope(dest + '/') or any(s.startswith(dest.rstrip('/') + '/') for s in SCOPE)):
            continue
        walk = [root] if root.is_file() else sorted(root.rglob('*')) if root.is_dir() else []
        for p in walk:
            if p.is_symlink() or not p.is_file():
                continue
            path = dest if p == root else '%s/%s' % (dest, p.relative_to(root))
            if in_scope(path):
                files[path] = p.read_bytes()
    with tempfile.TemporaryDirectory() as out:
        ledger = runpy.run_path(str(TOP/'pipeline/ledger.py'))['build'](
            str(TOP/'pipeline/sets'/(ASSEMBLY['set'] + '.txt')), str(TOP/'pipeline/targets'/(ASSEMBLY['target'] + '.json')),
            '%s/%s' % (ASSEMBLY['golden'], ASSEMBLY['donor']))
        declared = [p for p in ledger if in_scope(p) or any(s.startswith(p.rstrip('/') + '/') for s in SCOPE)]
        report, _ = materialize['run'](
            str(TOP/'pipeline/sets'/(ASSEMBLY['set'] + '.txt')), str(TOP/'pipeline/targets'/(ASSEMBLY['target'] + '.json')),
            ASSEMBLY['donor'], out, golden=ASSEMBLY['golden'], only=declared)
        unmade = [(r['path'], r['outcome'], r.get('reason')) for r in report if r['outcome'] not in ('made',)]
        for pack in Path(out).iterdir():
            made = pack/'files'
            if made.is_dir():
                for p in made.rglob('*'):
                    rel = str(p.relative_to(made))
                    if p.is_file() and not p.is_symlink() and in_scope(rel):
                        files[rel] = p.read_bytes()
    return files, unmade


def macho(commands, size=0x400):
    body = b''.join(struct.pack('<II', cmd, 8 + len(payload)) + payload for cmd, payload in commands)
    return (struct.pack('<IiIIIIII', 0xfeedfacf, 0x01000007, 3, 6, len(commands), len(body), 0, 0) + body).ljust(size, b'\0')


def dylib(cmd, name):
    payload = struct.pack('<IIII', 24, 2, 0, 0) + name.encode() + b'\0'
    return cmd, payload.ljust((len(payload) + 8 + 7) // 8 * 8 - 8, b'\0')


class Scanner(unittest.TestCase):
    """The scan itself, on authored Mach-O headers (always runs)."""

    def test_flags_relative_and_lists_rpath(self):
        data = macho([dylib(0xc, '/usr/lib/libSystem.B.dylib'), dylib(0xc, 'System/Library/x.dylib'),
                      dylib(0x80000018, '@loader_path/y.dylib'), dylib(0x8000001f, '@rpath/z.dylib'),
                      dylib(LC_RPATH, '@loader_path/../Frameworks')])
        relative, rpath = problems({'a': data})
        self.assertEqual(relative, ['a (slice at 0x0) LC_LOAD_DYLIB System/Library/x.dylib'])
        self.assertEqual(rpath, ["a (slice at 0x0) LC_REEXPORT_DYLIB @rpath/z.dylib; LC_RPATH ['@loader_path/../Frameworks']"])

    def test_universal_slices_and_non_macho(self):
        thin = macho([dylib(0x20, 'lib/rel.dylib')])
        fat = struct.pack('>II', 0xcafebabe, 1) + struct.pack('>IIIII', 0x01000007, 3, 0x1000, len(thin), 12)
        fat = fat.ljust(0x1000, b'\0') + thin
        self.assertEqual(problems({'f': fat, 'text': b'not a binary'})[0],
                         ['f (slice at 0x1000) LC_LAZY_LOAD_DYLIB lib/rel.dylib'])

    def test_scope(self):
        self.assertTrue(in_scope('System/Library/PrivateFrameworks/GPUCompiler.framework/Versions/31001/Libraries/libLLVM.dylib'))
        self.assertTrue(in_scope('System/Library/Frameworks/Metal.framework/Versions/A/XPCServices/MTLCompilerService.xpc/Contents/MacOS/MTLCompilerService'))
        self.assertFalse(in_scope('System/Library/Extensions/AppleHDA.kext/Contents/MacOS/AppleHDA'))


class ReleaseImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        got = installed_files()
        if got is None:
            raise unittest.SkipTest('the oclp_hardware row is not in the store')
        cls.files, cls.unmade = got

    def test_scans_the_stack(self):
        machos = [p for p, d in self.files.items() if slices(d)]
        for need in ('System/Library/Frameworks/Metal.framework/Versions/A/XPCServices/MTLCompilerService.xpc/Contents/MacOS/MTLCompilerService',
                     'System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/31001/MTLCompiler',
                     'System/Library/PrivateFrameworks/GPUCompiler.framework/Versions/31001/Libraries/libLLVM.dylib',
                     'System/Library/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver'):
            self.assertIn(need, machos)
        self.assertEqual(self.unmade, [], 'declared Metal-stack paths materialize could not make')

    def test_no_relative_load_paths(self):
        relative, rpath = problems(self.files)
        if rpath:
            print('\n@rpath links (with their LC_RPATHs):\n  ' + '\n  '.join(rpath), file=sys.stderr)
        self.assertEqual(relative, [], 'load paths dyld refuses in a restricted, hardened program')


if __name__ == '__main__':
    unittest.main()
