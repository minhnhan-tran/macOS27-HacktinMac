#!/bin/bash
# The CLI front end and the guest-signature policy (src/main.c, src/guest_sig.c):
# which guests m27 refuses (also with trustd unreachable, and when m27 is started
# as its own second stage), option parsing, M27_FLAGS, argc 0.
#   OUT=dir [M27=build/m27] [B=build] [T=60] tests/guest/cli.sh
# Prints PASS/FAIL per case; exit 1 if any failed.
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-$(mktemp -d -t m27-cli)}
M27=${M27:-$repo/build/m27}
B=${B:-$repo/build}
T=${T:-60}
FS=${M27_FS:-}   # a mounted macOS 27 System (optional; its cases skip without it)
mkdir -p "$OUT"
fails=0
pass() { echo "PASS $*"; }
bad() { echo "FAIL $*"; fails=$((fails + 1)); }
G=$OUT/hello
clang -arch arm64e -O1 -o "$G" "$here/hello.c" && codesign -s - -f "$G" 2>/dev/null || exit 2
clang -arch arm64e -O1 -o "$OUT/argc0" "$here/argc0.c" && codesign -s - -f "$OUT/argc0" 2>/dev/null || exit 2
clang -arch x86_64 -O1 -o "$OUT/spawn0" "$here/spawn0.c" && codesign -s - -f "$OUT/spawn0" 2>/dev/null || exit 2

# run NAME EXPECT_RC GREP_PATTERN|"" ARGS... (stderr+stdout in OUT/NAME.out)
run() {
    local name=$1 want=$2 pat=$3; shift 3
    (cd /tmp && timeout -s KILL "$T" "$@") >"$OUT/$name.out" 2>&1
    local rc=$?
    if [ $rc = "$want" ] && { [ -z "$pat" ] || grep -q -- "$pat" "$OUT/$name.out"; }; then pass "$name (rc $rc)"
    else bad "$name: rc $rc, want $want${pat:+ and /$pat/}: $(head -c 300 "$OUT/$name.out")"; fi
}
# also RUN_NAME NAME PATTERN / lacks RUN_NAME NAME PATTERN: that run's output has, or has not, the pattern
also() { grep -q -- "$3" "$OUT/$1.out" && pass "$2" || bad "$2: no /$3/ in: $(head -c 300 "$OUT/$1.out")"; }
lacks() { grep -q -- "$3" "$OUT/$1.out" && bad "$2: /$3/ in: $(head -c 300 "$OUT/$1.out")" || pass "$2"; }

# --- signature policy
run "ad hoc (linker-signed) guest runs" 7 "hello from arm64e" "$M27" "$G"
cp "$G" "$OUT/unsigned" && codesign --remove-signature "$OUT/unsigned"
run "unsigned guest refused" 126 "not code signed" "$M27" "$OUT/unsigned"
run "unsigned guest with --allow-unsigned runs" 7 "hello from arm64e" "$M27" --allow-unsigned "$OUT/unsigned"
run "unsigned guest with M27_FLAGS=u runs" 7 "hello from arm64e" env M27_FLAGS=u "$M27" "$OUT/unsigned"
run "unsigned guest, M27_FLAGS=u in a distrusted process: refused" 126 "not code signed" \
    env M27_FLAGS=u M27_DISTRUST_ENV=1 "$M27" "$OUT/unsigned"
