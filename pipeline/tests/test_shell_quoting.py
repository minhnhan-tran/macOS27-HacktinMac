"""Install-time recipe scripts quote every expansion, so a path with a space cannot split.

The user's install runs recipe scripts from the installer volume ("/Volumes/HAO 1"); an unquoted "$P/..." split at
the space and the install stopped ("Read-only file system: '1'"). The runner now starts them from a link without
spaces; this is the second line of defence. pipeline/shellquote.py lists each $NAME/${...} outside double quotes in
every apply.sh and src/*hook.sh; a deliberate split is allowed below with its reason. A few posts also run here from a
folder named "HAO 1", with stand-ins for install(1) and chown(1).
python3.14 -I -B -m unittest discover -s pipeline/tests -k shell_quoting"""
import fnmatch
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOP / 'pipeline'))
import shellquote   # noqa: E402

# Metal's recipes (CLAUDE.md lanes table): their scripts are metal's to change. Pending metal: each listed hit is a
# mode argument ("-m $1") in a put() helper, a single value, not a path.
METAL = ('golden-gate-metal-*', 'golden-gate-metal27', 'golden-gate-hello', 'golden-gate-live-text-*',
         'golden-gate-shader-caches', 'metal-compiler-compat', 'metal-metalold', 'metalold-selectors')
PENDING_METAL = {
    ('patches/golden-gate-hello/src/kc-hook.sh', '$1'),
    ('patches/golden-gate-metal-argbuf/src/kc-hook.sh', '$1'),
    ('patches/metalold-selectors/src/kc-hook.sh', '$1'),
}
# Deliberate splits: (script, token) -> why the expansion is unquoted.
INTENTIONAL = {
    ('patches/golden-gate-userspace/src/kc-hook.sh', '${GOLDEN_CRYPTEX_CACHE:+--cryptex-cache "$GOLDEN_CRYPTEX_CACHE"}'):
        'two words or none: the flag and its (quoted) value when the cache is set',
}


def scripts():
    return sorted(set(TOP.glob('patches/*/apply.sh')) | set(TOP.glob('patches/*/src/*hook.sh')))


def is_metal(path):
    return any(fnmatch.fnmatch(path.relative_to(TOP).parts[1], pat) for pat in METAL)


class Lexer(unittest.TestCase):
    def test_what_it_reports_and_skips(self):
        text = ('A=$P/x\n'                                   # assignment: one word
                'cp $P/a "$P/b"\n'                           # $P/a splits
                'H=$(cd "$(dirname "$0")" && pwd)\n'         # nested quoting levels
                'echo "n $(ls "$D" | wc -l) $(cat $F)"\n'    # $F splits inside $(...)
                "echo '$X' # $Y\n"
                'case $v in a) ;; esac\n'
                'i=$((i + $n))\n'
                "cat <<'EOF'\n$Z\nEOF\n"
                'put() { install -m $1 "$2"; }\n')
        self.assertEqual(shellquote.unquoted(text), [(2, '$P'), (4, '$F'), (11, '$1')])


class Quoting(unittest.TestCase):
    def test_every_expansion_is_quoted(self):
        found = set()
        for f in scripts():
            rel = str(f.relative_to(TOP))
            for line, tok in shellquote.unquoted(f.read_text()):
                found.add((rel, tok))
                if (rel, tok) in INTENTIONAL:
                    continue
                if is_metal(f):
                    self.assertIn((rel, tok), PENDING_METAL, 'metal recipe %s:%d %s: new unquoted expansion' % (rel, line, tok))
                    continue
                self.fail('%s:%d: %s is not quoted (a path with a space splits there)' % (rel, line, tok))
        # an allowance that no longer matches anything is dropped
        self.assertEqual(set(INTENTIONAL) - found, set())
        self.assertEqual(PENDING_METAL - found, set())


