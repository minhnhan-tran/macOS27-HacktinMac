#!/bin/sh
# Instructions the translator emits inline (atomics, bit fields, carry
# arithmetic, division, bit reversal, constant chains, integer SIMD): builds
# tests/guest/jitinsns.c for arm64e (guest) and arm64 (reference) and
# compares the output of m27's JIT, interpreter, helper-only JIT and AOT runs
# with the native one, line by line (one hash per instruction).
#   OUT=dir M27=build/m27 [T=60] tests/guest/jitinsns.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-${TMPDIR:-/tmp}/m27-jitinsns}
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
M27=${M27:-$repo/build/m27}
T=${T:-60}
set -e
for a in arm64e arm64; do
    clang -arch $a -march=armv8.5-a+fp16 -O1 -o "$OUT/jitinsns.$a" "$here/jitinsns.c"
    codesign -s - -f "$OUT/jitinsns.$a" 2>/dev/null
done
rm -rf "$OUT/aot"; mkdir -m 700 "$OUT/aot"
M27_CACHE="$OUT/aot" timeout -s KILL "$T" "$M27" --aot "$OUT/jitinsns.arm64e" >/dev/null 2>&1
set +e
timeout -s KILL "$T" "$OUT/jitinsns.arm64" >"$OUT/native.out" 2>&1
nrc=$?
fail=0
for v in jit interp helpers aot; do
    case $v in
    jit) set -- env M27_NO_AOT=1 "$M27" ;;
    interp) set -- env M27_NO_AOT=1 "$M27" --interp ;;
    helpers) set -- env M27_NO_AOT=1 M27_JIT_HELPERS_ONLY=1 "$M27" ;;
    aot) set -- env M27_CACHE="$OUT/aot" "$M27" ;;
    esac
    timeout -s KILL "$T" "$@" "$OUT/jitinsns.arm64e" >"$OUT/$v.out" 2>"$OUT/$v.err"
    rc=$?
    if [ $rc = $nrc ] && cmp -s "$OUT/native.out" "$OUT/$v.out"; then r=PASS
    else r=FAIL; fail=1; diff "$OUT/native.out" "$OUT/$v.out" | head -20; fi
    printf '%s %-8s rc=%s (native rc=%s, %s lines)\n' "$r" "$v" "$rc" "$nrc" "$(wc -l <"$OUT/native.out" | tr -d ' ')"
done
exit $fail
