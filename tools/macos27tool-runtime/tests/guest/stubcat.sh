#!/bin/sh
# Methods of an @objc extension on a Swift class with resilient ancestry (tests/guest/stubcat.swift):
# its superclass comes from a library built with library evolution (stubcat_lib.swift), so the class is
# emitted as an ObjC class stub (__objc_stublist) and the extension as a category on that stub in
# __objc_catlist2. m27 attaches such a category when the Swift runtime realizes the class (src/objc.c,
# m27_swift_class_stub). The helper is a universal Swift dylib (arm64 for the native reference, x86_64
# for m27, arm64e to link); the program is arm64 natively and arm64e under m27 (JIT, --interp, AOT).
# Each line must equal native arm64.
#   OUT=dir M27=/abs/build/m27 [T=60] tests/guest/stubcat.sh
set -u
# This fixture exercises the guest/native bridge through its universal helper.
export M27_NATIVE_APP_LIBS=1
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-$(mktemp -d -t m27-stubcat)}
M27=${M27:-$repo/build/m27}
T=${T:-60}
mkdir -p "$OUT" && OUT=$(cd "$OUT" && pwd)
for a in x86_64 arm64 arm64e; do
    xcrun swiftc -O -enable-library-evolution -parse-as-library -emit-library -module-name stubcat_lib \
        -emit-module -emit-module-path "$OUT/$a/stubcat_lib.swiftmodule" \
        -target $a-apple-macos27.0 -Xlinker -install_name -Xlinker "$OUT/libstubcat_lib.dylib" \
        -o "$OUT/$a/libstubcat_lib.dylib" "$here/stubcat_lib.swift" 2>"$OUT/build.lib.$a.err" ||
        { cat "$OUT/build.lib.$a.err"; exit 2; }
done
lipo -create "$OUT/x86_64/libstubcat_lib.dylib" "$OUT/arm64/libstubcat_lib.dylib" "$OUT/arm64e/libstubcat_lib.dylib" \
    -output "$OUT/libstubcat_lib.dylib" || exit 2
codesign -s - -f "$OUT/libstubcat_lib.dylib" 2>/dev/null
for a in arm64e arm64; do
    xcrun swiftc -O -parse-as-library -target $a-apple-macos27.0 -I "$OUT/$a" -L "$OUT" -lstubcat_lib \
        -o "$OUT/stubcat.$a" "$here/stubcat.swift" 2>"$OUT/build.$a.err" || { cat "$OUT/build.$a.err"; exit 2; }
    codesign -s - -f "$OUT/stubcat.$a" 2>/dev/null
done
# the shape under test: a stub, and a category in __objc_catlist2
otool -arch arm64e -l "$OUT/stubcat.arm64e" | grep -q __objc_catlist2 || { echo "stubcat: no __objc_catlist2 in the guest"; exit 2; }
otool -arch arm64e -l "$OUT/stubcat.arm64e" | grep -q __objc_stublist || { echo "stubcat: no __objc_stublist in the guest"; exit 2; }
rm -rf "$OUT/aot"; mkdir -m 700 "$OUT/aot"
timeout -s KILL "$T" "$M27" --aot --cache "$OUT/aot" "$OUT/stubcat.arm64e" >"$OUT/aot.gen" 2>&1 || echo "stubcat: aot generation failed"
timeout -s KILL "$T" "$OUT/stubcat.arm64" >"$OUT/native.out" 2>/dev/null; nrc=$?
echo "native    rc=$nrc lines=$(wc -l <"$OUT/native.out" | tr -d ' ')"
fail=0
[ $nrc = 0 ] || fail=1
for mode in jit interp aot; do
    case $mode in
    jit) set -- env M27_NO_AOT=1 "$M27" ;;
    interp) set -- env M27_NO_AOT=1 "$M27" --interp ;;
    aot) set -- env M27_CACHE="$OUT/aot" "$M27" ;;
    esac
    timeout -s KILL "$T" "$@" "$OUT/stubcat.arm64e" >"$OUT/$mode.out" 2>"$OUT/$mode.err"; rc=$?
    if [ $rc = $nrc ] && cmp -s "$OUT/native.out" "$OUT/$mode.out"; then r=SAME; else r=DIFF; fail=1; fi
    printf '%-9s rc=%s %s\n' "$mode" "$rc" "$r"
    [ $r = SAME ] || { diff "$OUT/native.out" "$OUT/$mode.out" | sed 's/^/    /' | head -12; tail -3 "$OUT/$mode.err" | sed 's/^/    err: /'; }
done
[ $fail = 0 ] && echo "stubcat: PASS" || echo "stubcat: FAIL"
exit $fail