STUBS = {
    # install(1) stand-in: same arguments (-d DIR..., or SRC... DEST), no owners
    'install': '#!/bin/sh\n'
               'd=0; while [ $# -gt 0 ]; do case "$1" in -d) d=1; shift;; -o|-g|-m) shift 2;; *) break;; esac; done\n'
               'if [ $d = 1 ]; then for x in "$@"; do mkdir -p "$x"; done; exit 0; fi\n'
               '[ $# -ge 2 ] || { echo "install: $# args" >&2; exit 1; }\n'
               'eval "dest=\\${$#}"\n'
               'if [ $# -gt 2 ] && [ ! -d "$dest" ]; then echo "install: $dest is not a folder" >&2; exit 1; fi\n'
               'while [ $# -gt 1 ]; do cp "$1" "$dest"; shift; done\n',
    'chown': '#!/bin/sh\nexit 0\n',
}


class SpacesInPaths(unittest.TestCase):
    """Posts run from a folder named "HAO 1" (the installer volume's name), into a target folder with a space too."""

    def run_apply(self, recipe, env, tmp):
        bin_ = tmp / 'bin'
        bin_.mkdir()
        for name, text in STUBS.items():
            (bin_ / name).write_text(text)
            (bin_ / name).chmod(0o755)
        env = dict(os.environ, PATH='%s:/usr/bin:/bin:/usr/sbin' % bin_, **{k: str(v) for k, v in env.items()})
        r = subprocess.run(['sh', '-eu', str(TOP / 'patches' / recipe / 'apply.sh')], env=env, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def kit(self, top, recipe):
        p = top / 'kit' / recipe
        if (TOP / 'patches' / recipe / 'src').is_dir():
            shutil.copytree(TOP / 'patches' / recipe / 'src', p / 'src')
        (p / 'inputs').mkdir(parents=True)
        (p / 'build').mkdir()
        return p

    def declared(self, recipe):
        return [f['path'] for f in json.loads((TOP / 'patches' / recipe / 'patch.json').read_text())['files']]

    def test_appstore_slices(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            p = self.kit(tmp / 'HAO 1', 'golden-gate-appstore-slices')
            (p / 'inputs/appstore_slice_guard').mkdir()
            (p / 'inputs/appstore_slice_guard/hao-slice-guard').write_text('guard')
            data = tmp / 'HAO 1' / 'Data volume'
            data.mkdir()
            self.run_apply('golden-gate-appstore-slices', {'P': p, 'DATA': data}, tmp)
            for f in self.declared('golden-gate-appstore-slices'):
                self.assertTrue((data / f).is_file(), f)

    def test_hao_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            p = self.kit(tmp / 'HAO 1', 'hao-command')
            (tmp / 'py/python/bin').mkdir(parents=True)
            (tmp / 'py/python/bin/python3.12').write_text('#!/bin/sh\n')
            (tmp / 'py/python/bin/python3.12').chmod(0o755)
            subprocess.run(['tar', '-czf', str(p / 'inputs/python_x86_64'), '-C', str(tmp / 'py'), 'python'], check=True)
            (p / 'build/hao/tools/macOS27Tool').mkdir(parents=True)
            (p / 'build/hao/tools/macOS27Tool/macOS27Tool').write_text('hao')
            data = tmp / 'HAO 1' / 'Data volume'
            (data / 'usr').mkdir(parents=True)
            self.run_apply('hao-command', {'P': p, 'DATA': data}, tmp)
            self.assertTrue((data / 'usr/local/bin/hao').is_file())
            self.assertTrue((data / 'usr/local/lib/hao/python/bin/python3.12').is_file())
            self.assertTrue((data / 'usr/local/lib/hao/hao/tools/macOS27Tool/macOS27Tool').is_file())

    def test_rec_startup_guard(self):
        # a Recovery recipe: several sources into one folder in one install(1)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            p = self.kit(tmp / 'HAO 1', 'rec-startup-guard')
            (p / 'build/bootguard').write_text('bootguard')
            bs = tmp / 'HAO 1' / 'Base System'
            (bs / 'System/Library/LaunchDaemons').mkdir(parents=True)
            self.run_apply('rec-startup-guard', {'P': p, 'BS': bs}, tmp)
            for f in self.declared('rec-startup-guard'):
                if f.startswith(('usr/local/', 'System/Library/LaunchDaemons/')):
                    self.assertTrue((bs / f).is_file(), f)


if __name__ == '__main__':
    unittest.main()
