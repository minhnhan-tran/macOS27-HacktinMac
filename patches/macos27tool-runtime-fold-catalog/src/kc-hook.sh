#!/bin/sh
# macos27tool-runtime-fold-catalog (B+catalog set only, in macos27tool-runtime-fold's place): macos27tool-runtime's installation with the
# payload built for the B+catalog cache family (row hao_runtime_fold_catalog), whose generated data names its cache UUID.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
"$OCLP_PYTHON" -I "$H/inputs/hao_runtime_fold_catalog/hao-install.py" "$1" "$H/inputs/hao_runtime_fold_catalog"
