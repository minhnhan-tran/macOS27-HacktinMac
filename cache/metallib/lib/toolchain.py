"""Where cache/metallib's Metal IR tools come from.

By default every tool runs through `xcrun` (Xcode or the Metal toolchain asset). With M27_METAL_LLVM set to a
folder holding LLVM 15.0.7's `llvm-as` and `llvm-dis` (and optionally `opt`), the AIR tools run from there instead,
so a Mac without Xcode (the user's Mac, an Intel Mac started from m27) can make the golden-gate rows:

    xcrun metal-objdump --disassemble X  ->  llvm-dis -opaque-pointers=0 X -o -
    xcrun metal-as ARGS                  ->  llvm-as -opaque-pointers=0 ARGS
    xcrun metal-opt ARGS                 ->  opt -opaque-pointers=0 ARGS

AIR is typed-pointer LLVM IR; 15.0.7 is the last release that still writes typed pointers. Anything else asked of
xcrun (`metal -c`, `metallib`, `clang`) has no bundled counterpart and is refused, so a recipe that needs Xcode
fails at once instead of opening the developer-tools install dialog. With the 26B131 golden-gate profile the
output is the same modules as xcrun's (Apple's metal-objdump prints the same text), not the same bytes.
"""
import hashlib
import os
from pathlib import Path

ENV = 'M27_METAL_LLVM'
TOOLS = {'metal-as': 'llvm-as', 'metal-objdump': 'llvm-dis', 'metal-opt': 'opt'}


class Unavailable(RuntimeError):
    pass


def bundled():
    value = os.environ.get(ENV)
    return Path(value) if value else None


def resolve(argv):
    """The command to run for ARGV (a list). Only `xcrun` commands change, and only when M27_METAL_LLVM is set."""
    argv = [str(a) for a in argv]
    folder = bundled()
    if folder is None or not argv or argv[0] != 'xcrun':
        return argv
    if len(argv) < 2 or argv[1] not in TOOLS:
        raise Unavailable('%s: needs Xcode (xcrun %s); %s names a bundled LLVM, which has no such tool'
                          % (' '.join(argv), argv[1] if len(argv) > 1 else '', ENV))
    tool, rest = argv[1], argv[2:]
    binary = folder / TOOLS[tool]
    if not binary.is_file():
        raise Unavailable('%s: %s is not in %s=%s' % (' '.join(argv), TOOLS[tool], ENV, folder))
    if tool == 'metal-objdump':
        if len(rest) != 2 or rest[0] != '--disassemble':
            raise Unavailable('%s: only metal-objdump --disassemble FILE is mapped' % ' '.join(argv))
        return [str(binary), '-opaque-pointers=0', rest[1], '-o', '-']
    return [str(binary), '-opaque-pointers=0', *rest]


def identity():
    """For build records: which tools made the output (None = xcrun)."""
    folder = bundled()
    if folder is None:
        return None
    return {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
            for name in sorted(TOOLS.values()) if (folder / name).is_file()}


def source(unit):
    """{commit, dirty} of the checkout holding UNIT (a folder), for build records. From git when the checkout has
    one; otherwise (a Mac without the command-line tools, where /usr/bin/git only offers to install them) from the
    checkout's .m27-source ({"commit": ..., "source": URL}, written when the source archive was fetched), dirty
    then None (unknown). Git is never called without a .git, so no install dialog opens."""
    import json
    import subprocess
    unit = Path(unit).resolve()
    top = next((p for p in (unit, *unit.parents) if (p / '.git').exists() or (p / '.m27-source').is_file()), None)
    if top is None:
        raise SystemExit('cache/metallib: no git checkout and no .m27-source above %s: cannot say which source made this' % unit)
    if (top / '.git').exists():
        git = lambda *a: subprocess.run(['git', '-C', str(unit), *a], capture_output=True, text=True, check=True).stdout.strip()
        return dict(commit=git('rev-parse', 'HEAD'), dirty=bool(git('status', '--porcelain', '--', str(unit))))
    data = json.loads((top / '.m27-source').read_text())
    return dict(commit=data['commit'], dirty=None)