cp "$G" "$OUT/tampered"
python3 -I - "$OUT/tampered" <<'PY'
import sys
p = sys.argv[1]; d = bytearray(open(p, "rb").read())
i = d.find(b"hello from arm64e"); d[i] ^= 0x20; open(p, "wb").write(d)
PY
run "tampered guest refused" 126 "code signature invalid" "$M27" "$OUT/tampered"
printf '<?xml version="1.0"?><plist version="1.0"><dict><key>com.apple.private.security.no-sandbox</key><true/></dict></plist>' >"$OUT/ents.plist"
cp "$G" "$OUT/entitled" && codesign -s - -f --entitlements "$OUT/ents.plist" "$OUT/entitled" 2>/dev/null
run "ad hoc guest claiming a private entitlement refused" 126 "claims entitlements m27 cannot grant" "$M27" "$OUT/entitled"
printf '<?xml version="1.0"?><plist version="1.0"><dict><key>com.apple.security.get-task-allow</key><true/></dict></plist>' >"$OUT/gta.plist"
cp "$G" "$OUT/gta" && codesign -s - -f --entitlements "$OUT/gta.plist" "$OUT/gta" 2>/dev/null
run "ad hoc guest with get-task-allow runs" 7 "hello from arm64e" "$M27" "$OUT/gta"
# A bundle's main executable binds the bundle's Info.plist, which Security finds only from its path in the
# bundle (r16: XProtect exited 126 with errSecCSInfoPlistFailed through /dev/fd/N). The bundle is a plain
# directory with Contents/Info.plist and a CLI hello, not an .app.
BD=$OUT/Tool
rm -rf "$BD" && mkdir -p "$BD/Contents/MacOS" "$BD/Contents/Resources"
cat >"$BD/Contents/Info.plist" <<'PL'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>CFBundleExecutable</key><string>tool</string>
<key>CFBundleIdentifier</key><string>local.m27.test.tool</string><key>CFBundlePackageType</key><string>BNDL</string></dict></plist>
PL
echo resource >"$BD/Contents/Resources/data.txt"
clang -arch arm64e -O1 -o "$BD/Contents/MacOS/tool" "$here/hello.c" && codesign -s - -f "$BD" 2>/dev/null || exit 2
clang -arch arm64 -O1 -o "$OUT/hello.arm64" "$here/hello.c" && codesign -s - -f "$OUT/hello.arm64" 2>/dev/null || exit 2
nat=$(cd /tmp && timeout -s KILL "$T" "$OUT/hello.arm64" 2>&1; echo "rc=$?")
for mode in jit interp; do
    opt=(); [ $mode = interp ] && opt=(--interp)
    got=$(cd /tmp && timeout -s KILL "$T" "$M27" ${opt[@]+"${opt[@]}"} "$BD/Contents/MacOS/tool" 2>&1; echo "rc=$?")
    [ "$got" = "$nat" ] && pass "bundle main executable runs, same as native arm64 ($mode)" ||
        bad "bundle main executable ($mode): native '$nat', m27 '$got'"
done
rm -rf "$OUT/ToolRes" "$OUT/ToolPlist" "$OUT/ToolText"; cp -R "$BD" "$OUT/ToolRes" && echo changed >"$OUT/ToolRes/Contents/Resources/data.txt"
run "bundle with a modified resource runs (exec checks no resources)" 7 "hello from arm64e" "$M27" "$OUT/ToolRes/Contents/MacOS/tool"
# exec checks no Info.plist binding (nor does m27, docs/security-model.md): a modified Info.plist runs natively, so it
# runs under m27. The native reference is the same bundle with an arm64 executable.
rm -rf "$OUT/ToolPlistN"
cp -R "$BD" "$OUT/ToolPlist" && sed -i '' 's/local.m27.test.tool/local.m27.test.evil/' "$OUT/ToolPlist/Contents/Info.plist"
cp -R "$BD" "$OUT/ToolPlistN" && cp "$OUT/hello.arm64" "$OUT/ToolPlistN/Contents/MacOS/tool" && codesign -s - -f "$OUT/ToolPlistN" 2>/dev/null &&
    sed -i '' 's/local.m27.test.tool/local.m27.test.evil/' "$OUT/ToolPlistN/Contents/Info.plist" || exit 2
natp=$(cd /tmp && timeout -s KILL "$T" "$OUT/ToolPlistN/Contents/MacOS/tool" 2>&1; echo "rc=$?")
gotp=$(cd /tmp && timeout -s KILL "$T" "$M27" "$OUT/ToolPlist/Contents/MacOS/tool" 2>&1; echo "rc=$?")
[ "$natp" = "$nat" ] && [ "$gotp" = "$natp" ] && pass "bundle with a modified Info.plist runs, as on native arm64" ||
    bad "bundle with a modified Info.plist: native '$natp', m27 '$gotp'"
cp -R "$BD" "$OUT/ToolText"
python3 -I - "$OUT/ToolText/Contents/MacOS/tool" <<'PY'
import sys
p = sys.argv[1]; d = bytearray(open(p, "rb").read())
i = d.find(b"hello from arm64e"); d[i] ^= 0x20; open(p, "wb").write(d)
PY
run "bundle with a tampered executable refused" 126 "does not match its hash" "$M27" "$OUT/ToolText/Contents/MacOS/tool"
if [ -n "$FS" ] && [ -f "$FS/bin/ls" ]; then
    run "Apple-signed 27.0 ls runs" 0 "Apple signature" "$M27" -v "$FS/bin/ls" -d /
