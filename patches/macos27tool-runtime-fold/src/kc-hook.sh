#!/bin/sh
# macos27tool-runtime-fold (fold-in sets only, in macos27tool-runtime's place): macos27tool-runtime's installation with the payload built for
# the rebuilt cache family (row hao_runtime_fold), whose generated data names that family's cache UUID.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
"$OCLP_PYTHON" -I "$H/inputs/hao_runtime_fold/hao-install.py" "$1" "$H/inputs/hao_runtime_fold"
