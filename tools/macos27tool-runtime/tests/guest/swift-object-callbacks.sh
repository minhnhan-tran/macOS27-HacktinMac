#!/bin/sh
# Native output plus zero NX, with an optional pre-fix negative control.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
M27=${M27:-$repo/build/m27}
OUT=${OUT:-$(mktemp -d -t m27-swift-object-callbacks)}
mkdir -p "$OUT/aot"
OUT=$(cd "$OUT" && pwd)
for p in swiftboxes observation-closures generic-objc-destruct generic-objc-layout; do
    source=$p
    set --
    if [ "$p" = generic-objc-layout ]; then
        source=generic-objc-destruct
        set -- -enable-experimental-feature LayoutStringValueWitnesses \
            -enable-experimental-feature LayoutStringValueWitnessesInstantiation \
            -Xfrontend -enable-layout-string-value-witnesses \
            -Xfrontend -enable-layout-string-value-witnesses-instantiation
    fi
    for a in arm64 arm64e; do
        xcrun swiftc -O -target "$a-apple-macos27.0" -module-name CallbackFixture \
            "$@" "$here/$source.swift" -o "$OUT/$p.$a"
        codesign -s - -f "$OUT/$p.$a" 2>/dev/null
    done
    timeout -s KILL 30 "$OUT/$p.arm64" >"$OUT/$p.native.out" 2>"$OUT/$p.native.err"
    timeout -s KILL 120 "$M27" --aot --cache "$OUT/aot" "$OUT/$p.arm64e" >"$OUT/$p.aot-build.log" 2>&1
    for m in jit interp aot; do
        case $m in
        jit|interp) set -- env M27_NO_AOT=1 ;;
        aot) set -- env M27_CACHE="$OUT/aot" ;;
        esac
        set -- "$@" M27_NX_STATS=1 "$M27" --stats
        [ "$m" != interp ] || set -- "$@" --interp
        timeout -s KILL 60 "$@" "$OUT/$p.arm64e" >"$OUT/$p.$m.out" 2>"$OUT/$p.$m.err"
        diff -u "$OUT/$p.native.out" "$OUT/$p.$m.out"
        grep -q '^m27: nx faults: 0 native->guest, 0 native->trap' "$OUT/$p.$m.err"
        if [ "$m" = aot ]; then grep -Eq 'aot_blocks=[1-9]' "$OUT/$p.$m.err"; fi
        echo "$p $m: SAME, zero NX"
    done
    if [ -n "${M27_BASE:-}" ]; then
        M27_NO_AOT=1 M27_NX_STATS=1 timeout -s KILL 60 "$M27_BASE" "$OUT/$p.arm64e" >"$OUT/$p.before.out" 2>"$OUT/$p.before.err"
        diff -u "$OUT/$p.native.out" "$OUT/$p.before.out"
        grep -Eq '^m27: nx faults: [1-9][0-9]* native->guest' "$OUT/$p.before.err"
        echo "$p baseline: SAME, faults reproduced"
    fi
done
echo 'swift-object-callbacks: PASS'
