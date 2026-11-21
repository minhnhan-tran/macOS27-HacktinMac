#!/bin/sh
set -eu
S=$1; H=$(cd "$(dirname "$0")" && pwd); IN=$H/inputs/ids_service
B=System/Library/PrivateFrameworks/IDS.framework/identityservicesd.app/Contents/MacOS/identityservicesd
L=usr/lib/m27/libm27-imessage-wifi-test.dylib
M=$S/usr/lib/m27/stub-identities
[ "$(shasum -a 256 "$S/$B" | cut -d' ' -f1)" = 5e978826aba256bd6bfd4275183a264470861f7a14a7999e813d83229b420e60 ] || { echo "$0: unexpected $B" >&2; exit 1; }
[ -f "$M" ]
install -o root -g wheel -m 755 "$IN/libm27-imessage-wifi-test.dylib" "$S/$L"
rm -f "$S/$B"; install -o root -g wheel -m 755 "$IN/identityservicesd" "$S/$B"
grep -qxF "$(cat "$IN/stub-identities.add")" "$M" || cat "$IN/stub-identities.add" >> "$M"
