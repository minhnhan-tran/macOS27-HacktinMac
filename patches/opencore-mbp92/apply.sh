#!/bin/sh
# opencore-mbp92 (post, right before macos27tool-opencore): the MacBookPro9,2's early Tahoe display and wired-network kexts in
# OpenCore's Kernel.Add, for any that neither kernel collection links (none on any recorded build).
set -eu
# The kit's Python for the Mac the assembly runs on (golden-image.py sets OCLP_PYTHON); the builder VM's share copy
# for the legacy runner.
PY=${OCLP_PYTHON:-/private/var/ssdshare/py/bin/python3.12}
"$PY" -I "$P/src/early_kexts.py" "$SYS" "$ESP" "$P/src/early-kexts.txt"
plutil -lint "$ESP/EFI/OC/config.plist"
