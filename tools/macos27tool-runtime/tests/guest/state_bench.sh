#!/bin/sh
# Native state/dispatch checks plus informational timing and dynamic stats.
# OUT=dir M27=build/m27 [N=100000] [ENGINES='jit interp helpers aot'] [T=60]
# N=100000000 ENGINES='jit aot' for longer performance runs.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-$(mktemp -d -t m27-state-bench)}
M27=${M27:-$repo/build/m27}
N=${N:-100000}
T=${T:-60}
ENGINES=${ENGINES:-'jit interp helpers aot'}
mkdir -p "$OUT"
for arch in arm64 arm64e; do
    clang -arch "$arch" -O1 -o "$OUT/state.$arch" "$here/state_bench.c"
    codesign -s - -f "$OUT/state.$arch" 2>/dev/null
done
mkdir -p "$OUT/aot"; chmod 700 "$OUT/aot"
timeout -s KILL "$T" "$M27" --aot --cache "$OUT/aot" "$OUT/state.arm64e" >"$OUT/aot-build.out" 2>"$OUT/aot-build.err"
for pattern in 0 1 2 3 4; do
    timeout -s KILL "$T" "$OUT/state.arm64" "$N" "$pattern" >"$OUT/native.$pattern.out" 2>"$OUT/native.$pattern.err"
    for mode in $ENGINES; do
        case $mode in
        jit) set -- env M27_NO_AOT=1 "$M27" ;;
        interp) set -- env M27_NO_AOT=1 "$M27" --interp ;;
        helpers) set -- env M27_NO_AOT=1 M27_JIT_HELPERS_ONLY=1 "$M27" ;;
        aot) set -- env M27_CACHE="$OUT/aot" "$M27" ;;
        *) echo "unknown engine $mode" >&2; exit 2 ;;
        esac
        # Timed run has no counters. A separate --stats run counts helpers.
        timeout -s KILL "$T" "$@" "$OUT/state.arm64e" "$N" "$pattern" >"$OUT/$mode.$pattern.out" 2>"$OUT/$mode.$pattern.err"
        cmp "$OUT/native.$pattern.out" "$OUT/$mode.$pattern.out"
        timeout -s KILL "$T" "$@" --stats "$OUT/state.arm64e" "$N" "$pattern" >"$OUT/$mode.$pattern.stats.out" 2>"$OUT/$mode.$pattern.stats.err"
        cmp "$OUT/native.$pattern.out" "$OUT/$mode.$pattern.stats.out"
        echo "PASS $mode pattern=$pattern"
        cat "$OUT/$mode.$pattern.err"
        grep '^m27-jit:' "$OUT/$mode.$pattern.stats.err" || :
    done
done
