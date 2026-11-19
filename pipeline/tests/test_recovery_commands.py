"""Every command the install path runs exists in the stick's Recovery, or hao's recovery-tools stands in for it.

The install (macOS27Tool install in the Tahoe 25G342 Recovery) runs golden-assemble.sh, golden-pristine.sh, golden-image.py
and its helpers, and every recipe's kc-hook.sh / hook.sh / apply.sh with their Python helpers. bench 2355 found
rsync missing 81 minutes into a QEMU install (diag#0027 found install, shasum, cmp, readlink). This test reads
pipeline/stages/recovery-commands-25G342.txt (the Recovery's /bin, /sbin, /usr/bin, /usr/sbin, /usr/libexec) and:
- statically, every command word of those shell scripts and every command a Python helper starts (a string
  literal opening a subprocess argument list, or an absolute /bin, /sbin, /usr/... path) must be in the Recovery,
  a recovery-tools stand-in, the kit's CPython, a shell builtin or function, or in ALLOWED with its reason;
- by running golden-assemble.sh on a stub kit with HAO_RECOVERY_COMMANDS (PATH = the Recovery's commands only).
"""
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
STAGES = TOP / 'pipeline' / 'stages'
LIST = STAGES / 'recovery-commands-25G342.txt'
SETS = ('golden-gate-26B131', 'dev-bs-test')
STAND_INS = set(runpy.run_path(str(STAGES / 'recovery-tools.py'))['TOOLS'])
KIT_PYTHON = {'python3', 'python3.12'}
BUILTIN = set('''. : [ [[ alias bg break builtin case cd command continue declare do done echo elif else esac eval exec
exit export false fc fg fi for function getopts hash if in jobs kill let local print printf pwd read readonly return
select set shift source test then time trap true type typeset ulimit umask unalias unset until wait while { } !
setopt unsetopt emulate zmodload autoload whence noglob integer float zparseopts'''.split())
# command -> why it is not needed in the Recovery
ALLOWED = {
    '/usr/bin/rsync': 'tools/oclp-hardware lib/files.py tries /usr/bin/rsync --version and merges with ditto when it '
                      'does not run (OCLP\'s own recoveryOS fallback)',
}
# (file, command) -> why that file's use never runs in the install
ALLOWED_IN = {
    ('tools/tahoe-donors/make', 'git'): 'only in "package" (the build record\'s commit); the install runs install-*',
    ('patches/hao-opencore/src/repair_binding.py', 'git'): 'only in "profile" (source_identity, build time); the '
                                                         'install runs "bind"',
}
# Files of a recipe folder that the install never runs (made into inputs before the build, or run on the target)
NOT_RUN = re.compile(r'(^|/)(tests?|build)/|/make$|\.zsh$|/derive\.py$|/coverage\.py$|/methods\.py$')
SEP = re.compile(r'\|\||&&|[;|&`]|\$\(|\(|\bthen\b|\bdo\b|\belse\b|\bif\b|\bwhile\b|\buntil\b|!')
PREFIX = {'exec', 'command', 'nohup', 'env', 'time', 'caffeinate'}
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
PY_CMD = re.compile(r"""(?:run|check_output|check_call|call|Popen|command|_run|output)\(\s*\[?\s*['"]([/\w.+-]+)['"]""")
PY_LIST = re.compile(r"""\[\s*['"](/?[\w.+-]+)['"]\s*,""")
ABSOLUTE = re.compile(r"""['"](/(?:usr/)?(?:s?bin|libexec)/[\w.+-]+)['"]""")


def recovery():
    paths = {l.strip() for l in LIST.read_text().splitlines() if l.strip() and not l.startswith('#')}
    return paths, {os.path.basename(p) for p in paths}


def recipes():
    names = []
    for s in SETS:
        for line in (TOP / 'pipeline' / 'sets' / (s + '.txt')).read_text().splitlines():
            name = line.split('#', 1)[0].strip()
            if name and name not in names:
                names.append(name)
    return names


def install_files():
    """(shell scripts, Python files) the install runs: the assembly and every recipe of the sets."""
    shell = [STAGES / 'golden-assemble.sh', STAGES / 'golden-pristine.sh']
    python = [STAGES / n for n in ('golden-image.py', 'golden-layout.py', 'golden-workspace.py', 'copy-hooks.py',
                                   'recovery-tools.py', 'kdk_intel_payload.py')] + [TOP / 'pipeline' / 'kit.py']
    python += [p for p in (TOP / 'tools' / 'oclp-hardware').rglob('*.py') if not NOT_RUN.search(str(p))]
    python += [p for p in (TOP / 'tools' / 'tahoe-donors').glob('make')]
    for name in recipes():
        folder = TOP / 'patches' / name
        for p in sorted(folder.rglob('*')):
            rel = str(p.relative_to(folder))
            if not p.is_file() or NOT_RUN.search('/' + rel):
                continue
            if p.suffix == '.sh' and (rel.startswith('src/') or rel == 'apply.sh'):
                shell.append(p)
            elif p.suffix == '.py' and rel.startswith('src/'):
                python.append(p)
    return [p for p in shell if p.is_file()], [p for p in python if p.is_file()]


