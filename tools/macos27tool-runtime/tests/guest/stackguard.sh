#!/bin/sh
# Argument slots that point into the main thread's stack guard (tests/guest/stackguard.c): m27's argument
# scan must not fault there, because xnu kills the process for a protection fault in the main stack's
# reservation (src/signals.c m27_probe_addr_ok). stackguard_lib.c is a universal helper no unit types, so
# every call is scanned in full. arm64 natively, arm64e under m27 (JIT, --interp, AOT, each also with
# M27_BRIDGE_CHECK=1, whose check scans every slot): same stdout, rc 0.
#   OUT=dir M27=/abs/build/m27 [T=30] tests/guest/stackguard.sh     exit 1 on any difference
set -u
# This fixture exercises the guest/native bridge through its universal helper.
export M27_NATIVE_APP_LIBS=1
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-$(mktemp -d -t m27-stackguard)}
M27=${M27:-$repo/build/m27}
T=${T:-30}
mkdir -p "$OUT" && OUT=$(cd "$OUT" && pwd)
clang -arch x86_64 -arch arm64 -arch arm64e -dynamiclib -O1 -install_name "$OUT/libstackguard.dylib" \
    -o "$OUT/libstackguard.dylib" "$here/stackguard_lib.c" && codesign -s - -f "$OUT/libstackguard.dylib" 2>/dev/null || exit 2
for a in arm64 arm64e; do
    clang -arch $a -O1 -o "$OUT/stackguard.$a" "$here/stackguard.c" "$OUT/libstackguard.dylib" &&
        codesign -s - -f "$OUT/stackguard.$a" 2>/dev/null || exit 2
done
rm -rf "$OUT/aot"; mkdir -m 700 "$OUT/aot"
timeout -s KILL "$T" "$M27" --aot --cache "$OUT/aot" "$OUT/stackguard.arm64e" >"$OUT/aot.gen" 2>&1 || echo "stackguard: aot generation failed"
timeout -s KILL "$T" "$OUT/stackguard.arm64" >"$OUT/native.out" 2>/dev/null; nrc=$?
echo "native       rc=$nrc $(cat "$OUT/native.out")"
fail=0
[ $nrc = 0 ] || fail=1
for mode in jit interp aot jit-check interp-check aot-check; do
    case $mode in
    jit*) set -- env M27_NO_AOT=1 ;;
    interp*) set -- env M27_NO_AOT=1 M27_INTERP=1 ;;
    aot*) set -- env M27_CACHE="$OUT/aot" ;;
    esac
    case $mode in *-check) set -- "$@" M27_BRIDGE_CHECK=1 ;; esac
    timeout -s KILL "$T" "$@" "$M27" "$OUT/stackguard.arm64e" >"$OUT/$mode.out" 2>"$OUT/$mode.err"; rc=$?
    if [ $rc = $nrc ] && cmp -s "$OUT/native.out" "$OUT/$mode.out"; then r=SAME; else r=DIFF; fail=1; fi
    printf '%-12s rc=%s %s\n' "$mode" "$rc" "$r"
done
[ $fail = 0 ] && echo "stackguard: PASS" || echo "stackguard: FAIL"
exit $fail
