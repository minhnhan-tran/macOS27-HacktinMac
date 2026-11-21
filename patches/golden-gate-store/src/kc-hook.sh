#!/bin/sh
set -eu
S=$1; H=$(cd "$(dirname "$0")" && pwd)
E=$H/inputs/edit_engine/stub-edit
[ -f "$S/usr/lib/m27/stub-identities" ]
rm -f "$S/usr/lib/m27/libm27-appstore-policy.dylib" "$S/usr/lib/m27/m27-appstore-policy.dylib"
install -o root -g wheel -m 755 "$H/inputs/store_service/libm27-appstore-policy.dylib" "$S/usr/lib/m27/m27-appstore-policy.dylib"
for f in appstored appstoreagent app-store-ui; do
    "${OCLP_PYTHON:?}" -I "$E" "$S" "$H/build/$f.json"
done