else echo "SKIP Apple-signed guest (M27_FS: no macOS 27 System at '$FS')"; fi
# flip one byte of FILE's arm64 slice (the one m27 runs) at its __TEXT,__text + 64
flip_text() {
    python3 -I - "$1" <<'PY'
import struct, sys
p = sys.argv[1]; d = bytearray(open(p, "rb").read())
base = 0
if d[:4] in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
    wide = d[3] == 0xbf; n = struct.unpack_from(">I", d, 4)[0]; best = None
    for k in range(n):
        if wide: cpu, sub, off = struct.unpack_from(">iIQ", d, 8 + 32 * k)
        else: cpu, sub, off = struct.unpack_from(">iII", d, 8 + 20 * k)
        r = (3 if sub >> 24 in (0, 0x80) else 2) if sub & 0xffffff == 2 else 1
        if cpu == 0x0100000c and (best is None or r > best[0]): best = (r, off)
    base = best[1]
ncmds = struct.unpack_from("<I", d, base + 16)[0]; o = base + 32
for _ in range(ncmds):
    cmd, size = struct.unpack_from("<II", d, o)
    if cmd == 0x19 and d[o + 8:o + 14] == b"__TEXT":
        for s in range(struct.unpack_from("<I", d, o + 64)[0]):
            so = o + 72 + 80 * s
            if d[so:so + 6] == b"__text":
                at = base + struct.unpack_from("<I", d, so + 48)[0] + 64
                d[at] ^= 0x01; open(p, "wb").write(d); sys.exit(0)
    o += size
sys.exit("no __text")
PY
}
cp "$G" "$OUT/tampered-text"; flip_text "$OUT/tampered-text"
run "ad hoc guest with one __text byte flipped refused (page hash)" 126 "does not match its hash" "$M27" "$OUT/tampered-text"
cp "$G" "$OUT/trailing" && printf 'unsigned tail' >>"$OUT/trailing"
run "bytes after the code signature refused" 126 "code signature invalid" "$M27" "$OUT/trailing"
# a second LC_CODE_SIGNATURE (the same range, in the header padding): which signature would be checked and
# which would give the policy must not be a question
python3 -I - "$G" "$OUT/twosig" <<'PY'
import struct, sys
src, dst = sys.argv[1], sys.argv[2]
d = bytearray(open(src, "rb").read())
ncmds, sizeofcmds = struct.unpack_from("<II", d, 16)
o = 32
for _ in range(ncmds):
    cmd, size = struct.unpack_from("<II", d, o)
    if cmd == 0x1d: sig = bytes(d[o:o + 16])
    o += size
assert not any(d[o:o + 16]), "no room in the header padding"
d[o:o + 16] = sig
struct.pack_into("<II", d, 16, ncmds + 1, sizeofcmds + 16)
open(dst, "wb").write(d)
PY
chmod +x "$OUT/twosig"
run "guest with two LC_CODE_SIGNATURE commands refused" 126 "more than one LC_CODE_SIGNATURE" "$M27" "$OUT/twosig"

# --- the hand-off to the second stage carries no trust. Anyone can start m27 as
# its own stage 2 (M27_EXEC naming argv[0]); M27_EXEC_ID is where a first stage's
# verdict once travelled. The file is checked in the process that maps it.
# forged NAME EXPECT_RC PATTERN FILE [ENV=VALUE...]
forged() {
    local name=$1 want=$2 pat=$3 file=$4; shift 4
    run "$name" "$want" "$pat" env M27_EXEC="$file" M27_EXEC_ID="$(stat -f '%d:%i' "$file")" "$@" \
        /bin/bash -c 'exec -a "$1" "$2"' - "$file" "$M27"
}
forged "forged stage-2 hand-off, unsigned guest: refused" 126 "not code signed" "$OUT/unsigned"
forged "forged stage-2 hand-off, tampered guest: refused" 126 "code signature invalid" "$OUT/tampered"
forged "forged stage-2 hand-off, guest claiming a private entitlement: refused" 126 "claims entitlements m27 cannot grant" "$OUT/entitled"
forged "forged stage-2 hand-off, ad hoc guest: checked, then run" 7 "hello from arm64e" "$G"
also "forged stage-2 hand-off, ad hoc guest: checked, then run" "a hand-off without the tagged-pointer switch is reported" \
    "started without OBJC_DISABLE_TAG_OBFUSCATION"
forged "forged stage-2 hand-off with the unsigned switch, distrusted process: refused" 126 "not code signed" "$OUT/unsigned" \
    M27_EXEC_FLAGS=u M27_DISTRUST_ENV=1
# a program that does not exist is reported; its arguments are not read as m27's options
run "missing program: open error" 126 "open $OUT/missing: No such file or directory" "$M27" "$OUT/missing" --allow-unsigned "$OUT/unsigned"
lacks "missing program: open error" "missing program: its arguments are not m27's options" "hello from arm64e"

