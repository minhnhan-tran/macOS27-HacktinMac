#!/bin/sh
# Build tests/guest/nativesig.c (+ the native helper dylib nativesighelper.c)
# for arm64e (guest) and arm64 (reference). Each mode runs natively and under
# m27 (JIT, --interp, with an AOT cache); stdout and the exit status must
# match the native run. A hang is killed after T seconds (rc 137).
# tokspin is the exception: natively the failed sigreturn's trap is blocked
# and the thread spins forever (rc 137); m27 takes SIGTRAP's default action
# (rc 133).
# sigret, sigretbad, sigretfwd, sigretpolicy (a failed __sigreturn, nativesighelper.c
# nsh_sigret) run under m27 with the diagnostics log (M27_DIAG_DIR): the
# status must be native arm64's (SIGTRAP, rc 133) and the log must hold the
# failed-sigreturn report with the token verdict and the last handler action
# the case expects.
# giveup and giveupbus (a native fault no handler takes: m27 gives it up) run
# under m27 twice more, with M27_SIGRETURN_USER=1 and M27_SIGRETURN_KERNEL=1:
# the exit status must be the native fault's (SIGSEGV 139, SIGBUS 138), and
# m27's give-up line must say how: "resumed in user space" (the instruction
# faults again with SIG_DFL, no __sigreturn) in the user runs, "returning
# through __sigreturn (kernel: switch)" in the kernel runs.
#   OUT=dir M27=build/m27 [T=20] [MODES="crc ret ..."] tests/guest/nativesig.sh
set -u
# This fixture exercises the guest/native bridge through its universal helper.
export M27_NATIVE_APP_LIBS=1
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
OUT=${OUT:-$(mktemp -d -t m27-nativesig)}
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
M27=${M27:-$repo/build/m27}
T=${T:-20}
MODES=${MODES:-crc crcnull ret readback raise tok tokspin sigret sigretbad sigretfwd sigretpolicy giveup giveupbus}
set -e
# two helpers: the guest's (x86_64, which m27 binds, and arm64e to link
# against) and the reference's (arm64 alone: an arm64 process given a fat
# file with arm64e may load that slice, whose handler signs the pc it sets)
mkdir -p "$OUT/g" "$OUT/arm"
clang -arch x86_64 -arch arm64e -dynamiclib -O1 \
    -install_name "$OUT/g/libnativesighelper.dylib" -o "$OUT/g/libnativesighelper.dylib" "$here/nativesighelper.c"
clang -arch arm64 -dynamiclib -O1 \
    -install_name "$OUT/arm/libnativesighelper.dylib" -o "$OUT/arm/libnativesighelper.dylib" "$here/nativesighelper.c"
codesign -s - -f "$OUT/g/libnativesighelper.dylib" "$OUT/arm/libnativesighelper.dylib" 2>/dev/null
clang -arch arm64e -O1 -L"$OUT/g" -lnativesighelper -o "$OUT/nativesig.arm64e" "$here/nativesig.c"
clang -arch arm64 -O1 -L"$OUT/arm" -lnativesighelper -o "$OUT/nativesig.arm64" "$here/nativesig.c"
codesign -s - -f "$OUT/nativesig.arm64e" "$OUT/nativesig.arm64" 2>/dev/null
rm -rf "$OUT/aot"; mkdir -m 700 "$OUT/aot"
timeout -s KILL "$T" "$M27" --aot --cache "$OUT/aot" "$OUT/nativesig.arm64e" >/dev/null 2>&1
set +e
fail=0
for m in $MODES; do
    timeout -s KILL "$T" "$OUT/nativesig.arm64" $m >"$OUT/$m.native.out" 2>"$OUT/$m.native.err"
    nrc=$?
    runs="jit interp aot"
    case $m in giveup*) runs="jit interp aot jit-user interp-user aot-user jit-kern interp-kern aot-kern" ;; esac
    for v in $runs; do
        case $v in
        jit*) set -- env M27_NO_AOT=1 "$M27" ;;
        interp*) set -- env M27_NO_AOT=1 "$M27" --interp ;;
        aot*) set -- env M27_CACHE="$OUT/aot" "$M27" ;;
        esac
        case $v in
        *-user) set -- env M27_SIGRETURN_USER=1 "$@" ;;
        *-kern) set -- env M27_SIGRETURN_KERNEL=1 "$@" ;;
        esac
        diag=
        case $m in sigret*)
            diag=$OUT/diag.$m.$v; rm -rf "$diag"; mkdir -p "$diag"
            set -- env M27_DIAG_DIR="$diag" "$@" ;;
        esac
        timeout -s KILL "$T" "$@" "$OUT/nativesig.arm64e" $m >"$OUT/$m.$v.out" 2>"$OUT/$m.$v.err"
        rc=$?
        want=$nrc
        [ $m = tokspin ] && want=133
        if [ $rc = $want ] && cmp -s "$OUT/$m.native.out" "$OUT/$m.$v.out"; then r=SAME; else r=DIFF; fail=1; fi
        case $m.$v in
        giveup*-user) gl='given up: SIG_DFL, the faulting instruction resumed in user space to fault again' ;;
        giveup*-kern) gl='given up: SIG_DFL, returning through __sigreturn \(kernel: switch\) to fault again' ;;
        giveup*) gl='given up: SIG_DFL, ' ;;
        *) gl= ;;
        esac
        if [ -n "$gl" ] && [ $r = SAME ] && ! grep -Eq "signal (10|11) \(code [0-9]+, addr 0x[0-9a-f]+\) $gl" "$OUT/$m.$v.err"; then
            r="DIFF (no give-up line '$gl')"; fail=1
        fi
        if [ -n "$diag" ]; then
            # the report: its first line, the verdict, and whose frame failed
            case $m in
            sigret) pats='uctx readable, token matches|last m27 handler action on this thread: nx redirect of a native call to guest ' ;;
            sigretbad) pats='errno=14 \(EFAULT\) uctx=0x[0-9a-f]* token=0x5678: uctx not readable, token mismatch' ;;
            sigretfwd) pats='uctx readable, token matches|forwarded to the handler .*nsh_tok_segv.*: the failed frame.s address, token unchanged' ;;
            sigretpolicy) pats='errno=1 \(EPERM\).*uctx readable, token matches|EPERM can be a syscall-unix policy denial of SYS_sigreturn before frame/token validation' ;;
            esac
            log=$diag/console.log
            echo "$pats" | tr '|' '\n' | while read -r p; do
                grep -Eq "failed __sigreturn sig=4 at .*_sigtramp\+0x[0-9a-f]+ \(ud2 after the call of __sigreturn 0x" "$log" 2>/dev/null &&
                    grep -Eq "$p" "$log" 2>/dev/null || echo "no '$p'"
            done >"$OUT/$m.$v.diag"
            [ -s "$OUT/$m.$v.diag" ] && { r="DIFF (report: $(head -1 "$OUT/$m.$v.diag"))"; fail=1; }
        fi
        printf '%-9s %-11s native rc=%-3s m27 rc=%-3s %s\n' "$m" "$v" "$nrc" "$rc" "$r"
    done
done
exit $fail