def shell_commands(path):
    text = path.read_text(errors='replace')
    funcs = set(re.findall(r'^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)', text, re.M))
    end = None
    for i, line in enumerate(text.splitlines(), 1):
        if end:
            if line.strip() == end:
                end = None
            continue
        m = HEREDOC.search(line)
        if m:
            end = m.group(1)
        code = re.sub(r"'[^']*'", "''", line)
        code = re.sub(r'"[^"$`]*"', '""', code)
        code = re.sub(r'(^|\s)#.*', '', code)
        for seg in SEP.split(code):
            toks = seg.strip().split()
            while toks and re.match(r'^[A-Za-z_]\w*=', toks[0]):
                toks.pop(0)
            while toks and toks[0] in PREFIX:
                toks.pop(0)
                while toks and toks[0].startswith('-'):
                    toks.pop(0)
            if not toks:
                continue
            t = toks[0]
            if not re.match(r'^/?[A-Za-z_][\w./+-]*$', t) or t in BUILTIN or t in funcs or t.endswith(')'):
                continue
            yield t, i


def python_commands(path):
    for i, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
        if line.lstrip().startswith('#'):
            continue
        for rx in (PY_CMD, PY_LIST, ABSOLUTE):
            for m in rx.finditer(line):
                yield m.group(1), i


