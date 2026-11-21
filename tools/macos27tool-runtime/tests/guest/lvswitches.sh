#!/bin/bash
# The switches that add code or trust to an open-tier process (M27_CACHE,
# M27_GUEST_ROOT, M27_IF_DIR, M27_ASYNC_CONTRACT_LIBRARY, M27_TRUST_LIST) and
# M27_NO_LIBVAL are ignored for a program whose signature requires library
# validation (src/guest_sig.c m27_guest_switches_for, src/libval.c): natively
# its environment cannot load code into it. M27_IF_DIR names a directory with a
# thunk library whose native half's initializer leaves a marker file
# (tests/guest/lvswitchesmark.c): an ad hoc program (the control) loads it, a
# hardened one (the hardened runtime, or the library-validation flag) must not,
# in the CLI route and embedded, and each switch the
# hardened program ignores is named on stderr (M27_ASYNC_CONTRACT_LIBRARY, a
# file that does not exist, is set for the hardened program only). An
# embedded program whose ARM slice is not the one its stub names (another
# program's, or its own with a changed byte) ends with status 126 before
# anything the environment selects loads.
#   OUT=dir [B=build] [T=60] tests/guest/lvswitches.sh
# Prints PASS/FAIL per case; exit 1 if any failed.
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-${TMPDIR:-/tmp}/m27-lvswitches}
B=${B:-$repo/build}
T=${T:-60}
M27=${M27:-$B/m27}
rm -rf "$OUT"; mkdir -p "$OUT/if" "$OUT/emb" "$OUT/cache" "$OUT/root"
chmod 755 "$OUT" "$OUT/if"
cd "$OUT" || exit 2
fails=0
pass() { echo "PASS $*"; }
bad() { echo "FAIL $*"; fails=$((fails + 1)); }

printf 'int main(void) { return 7; }\n' >prog.c
clang -arch arm64e -O1 -o plain prog.c && codesign -s - -f plain 2>/dev/null || exit 2
cp plain hardened && codesign -s - -f -o runtime hardened 2>/dev/null || exit 2
# the library-validation flag without the hardened runtime. m27-embed gives its stub the hardened runtime,
# whose process is guarded and reads no switch; its stub's slice is signed again without it here, so the
# embedded process is open-tier and the library-validation rule is what refuses the switches
cp plain library && codesign -s - -f -o library library 2>/dev/null || exit 2
for p in plain hardened library; do
    python3 "$repo/tools/m27-embed" --stub "$B/m27-stub" --host-test -o "emb/$p" "$p" >/dev/null || exit 2
done
lipo emb/library -thin x86_64 -output stub.x86 && codesign -d --entitlements :- stub.x86 >stub.ent 2>/dev/null &&
    codesign -s - -f --entitlements stub.ent -i "$(codesign -dv stub.x86 2>&1 | sed -n 's/^Identifier=//p')" stub.x86 2>/dev/null &&
    lipo emb/library -replace x86_64 stub.x86 -output emb/library.open && mv emb/library.open emb/library || exit 2
codesign -dv --arch x86_64 emb/library 2>&1 | grep -q 'flags=.*runtime' && exit 2
clang -arch x86_64 -arch arm64 -dynamiclib -o if/mark.thunks "$here/lvswitchesmark.c" && codesign -s - -f if/mark.thunks 2>/dev/null || exit 2
chmod 644 if/mark.thunks
: >trust.list

# run NAME ARGS...: with every switch set; stdout+stderr in NAME.out, status in rc, the marker in marked
run() {
    local name=$1; shift
    rm -f mark
    env M27_IF_DIR="$OUT/if" M27_CACHE="$OUT/cache" M27_GUEST_ROOT="$OUT/root" M27_TRUST_LIST="$OUT/trust.list" \
        M27_NO_LIBVAL=1 LVSWITCHES_MARK="$OUT/mark" ${ASYNC:+M27_ASYNC_CONTRACT_LIBRARY="$ASYNC"} \
        timeout -s KILL "$T" "$@" >"$name.out" 2>&1
    rc=$?
    marked=no; [ -e mark ] && marked=yes
}
has() { grep -q -- "$2" "$1.out"; }
switches="M27_CACHE M27_GUEST_ROOT M27_IF_DIR M27_ASYNC_CONTRACT_LIBRARY M27_TRUST_LIST"

for route in cli emb; do
    if [ $route = cli ]; then pl=("$M27" plain); hd=("$M27" hardened); hn=hardened; else pl=(arch -x86_64 emb/plain); hd=(arch -x86_64 emb/library); hn=library; fi
    ASYNC= run "$route-plain" "${pl[@]}"
    [ $rc = 7 ] && [ $marked = yes ] && ! has "$route-plain" "ignored: the program requires" &&
        pass "$route: ad hoc program: M27_IF_DIR's thunk library loads (the control)" ||
        bad "$route: ad hoc program: rc $rc, marker $marked: $(head -c 300 "$route-plain.out")"
    ASYNC="$OUT/none.dylib" run "$route-hardened" "${hd[@]}"
    missing=""
    for s in $switches M27_NO_LIBVAL; do has "$route-hardened" "$s ignored: the program requires library validation" || missing="$missing $s"; done
    [ $rc = 7 ] && [ $marked = no ] && [ -z "$missing" ] &&
        pass "$route: $hn program: no thunk library loaded, every switch named as ignored" ||
        bad "$route: $hn program: rc $rc, marker $marked, not named:$missing: $(head -c 300 "$route-hardened.out")"
done
# The stub of the library-flag program with its ARM slice replaced: by the ad hoc program's (valid, no
# library validation), and by its own with a byte changed. The stub names neither, so the process must
# end with status 126 before anything the environment selects loads.
python3 -I - "$repo/tools" emb/library plain emb/replaced emb/corrupt <<'PY' || exit 2
import sys
sys.path.insert(0, sys.argv[1])
import m27_fat
a, p = open(sys.argv[2], "rb").read(), open(sys.argv[3], "rb").read()
arm = [bytes(s) for c, _, s in m27_fat.slices(a) if c == m27_fat.CPU_ARM64][0]
open(sys.argv[4], "wb").write(m27_fat.replace_slice(a, m27_fat.CPU_ARM64, p))
bad = bytearray(arm)
bad[0x40] ^= 1
open(sys.argv[5], "wb").write(m27_fat.replace_slice(a, m27_fat.CPU_ARM64, bytes(bad)))
PY
chmod +x emb/replaced emb/corrupt
for c in replaced corrupt; do
    ASYNC= run "emb-$c" arch -x86_64 "emb/$c"
    [ $rc = 126 ] && [ $marked = no ] && pass "emb: the library program's stub, ARM slice $c: refused (126), no thunk library loaded" ||
        bad "emb: ARM slice $c: rc $rc, marker $marked: $(head -c 300 "emb-$c.out")"
done
run emb-hardened arch -x86_64 emb/hardened
[ $rc = 7 ] && [ $marked = no ] && has emb-hardened "M27_IF_DIR ignored (guarded process)" &&
    pass "emb: hardened program (hardened stub, guarded): no thunk library loaded" ||
    bad "emb: hardened program: rc $rc, marker $marked: $(head -c 300 emb-hardened.out)"
echo "lvswitches: $fails failed"
exit $((fails != 0))
