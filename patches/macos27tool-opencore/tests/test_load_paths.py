"""Every non-Apple Mach-O the release image installs links its libraries by paths dyld follows under the AMFI dyld
policy this recipe restores (r72: hardened programs, and amfi_force_policy=8: CS_RESTRICT programs).
python3.14 -I -B -m unittest discover -s patches/macos27tool-opencore/tests -p test_load_paths.py

The rule (dyld-1378 dyld/Loader.cpp; AMFI 25G330 macos_dyld_policy_at_path / _env_vars; README "Load paths"):
- a file-relative path (not "/" and not "@", Loader.cpp isFileRelativePath) is refused whenever AMFI denies the path
  variables (allowEnvVarsPath): every hardened program not entitled to DYLD_* variables, and every restricted one
  without library validation ("relative path not allowed in hardened program"). That covers LC_LOAD_DYLIB and kin,
  and an LC_RPATH entry such a link would resolve through;
- @executable_path is not expanded in a restricted program (allowAtPaths off), nor is @loader_path in the main
  executable's own LC_RPATH; an @rpath link then needs an absolute LC_RPATH whose expansion exists.
A dylib can be loaded by any client, so it is held to the strict rule; a main executable is held to it when it can be
hardened or restricted (it has the hardened-runtime flag or entitlements).

The files: OCLP's hardware overlay (row oclp_hardware, every entry of its install map), every path the release set
declares, made by pipeline/materialize.py (rows, edit specs applied), tar trees declared from a row, and the
m27_runtime payload (root/ as installed, embed/stubs: the launch slices m27 embeds in programs). Every macho.add-dylib
edit of the set must name an absolute path (the Apple files they edit are not on the host). Skips without the store."""
import json
import os
from pathlib import Path
import runpy
import struct
import tarfile
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[3]
ASSEMBLY = {'set': 'alpha-v0.1.0', 'target': 'mbp92', 'golden': '26B131', 'donor': '25G342'}
LINKS = {0xc: 'LC_LOAD_DYLIB', 0x80000018: 'LC_LOAD_WEAK_DYLIB', 0x8000001f: 'LC_REEXPORT_DYLIB',
         0x20: 'LC_LAZY_LOAD_DYLIB', 0x80000023: 'LC_LOAD_UPWARD_DYLIB'}