class RecoveryCommands(unittest.TestCase):
    def test_list_is_the_recovery(self):
        paths, names = recovery()
        self.assertGreater(len(paths), 400)
        for present in ('/usr/bin/ditto', '/usr/bin/codesign', '/usr/bin/kmutil', '/usr/sbin/diskutil',
                        '/usr/bin/caffeinate', '/usr/sbin/bless'):
            self.assertIn(present, paths)
        for absent in ('rsync', 'install', 'shasum', 'cmp', 'readlink', 'python3'):
            self.assertNotIn(absent, names)

    def test_every_command_of_the_install_path_is_there(self):
        paths, names = recovery()
        host = set()
        for d in ('/bin', '/sbin', '/usr/bin', '/usr/sbin', '/usr/libexec'):
            host |= set(os.listdir(d)) if os.path.isdir(d) else set()
        shell, python = install_files()
        self.assertGreater(len(shell), 10)
        missing = []
        for files, extract, commands_only in ((shell, shell_commands, False), (python, python_commands, True)):
            for f in files:
                for cmd, line in extract(f):
                    base = os.path.basename(cmd)
                    if cmd in ALLOWED or cmd in paths or (str(f.relative_to(TOP)), base) in ALLOWED_IN:
                        continue
                    if cmd.startswith('/'):
                        if os.path.exists(cmd) or base in host:
                            missing.append('%s:%d %s' % (f.relative_to(TOP), line, cmd))
                        continue
                    if base in names or base in STAND_INS or base in KIT_PYTHON:
                        continue
                    if commands_only and base not in host:
                        continue   # a Python string that is no command
                    if not commands_only and base not in host and not shutil.which(base):
                        continue   # a word the extractor took for a command (a path, a variable's text)
                    missing.append('%s:%d %s' % (f.relative_to(TOP), line, cmd))
        self.assertEqual([], sorted(set(missing)), 'commands the Recovery lacks (add a recovery-tools stand-in or use one it has)')

    def test_pristine_uses_no_rsync(self):
        self.assertNotRegex((STAGES / 'golden-pristine.sh').read_text().split('\n# ')[0], r'(?m)^\s*rsync\b')

    def test_assembly_with_only_the_recovery_on_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit, local = Path(tmp) / 'kit', Path(tmp) / 'local'
            py = kit / ('py' if os.uname().machine == 'arm64' else 'py-x86_64') / 'bin'
            py.mkdir(parents=True)
            (py.parent / 'lib/python3.12/site-packages').mkdir(parents=True)   # where the copy gets its sitecustomize
            (py / 'python3.12').symlink_to(sys.executable)
            for d in ('assembly-source', 'site', 'hooks', 'post'):
                (kit / d).mkdir()
            for name in ('recovery-tools.py', 'copy-hooks.py'):
                shutil.copy2(STAGES / name, kit / 'assembly-source')
            (kit / 'kit.json').write_text('{}')
            r = subprocess.run(['/bin/sh', str(STAGES / 'golden-assemble.sh'), str(kit), str(local),
                                '--owner', '%d:%d' % (os.getuid(), os.getgid())],
                               env={'HOME': tmp, 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
                                    'HAO_RECOVERY_COMMANDS': str(LIST)},
                               capture_output=True, text=True, timeout=60)
            out = r.stdout + r.stderr
            self.assertIn('PATH holds only the commands of', out)
            for tool in sorted(STAND_INS - {'awake'}):
                self.assertIn("no %s on this Mac: hao's recovery-tools stands in for it" % tool, out)
            self.assertIn('caffeinate -dimsu keeps this Mac awake', out)
            links = {p.name for p in (local / 'recovery-only').iterdir()}
            self.assertIn('ditto', links)
            self.assertEqual('rsync' in links, os.access('/usr/bin/rsync', os.X_OK))   # only for absolute-path shims
            # the stub kit has no kit.py: the assembly stops at the render, after the PATH and the stand-ins
            self.assertNotEqual(r.returncode, 0)
            # a second run (a resume, a reused --work) starts from fresh copies of the kit: a file added to a copy is
            # gone, the resume records stay, and nothing was written into the kit (no __pycache__)
            (local / 'source' / 'extra.pth').write_text('import os')
            (local / 'work').mkdir(exist_ok=True); (local / 'work' / 'build.json').write_text('{}')
            subprocess.run(['/bin/sh', str(STAGES / 'golden-assemble.sh'), str(kit), str(local),
                            '--owner', '%d:%d' % (os.getuid(), os.getgid())],
                           env={'HOME': tmp, 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HAO_RECOVERY_COMMANDS': str(LIST)},
                           capture_output=True, text=True, timeout=60)
            self.assertFalse((local / 'source' / 'extra.pth').exists())
            self.assertTrue((local / 'source' / 'recovery-tools.py').is_file())
            self.assertTrue((local / 'work' / 'build.json').is_file())
            self.assertEqual([p for p in kit.rglob('__pycache__')], [])
            # every start of the work copy's Python (even with -I, which ignores PYTHONDONTWRITEBYTECODE) writes no
            # bytecode: a failed install leaves the USB installer as its manifest says, so --resume passes the check
            site = local / 'py/lib/python3.12/site-packages/sitecustomize.py'
            self.assertIn('sys.dont_write_bytecode = True', site.read_text())
            self.assertFalse((py.parent / 'lib/python3.12/site-packages/sitecustomize.py').exists())   # not the kit's

    def test_a_work_folder_with_a_space_is_reached_through_a_link_without_one(self):
        # a second USB drive mounts as "HAO 1": recipe hooks and posts (plain sh, some paths unquoted) must not see it
        with tempfile.TemporaryDirectory() as tmp:
            kit, local = Path(tmp) / 'kit', Path(tmp) / 'HAO 1' / 'local'
            py = kit / ('py' if os.uname().machine == 'arm64' else 'py-x86_64') / 'bin'
            py.mkdir(parents=True)
            (py / 'python3.12').symlink_to(sys.executable)
            for d in ('assembly-source', 'site', 'hooks', 'post'):
                (kit / d).mkdir()
            for name in ('recovery-tools.py', 'copy-hooks.py'):
                shutil.copy2(STAGES / name, kit / 'assembly-source')
            (kit / 'kit.json').write_text('{}')
            r = subprocess.run(['/bin/sh', str(STAGES / 'golden-assemble.sh'), str(kit), str(local),
                                '--owner', '%d:%d' % (os.getuid(), os.getgid())],
                               env={'HOME': tmp, 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
                                    'HAO_RECOVERY_COMMANDS': str(LIST)},
                               capture_output=True, text=True, timeout=60)
            out = r.stdout + r.stderr
            used = re.search(r"path has a space; using (\S+)", out)
            self.assertTrue(used, out)
            self.assertNotIn(' ', used.group(1))
            self.assertEqual(os.path.realpath(used.group(1)), os.path.realpath(local))
            self.assertTrue((local / 'source' / 'recovery-tools.py').is_file())       # the copies are in the folder
            for w in (local / 'recovery-bin').iterdir():                              # and the stand-ins name no space
                self.assertNotIn('HAO 1', w.read_text())


if __name__ == '__main__':
    unittest.main()
