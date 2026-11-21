"""patch.json "files" and edits/*.json declare what src/install.py does; these tests tie each declaration to the code.
python3.14 -I -B -m unittest discover -s patches/golden-gate-wifi-compat/tests

Always: every spec follows the edit-spec format and is listed; declared paths, rows and flags are the ones install.py
uses; set_install_name and install_wrapper_compat give golden-gate-userspace's bytes; the rename matcher is the
difference set_install_name makes (synthetic dylib, applied by the engine).
With WIFI_COMPAT_OCLP_IO80211 and WIFI_COMPAT_OCLP_WIFIP2P (OCLP 13.7.2-25 wrapper files): each spec's output, signed
as the spec says, equals install_wrapper_compat's rename, and its expect hash."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
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


install = load('wifi_install', PATCH/'src/install.py')
ENGINE_PATHS = [str(TOP/'tools/edit-engine/src'), str(TOP/'tools/edit-engine/tests')]
sys.path[0:0] = ENGINE_PATHS
import engine   # noqa: E402
import fixture   # noqa: E402
import spec as S   # noqa: E402
for entry in ENGINE_PATHS:
    sys.path.remove(entry)
sys.modules.pop('macho', None)   # the engine's; golden-gate-userspace's own is loaded below
sys.path.insert(0, str(TOP/'patches/golden-gate-userspace/src'))
userspace_macho = load('macho', TOP/'patches/golden-gate-userspace/src/macho.py')
sys.path.remove(str(TOP/'patches/golden-gate-userspace/src'))

PATCH_JSON = json.loads((PATCH/'patch.json').read_text())
FILES = {f['path']: f for f in PATCH_JSON['files']}
SPECS = {n.name: S.load(n) for n in sorted((PATCH/'edits').glob('*.json'))}
BY_PATH = {s['path']: (n, s) for n, s in SPECS.items()}
WRAPPERS = ((install.IO80211, 'IO80211', 'io80211_compat', 'WIFI_COMPAT_OCLP_IO80211'),
            (install.WIFIP2P, 'WiFiPeerToPeer', 'wifip2p_compat', 'WIFI_COMPAT_OCLP_WIFIP2P'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def at_path(value):
    return Path(value) if value and Path(value).is_file() else None


class Declaration(unittest.TestCase):
    def test_specs_follow_the_format_and_are_all_listed(self):
        self.assertEqual(len(SPECS), len(BY_PATH))
        for name, spec in SPECS.items():
            self.assertEqual(S.validate(spec), [], name)
        self.assertEqual({f['edits'][6:] for f in PATCH_JSON['files'] if 'edits' in f}, set(SPECS))
        self.assertEqual(sorted(PATCH_JSON['inputs']), sorted(row for _, _, row, _ in WRAPPERS))

    def test_declared_paths_rows_and_flags_are_the_ones_install_py_uses(self):
        for folder, leaf, row, _ in WRAPPERS:
            self.assertEqual(FILES[folder + leaf]['origin'], dict(FILES[folder + leaf]['origin'], kind='m27', row=row))
            self.assertIn(folder + leaf + 'OCLP.dylib', BY_PATH)   # install_wrapper_compat: LEAF + SUFFIX + '.dylib'
            for path in (folder + leaf, folder + leaf + 'OCLP.dylib'):
                self.assertEqual(FILES[path]['install'], {'mode': '0755', 'owner': 'root:wheel', 'flags': ['restricted']})
        hook = (PATCH/'src/kc-hook.sh').read_text()
        self.assertIn('"$H/inputs/io80211_compat" "$H/inputs/wifip2p_compat"', hook)

    def test_set_install_name_gives_golden_gate_userspaces_bytes(self):
        for folder, leaf, _, _ in WRAPPERS:
            old, new = ('/' + folder + leaf).encode(), ('/' + folder + leaf + 'OCLP.dylib').encode()
            data = fixture.dylib(size=0x4000, text_at=0x1000, text_size=0x1000, ident=old)
            mine, theirs = bytearray(data), bytearray(data)
            install.set_install_name(mine, old, new)
            userspace_macho.set_install_name(theirs, old, new)
            self.assertNotEqual(bytes(mine), data)
            self.assertEqual(mine, theirs)

    def test_install_wrapper_compat_is_golden_gate_userspaces(self):
        """The same steps as golden-gate-userspace's (its CoreWiFi step): the function bodies match line for line,
        docstring aside."""
        def body(path, name):
            text = path.read_text()
            start = text.index('def %s(' % name)
            end = text.index('\n\n\n', start)
            lines = text[start:end].splitlines()
            doc_end = next(i for i, l in enumerate(lines[1:], 1) if l.rstrip().endswith('"""'))
            return lines[doc_end + 1:]
        self.assertEqual(body(PATCH/'src/install.py', 'install_wrapper_compat'),
                         body(TOP/'patches/golden-gate-userspace/src/install.py', 'install_wrapper_compat'))
        def whole(path, name):
            text = path.read_text()
            start = text.index('def %s(' % name)
            return text[start:text.index('\n\n\n', start)]
        self.assertEqual(whole(PATCH/'src/install.py', 'install_file'),
                         whole(TOP/'patches/golden-gate-userspace/src/install.py', 'install_file'))

    def test_rename_matcher_is_the_difference_set_install_name_makes(self):
        for folder, leaf, _, _ in WRAPPERS:
            old, new = ('/' + folder + leaf).encode(), ('/' + folder + leaf + 'OCLP.dylib').encode()
            data = fixture.dylib(size=0x4000, text_at=0x1000, text_size=0x1000, ident=old)
            mine = bytearray(data)
            install.set_install_name(mine, old, new)
            sites = []
            for i, (a, b) in enumerate(zip(data, mine)):
                if a != b:
                    if sites and i - (sites[-1]['at'] + len(sites[-1]['before']) // 2) < 16:
                        end = sites[-1]['at'] + len(sites[-1]['before']) // 2
                        sites[-1]['before'] += data[end:i + 1].hex()
                        sites[-1]['after'] += bytes(mine[end:i + 1]).hex()
                    else:
                        sites.append({'at': i, 'before': data[i:i + 1].hex(), 'after': bytes(mine[i:i + 1]).hex()})
            template = BY_PATH[folder + leaf + 'OCLP.dylib'][1]
            fake = copy.deepcopy(template)
            fake['edits'][0]['resolved'] = {sha(data): sites}
            fake['sign'] = None
            fake['expect'] = {}
            out, _ = engine.apply(fake, data, repo=str(TOP))
            self.assertEqual(out, bytes(mine), leaf)
            self.assertEqual(template['sign'],
                             {'mode': 'adhoc', 'preserve': ['identifier', 'entitlements', 'flags', 'runtime']})


@unittest.skipUnless(all(at_path(os.environ.get(env)) for *_, env in WRAPPERS),
                     'set WIFI_COMPAT_OCLP_IO80211 and WIFI_COMPAT_OCLP_WIFIP2P')
class RealRenames(unittest.TestCase):
    def test_renamed_wrappers_equal_install_wrapper_compat(self):
        for folder, leaf, _, env in WRAPPERS:
            source = at_path(os.environ.get(env))
            spec = BY_PATH[folder + leaf + 'OCLP.dylib'][1]
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                # install_wrapper_compat, step by step: the install name, then codesign with the file's identifier
                data = bytearray(source.read_bytes())
                install.set_install_name(data, ('/' + folder + leaf).encode(), ('/' + folder + leaf + 'OCLP.dylib').encode())
                reference = tmp/'reference.dylib'
                reference.write_bytes(data)
                details = subprocess.run(['codesign', '-dv', str(source)], check=True, capture_output=True,
                                         text=True).stderr
                identifier = next(l.split('=', 1)[1] for l in details.splitlines() if l.startswith('Identifier='))
                subprocess.run(['codesign', '-f', '-s', '-', '-i', identifier,
                                '--preserve-metadata=entitlements,flags,runtime', str(reference)],
                               check=True, capture_output=True)
                out, _ = engine.apply(spec, source.read_bytes(), repo=str(TOP))
                mine = tmp/'declared.dylib'
                mine.write_bytes(out)
                subprocess.run(S.codesign_args(spec['sign'], identifier) + [str(mine)], check=True, capture_output=True)
                self.assertEqual(mine.read_bytes(), reference.read_bytes(), leaf)
                self.assertEqual(sha(mine.read_bytes()), spec['expect'][sha(source.read_bytes())])


if __name__ == '__main__':
    unittest.main()