LC_RPATH, LC_CODE_SIGNATURE = 0x8000001c, 0x1d
MH_EXECUTE = 2
CS_RUNTIME = 0x10000
MACHO = (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf')
# path (or prefix ending in /) -> why a finding there is safe. Every entry needs its evidence.
ALLOW = {}


def relative(path):
    """dyld's isFileRelativePath (Loader.cpp): anything not absolute and not an @ path, ./ and ../ included."""
    return not path.startswith('/') and not path.startswith('@')


def slices(data):
    """File offsets of the 64-bit Mach-O slices of DATA (thin or universal); [] for anything else."""
    if len(data) < 8:
        return []
    if struct.unpack_from('<I', data, 0)[0] == 0xfeedfacf:
        return [0]
    if data[:4] in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        wide, n = data[3] == 0xbf, struct.unpack_from('>I', data, 4)[0]
        if n > 32:                                       # a Java class file shares the magic
            return []
        offs = [struct.unpack_from('>Q' if wide else '>I', data, 8+i*(32 if wide else 20)+8)[0] for i in range(n)]
        return [o for o in offs if o+4 <= len(data) and struct.unpack_from('<I', data, o)[0] == 0xfeedfacf]
    return []


def header(data, base):
    """(filetype, [(command, path)], [rpath], code-directory flags or None, has entitlements) of the slice at BASE."""
    filetype, ncmds = struct.unpack_from('<II', data, base+12)
    at, links, rpaths, sig = base+32, [], [], None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if cmd in LINKS or cmd == LC_RPATH:
            name = data[at+struct.unpack_from('<I', data, at+8)[0]:at+size].split(b'\0')[0].decode('utf-8', 'replace')
            (rpaths.append(name) if cmd == LC_RPATH else links.append((LINKS[cmd], name)))
        elif cmd == LC_CODE_SIGNATURE:
            sig = struct.unpack_from('<I', data, at+8)[0]
        at += size
    flags, entitled = None, False
    if sig is not None:
        blob = base+sig
        try:
            for i in range(struct.unpack_from('>I', data, blob+8)[0]):
                kind, off = struct.unpack_from('>II', data, blob+12+i*8)
                if kind == 0:
                    flags = struct.unpack_from('>I', data, blob+off+12)[0]
                elif kind in (5, 7):
                    entitled = True
        except struct.error:
            pass
    return filetype, links, rpaths, flags, entitled


def strict(filetype, flags, entitled):
    """Whether a slice is held to the hardened/restricted rule: any library, or a program that can be hardened or
    restricted."""
    return filetype != MH_EXECUTE or entitled or bool((flags or 0) & CS_RUNTIME)


def refusals(files):
    """[line] for every load in FILES {installed path: bytes} that dyld refuses under the policy."""
    out = []
    for path in sorted(files):
        if any(path == a or (a.endswith('/') and path.startswith(a)) for a in ALLOW):
            continue
        data = files[path]
        for base in slices(data):
            filetype, links, rpaths, flags, entitled = header(data, base)
            if not strict(filetype, flags, entitled):
                continue
            main = filetype == MH_EXECUTE
            where = '%s (slice %#x)' % (path, base)
            for rp in rpaths:
                if relative(rp):
                    out.append('%s LC_RPATH %s: relative, refused in a hardened program' % (where, rp))
            for cmd, name in links:
                if relative(name):
                    out.append('%s %s %s: relative, refused in a hardened program' % (where, cmd, name))
                elif name.startswith('@executable_path'):
                    out.append('%s %s %s: @executable_path is not expanded in a restricted program' % (where, cmd, name))
                elif name.startswith('@rpath/') and rpaths:
                    tail = name[len('@rpath/'):]
                    usable = [r for r in rpaths if r.startswith('/') or (not main and r.startswith('@loader_path'))]
                    found = [r for r in usable if not r.startswith('/')
                             or os.path.normpath('%s/%s' % (r, tail)).lstrip('/') in files
                             or not os.path.normpath('%s/%s' % (r, tail)).lstrip('/').startswith(OURS)]
                    if not found:
                        out.append('%s %s %s: no LC_RPATH a restricted program expands to it (%s)'
                                   % (where, cmd, name, rpaths))
    return out


OURS = ('usr/lib/m27/', 'usr/libexec/m27', 'usr/local/')     # where a missing @rpath target is ours, not Apple's


def add_dylib_paths(set_file):
    """[(spec, path)] of every macho.add-dylib edit in the recipes of SET_FILE."""
    names = [line.split('#')[0].strip() for line in Path(set_file).read_text().splitlines()]
    out = []
    for name in filter(None, names):
        for spec in sorted((TOP/'patches'/name/'edits').glob('*.json')):
            for edit in json.loads(spec.read_text()).get('edits', []):
                if edit.get('op') == 'macho.add-dylib':
                    out.append((str(spec.relative_to(TOP)), edit.get('args', {}).get('path', '')))
    return out


def installed_files():
    """({installed path: bytes} of the non-Apple Mach-Os of the release image, [paths not made]) or None."""
    materialize = runpy.run_path(str(TOP/'pipeline/materialize.py'))
    rows = materialize['repo_rows'](ASSEMBLY['donor'], ASSEMBLY['golden'])
    art, m27 = rows.get('oclp_hardware'), rows.get('m27_runtime')
    if not art or not (Path(art)/'install-map.json').is_file() or not m27 or not Path(m27).is_dir():
        return None
    files = {}

    def take(path, data):
        if data[:4] in MACHO:
            files[path] = data

    sources = materialize['Sources'].from_repo(rows)
    for dest, _, rel in sources.oclp_map:                  # map order: a later entry overwrites an earlier one
        root = Path(sources.oclp_payload, rel)
        for p in ([root] if root.is_file() else sorted(root.rglob('*')) if root.is_dir() else []):
            if p.is_file() and not p.is_symlink():
                take(dest if p == root else '%s/%s' % (dest, p.relative_to(root)), p.read_bytes())
    for p in sorted(Path(m27, 'root').rglob('*')):
        if p.is_file() and not p.is_symlink():
            take(str(p.relative_to(Path(m27, 'root'))), p.read_bytes())
    for p in sorted(Path(m27, 'embed', 'stubs').glob('*')):
        take('(m27 stub) %s' % p.name, p.read_bytes())
    setfile = TOP/'pipeline/sets'/(ASSEMBLY['set']+'.txt')
    target = TOP/'pipeline/targets'/(ASSEMBLY['target']+'.json')
    with tempfile.TemporaryDirectory() as out:
        report, ledger = materialize['run'](str(setfile), str(target), ASSEMBLY['donor'], out, golden=ASSEMBLY['golden'])
        for pack in Path(out).iterdir():
            made = pack/'files'
            if made.is_dir():
                for p in made.rglob('*'):
                    if p.is_file() and not p.is_symlink():
                        take(str(p.relative_to(made)), p.read_bytes())
    unmade = []
    for r in report:
        if r['outcome'] in ('made', 'deferred', 'unpacked'):
            continue
        row = rows.get(tar_row(r['path']))
        if row and str(row).endswith(('.tar.gz', '.tgz', '.tar')) and Path(row).is_file():
            with tarfile.open(row) as tar:                 # a tree declared from a tar row (macos27tool-command's CPython)
                for m in tar.getmembers():
                    if m.isfile():
                        rel = m.name.split('/', 1)[1] if '/' in m.name else m.name
                        take('%s/%s' % (r['path'], rel), tar.extractfile(m).read())
            continue
        unmade.append((r['path'], r['outcome'], r.get('reason')))
    return files, unmade


def tar_row(path):
    """The row a declared tree at PATH comes from, if a recipe declares it with "tree" and an origin row."""
    for patch in sorted((TOP/'patches').glob('*/patch.json')):
        for f in json.loads(patch.read_text()).get('files', []):
            if isinstance(f, dict) and f.get('path') == path and f.get('tree'):
                return (f.get('origin') or {}).get('row')
    return None


def macho(commands, filetype=6, sign=None):
    """A thin x86_64 Mach-O of COMMANDS [(cmd, payload)]; SIGN (flags, entitled) adds a code signature."""
    body = b''.join(struct.pack('<II', c, 8+len(p))+p for c, p in commands)
    ncmds = len(commands)
    if sign is not None:
        body += struct.pack('<IIII', LC_CODE_SIGNATURE, 16, 0, 0)
        ncmds += 1
    data = bytearray((struct.pack('<IiIIIIII', 0xfeedfacf, 0x01000007, 3, filetype, ncmds, len(body), 0, 0)+body)
                     .ljust(0x400, b'\0'))
    if sign is not None:
        flags, entitled = sign
        cd = struct.pack('>IIIII', 0xfade0c02, 44, 0x20400, flags, 0).ljust(44, b'\0')
        index = [(0, 12+8*(1+entitled))] + ([(5, 12+8*2+len(cd))] if entitled else [])
        blob = struct.pack('>III', 0xfade0cc0, 0, len(index)) + b''.join(struct.pack('>II', *e) for e in index) + cd
        blob += struct.pack('>II', 0xfade7171, 8) if entitled else b''
        at = 32+len(body)-16
        data[at+8:at+16] = struct.pack('<II', len(data), len(blob))
        data += blob
    return bytes(data)


def name_cmd(cmd, name):
    payload = (struct.pack('<IIII', 24, 2, 0, 0) if cmd != LC_RPATH else struct.pack('<I', 12)) + name.encode()+b'\0'
    return cmd, payload.ljust((len(payload)+8+7)//8*8-8, b'\0')


class Rule(unittest.TestCase):
    """The rule on authored Mach-O headers (always runs)."""

    def test_relative_link_in_a_library(self):
        lib = macho([name_cmd(0xc, '/usr/lib/libSystem.B.dylib'),
                     name_cmd(0xc, 'System/Library/PrivateFrameworks/X.framework/Libraries/libX_helper.dylib'),
                     name_cmd(0x80000018, '@loader_path/y.dylib'), name_cmd(0xc, './z.dylib')])
        self.assertEqual(refusals({'a.dylib': lib}), [
            'a.dylib (slice 0x0) LC_LOAD_DYLIB System/Library/PrivateFrameworks/X.framework/Libraries/libX_helper.dylib: '
            'relative, refused in a hardened program',
            'a.dylib (slice 0x0) LC_LOAD_DYLIB ./z.dylib: relative, refused in a hardened program'])

    def test_executable_path_and_rpaths(self):
        lib = macho([name_cmd(0xc, '@executable_path/../Frameworks/q.dylib'), name_cmd(LC_RPATH, 'lib')])
        self.assertEqual(len(refusals({'b.dylib': lib})), 2)
        prog = macho([name_cmd(0xc, '@rpath/libm27.dylib'), name_cmd(LC_RPATH, '@executable_path'),
                      name_cmd(LC_RPATH, '/usr/lib/m27')], filetype=MH_EXECUTE, sign=(CS_RUNTIME | 2, True))
        self.assertEqual(refusals({'usr/libexec/m27': prog, 'usr/lib/m27/libm27.dylib': lib[:0]+b'x'}), [])
        self.assertEqual(len(refusals({'usr/libexec/m27': prog})), 1, 'the absolute rpath names a file not installed')
        only_exec = macho([name_cmd(0xc, '@rpath/libm27.dylib'), name_cmd(LC_RPATH, '@executable_path')],
                          filetype=MH_EXECUTE, sign=(2, True))
        self.assertEqual(len(refusals({'p': only_exec})), 1)

    def test_plain_program_is_not_held(self):
        prog = macho([name_cmd(0xc, '@executable_path/../lib/libpython.dylib'),
                      name_cmd(LC_RPATH, '@executable_path/../lib')], filetype=MH_EXECUTE)
        self.assertEqual(refusals({'python3': prog}), [])
        self.assertEqual(len(refusals({'python3': macho([name_cmd(0xc, '@executable_path/l.dylib')], MH_EXECUTE,
                                                         sign=(CS_RUNTIME, False))})), 1)

    def test_universal(self):
        thin = macho([name_cmd(0x20, 'lib/rel.dylib')])
        fat = (struct.pack('>II', 0xcafebabe, 1)+struct.pack('>IIIII', 0x01000007, 3, 0x1000, len(thin), 12)).ljust(0x1000, b'\0')
        self.assertEqual(refusals({'f': fat+thin, 'text': b'not a binary'}),
                         ['f (slice 0x1000) LC_LAZY_LOAD_DYLIB lib/rel.dylib: relative, refused in a hardened program'])


class ReleaseImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        got = installed_files()
        if got is None:
            raise unittest.SkipTest('the oclp_hardware or m27_runtime row is not in the store')
        cls.files, cls.unmade = got

    def test_scans_the_image(self):
        for need in ('System/Library/PrivateFrameworks/GPUCompiler.framework/Versions/31001/Libraries/libLLVM.dylib',
                     'System/Library/PrivateFrameworks/IO80211.framework/Versions/A/IO80211',
                     'System/Library/PrivateFrameworks/WiFiPeerToPeer.framework/Versions/A/WiFiPeerToPeerOld.dylib',
                     'usr/libexec/m27', 'usr/lib/m27/libm27.dylib', 'usr/lib/windowserver_filter.dylib'):
            self.assertIn(need, self.files)
        self.assertGreater(sum(p.startswith('(m27 stub)') for p in self.files), 100)
        self.assertTrue(any(p.startswith('usr/local/lib/m27/python/') for p in self.files))
        # what materialize cannot make on the host: Apple's own Golden Gate files (edited in place, see
        # test_added_dylibs_are_absolute) and Golden Gate's user template (cpio data, no Mach-O), nothing else
        self.assertEqual([u for u in self.unmade if 'no --system root' not in str(u[2])
                          and not u[0].startswith('Library/User Template/')], [])

    def test_no_refused_load_paths(self):
        self.assertEqual(refusals(self.files), [], 'load paths dyld refuses in a hardened or restricted program')

    def test_added_dylibs_are_absolute(self):
        added = add_dylib_paths(TOP/'pipeline/sets'/(ASSEMBLY['set']+'.txt'))
        self.assertTrue(added)
        self.assertEqual([(s, p) for s, p in added if not p.startswith('/')], [])


if __name__ == '__main__':
    unittest.main()
