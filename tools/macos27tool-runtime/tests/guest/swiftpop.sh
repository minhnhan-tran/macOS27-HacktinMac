#!/bin/sh
# The swifttailcc callee pop of guest Swift partials that the native executor resumes from a main-queue job
# (src/runtime.c callee_pop, src/swiftcc.c arg_slots).
# Builds tests/guest/swiftpop.swift with -Osize for arm64e (guest) and arm64 (reference), then:
#   - arm64 natively, arm64e under m27 with the JIT, --interp and an AOT cache, and with M27_SWIFT_POP=signature
#     (the scan alone) and =frame (the caller frame alone): each run must print what native arm64 prints and exit 0;
#   - M27_BRIDGE_CHECK=1 with M27_DIAG_DIR: no "swifttailcc pop" disagreement in the diagnostics log.
# Before the fix every m27 run aborted in objc_autoreleasePoolPop ("autorelease pool page ... corrupted").
#   OUT=dir M27=/abs/build/m27 [T=60] tests/guest/swiftpop.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-$(mktemp -d -t m27-swiftpop)}
M27=${M27:-$repo/build/m27}
T=${T:-60}
mkdir -p "$OUT" && OUT=$(cd "$OUT" && pwd)
fail=0
for a in arm64e arm64; do
    xcrun swiftc -Osize -target $a-apple-macos27.0 -o "$OUT/swiftpop.$a" "$here/swiftpop.swift" || { echo "swiftpop: build failed"; exit 1; }
    codesign -s - -f "$OUT/swiftpop.$a" 2>/dev/null
done
rm -rf "$OUT/aot" "$OUT/diag"; mkdir -m 700 "$OUT/aot" "$OUT/diag"
timeout -s KILL "$T" "$M27" --aot --cache "$OUT/aot" "$OUT/swiftpop.arm64e" >"$OUT/aot.gen" 2>&1 || echo "swiftpop: aot generation failed"
timeout -s KILL "$T" "$OUT/swiftpop.arm64" >"$OUT/native.out" 2>"$OUT/native.err"
rc=$?
echo "native    rc=$rc $(cat "$OUT/native.out")"
[ $rc = 0 ] || fail=1
for mode in jit interp aot signature frame check; do
    case $mode in
    jit) set -- env M27_NO_AOT=1 "$M27" ;;
    interp) set -- env M27_NO_AOT=1 "$M27" --interp ;;
    aot) set -- env M27_CACHE="$OUT/aot" "$M27" ;;
    signature) set -- env M27_NO_AOT=1 M27_SWIFT_POP=signature "$M27" ;;
    frame) set -- env M27_NO_AOT=1 M27_SWIFT_POP=frame "$M27" ;;
    check) set -- env M27_NO_AOT=1 M27_BRIDGE_CHECK=1 M27_DIAG_DIR="$OUT/diag" "$M27" ;;
    esac
    timeout -s KILL "$T" "$@" "$OUT/swiftpop.arm64e" >"$OUT/$mode.out" 2>"$OUT/$mode.err"
    rc=$?
    r=SAME
    [ $rc = 0 ] && cmp -s "$OUT/native.out" "$OUT/$mode.out" || r=DIFF
    if [ $mode = check ]; then
        n=$(cat "$OUT/diag/console.log" 2>/dev/null | grep -c 'swifttailcc pop of guest')
        [ "$n" = 0 ] || r="$r(pop disagreements: $n)"
    fi
    [ "$r" = SAME ] || fail=1
    printf '%-9s rc=%s %s\n' "$mode" "$rc" "$r"
    [ "$r" = SAME ] || { grep -h 'swifttailcc pop' "$OUT/diag/console.log" 2>/dev/null | head -4
        grep -v '^m27: \(blocks\|threads\|bridge\)\|^m27-\|: bridge: ' "$OUT/$mode.err" | head -6; } | sed 's/^/    /'
done
[ $fail = 0 ] && echo "swiftpop: PASS" || echo "swiftpop: FAIL"
exit $fail
