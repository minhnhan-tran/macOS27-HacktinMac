#!/bin/sh
# Guest heap blocks among native call arguments and the values that are not
# blocks (src/blocks.c read_words, the fault-tolerant m27_probe_read8, and
# the mask tracking that keeps it from faulting where SIGSEGV/SIGBUS are
# blocked: src/signals.c, src/process_identity.c). tests/guest/heapprobe.c's
# sections run natively (arm64) and under m27 with the JIT, --interp, AOT and
# M27_SIGRETURN_USER=1 (the user-space resume, P27) and M27_NO_PROBE=1, with MallocNanoZone=0 MallocDebugReport=stderr so libmalloc
# prints its "NO ZONE for ptr" errors to stderr. Each run must print what
# native arm64 prints, write no NO ZONE line, take no NX fault (the moved
# blocks reach libdispatch as thunks) and finish (no fault re-executed
# forever with SIGSEGV blocked). The JIT and user runs must have taken probe faults
# (the unreadable values were read through m27_probe_read8). M27_BASE: a
# m27 from before the fix, which must reproduce the NO ZONE lines.
#   OUT=dir M27=/abs/build/m27 [MODES="jit interp aot user noprobe"] [M27_BASE=/abs/old/m27] tests/guest/heapprobe.sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
M27=${M27:-$repo/build/m27}
OUT=${OUT:-$(mktemp -d -t m27-heapprobe)}
MODES=${MODES:-jit interp aot user noprobe}
mkdir -p "$OUT/aot"
OUT=$(cd "$OUT" && pwd)
for a in arm64 arm64e; do
    clang -arch "$a" -O1 -fblocks "$here/heapprobe.c" -o "$OUT/heapprobe.$a"
    codesign -s - -f "$OUT/heapprobe.$a" 2>/dev/null
done
export MallocNanoZone=0 MallocDebugReport=stderr
timeout -s KILL 30 "$OUT/heapprobe.arm64" >"$OUT/native.out" 2>"$OUT/native.err"
if grep -q 'NO ZONE' "$OUT/native.err"; then echo "heapprobe: native run printed NO ZONE"; exit 1; fi
timeout -s KILL 120 "$M27" --aot --cache "$OUT/aot" "$OUT/heapprobe.arm64e" >"$OUT/aot-build.log" 2>&1
fail=0
run() {   # mode m27
    m=$1 h=$2
    case $m in
    jit | base) set -- env M27_NO_AOT=1 ;;
    interp) set -- env M27_NO_AOT=1 ;;
    aot) set -- env M27_CACHE="$OUT/aot" ;;
    user) set -- env M27_NO_AOT=1 M27_SIGRETURN_USER=1 ;;   # probe faults resumed in user space (P27)
    noprobe) set -- env M27_NO_AOT=1 M27_NO_PROBE=1 ;;
    esac
    set -- "$@" M27_NO_IFUNITS=1 M27_NX_STATS=1 "$h"
    [ "$m" != interp ] || set -- "$@" --interp
    rc=0
    timeout -s KILL 60 "$@" "$OUT/heapprobe.arm64e" >"$OUT/$m.out" 2>"$OUT/$m.err" || rc=$?
    nz=$(grep -c 'NO ZONE' "$OUT/$m.err" || true)
}
for m in $MODES; do
    run "$m" "$M27"
    why=
    [ "$rc" = 0 ] || why="$why exit $rc"
    cmp -s "$OUT/native.out" "$OUT/$m.out" || why="$why output differs"
    [ "$nz" = 0 ] || why="$why $nz NO ZONE lines"
    grep -q '^m27: nx faults: 0 native->guest, 0 native->trap' "$OUT/$m.err" || why="$why NX faults"
    if [ "$m" = jit ] || [ "$m" = user ]; then
        pf=$(sed -n 's/^m27: signal returns: .*probe \([0-9]*\)\/\([0-9]*\).*/\1 \2/p' "$OUT/$m.err" | tail -1)
        set -- $pf
        [ $# = 2 ] && [ $(($1 + $2)) -gt 0 ] || why="$why no probe fault taken"
    fi
    if [ -n "$why" ]; then
        echo "heapprobe $m: FAIL:$why"
        diff -u "$OUT/native.out" "$OUT/$m.out" || true
        fail=1
    else
        echo "heapprobe $m: SAME, 0 NO ZONE, 0 NX faults"
    fi
done
if [ -n "${M27_BASE:-}" ]; then
    run base "$M27_BASE"
    if [ "$nz" -gt 0 ]; then echo "heapprobe base: $nz NO ZONE lines reproduced"
    else echo "heapprobe base: FAIL: no NO ZONE line"; fail=1; fi
fi
[ $fail = 0 ] && echo 'heapprobe: PASS'
exit $fail
