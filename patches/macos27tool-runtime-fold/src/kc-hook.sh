#!/bin/sh
# macos27tool-runtime-fold (fold-in sets only, in macos27tool-runtime's place): macos27tool-runtime's installation with the payload built for
# the rebuilt cache family (row m27_runtime_fold), whose generated data names that family's cache UUID.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
# On a Recovery install, TMPDIR is on M27 (mounted noowners) and Recovery's /private/var/tmp
# is a 2.5 GiB tmpfs too small for ~3.5 GiB of AOT staging; data/private is firmlinked into
# system (so legacy_stubs would walk staged stubs there). Stage on preboot or data root
# (both owners-enabled APFS volumes sharing container free space, not firmlinked into system),
# falling back to /private/var/tmp.
MNT=$(dirname "$1")
for cand in "$MNT/preboot" "$MNT/data" /private/var/tmp; do
    if [ -d "$cand" ] && [ -w "$cand" ]; then
        W=$(mktemp -d "$cand/macos27tool-runtime.XXXXXX")
        trap 'rm -rf "$W"' EXIT
        export TMPDIR="$W"
        break
    fi
done
"$OCLP_PYTHON" -I "$H/inputs/m27_runtime_fold/m27-install.py" "$1" "$H/inputs/m27_runtime_fold"
