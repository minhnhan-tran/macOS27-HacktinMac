"""Assemble an OpenCore EFI tree for Darwin 27 from the tested OVMF configuration.

Adds the IntelCommpage KEC, any number of extra kexts (for example M27Exec), the
kernel repairs from tools/kernel-fixes.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import tarfile
import tempfile
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def reference_tree(reference, out):
    """Copy the tested reference's EFI/ into the new folder OUT; return its parsed EFI/OC/config.plist.
    Shared with pipeline/stages/build-efi.py (base qemu_reference)."""
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(reference/'EFI', out/'EFI')
    return plistlib.loads((out/'EFI/OC/config.plist').read_bytes())


def add_apfs_driver(config, out, apfs):
    """Load APFS driver APFS from OUT's EFI/OC/Drivers (copied there unless it is that file) and connect it to every
    APFS container: host-created APFS containers may lack a usable jumpstart driver (archived B1/B2)."""
    dest = out/'EFI/OC/Drivers'/apfs.name
    if not (dest.exists() and dest.samefile(apfs)):
        shutil.copy2(apfs, dest)
    if not any(entry['Path'] == apfs.name for entry in config['UEFI']['Drivers']):
        config['UEFI']['Drivers'].append(dict(Arguments='', Comment=apfs.name,
            Enabled=True, LoadEarly=False, Path=apfs.name))
    config['UEFI']['APFS']['GlobalConnect'] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True, help='Directory containing tested VM EFI/')
    parser.add_argument('--donor-esp', type=Path, required=True, help='OCLP EFI tar used by the target image')
    parser.add_argument('--kext', type=Path, action='append', default=[],
                        help='Extra kext bundle to load on this Darwin major (repeatable)')
    parser.add_argument('--darwin-major', type=int, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--boot-arg', action='append', default=[], help='Additional VM-only diagnostic boot argument')
    parser.add_argument('--early-vm-capture', type=Path, help='Experimental initializer repair: captured KC layout JSON')
    parser.add_argument('--boot-kc', type=Path, help='Kernel collection bound to repairs')
    parser.add_argument('--kernel-repair-artifact', type=Path, help='Stored kernel-fixes build-repairs output')
    parser.add_argument('--commpage-kext', type=Path, help='Optional 26.5 commpage restoration kext')
    args = parser.parse_args()
    if args.early_vm_capture and args.kernel_repair_artifact:
        parser.error('choose --early-vm-capture or --kernel-repair-artifact')
    if (args.early_vm_capture or args.kernel_repair_artifact) and not args.boot_kc:
        parser.error('kernel repairs require --boot-kc')
    out = args.out.resolve()
    config = reference_tree(args.reference, out)
    with tempfile.TemporaryDirectory() as temporary:
        donor = Path(temporary)
        with tarfile.open(args.donor_esp) as archive:
            archive.extractall(donor, filter='data')
        target = plistlib.loads((donor/'EFI/OC/config.plist').read_bytes())
        # Keep the proven virtual devices; take current OCLP compatibility kexts.
        names = {'Lilu.kext', 'RestrictEvents.kext', 'AMFIPass.kext',
                 'RSRHelper.kext', 'CryptexFixup.kext',
                 'AppleIntelCPUPowerManagement.kext', 'AppleIntelCPUPowerManagementClient.kext'}
        selected = [entry for entry in target['Kernel']['Add']
                    if entry['Enabled'] and entry['BundlePath'] in names]
        for entry in selected:
            dest = out/'EFI/OC/Kexts'/entry['BundlePath']
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(donor/'EFI/OC/Kexts'/entry['BundlePath'], dest)
        config['Kernel']['Add'] = selected + [entry for entry in config['Kernel']['Add']
                                               if entry['BundlePath'] not in names]
        config['Booter']['Patch'] = target['Booter']['Patch']
        apple = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
        args_list = target['NVRAM']['Add'][apple]['boot-args'].split()
        config['NVRAM']['Add'][apple]['boot-args'] = ' '.join(dict.fromkeys(args_list + ['-v', '-lilubetaall'] + args.boot_arg))
        for guid, values in target['NVRAM']['Add'].items():
            for key, value in values.items():
                if key in ('revpatch', 'revblock', 'OCLP-Model', 'OCLP-Version'):
                    config['NVRAM']['Add'].setdefault(guid, {})[key] = value
                    deleted = config['NVRAM']['Delete'].setdefault(guid, [])
                    if key not in deleted:
                        deleted.append(key)
        add_apfs_driver(config, out, donor/'EFI/OC/Drivers/apfs_aligned.efi')
    if args.commpage_kext:
        shutil.copytree(args.commpage_kext, out/'EFI/OC/Kexts/IntelCommpage.kext')
        config['Kernel']['Add'].append(dict(Arch='x86_64', BundlePath='IntelCommpage.kext',
            Comment='UUID-bound macOS 26.5 commpage source restoration', Enabled=True,
            ExecutablePath='Contents/MacOS/IntelCommpage', PlistPath='Contents/Info.plist',
            MinKernel=f'{args.darwin_major}.0.0', MaxKernel=f'{args.darwin_major}.99.99'))
    for kext in args.kext:
        info = plistlib.loads((kext/'Contents/Info.plist').read_bytes())
        shutil.copytree(kext, out/'EFI/OC/Kexts'/kext.name)
        config['Kernel']['Add'] = [e for e in config['Kernel']['Add'] if e['BundlePath'] != kext.name]
        config['Kernel']['Add'].append(dict(Arch='x86_64', BundlePath=kext.name,
            Comment=info['CFBundleIdentifier'], Enabled=True,
            ExecutablePath='Contents/MacOS/'+info['CFBundleExecutable'], PlistPath='Contents/Info.plist',
            MinKernel=f'{args.darwin_major}.0.0', MaxKernel=f'{args.darwin_major}.99.99'))
    here = Path(__file__).resolve().parent
    repair, repair_package = None, None
    if args.early_vm_capture:
        package = ROOT/'tools/kernel-fixes'
        generate = runpy.run_path(str(ROOT/'tools/kernel-foundation/early_vm.py'))['generate']
        patches, repair = generate(args.early_vm_capture, args.boot_kc)
        config['Kernel']['Patch'].extend(patches)
        repair_package = str(package)
    elif args.kernel_repair_artifact:
        artifact = args.kernel_repair_artifact
        repair = json.loads((artifact/'build.json').read_text())['inputs']['early_vm_repair']
        if hashlib.sha256(args.boot_kc.read_bytes()).hexdigest() != repair['capture']['collection_sha256']:
            raise ValueError('Kernel repair artifact does not match BootKC')
        donor_config = plistlib.loads((artifact/'EFI/OC/config.plist').read_bytes())
        config['Kernel']['Patch'].extend(donor_config['Kernel']['Patch'])
    (out/'EFI/OC/config.plist').write_bytes(plistlib.dumps(config))
    source = runpy.run_path(str(HERE.parents[1]/'pipeline'/'source_commit.py'))
    record = dict(unit='tools/kernel-foundation', revision=runpy.run_path(str(Path(__file__).resolve().parent / 'make'))['REVISION'], operation='vm-esp',
        date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        commit=source['commit'](ROOT), dirty=source['dirty'](ROOT),
        inputs=dict(reference=str(args.reference.resolve()), reference_files=hashes(args.reference/'EFI'),
            donor_esp=str(args.donor_esp.resolve()),
            donor_sha256=hashlib.sha256(args.donor_esp.read_bytes()).hexdigest(),
            kexts={str(k.resolve()): hashes(k) for k in args.kext}, darwin_major=args.darwin_major, boot_args=args.boot_arg,
            early_vm_repair=repair, early_vm_package=repair_package,
            kernel_repair_artifact=str(args.kernel_repair_artifact.resolve()) if args.kernel_repair_artifact else None,
            kernel_repair_files=hashes(args.kernel_repair_artifact) if args.kernel_repair_artifact else None,
            commpage_kext=str(args.commpage_kext.resolve()) if args.commpage_kext else None,
            commpage_files=hashes(args.commpage_kext) if args.commpage_kext else None),
        files=hashes(out))
    (out/'build.json').write_text(json.dumps(record, indent=2)+'\n')
    print(out)


if __name__ == '__main__':
    main()
