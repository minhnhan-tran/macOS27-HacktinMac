"""recovery-tools.py NAME ARGS...: the POSIX tools the assembly and its hooks call that a macOS Recovery does not have.

The Tahoe Recovery (BaseSystem 25G342) has no /usr/bin/install, shasum, cmp or readlink (QEMU 2026-11-13:
golden-assemble.sh stopped at "install: command not found"), and the hooks use all four. golden-assemble.sh makes a
wrapper for each one missing on the Mac it runs on, first on PATH, which runs this file with the kit's CPython. Only
the forms the assembly uses are implemented; anything else exits 64 naming the option, so a new use fails loudly
instead of doing something else:

  install -d [-o U] [-g G] [-m MODE] DIR...       make directories (with parents)
  install [-o U] [-g G] [-m MODE] SRC DEST         copy SRC to DEST (a file, or into a directory), then owner and mode
  shasum -a 256 FILE...                            "<hex>  <file>" lines, as shasum prints them
  cmp -s A B                                       exit 0 when the bytes are equal, 1 when not, 2 on an error
  readlink PATH                                    the link's target; exit 1 when PATH is not a link
  awake PID                                        hold sleep assertions (as caffeinate -dims -w PID) until PID exits;
                                                   golden-assemble.sh uses it where the Mac has no caffeinate

Owners and groups are names (root, wheel) or numbers. Python 3.9+, standard library only.
"""
import ctypes
import grp
import hashlib
import os
import pwd
import shutil
import sys
import time

USAGE = 64


def die(name, msg, code=USAGE):
    print('%s (hao recovery-tools): %s' % (name, msg), file=sys.stderr)
    return code


def uid(v):
    return int(v) if v.isdigit() else pwd.getpwnam(v).pw_uid


def gid(v):
    return int(v) if v.isdigit() else grp.getgrnam(v).gr_gid


def install(args):
    owner = group = mode = None
    directories = False
    i = 0
    while i < len(args) and args[i].startswith('-') and args[i] != '-':
        a = args[i]
        if a == '-d':
            directories = True
            i += 1
        elif a in ('-o', '-g', '-m') and i + 1 < len(args):
            v = args[i + 1]
            if a == '-o':
                owner = v
            elif a == '-g':
                group = v
            else:
                try:
                    mode = int(v, 8)
                except ValueError:
                    return die('install', 'mode %r is not octal' % v)
            i += 2
        else:
            return die('install', 'option %s is not supported here' % a)
    rest = args[i:]

    def finish(path):
        if owner is not None or group is not None:
            os.chown(path, uid(owner) if owner is not None else -1, gid(group) if group is not None else -1)
        if mode is not None:
            os.chmod(path, mode)

    if directories:
        if not rest:
            return die('install', 'no directory named')
        for d in rest:
            os.makedirs(d, exist_ok=True)
            finish(d)
        return 0
    if len(rest) != 2:
        return die('install', 'expected SRC DEST, got %d operands' % len(rest))
    src, dest = rest
    if os.path.isdir(dest):
        dest = os.path.join(dest, os.path.basename(src))
    if os.path.lexists(dest):
        os.unlink(dest)           # as BSD install: a new file, never written through a link or into a running binary
    shutil.copyfile(src, dest)
    os.chmod(dest, 0o755 if mode is None else mode)
    finish(dest)
    return 0


def shasum(args):
    if args[:2] != ['-a', '256'] or len(args) < 3:
        return die('shasum', 'only "shasum -a 256 FILE..." is supported here')
    code = 0
    for f in args[2:]:
        h = hashlib.sha256()
        try:
            with open(f, 'rb') as fh:
                for block in iter(lambda: fh.read(8 << 20), b''):
                    h.update(block)
        except OSError as x:
            print('shasum: %s: %s' % (f, x.strerror), file=sys.stderr)
            code = 1
            continue
        print('%s  %s' % (h.hexdigest(), f))
    return code


def cmp(args):
    if len(args) != 3 or args[0] != '-s':
        return die('cmp', 'only "cmp -s A B" is supported here', 2)
    a, b = args[1:]
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return 1
        with open(a, 'rb') as fa, open(b, 'rb') as fb:
            while True:
                x, y = fa.read(8 << 20), fb.read(8 << 20)
                if x != y:
                    return 1
                if not x:
                    return 0
    except OSError:
        return 2


def readlink(args):
    if len(args) != 1 or args[0].startswith('-'):
        return die('readlink', 'only "readlink PATH" is supported here')
    try:
        print(os.readlink(args[0]))
        return 0
    except OSError:
        return 1


# What caffeinate -dims asserts: no idle display/disk/system sleep, no system sleep on AC power. A Recovery idle-sleeps
# after about ten minutes without input (bench2 2355: the QEMU install stopped 10.5 min in), so the whole install
# holds them.
AWAKE_TYPES = ('PreventUserIdleDisplaySleep', 'PreventDiskIdle', 'PreventUserIdleSystemSleep', 'PreventSystemSleep')


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def awake(args, poll=2.0):
    if len(args) != 1 or not args[0].isdigit():
        return die('awake', 'only "awake PID" is supported here')
    pid = int(args[0])
    try:
        cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        iokit = ctypes.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
    except OSError as e:
        return die('awake', 'no IOKit here (%s): the Mac may sleep during the install' % e, 1)
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int
    iokit.IOPMAssertionCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                                  ctypes.POINTER(ctypes.c_uint32)]
    iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
    cfstr = lambda s: cf.CFStringCreateWithCString(None, s.encode(), 0x08000100)   # kCFStringEncodingUTF8
    name = cfstr('macOS27Tool install: assembling the macOS image')
    held = []
    for kind in AWAKE_TYPES:
        aid = ctypes.c_uint32(0)
        if iokit.IOPMAssertionCreateWithName(cfstr(kind), 255, name, ctypes.byref(aid)) == 0:   # kIOPMAssertionLevelOn
            held.append(aid.value)
        else:
            print('awake (hao recovery-tools): %s not granted' % kind, file=sys.stderr)
    if not held:
        return die('awake', 'no sleep assertion granted: the Mac may sleep during the install', 1)
    try:
        while alive(pid):
            time.sleep(poll)
    finally:
        for aid in held:
            iokit.IOPMAssertionRelease(aid)
    return 0


TOOLS = {'awake': awake, 'install': install, 'shasum': shasum, 'cmp': cmp, 'readlink': readlink}


def main(argv):
    if not argv or argv[0] not in TOOLS:
        print(__doc__, file=sys.stderr)
        return USAGE
    return TOOLS[argv[0]](argv[1:])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
