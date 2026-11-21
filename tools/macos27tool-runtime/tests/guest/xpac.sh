#!/bin/sh
# tests/guest/xpac.c for arm64e (guest) and arm64 (reference): XPAC* on x30
# in functions m27 enters with its return sentinel, and XPAC* results. Runs
# natively and under m27 (JIT, --interp, AOT); stdout and rc must match.
#   OUT=dir M27=/abs/build/m27 [T=30] tests/guest/xpac.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-$(mktemp -d -t m27-xpac)}
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
M27=${M27:-$repo/build/m27}
T=${T:-30}
set -e
for a in arm64e arm64; do
    clang -arch $a -O1 -o "$OUT/xpac.$a" "$here/xpac.c"
    codesign -s - -f "$OUT/xpac.$a" 2>/dev/null
done
rm -rf "$OUT/aot"; mkdir -m 700 "$OUT/aot"
timeout -s KILL "$T" "$M27" --aot --cache "$OUT/aot" "$OUT/xpac.arm64e" >/dev/null 2>&1
set +e
timeout -s KILL "$T" "$OUT/xpac.arm64" >"$OUT/native.out" 2>"$OUT/native.err"
nrc=$?
fail=0
[ $nrc = 0 ] || { echo "native run failed rc=$nrc"; fail=1; }
for v in jit interp aot; do
    case $v in
    jit) set -- env M27_NO_AOT=1 "$M27" ;;
    interp) set -- env M27_NO_AOT=1 "$M27" --interp ;;
    aot) set -- env M27_CACHE="$OUT/aot" "$M27" ;;
    esac
    timeout -s KILL "$T" "$@" "$OUT/xpac.arm64e" >"$OUT/$v.out" 2>"$OUT/$v.err"
    rc=$?
    if [ $rc = $nrc ] && cmp -s "$OUT/native.out" "$OUT/$v.out"; then r=SAME; else r=DIFF; fail=1; fi
    printf '%-6s native rc=%-3s m27 rc=%-3s %s\n' "$v" "$nrc" "$rc" "$r"
done
exit $fail
