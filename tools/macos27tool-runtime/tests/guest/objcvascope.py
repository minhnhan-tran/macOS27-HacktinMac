#!/usr/bin/env python3
"""Native method collisions with named variadic recipes, versus native ARM."""
import os
from pathlib import Path
import subprocess

# Keep the authored helper native to exercise the bridge.
os.environ["M27_NATIVE_APP_LIBS"] = "1"

root = Path(__file__).resolve().parents[2]
here = Path(__file__).resolve().parent
out = Path(os.environ.get('OUT', root / 'build/objcvascope')).resolve()
out.mkdir(parents=True, exist_ok=True)
m27 = os.environ.get('M27', str(root / 'build/m27'))
base = os.environ.get('M27_BASE')

def command(args, **kw):
    return subprocess.run(args, check=True, **kw)

lib = out / 'libobjcvascopehelper.dylib'
command(['clang', '-arch', 'x86_64', '-arch', 'arm64', '-arch', 'arm64e', '-dynamiclib', '-O1', '-fobjc-arc', '-framework', 'Foundation', str(here / 'objcvascopehelper.m'), '-install_name', str(lib), '-o', str(lib)])
command(['codesign', '-f', '-s', '-', str(lib)])
for arch in ('arm64', 'arm64e'):
    binary = out / ('probe.' + arch)
    command(['clang', '-arch', arch, '-O1', '-fobjc-arc', '-framework', 'Foundation', str(here / 'objcvascope.m'), '-L' + str(out), '-lobjcvascopehelper', '-o', str(binary)])
    command(['codesign', '-f', '-s', '-', str(binary)])

def run(name, args, env=None):
    p = subprocess.run(['timeout', '-s', 'KILL', '60', *map(str, args)], capture_output=True, env={**os.environ, **(env or {})})
    (out / (name + '.out')).write_bytes(p.stdout)
    (out / (name + '.err')).write_bytes(p.stderr)
    return p

cache = out / 'aot'
cache.mkdir(mode=0o700, exist_ok=True)
p = run('aotgen', [m27, '--aot', '--cache', cache, out / 'probe.arm64e'], {})
assert p.returncode == 0 and list(cache.glob('*.aot')), p.stderr.decode()
for section in ('controls', 'collision'):
    native = run(section + '.native', [out / 'probe.arm64', section])
    assert native.returncode == 0, native.stderr.decode()
    for mode in ('jit', 'interp', 'aot'):
        args = [m27] + (['--interp'] if mode == 'interp' else []) + [out / 'probe.arm64e', section]
        env = {'M27_CACHE': str(cache), 'M27_VERBOSE': '1'} if mode == 'aot' else {'M27_NO_AOT': '1'}
        actual = run(section + '.' + mode, args, env)
        assert actual.returncode == 0 and actual.stdout == native.stdout, (section, mode, actual.returncode, actual.stdout, actual.stderr.decode())
        if mode == 'aot':
            assert b'AOT cache ' in actual.stderr and b' loaded:' in actual.stderr, actual.stderr.decode()
        print(section, mode, 'SAME')
    if base:
        actual = run(section + '.baseline', [base, out / 'probe.arm64e', section], {'M27_NO_AOT': '1'})
        if section == 'collision':
            assert actual.returncode != 0 or actual.stdout != native.stdout, 'baseline must reproduce the collision'
        else:
            assert actual.returncode == 0 and actual.stdout == native.stdout
        print(section, 'baseline', actual.returncode)
print('objcvascope: PASS')
