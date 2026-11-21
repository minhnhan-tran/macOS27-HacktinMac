#!/bin/sh
# Compare SIMD workload checksums with native; separate timing and stats runs.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-$(mktemp -d -t m27-coverage-bench)}
M27=${M27:-$repo/build/m27}
N=${N:-50000}
T=${T:-120}
mkdir -p "$OUT/aot"; chmod 700 "$OUT/aot"
for arch in arm64 arm64e; do
    clang -arch "$arch" -march=armv8.5-a+fp16 -O1 -o "$OUT/bench.$arch" "$here/coverage_bench.c"
    codesign -s - -f "$OUT/bench.$arch" 2>/dev/null
done
timeout -s KILL "$T" "$OUT/bench.arm64" "$N" >"$OUT/native.out" 2>"$OUT/native.err"
timeout -s KILL "$T" "$M27" --aot --cache "$OUT/aot" "$OUT/bench.arm64e" >"$OUT/build.out" 2>"$OUT/build.err"
for mode in jit interp helpers aot; do
    case $mode in
        jit) set -- env M27_NO_AOT=1 "$M27" ;;
        interp) set -- env M27_NO_AOT=1 "$M27" --interp ;;
        helpers) set -- env M27_NO_AOT=1 M27_JIT_HELPERS_ONLY=1 "$M27" ;;
        aot) set -- env M27_CACHE="$OUT/aot" "$M27" ;;
    esac
    timeout -s KILL "$T" "$@" "$OUT/bench.arm64e" "$N" >"$OUT/$mode.out" 2>"$OUT/$mode.err"
    cmp "$OUT/native.out" "$OUT/$mode.out"
    echo "PASS $mode coverage benchmark checksum"
    case $mode in jit|aot)
        timeout -s KILL "$T" "$@" --stats "$OUT/bench.arm64e" "$N" >"$OUT/$mode.stats.out" 2>"$OUT/$mode.stats.err"
        cmp "$OUT/native.out" "$OUT/$mode.stats.out"
        grep '^m27-jit:' "$OUT/$mode.stats.err" ;;
    esac
done
