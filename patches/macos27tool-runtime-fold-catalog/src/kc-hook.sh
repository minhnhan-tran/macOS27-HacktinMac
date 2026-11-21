#!/bin/sh
# macos27tool-runtime-fold-catalog (B+catalog set only, in macos27tool-runtime-fold's place): macos27tool-runtime's installation with the
# payload built for the B+catalog cache family (row m27_runtime_fold_catalog), whose generated data names its cache UUID.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
"$OCLP_PYTHON" -I "$H/inputs/m27_runtime_fold_catalog/m27-install.py" "$1" "$H/inputs/m27_runtime_fold_catalog"
