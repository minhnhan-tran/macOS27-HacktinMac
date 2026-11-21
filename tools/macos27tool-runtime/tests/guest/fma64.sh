#!/bin/sh
# Build tests/guest/fma64.c (double-precision fused multiply-add: every FMLA,
# FMLS and FMADD-family form on near-tie, cancelling, overflowing, tiny and
# special operands under each FPCR rounding/FZ/DN setting) for arm64e (guest)
# and arm64 (reference). Run arm64 natively and arm64e under m27 with the
# JIT, with --interp, helpers-only and from an AOT cache; each run prints
# the result and FPSR hashes native arm64 prints. Exit status 0 only if
# every run matches.
#   OUT=dir M27=build/m27 [T=60] tests/guest/fma64.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-$(mktemp -d -t m27-fma64)}
M27=${M27:-$repo/build/m27}
T=${T:-60}
mkdir -p "$OUT"
set -e
for a in arm64e arm64; do
    clang -arch $a -O1 -fno-builtin -o "$OUT/fma64.$a" "$here/fma64.c"
    codesign -s - -f "$OUT/fma64.$a" 2>/dev/null
done
rm -rf "$OUT/aot"; mkdir -m 700 "$OUT/aot"
timeout -s KILL "$T" "$M27" --aot --cache "$OUT/aot" "$OUT/fma64.arm64e" >/dev/null 2>&1
set +e
timeout -s KILL "$T" "$OUT/fma64.arm64" >"$OUT/native.out" 2>"$OUT/native.err"
rc=$?
echo "native   rc=$rc lines=$(wc -l <"$OUT/native.out" | tr -d ' ')"
fail=0
[ $rc = 0 ] || fail=1
for mode in jit interp helpers aot; do
    case $mode in
    jit) set -- env M27_NO_AOT=1 "$M27" ;;
    helpers) set -- env M27_NO_AOT=1 M27_JIT_HELPERS_ONLY=1 "$M27" ;;
    interp) set -- env M27_NO_AOT=1 "$M27" --interp ;;
    aot) set -- env M27_CACHE="$OUT/aot" "$M27" ;;
    esac
    timeout -s KILL "$T" "$@" "$OUT/fma64.arm64e" >"$OUT/$mode.out" 2>"$OUT/$mode.err"
    rc=$?
    if [ $rc = 0 ] && cmp -s "$OUT/native.out" "$OUT/$mode.out"; then r=SAME; else r=DIFF; fail=1; fi
    echo "$mode $(printf '%-6s' '') rc=$rc $r"
    [ $r = SAME ] || diff "$OUT/native.out" "$OUT/$mode.out" | sed 's/^/    /'
done
[ $fail = 0 ] && echo "fma64: PASS" || echo "fma64: FAIL"
exit $fail