# --- the verdict needs no daemon: launchd starts arm64-only programs before
# trustd exists (a Security-only check refuses seputil at PID 5 with CSSMERR_TP_NOT_TRUSTED)
printf '(version 1)\n(allow default)\n(deny mach-lookup (global-name "com.apple.trustd") (global-name "com.apple.trustd.agent"))\n' >"$OUT/notrustd.sb"
NT=(sandbox-exec -f "$OUT/notrustd.sb")
run "ad hoc guest, trustd unreachable" 7 "hello from arm64e" "${NT[@]}" "$M27" "$G"
# An arm64 kernel registers the host's own 16 KiB-page arm64e signatures (an
# x86_64 one refuses every page size but 4 KiB): the Apple-silicon host only.
if [ "$(arch -x86_64 sysctl -n sysctl.proc_translated 2>/dev/null)" = 1 ] && lipo -archs /bin/ls 2>/dev/null | grep -q arm64e; then
    run "host Apple platform arm64e slice of ls, trustd unreachable: kernel verdict" 0 "Apple signature (kernel: platform binary)" \
        "${NT[@]}" "$M27" -v /bin/ls -d /
    # M27_LIBVAL_USER: the signer as on a kernel that cannot hold the signature. Security.framework
    # answers in a child of the process that runs the guest, which trusts the child's exit status only.
    # M27_LIBVAL_USER is a test switch: only a build with -DM27_TEST_SWITCHES reads it.
    if env M27_LIBVAL_USER=1 "$M27" /bin/ls -d / 2>&1 | grep -q "M27_LIBVAL_USER ignored"; then
        echo "SKIP signer from Security.framework in a child (needs a M27_TEST_SWITCHES build)"
    else
        run "the same slice, signer from Security.framework in a child" 0 "Apple signature (Security.framework)" \
            env M27_LIBVAL_USER=1 "$M27" -v /bin/ls -d /
        run "the same, trustd unreachable: refused" 126 "the cdhash is in no trusted cdhash list" \
            "${NT[@]}" env M27_LIBVAL_USER=1 "$M27" /bin/ls -d /
    fi
else echo "SKIP host arm64e ls with trustd unreachable (no Rosetta or no arm64e /bin/ls)"; fi
AT=$FS/usr/bin/dserr
if [ -n "$FS" ] && [ -f "$AT" ]; then
    echo "$AT" >"$OUT/trust.in"
    "$repo/tools/m27-trust-list" "$FS" "$OUT/trust.in" >"$OUT/trusted-cdhashes" 2>/dev/null
    chmod 644 "$OUT/trusted-cdhashes"
    run "Apple-signed 27.0 arm64-only dserr, trustd unreachable: trusted cdhash list" 0 "eDSRecordNotFound" \
        "${NT[@]}" env M27_TRUST_LIST="$OUT/trusted-cdhashes" "$M27" "$AT" -14136
    cp "$AT" "$OUT/dserr-tampered"; flip_text "$OUT/dserr-tampered"
    run "listed Apple-signed program with one __text byte flipped refused (page hash)" 126 "does not match its hash" \
        "${NT[@]}" env M27_TRUST_LIST="$OUT/trusted-cdhashes" "$M27" "$OUT/dserr-tampered" -14136
    run "M27_TRUST_LIST ignored in a distrusted process" 126 "code signature invalid" \
        "${NT[@]}" env M27_TRUST_LIST="$OUT/trusted-cdhashes" M27_DISTRUST_ENV=1 "$M27" "$AT" -14136
    chmod 666 "$OUT/trusted-cdhashes"
    run "world-writable M27_TRUST_LIST not used" 126 "M27_TRUST_LIST=.*not used" \
        "${NT[@]}" env M27_TRUST_LIST="$OUT/trusted-cdhashes" "$M27" "$AT" -14136
    chmod 644 "$OUT/trusted-cdhashes"
else echo "SKIP trusted cdhash list (M27_FS: no macOS 27 System at '$FS')"; fi
if [ -u /usr/bin/login ] && [ -r /usr/bin/login ] && [ "$(stat -f %u /usr/bin/login)" != "$(id -u)" ]; then
    run "set-user-ID guest owned by another user refused (host login)" 126 "set-user-ID program" "$M27" /usr/bin/login -h
else echo "SKIP set-user-ID guest (no readable /usr/bin/login owned by another user)"; fi

