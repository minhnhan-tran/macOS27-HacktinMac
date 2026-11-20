#!/usr/bin/env python3
"""Build a target's base EFI (the ESP's EFI/ tree) from its "efi_build" and the pinned OpenCore release.

The target's "efi_build" says which base:
- "base": "oclp" (the default when "base" is absent; MacBookPro9,2): OCLP's OpenCore build for the target's model from
  the pinned OCLP source (--source, --site, --revision) and "settings" (OCLP build settings, SETTINGS), without OCLP's
  application; every *.efi it generated is replaced by the release's.
- "base": "qemu_reference" (generic): the qemu_reference_efi row's tree (--reference: the OpenCore tree tested on OVMF,
  EFI/BOOT/BOOTx64.efi and EFI/OC with OpenCore.efi, Drivers, Kexts and its config.plist), copied as
  tools/kernel-foundation vm_esp.py copies it, its *.efi replaced by the release's, its apfs_aligned.efi loaded as
  vm_esp.py loads it. No OCLP configuration is generated; "settings" is refused.
The set's post recipes then complete the ESP (opencore-<model>, macos27tool-opencore).
"""
import argparse
import hashlib
import json
import logging
from pathlib import Path
import plistlib
import re
import runpy
import zipfile
import shutil
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = {'sip_status', 'secure_status', 'serial_settings', 'opencore_debug',
            'showpicker', 'oc_timeout', 'disable_mediaanalysisd', 'boot_efi'}
BASES = ('oclp', 'qemu_reference')
REFERENCE_KEYS = {'base', 'comment'}


def base_of(recipe, name):
    """The base of target NAME's "efi_build" (refused when it has none, or an unknown one)."""
    if 'efi_build' not in recipe:   # never another target's ESP (golden-run refuses such a target before staging)
        raise ValueError('target %s has no "efi_build": its base ESP is not defined' % name)
    base = recipe['efi_build'].get('base', 'oclp')
    if base not in BASES:
        raise ValueError('target %s: unknown "efi_build" base %r (known: %s)' % (name, base, ', '.join(BASES)))
    if base == 'qemu_reference' and set(recipe['efi_build']) - REFERENCE_KEYS:
        raise ValueError('target %s: "efi_build" base qemu_reference takes no %s' % (
            name, ', '.join(sorted(set(recipe['efi_build']) - REFERENCE_KEYS))))
    return base


def release_binaries(tree, archive, rename=lambda relative: relative):
    """Replace each *.efi under TREE with the release ARCHIVE's X64/ copy of the same path (RENAME maps a path of TREE
    to the release's); return the replaced paths. Files the release does not have are kept."""
    replaced = []
    with zipfile.ZipFile(archive) as release:
        members = set(release.namelist())
        for path in sorted(tree.rglob('*.efi')):
            relative = str(path.relative_to(tree))
            name = 'X64/'+rename(relative)
            if name in members:
                path.write_bytes(release.read(name))
                replaced.append(relative)
    return replaced


def files_of(out):
    return {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob('*')) if p.is_file()}


def build_reference(recipe, target, opencore, selected, reference, out):
    """Base qemu_reference: the reference tree with the selected release's binaries (see the module docstring)."""
    if reference is None:
        raise ValueError('target %s: "efi_build" base qemu_reference needs --reference (row qemu_reference_efi)'
                         % target.stem)
    reference = Path(reference).resolve()
    vm_esp = runpy.run_path(str(ROOT/'tools/kernel-foundation/vm_esp.py'))
    lock = runpy.run_path(str(ROOT/'pipeline/lock.py'))
    for name in ('EFI/BOOT/BOOTx64.efi', 'EFI/OC/OpenCore.efi', 'EFI/OC/config.plist'):
        if not (reference/name).is_file():
            raise ValueError('reference %s has no %s' % (reference, name))
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='efi-build-', dir=out.parent) as temporary:
        work = Path(temporary)/'esp'
        config = vm_esp['reference_tree'](reference, work)
        replaced = release_binaries(work, opencore)
        apfs = work/'EFI/OC/Drivers/apfs_aligned.efi'
        if apfs.is_file():
            vm_esp['add_apfs_driver'](config, work, apfs)
        (work/'EFI/OC/config.plist').write_bytes(plistlib.dumps(config))
        shutil.copytree(work, out)
    shared = ROOT/'tools/kernel-foundation/vm_esp.py'
    return dict(schema=1, operation='qemu-reference-base-efi', base='qemu_reference', model=recipe['model'],
                builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                shared_sha256={str(shared.relative_to(ROOT)): hashlib.sha256(shared.read_bytes()).hexdigest()},
                opencore_version=selected[1], opencore_variant=selected[2],
                opencore_archive_sha256=hashlib.sha256(opencore.read_bytes()).hexdigest(), opencore_replaced=replaced,
                reference_sha256=lock['content_sha256'](str(reference)),
                target_sha256=hashlib.sha256(target.read_bytes()).hexdigest(), files=files_of(out),
                scope='base EFI; image-specific hao additions are applied during image assembly')


