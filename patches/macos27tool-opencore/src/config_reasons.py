"""Every enabled part of an image's OpenCore config.plist has a recorded reason (PROTOCOL S18).

  config_reasons.py check CONFIG [--reasons FILE] [--kexts DIR] [--target-data FILE]
        exit 1 and name each element without a reason; with --kexts (the ESP's EFI/OC/Kexts), also each enabled
        kext whose files are not exactly the ones kext-provenance.json pins. The target's platform_data
        (--target-data, default M27_TARGET_DATA) adds the model's own reasons and kext provenance
  config_reasons.py list CONFIG                       print the config's elements, one per line

An element is one setting someone chose: an enabled kext, Kernel Patch or Block entry, ACPI or DeviceProperties
entry, a Kernel/Booter/UEFI quirk that is on (with its value when it is a number), a Misc.Security value, an NVRAM
Add key (with its value for the keys that weaken security: csr-active-config, revblock, revpatch), an NVRAM Delete key,
and WriteFlash. Boot-args' arguments have their reasons in edits/config.json (arg_reasons). config-reasons.json maps
each element to its reason; a reason that starts with "UNTRACED:" says what is not known yet and which test settles it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import sys

REASONS = Path(__file__).resolve().parent / 'config-reasons.json'
PROVENANCE = Path(__file__).resolve().parent / 'kext-provenance.json'
VALUE_KEYS = {'csr-active-config', 'revblock', 'revpatch'}


def _value(v):
    return v.hex() if isinstance(v, bytes) else str(v)


def elements(config):
    """The sorted element strings of a parsed config.plist."""
    out = set()
    kernel = config.get('Kernel', {})
    out |= {'kext:' + k['BundlePath'] for k in kernel.get('Add', []) if k.get('Enabled')}
    out |= {'kernel-patch:' + p.get('Comment', '?') for p in kernel.get('Patch', []) if p.get('Enabled')}
    out |= {'kernel-block:' + b.get('Identifier', '?') for b in kernel.get('Block', []) if b.get('Enabled')}
    out |= {'acpi:' + a.get('Path', '?') for a in config.get('ACPI', {}).get('Add', []) if a.get('Enabled')}
    out |= {'device:' + d for d in config.get('DeviceProperties', {}).get('Add', {})}
    for section, quirks in (('kernel', kernel.get('Quirks', {})), ('booter', config.get('Booter', {}).get('Quirks', {})),
                            ('uefi', config.get('UEFI', {}).get('Quirks', {}))):
        for name, v in quirks.items():
            if v is True:
                out.add(f'{section}-quirk:{name}')
            elif isinstance(v, int) and not isinstance(v, bool) and v > 0:      # -1 and 0: not set
                out.add(f'{section}-quirk:{name}={v}')
    out |= {f'security:{k}={_value(v)}' for k, v in config.get('Misc', {}).get('Security', {}).items()
            if k not in ('PasswordHash', 'PasswordSalt')}
    nvram = config.get('NVRAM', {})
    for guid, keys in nvram.get('Add', {}).items():
        for k, v in keys.items():
            out.add(f'nvram-add:{guid}:{k}' + (f'={_value(v)}' if k in VALUE_KEYS else ''))
    for guid, keys in nvram.get('Delete', {}).items():
        out |= {f'nvram-delete:{guid}:{k}' for k in keys}
    out.add(f'nvram:WriteFlash={nvram.get("WriteFlash")}')
    return sorted(out)


def target_data(path=None):
    """The target's platform_data (M27_TARGET_DATA, docs/layout.md), {} when there is none. A model's own reasons
    ("opencore_reasons"), its kext provenance ("kext_provenance") and whether it is the generic image
    ("opencore_generic") live there, so this OS-layer file names no model."""
    path = path or os.environ.get('M27_TARGET_DATA')
    return json.loads(Path(path).read_text()) if path and Path(path).is_file() else {}


def load_reasons(path=REASONS, data=None):
    """config-reasons.json plus the target's opencore_reasons (a model's entry replaces the base one of that name)."""
    reasons = dict(json.loads(Path(path).read_text()), **(data or {}).get('opencore_reasons', {}))
    bad = [k for k, v in reasons.items() if not isinstance(v, str) or not v.strip()]
    if bad:
        raise ValueError('empty reasons: ' + ', '.join(bad))
    return reasons


GENERIC_ONLY = 'generic image only'
def is_generic(config, data=None):
    """The generic image's config (opencore-generic): its target says so ("opencore_generic" in platform_data) and
    the SMBIOS is created on firmware that has none."""
    return (bool((data or {}).get('opencore_generic'))
            and config.get('PlatformInfo', {}).get('UpdateSMBIOSMode') == 'Create')


def missing(config, reasons, data=None):
    """Elements without a reason, and elements whose reason is the generic image's in a config that is not it
    (VirtualSMC and the non-Apple-firmware quirks never reach a Mac's image)."""
    generic = is_generic(config, data)
    return [e for e in elements(config) if e not in reasons
            or (reasons[e].startswith(GENERIC_ONLY) and not generic)]


def provenance_problems(config, kexts, provenance):
    """Why an enabled kext's bundle is not the one kext-provenance.json pins: every enabled top-level bundle must be
    listed; a third-party one ("oclp-payload", "oclp-generated") must hold exactly the pinned files with the pinned
    sha256 (traced to upstream OCLP 3.0.0-rc.5 and, where one exists, the Acidanthera release). m27's own kexts
    ("m27") are bound or stamped per image and checked by kc_bind.py and repairs.py instead."""
    bad = []
    bundles = sorted({k['BundlePath'].split('/Contents/')[0] for k in config.get('Kernel', {}).get('Add', [])
                      if k.get('Enabled')})
    for name in bundles:
        entry = provenance.get(name)
        if entry is None:
            bad.append(f'{name}: no provenance (kext-provenance.json); not loaded from an unknown source')
            continue
        if entry['kind'] == 'm27':
            continue
        folder = Path(kexts) / name
        have = {str(f.relative_to(folder)): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in sorted(folder.rglob('*')) if f.is_file()} if folder.is_dir() else {}
        if have != entry['files']:
            changed = sorted(k for k in set(have) | set(entry['files']) if have.get(k) != entry['files'].get(k))
            bad.append(f'{name}: files differ from {entry["source"]}: {", ".join(changed[:5])}')
    return bad


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('check', 'list'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--reasons', type=Path, default=REASONS)
    parser.add_argument('--kexts', type=Path, help="the ESP's EFI/OC/Kexts: also check each enabled kext's provenance")
    parser.add_argument('--provenance', type=Path, default=PROVENANCE)
    parser.add_argument('--target-data', type=Path, help="the target's platform_data (default: M27_TARGET_DATA)")
    args = parser.parse_args()
    data = target_data(args.target_data)
    with open(args.config, 'rb') as f:
        config = plistlib.load(f)
    if args.command == 'list':
        print('\n'.join(elements(config)))
        return 0
    reasons = load_reasons(args.reasons, data)
    lacking = missing(config, reasons, data)
    if lacking:
        print('config-reasons: no recorded reason (patches/macos27tool-opencore/src/config-reasons.json), or a generic-image-only'
              ' element in a model image, for:', file=sys.stderr)
        print('\n'.join('  ' + e for e in lacking), file=sys.stderr)
        return 1
    if args.kexts:
        provenance = dict(json.loads(args.provenance.read_text()), **data.get('kext_provenance', {}))
        bad = provenance_problems(config, args.kexts, provenance)
        if bad:
            print('config-reasons: kexts whose source is not the pinned upstream one:', file=sys.stderr)
            print('\n'.join('  ' + b for b in bad), file=sys.stderr)
            return 1
    untraced = sum(1 for e in elements(config) if reasons[e].startswith('UNTRACED:'))
    print(f'config-reasons: every element of {args.config.name} has a reason ({untraced} untraced, under test)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
