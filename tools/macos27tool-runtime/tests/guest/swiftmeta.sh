#!/bin/sh
# Build tests/guest/swiftmeta.swift for arm64e (guest) and arm64 (reference), with layout-string value witnesses
# as the 27.0 arm64-only programs are built. Run arm64 natively and arm64e under m27 with the JIT, --interp and an
# AOT cache: the whole program, then each section on its own (`swiftmeta SECTION`). Every m27 run must print
# exactly what native arm64 prints, exit 0, and leave no libobjc warning on stderr. Every m27 run has
# M27_NX_STATS=1; its native->guest NX fault count goes to $OUT/nx.tsv (mode, section, faults) and to the table,
# and the fault sites to $OUT/MODE.SECTION.err.
# Exit status 0 only if every run matches.
#   OUT=dir M27=/abs/build/m27 [T=60] [MODES="jit interp aot"] tests/guest/swiftmeta.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-$(mktemp -d -t m27-swiftmeta)}
M27=${M27:-$repo/build/m27}
T=${T:-60}
MODES=${MODES:-jit interp aot}
SECTIONS="classes witness vwt keypath async mirror"
# swift-6.4 frontend: both the flags and the experimental features are needed for swift_cvw_* witnesses
LS="-Xfrontend -enable-layout-string-value-witnesses -Xfrontend -enable-layout-string-value-witnesses-instantiation
    -enable-experimental-feature LayoutStringValueWitnesses -enable-experimental-feature LayoutStringValueWitnessesInstantiation"
mkdir -p "$OUT"
set -e
for a in arm64e arm64; do
    # shellcheck disable=SC2086
    swiftc -O $LS -target $a-apple-macos27.0 -o "$OUT/swiftmeta.$a" "$here/swiftmeta.swift"
    codesign -s - -f "$OUT/swiftmeta.$a" 2>/dev/null
done
set +e
nm -u "$OUT/swiftmeta.arm64e" | grep -q '_swift_cvw_initWithCopy$' || { echo "swiftmeta: no layout-string witnesses in the build"; exit 1; }
case " $MODES " in *" aot "*)
    rm -rf "$OUT/aot"; mkdir -m 700 "$OUT/aot"
    timeout -s KILL 120 "$M27" --aot --cache "$OUT/aot" "$OUT/swiftmeta.arm64e" >"$OUT/aot.log" 2>&1 ||
        echo "aot: --aot failed: $(tail -1 "$OUT/aot.log")" ;;
esac

fail=0
: >"$OUT/nx.tsv"
nrc=
for s in all $SECTIONS; do
    arg=$s; [ $s = all ] && arg=
    timeout -s KILL "$T" "$OUT/swiftmeta.arm64" $arg >"$OUT/native.$s.out" 2>"$OUT/native.$s.err"
    rc=$?
    nrc="$nrc $rc"
    [ $rc = 0 ] || fail=1
done
echo "native   rc=$(echo $nrc | tr ' ' ,) lines=$(wc -l <"$OUT/native.all.out" | tr -d ' ')"
printf '%-8s' section; for m in $MODES; do printf ' %-16s' "$m (nx)"; done; echo
for s in all $SECTIONS; do
    arg=$s; [ $s = all ] && arg=
    printf '%-8s' $s
    detail=
    for m in $MODES; do
        case $m in
        jit) set -- env M27_NO_AOT=1 M27_NX_STATS=1 "$M27" ;;
        interp) set -- env M27_NO_AOT=1 M27_NX_STATS=1 "$M27" --interp ;;
        aot) set -- env M27_CACHE="$OUT/aot" M27_NX_STATS=1 "$M27" ;;
        esac
        o=$OUT/$m.$s
        timeout -s KILL "$T" "$@" "$OUT/swiftmeta.arm64e" $arg >"$o.out" 2>"$o.err"
        rc=$?
        nx=$(sed -n 's/^m27: nx faults: \([0-9]*\) native->guest.*/\1/p' "$o.err" | tail -1)
        [ -n "$nx" ] || nx=-
        printf '%s\t%s\t%s\n' $m $s $nx >>"$OUT/nx.tsv"
        if [ $rc = 0 ] && cmp -s "$OUT/native.$s.out" "$o.out" && ! grep -q '^objc\[' "$o.err"; then r=SAME; else
            r=DIFF; fail=1
            detail="$detail$(printf '\n  %s %s rc=%s\n' $m $s $rc)$( { diff "$OUT/native.$s.out" "$o.out" | head -10
                grep '^objc\[\|^m27' "$o.err" | grep -v '^m27: nx fault' | head -5; } | sed 's/^/    /')"
        fi
        printf ' %-16s' "$r ($nx)"
    done
    echo
    [ -z "$detail" ] || printf '%s\n' "$detail"
done
[ $fail = 0 ] && echo "swiftmeta: PASS" || echo "swiftmeta: FAIL"
exit $fail