def build(source, site, target, revision, opencore, out, reference=None):
    target, out = Path(target), Path(out).resolve()
    opencore=Path(opencore)
    selected=re.fullmatch(r'OpenCore-([0-9.]+)-(DEBUG|RELEASE)\.zip',opencore.name)
    if not selected:raise ValueError('expected a versioned OpenCore release archive')
    recipe = json.loads(target.read_text())
    base = base_of(recipe, target.stem)
    if out.exists():
        raise ValueError('EFI output must be new')
    if base == 'qemu_reference':
        return build_reference(recipe, target, opencore, selected, reference, out)
    if reference is not None:
        raise ValueError('target %s: --reference is for "efi_build" base qemu_reference only' % target.stem)
    source = Path(source).resolve()
    settings = recipe['efi_build']['settings']
    if set(settings) - SETTINGS:
        raise ValueError('unsupported EFI setting: '+str(sorted(set(settings)-SETTINGS)))
    if site:
        sys.path.insert(0, str(Path(site).resolve()))
    # Upstream __init__ imports the application entry point. Import only the
    # source package namespace: configuration generation needs no GUI or updater.
    package = types.ModuleType('opencore_legacy_patcher')
    package.__path__ = [str(source/'opencore_legacy_patcher')]
    sys.modules[package.__name__] = package
    from opencore_legacy_patcher import constants
    from opencore_legacy_patcher.detections import device_probe
    from opencore_legacy_patcher.efi_builder.build import BuildOpenCore
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='efi-build-', dir=out.parent) as temporary:
        configuration = constants.Constants()
        configuration.current_path = Path(temporary)
        configuration.payload_path = source/'payloads'
        configuration.custom_model = recipe['model']
        # External-model generation uses OCLP's model dataset. Never probe the
        # build host, read its preferences, or generate replacement serials.
        configuration.computer = device_probe.Computer()
        for name, value in settings.items():
            if type(value) is not type(getattr(configuration,name)):
                raise ValueError('wrong type for EFI setting: '+name)
            setattr(configuration,name,value)
        BuildOpenCore(recipe['model'], configuration)
        generated = configuration.opencore_release_folder
        config_path = generated/'EFI/OC/config.plist'
        config = plistlib.loads(config_path.read_bytes())
        config['#Revision']['Build-Version'] = configuration.patcher_version+' source '+revision
        config['#Revision']['OpenCore-Version'] = selected[1]+' - '+selected[2]
        # The model builder's bundled core can differ from our selected release.
        # Replace the complete selected OpenCore binary family, not just its core.
        release_binaries(generated, opencore, lambda relative: 'EFI/BOOT/BOOTx64.efi'
                         if relative=='System/Library/CoreServices/boot.efi' else relative)
        # OpenCore discovers this standard Windows path without BlessOverride.
        # Both pinned validators reject the redundant upstream entry.
        config['Misc']['BlessOverride']=[p for p in config['Misc']['BlessOverride']
                                         if p!='\\EFI\\Microsoft\\Boot\\bootmgfw.efi']
        config_path.write_bytes(plistlib.dumps(config))
        shutil.copytree(generated,out)
        record = dict(schema=1, operation='oclp-base-efi', model=recipe['model'],
                      source='https://github.com/dortania/OpenCore-Legacy-Patcher',
                      source_revision=revision, builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      oclp_version=configuration.patcher_version,
                      opencore_version=selected[1], opencore_variant=selected[2],
                      opencore_archive_sha256=hashlib.sha256(opencore.read_bytes()).hexdigest(), settings=settings,
                      target_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                      files=files_of(out),
                      scope='base EFI; image-specific hao additions are applied during image assembly')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ('source','target','opencore','out','record'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--site',type=Path)
    parser.add_argument('--reference',type=Path,help='the qemu_reference_efi row (base qemu_reference only)')
    parser.add_argument('--revision',required=True)
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # OCLP's terminal helper parses process arguments even through its build API.
    # Our CLI has already been parsed; do not expose it to upstream's parser.
    arguments=sys.argv
    try:
        sys.argv=sys.argv[:1]
        result=build(args.source,args.site,args.target,args.revision,args.opencore,args.out,args.reference)
    finally:
        sys.argv=arguments
    args.record.write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
