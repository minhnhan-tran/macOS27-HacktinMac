#!/bin/sh
# opencore-generic (post, right before macos27tool-opencore; pack generic): the generic model's part of OpenCore for virtual
# machines and non-Apple PCs: SMBIOS iMac20,1 with this image's own serials, VirtualSMC, and the firmware quirks of the
# tested QEMU reference. macos27tool-opencore then adds the Darwin 27 parts and checks every element's reason.
set -eu
# The kit's Python for the Mac the assembly runs on (golden-image.py sets OCLP_PYTHON); the builder VM's share copy
# for the legacy runner.
PY=${OCLP_PYTHON:-/private/var/ssdshare/py/bin/python3.12}
"$PY" -I "$P/src/generic.py" "$ESP" "$P/inputs/qemu_reference_efi" "$P/inputs/oclp_source"
plutil -lint "$ESP/EFI/OC/config.plist"
