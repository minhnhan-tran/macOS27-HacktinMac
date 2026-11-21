#!/bin/sh
# Runs (inside a booted image: no developer tools needed) every ARM-only or m27-embedded launchd job of a system image under M27 and under BASELINE_M27 and fails on a refusal or m27
# abort that the baseline does not show; a job that stops at a host-only check is UNKNOWN (judge it on a booted image). Jobs that link a window or UI framework are not run (no GUI on the host). Run as root, each job runs as nobody, so no job can act on the machine.
#   OUT=dir [T=15] [J=4] tests/image/launchd-scan.sh ROOT M27 BASELINE_M27
# In an image: ROOT=/, M27=/usr/libexec/m27, BASELINE_M27 a copied m27 with its libm27 and libm27_identity beside it.
set -u
if [ "${1:-}" = --run ]; then   # one job: --run M27 DIR NAME
    as=; [ "$(id -u)" = 0 ] && as="sudo -n -u nobody"
    $as "$2" "$OUT/bin/$4" > /dev/null 2> "$OUT/$3/$4.err" < /dev/null & p=$!
    ( sleep "$T"; kill -KILL $p 2>/dev/null ) & w=$!
    wait $p; echo $? > "$OUT/$3/$4.rc"; kill $w 2>/dev/null; wait $w 2>/dev/null
    grep -E "boundary refused:|^m27(\[[^]]*\])?: fatal|commpage .*: fault" "$OUT/$3/$4.err" |
        sed -E 's/^m27\[[^]]*\]/m27/; s/0x[0-9a-f]+/0x/g; s/(at offset|depth) [0-9]+/\1 N/g' | sort -u > "$OUT/$3/$4.keys"
    exit 0
fi
[ $# = 3 ] || { echo "usage: OUT=dir tests/image/launchd-scan.sh ROOT M27 BASELINE_M27" >&2; exit 2; }
ROOT=$1 M27=$2 BASE=$3
: "${OUT:?OUT must name a directory}" "${T:=15}" "${J:=4}"
mkdir -p "$OUT/bin" "$OUT/new" "$OUT/base" && chmod 755 "$OUT" "$OUT/bin"
: > "$OUT/jobs"
for d in System/Library/LaunchDaemons System/Library/LaunchAgents Library/LaunchDaemons Library/LaunchAgents; do
    [ -d "$ROOT/$d" ] || continue
    for p in "$ROOT/$d"/*.plist; do
        [ -f "$p" ] || continue
        prog=$(plutil -extract Program raw "$p" 2>/dev/null || plutil -extract ProgramArguments.0 raw "$p" 2>/dev/null) || continue
        case $prog in /*) ;; *) continue ;; esac
        case $prog in *.app/*) continue ;; esac
        bin=$ROOT$prog
        [ -f "$bin" ] || continue
        archs=$(file -b "$bin" 2>/dev/null) || continue
        case $archs in *arm64*) ;; *) continue ;; esac
        case $archs in *x86_64*) grep -aq __m27_slice "$bin" || continue ;; esac
        grep -aqE "/(AppKit|SkyLight|UIKitCore)\.framework/" "$bin" && continue
        name=$(basename "$p" .plist)
        grep -qx "$name" "$OUT/jobs" && continue
        cp "$bin" "$OUT/bin/$name" && echo "$name" >> "$OUT/jobs"
    done
done
export OUT T
xargs -P "$J" -I{} "$0" --run "$M27" new {} < "$OUT/jobs"
fails=0
while read -r name; do
    if [ ! -s "$OUT/new/$name.keys" ]; then
        grep -qE "may only be started by|Fatal error:|entitlement" "$OUT/new/$name.err" && echo "UNKNOWN $name (stopped at a host-only check)"
        rc=$(cat "$OUT/new/$name.rc"); case $rc in 132|133|134|138|139) [ -s "$OUT/new/$name.err" ] || echo "UNKNOWN $name (rc $rc, no message)" ;; esac
        continue
    fi
    "$0" --run "$BASE" base "$name"
    extra=$(comm -23 "$OUT/new/$name.keys" "$OUT/base/$name.keys")
    if [ -z "$extra" ] && grep -qE "may only be started by|Fatal error:|entitlement" "$OUT/base/$name.err"; then
        echo "UNKNOWN $name (the baseline stopped at a host-only check)"; continue
    fi
    if [ -n "$extra" ]; then
        fails=$((fails + 1)); echo "NEW   $name"; echo "$extra" | sed 's/^/      /'
    else
        echo "SAME  $name"
    fi
done < "$OUT/jobs"
echo "launchd-scan: $(wc -l < "$OUT/jobs" | tr -d ' ') jobs, $fails with a new refusal"
[ $fails = 0 ]
