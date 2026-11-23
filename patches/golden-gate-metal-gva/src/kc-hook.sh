#!/bin/sh
set -eu
S="${1:?SYSROOT}"; W="${2:?WORK}"
H=$(dirname "$0")
T="$S/System/Library/PrivateFrameworks/AppleGVA.framework/Versions/A/AppleGVA"
[ -f "$T" ] || { echo "kc-hook: missing AppleGVA at $T" >&2; exit 1; }
"${OCLP_PYTHON:?}" "$H/edit-gva.py" "$T" "$W/AppleGVA" --sign
mkdir -p "$W/backup"
cp -p "$T" "$W/backup/AppleGVA"
cp -f "$W/AppleGVA" "$T"
chown 0:0 "$T"
chmod 755 "$T"
chflags restricted "$T"
shasum -a 256 "$T"
