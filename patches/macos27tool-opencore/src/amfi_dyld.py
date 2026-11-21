"""Hardened programs keep AMFI's dyld environment policy on an image with SIP's filesystem bit allowed.

  amfi_dyld.py --binder STAGE --boot-kc KC --config CONFIG [--record FILE]
        add (or replace) one OpenCore Kernel.Patch in CONFIG, bound to KC's AMFI; exit 1 and name the check on any
        refusal (CONFIG is then unchanged)
  amfi_dyld.py --binder STAGE --boot-kc KC --show     print the patch entry it would add, as JSON

Why: AMFI answers dyld's policy question (amfi_check_dyld_policy_self) from facts it collects about the process
(macos_dyld_policy_collect_state). One fact is "hardened": csproc_hardened_runtime(proc) && hardeningEnabled().
hardeningEnabled() is AMFI's platformHardeningEnable, which AMFI clears at start when csr_check(CSR_ALLOW_UNRESTRICTED_FS)
succeeds, as it does with the image's csr-active-config 0x803. So on the image no program counts as hardened, and dyld
honours DYLD_INSERT_LIBRARIES and the other DYLD_* variables even in hardened platform programs (measured on 26B131:
a library inserted into m27's hardened stub of /usr/bin/sw_vers, for an ordinary user and for root).

The patch replaces only that call: inside the dyld policy, a process whose own signature has the hardened runtime counts
as hardened again (the result is used only by macos_dyld_policy_*). Everything else platformHardeningEnable decides
(the hardening flags AMFI forces onto platform binaries: library validation, CS_KILL) stays as it is; turning it on as a
whole (amfi_force_policy=2) would force library validation into every Apple program, including those OCLP's Metal
loads into. AMFI then denies hardened programs the DYLD_* path variables (DYLD_INSERT_LIBRARIES, library and framework paths)
as on a Mac with SIP on, unless the program is entitled to them (com.apple.security.cs.allow-dyld-environment-variables,
get-task-allow). Some dyld switches are still read; library interposing and @-paths have their own rules.

Checks, each of which refuses: AMFI is a member of KC; the site occurs exactly once in KC and lies in AMFI's __TEXT;
its call goes to a function of hardeningEnabled()'s shape (reads a RIP-relative flag, returns 1 if set, else
tail-calls on; the shape, not which flag); the
replacement keeps the length. The patch is limited to AMFI (Identifier) and KC's Darwin version (MinKernel = MaxKernel),
so it never touches the Tahoe Recovery's kernel. The build checks its input and the config it writes, not the running
kernel: OpenCore skips a patch whose bytes it does not find, so only a boot test shows it applied.
"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import runpy
import struct
import sys

AMFI = 'com.apple.driver.AppleMobileFileIntegrity'
COMMENT = 'm27: hardened programs keep the dyld environment policy (AMFI)'
# xor r12d,r12d; mov ecx,0; test eax,eax; je +0xb; call hardeningEnabled; movzx ecx,al; shl ecx,7;
# mov eax,0xffffff7f; and eax,[rbx]; or eax,ecx; mov [rbx],eax   (state bit 0x80: "hardened")
SITE = re.compile(rb'\x45\x31\xe4\xb9\x00\x00\x00\x00\x85\xc0\x74\x0b\xe8(....)\x0f\xb6\xc8\xc1\xe1\x07'
                  rb'\xb8\x7f\xff\xff\xff\x23\x03\x09\xc8\x89\x03', re.S)
CALL = 12                       # offset of the call in SITE
CALL_TRUE = b'\xb0\x01\x0f\x1f\x00'   # mov al,1; nop dword [rax]
# push rbp; mov rbp,rsp; cmp dword [rip+X],0; je +4; mov al,1; pop rbp; ret; pop rbp; jmp ...
HARDENING_ENABLED = re.compile(rb'\x55\x48\x89\xe5\x83\x3d....\x00\x74\x04\xb0\x01\x5d\xc3\x5d[\xe9\xeb]', re.S)


class Refused(ValueError):
    pass


def site(data, member):
    """(file offset of the Find bytes, Find, Replace, the hardeningEnabled() address) for KC data and its AMFI
    member (a kcbind Member)."""
    hits = [m for m in SITE.finditer(data)]
    if len(hits) != 1:
        raise Refused(f'the AMFI dyld policy site occurs {len(hits)} times in the collection (expected once)')
    hit = hits[0]
    base, size, fileoff, filesize = member.segments.get('__TEXT', (0, 0, 0, 0))
    if not fileoff <= hit.start() < hit.end() <= fileoff+filesize:
        raise Refused('the AMFI dyld policy site is not in AMFI\'s __TEXT')
    call_va = base+hit.start()-fileoff+CALL
    target = call_va+5+struct.unpack('<i', hit.group(1))[0]
    if not base <= target < base+filesize:
        raise Refused('the site\'s call leaves AMFI')
    start = fileoff+target-base
    if not HARDENING_ENABLED.match(data, start):
        raise Refused(f'the site\'s call target {target:#x} is not hardeningEnabled()')
    find = hit.group(0)
    replace = find[:CALL]+CALL_TRUE+find[CALL+5:]
    if len(replace) != len(find):
        raise Refused('Replace and Find lengths differ')
    return hit.start(), find, replace, target


def entry(find, replace, darwin):
    return dict(Arch='x86_64', Base='', Comment=COMMENT, Count=1, Enabled=True, Find=find, Identifier=AMFI, Limit=0,
                Mask=b'', MaxKernel=darwin, MinKernel=darwin, Replace=replace, ReplaceMask=b'', Skip=0)


def bind(kcbind, data):
    """(OpenCore Kernel.Patch entry, record) for the collection bytes DATA."""
    try:
        kc = kcbind.Collection(data)
        member = kc.member(AMFI)
        darwin = kc.darwin_version()
    except kcbind.BindError as error:
        raise Refused(str(error))
    offset, find, replace, target = site(kc.data, member)
    record = dict(unit='patches/macos27tool-opencore', operation='amfi-dyld', boot_kc_sha256=kc.sha256,
                  amfi_uuid=member.uuid.hex() if member.uuid else None, darwin_version=darwin,
                  site_fileoff=hex(offset), hardening_enabled=hex(target), find=find.hex(), replace=replace.hex())
    return entry(find, replace, darwin), record


def install(config, patch):
    """CONFIG (a parsed config.plist) with PATCH replacing any earlier entry of the same Comment."""
    kernel = config.setdefault('Kernel', {})
    kernel['Patch'] = [p for p in kernel.get('Patch', []) if p.get('Comment') != COMMENT]+[patch]
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--binder', required=True, type=Path, help='tools/kc-binder as make.sh staged it')
    parser.add_argument('--boot-kc', required=True, type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--record', type=Path)
    parser.add_argument('--show', action='store_true')
    args = parser.parse_args(argv)
    kc_bind = runpy.run_path(str(Path(__file__).resolve().parent/'kc_bind.py'))
    try:
        kcbind = kc_bind['binder_modules'](args.binder)[0]
        patch, record = bind(kcbind, args.boot_kc.read_bytes())
    except (Refused, kc_bind['Refused']) as error:
        print(f'amfi_dyld: refused: {error}', file=sys.stderr)
        return 1
    if args.show or not args.config:
        print(json.dumps(dict(record, entry={k: v.hex() if isinstance(v, bytes) else v for k, v in patch.items()}),
                         indent=1))
        return 0
    config = install(plistlib.loads(args.config.read_bytes()), patch)
    args.config.write_bytes(plistlib.dumps(config))
    if args.record:
        record['config_sha256'] = hashlib.sha256(args.config.read_bytes()).hexdigest()
        args.record.write_text(json.dumps(record, indent=1)+'\n')
    print(f'amfi_dyld: hardened programs keep the dyld environment policy (AMFI {record["amfi_uuid"]}, '
          f'Darwin {record["darwin_version"]}, site {record["site_fileoff"]})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
