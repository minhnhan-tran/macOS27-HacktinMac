#!/bin/sh
# After golden-gate-userspace installed the 27.0 userspace: install m27 and give
# every arm64-only 27.0 executable an x86_64 slice that runs it through m27.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
"$OCLP_PYTHON" -I "$H/inputs/m27_runtime/m27-install.py" "$1" "$H/inputs/m27_runtime"