# --- options and M27_FLAGS
v=(); for k in $(seq 40); do v+=(-v); done
run "40 -v options (flags buffer was 16 bytes)" 7 "hello from arm64e" "$M27" "${v[@]}" "$G"
vv=(); for k in $(seq 20); do vv+=(-vv); done
run "20 -vv options" 7 "hello from arm64e" "$M27" "${vv[@]}" "$G"
cp "$G" "$OUT/-v"
run "a program named -v after -- (M27Exec's #! form)" 7 "hello from arm64e" "$M27" -- "$OUT/-v"
run "M27_FLAGS=s adds to the CLI options" 7 "m27: blocks=" env M27_FLAGS=s "$M27" "$G"
run "M27_FLAGS=V alone is verbose" 7 "m27: loaded" env M27_FLAGS=V "$M27" "$G"
run "unknown option: usage" 64 "usage: m27" "$M27" --bogus "$G"
# A switch the process's tier ignores is named without -v. The command line's
# switches are apart from an inherited M27_FLAGS: each is honoured where the
# environment switch of the same effect is (-v as M27_VERBOSE, in a guarded process too).
run "--allow-unsigned in a distrusted process: refused" 126 "not code signed" env M27_DISTRUST_ENV=1 "$M27" --allow-unsigned "$OUT/unsigned"
also "--allow-unsigned in a distrusted process: refused" "the ignored --allow-unsigned is named without -v" \
    "^m27: --allow-unsigned ignored (guarded process)"
run "M27_FLAGS=u in a distrusted process: the ignored M27_FLAGS is named without -v" 126 "^m27: M27_FLAGS ignored (guarded process)" \
    env M27_FLAGS=u M27_DISTRUST_ENV=1 "$M27" "$OUT/unsigned"
run "-v in a distrusted process is honoured" 7 "ad hoc signature" env M27_FLAGS=s M27_DISTRUST_ENV=1 "$M27" -v "$G"
lacks "-v in a distrusted process is honoured" "the inherited M27_FLAGS=s beside it is not" "^m27: blocks="
run "--stats in a distrusted process: ignored and named" 7 "^m27: --stats ignored (guarded process)" env M27_DISTRUST_ENV=1 "$M27" --stats "$G"
lacks "--stats in a distrusted process: ignored and named" "no statistics there" "^m27: blocks="
run "M27_VERBOSE=0 beats -v (the environment beats flags)" 7 "hello from arm64e" env M27_VERBOSE=0 "$M27" -v "$G"
lacks "M27_VERBOSE=0 beats -v (the environment beats flags)" "no verbose output then" "signature ("
# M27_VERBOSE=3 traces the word at x0 of every native call: only when it is mapped
for a in arm64e arm64; do clang -arch $a -O1 -o "$OUT/tracearg.$a" "$here/tracearg.c" && codesign -s - -f "$OUT/tracearg.$a" 2>/dev/null; done
nat=$(cd /tmp && timeout -s KILL "$T" "$OUT/tracearg.arm64" 2>&1; echo "rc=$?")
for mode in jit interp; do
    opt=(); [ $mode = interp ] && opt=(--interp)
    got=$(cd /tmp && timeout -s KILL "$T" env M27_VERBOSE=3 "$M27" ${opt[@]+"${opt[@]}"} "$OUT/tracearg.arm64e" 2>/dev/null
          echo "rc=$?")
    [ "$got" = "$nat" ] && pass "M27_VERBOSE=3 with an unmapped pointer-like x0, same as native arm64 ($mode)" ||
        bad "M27_VERBOSE=3 with an unmapped pointer-like x0 ($mode): native '$nat', m27 '$got'"
done

# --- argc 0
r=$("$OUT/spawn0" "$M27" 2>&1 | tail -1)
[ "$r" = "exit 64" ] && pass "CLI with an empty argv: usage ($r)" || bad "CLI with an empty argv: $r"
if [ -x "$repo/tools/m27-embed" ] && [ -f "$B/m27-stub" ]; then
    "$repo/tools/m27-embed" --host-test --stub "$B/m27-stub" -o "$OUT/argc0.emb" "$OUT/argc0" >/dev/null 2>&1
    r=$(cd /tmp && timeout -s KILL "$T" "$OUT/spawn0" "$OUT/argc0.emb" 2>&1 | tail -1)
    [ "$r" = "exit 3" ] && pass "embedded program with an empty argv ($r)" || bad "embedded program with an empty argv: $r"
else echo "SKIP embedded program with an empty argv (no tools/m27-embed or $B/m27-stub)"; fi
echo "cli: $fails failed"
exit $((fails != 0))
