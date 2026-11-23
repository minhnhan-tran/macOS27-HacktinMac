#!/usr/bin/env python3
"""edit-gva.py — Make OCLP's AppleGVA report Intel hardware JPEG decode unavailable.

Usage: edit-gva.py AppleGVA-in AppleGVA-out [--sign]
"""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

EXPECT_IN = '2a4fb1829b881c035bc416103a8f278fd5ffc46dfdd09c82eab7a242bf1b16c7'
EXPECT_OUT = '064010304c1831fc7531831bf526efed2844629d03f78231442ed90eb15f8b6a'

# _AVFJPG_GetJPEGDecodeAvailability (OCLP AppleGVA 11.7.10 x86_64):
# push rbp; mov rbp,rsp; push r15; push r14 ... -> xor eax,eax; ret
# (0 is the function's own "unsupported" return value.)
BEFORE = bytes.fromhex('554889e541574156')
AFTER = bytes.fromhex('31c0c3e541574156')
SYMBOL = '_AVFJPG_GetJPEGDecodeAvailability'
MH_MAGIC_64 = 0xFEEDFACF


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def edit_bytes(data):
    if int.from_bytes(data[:4], 'little') != MH_MAGIC_64:
        raise ValueError('expected an x86_64 thin Mach-O')
    n = data.count(BEFORE)
    if n != 1:
        raise ValueError(f'expected 1 copy of {SYMBOL} prologue ({BEFORE.hex()}), found {n}')
    at = data.index(BEFORE)
    return data[:at] + AFTER + data[at + len(AFTER):]


def sign(path):
    ident = 'com.apple.AppleGVA'
    for line in subprocess.check_output(['codesign', '-dv', str(path)], stderr=subprocess.STDOUT, text=True).splitlines():
        if line.startswith('Identifier='):
            ident = line.split('=', 1)[1]
    ent = path.with_suffix('.entitlements.plist')
    ent_xml = subprocess.check_output(['codesign', '-d', '--entitlements', ':-', str(path)], stderr=subprocess.DEVNULL)
    cmd = ['codesign', '--force', '--sign', '-', '--identifier', ident]
    if ent_xml.strip():
        ent.write_bytes(ent_xml)
        cmd += ['--entitlements', str(ent)]
    try:
        subprocess.check_call(cmd + [str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        ent.unlink(missing_ok=True)


def main():
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != '--sign'):
        print('usage: edit-gva.py AppleGVA-in AppleGVA-out [--sign]', file=sys.stderr)
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    do_sign = len(sys.argv) == 4
    data = src.read_bytes()
    h = sha256(data)
    if h != EXPECT_IN:
        raise SystemExit(f'edit-gva: unexpected AppleGVA input sha256 {h} (want {EXPECT_IN})')
    out = edit_bytes(data)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if do_sign:
        shutil.copy2(src, dst)
        dst.write_bytes(out)
        sign(dst)
        final_h = sha256(dst.read_bytes())
        if final_h != EXPECT_OUT:
            raise SystemExit(f'edit-gva: unexpected signed output sha256 {final_h} (want {EXPECT_OUT})')
        print(f'edit-gva: {h[:12]} -> {final_h[:12]} (signed)')
    else:
        dst.write_bytes(out)
        print(f'edit-gva: {h[:12]} -> {sha256(out)[:12]} (unsigned)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
