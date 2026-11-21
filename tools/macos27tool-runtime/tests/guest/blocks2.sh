#!/bin/sh
# Build tests/guest/blocks2.m (+ native helper dylib) and blocks.c for arm64e
# (guest) and arm64 (reference); run arm64 natively and arm64e under m27:
# JIT, --interp, JIT with M27_NO_BLOCK_THUNKS=1, and optionally a baseline
# m27 ($M27_BASE). Every m27 run has M27_NX_STATS=1. Prints per-run
# SAME/DIFF against native arm64 and the nx fault line.
# SECTIONS="1 2 ..." also runs each blocks2 section on its own (a crash in
# one section then does not hide the others).
#   OUT=dir M27=build/m27 [M27_BASE=/path/to/old/m27] [T=60] [SECTIONS=..] tests/guest/blocks2.sh
set -u
# This fixture exercises the guest/native bridge through its universal helper.
export M27_NATIVE_APP_LIBS=1
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-$(mktemp -d -t m27-blocks2)}
M27=${M27:-$repo/build/m27}
T=${T:-60}
mkdir -p "$OUT"
set -e
clang -arch x86_64 -arch arm64 -arch arm64e -dynamiclib -O1 -fblocks -I"$here" \
    -install_name "$OUT/libblockhelper.dylib" -o "$OUT/libblockhelper.dylib" "$here/blockhelper.c"
codesign -s - -f "$OUT/libblockhelper.dylib" 2>/dev/null
for a in arm64e arm64; do
    clang -arch $a -O1 -fobjc-arc -I"$here" -framework Foundation -L"$OUT" -lblockhelper \
        -o "$OUT/blocks2.$a" "$here/blocks2.m"
    clang -arch $a -O1 -o "$OUT/blocks.$a" "$here/blocks.c"
    codesign -s - -f "$OUT/blocks2.$a" "$OUT/blocks.$a" 2>/dev/null
done
set +e
run() {  # args name label cmd...  (args: program arguments, "-" for none)
    ra=$1; rn=$2; rl=$3; shift 3
    [ "$ra" = - ] && ra=
    timeout -s KILL "$T" "$@" $ra >"$OUT/$rn.$rl.out" 2>"$OUT/$rn.$rl.err"
    rc=$?
    if cmp -s "$OUT/$rn.native.out" "$OUT/$rn.$rl.out"; then rr=SAME; else rr=DIFF; fi
    nx=$(grep 'nx faults' "$OUT/$rn.$rl.err" | head -1 | sed 's/^m27: //')
    bl=$(grep 'm27-blocks:' "$OUT/$rn.$rl.err" | head -1 | sed 's/^m27-blocks: //')
    printf '%-10s %-8s rc=%-3s %-4s %s | %s\n' "$rn" "$rl" "$rc" "$rr" "$nx" "$bl"
}
matrix() {  # prog args name
    p=$1; a=$2; nm=$3
    timeout -s KILL "$T" "$OUT/$p.arm64" ${a#-} >"$OUT/$nm.native.out" 2>"$OUT/$nm.native.err"
    echo "$nm native rc=$?"
    run "$a" $nm jit      env M27_NX_STATS=1 "$M27" --stats "$OUT/$p.arm64e"
    run "$a" $nm interp   env M27_NX_STATS=1 "$M27" --stats --interp "$OUT/$p.arm64e"
    run "$a" $nm nothunk  env M27_NX_STATS=1 M27_NO_BLOCK_THUNKS=1 "$M27" --stats "$OUT/$p.arm64e"
    run "$a" $nm nothunki env M27_NX_STATS=1 M27_NO_BLOCK_THUNKS=1 "$M27" --stats --interp "$OUT/$p.arm64e"
    if [ -n "${M27_BASE:-}" ]; then
        run "$a" $nm base     env M27_NX_STATS=1 "$M27_BASE" --stats "$OUT/$p.arm64e"
    fi
}
matrix blocks - blocks
matrix blocks2 - blocks2
for s in ${SECTIONS:-}; do matrix blocks2 $s blocks2-t$s; done
