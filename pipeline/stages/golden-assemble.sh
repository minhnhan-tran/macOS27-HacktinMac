#!/bin/sh
# golden-assemble.sh KIT LOCAL [--owner USER:GROUP] [--disk diskN --confirm-disk diskN [--external] [--disk-identity FILE]] [--resume WORK]
#                     [--no-cryptex-cache]
#
# Assemble the Golden Gate image from a staged kit (pipeline/kit.py) on the Mac this runs on: the arm64 builder VM
# (golden-run's guest job), an Intel Mac building onto an external SSD (macOS27Tool build-disk) or the target's Recovery
# (macOS27Tool install). KIT is read only. LOCAL (new or empty, on a disk the assembly does not erase) gets local copies of
# the assembly source, this Mac's CPython (KIT/py or KIT/py-x86_64, by uname -m), the site packages, hooks and posts,
# the rendered configuration (LOCAL/config.json) and the work folder; the target is LOCAL/target.img, or the whole
# disk with --disk (golden-image.py's DiskTarget refuses a disk the installer's checks refuse: the Mac's own internal
# disk, or with --external an external SSD only). --resume WORK: the work folder of a failed run on the same target
# (another LOCAL): golden-image.py continues at its failed step when that step can be run again (its RESUMABLE).
# --no-cryptex-cache: neither reuse nor store sealed cryptex images in KIT/cryptex-cache (for an assembly whose kit
# nobody collects afterwards, such as macOS27Tool install from a stick: the store would fill the stick).
# Exit status: golden-image.py's, or the first failing copy's.
set -u
set -o pipefail
[ $# -ge 2 ] || { echo 'usage: golden-assemble.sh KIT LOCAL [--owner U:G] [--disk diskN --confirm-disk diskN [--external] [--disk-identity FILE]] [--resume WORK] [--no-cryptex-cache]' >&2; exit 2; }
G=$1; W=$2; shift 2
OWNER=root:wheel; DISK=; CONFIRM=; EXTERNAL=; RESUME=; NOCACHE=; IDENTITY=
while [ $# -gt 0 ]; do
    case $1 in
        --owner) OWNER=$2; shift 2 ;;
        --disk) DISK=$2; shift 2 ;;
        --confirm-disk) CONFIRM=$2; shift 2 ;;
        --disk-identity) IDENTITY=$2; shift 2 ;;
        --external) EXTERNAL=--external; shift ;;
        --resume) RESUME=$2; shift 2 ;;
        --no-cryptex-cache) NOCACHE=--no-cryptex-cache; shift ;;
        *) echo "golden-assemble.sh: unknown option $1" >&2; exit 2 ;;
    esac
done
# Recipe hooks and posts run from LOCAL and are plain sh: a space in its path (a second USB drive mounts as "M27 1")
# would split their unquoted paths. LOCAL is reached through a link without spaces; the files stay where they are.
case $W in
    *' '*) mkdir -p "$W" || exit $?
           SPACELESS=$(mktemp -d /private/var/tmp/m27-local.XXXXXX) || exit $?
           ln -s "$W" "$SPACELESS/local" || exit $?
           W=$SPACELESS/local
           echo "golden-assemble.sh: the work folder's path has a space; using $W" ;;
esac
case $(uname -m) in
    arm64) PYDIR=py ;;
    x86_64) PYDIR=py-x86_64 ;;
    *) echo "golden-assemble.sh: no kit Python for $(uname -m)" >&2; exit 2 ;;
esac
[ -f "$G/kit.json" ] || { echo "golden-assemble.sh: $G has no kit.json" >&2; exit 2; }
[ -x "$G/$PYDIR/bin/python3.12" ] || { echo "golden-assemble.sh: the kit has no $PYDIR/ CPython" >&2; exit 2; }
# M27_RECOVERY_COMMANDS=FILE (golden-run --recovery-commands, tests): only the commands FILE lists (a Recovery's
# /bin, /sbin, /usr/bin, /usr/sbin, /usr/libexec) are on PATH, as in the target's Recovery, so a command the Recovery
# lacks fails here too. Calls by absolute path are checked by pipeline/tests/test_recovery_commands.py.
if [ -n "${M27_RECOVERY_COMMANDS:-}" ]; then
    mkdir -p "$W/recovery-only" || exit $?
    grep -v '^#' "$M27_RECOVERY_COMMANDS" | while read -r c; do
        [ -n "$c" ] && [ -e "$c" ] && ln -sf "$c" "$W/recovery-only/${c##*/}"
    done
    # A limited PATH cannot hide what this Mac has at an absolute path. OCLP's hardware tool probes /usr/bin/rsync
    # --version: a Recovery has none and the tool merges with ditto; a full macOS has its rsync shim, which passes
    # the probe and then starts "rsync" from PATH. So on a Mac with /usr/bin/rsync it is linked here too (the
    # Recovery's ditto path is checked by pipeline/tests/test_recovery_commands.py and run by the QEMU stick test).
    [ ! -x /usr/bin/rsync ] || ln -sf /usr/bin/rsync "$W/recovery-only/rsync"
    PATH="$W/recovery-only"; export PATH
    echo "golden-assemble.sh: PATH holds only the commands of $M27_RECOVERY_COMMANDS"
fi
# The Mac stays awake until this script exits: a Recovery idle-sleeps after about ten minutes without input
# (bench2 2355: a QEMU install slept 10.5 min in and did not come back). caffeinate where the Mac has it, otherwise the
# kit's recovery-tools (IOKit assertions); either one watches this shell's pid and ends with it.
stay_awake() {
    if command -v caffeinate >/dev/null 2>&1; then
        caffeinate -dimsu -w $$ &
        echo "golden-assemble.sh: caffeinate -dimsu keeps this Mac awake until the assembly ends (pid $!)"
    else
        "$G/$PYDIR/bin/python3.12" -I -B "$G/assembly-source/recovery-tools.py" awake $$ &
        echo "golden-assemble.sh: no caffeinate on this Mac: m27's recovery-tools keeps it awake (pid $!)"
    fi
}
stay_awake
# The copies the assembly runs are made fresh from the kit each run (a fresh install, a resume, a reused --work): nothing
# left in them from before is ever run. The kit is the one macOS27Tool install checked against the USB installer's manifest;
# the resume records (work/) and tmp stay. -B on every start of the kit's CPython: no __pycache__ is written into the kit.
rm -rf "${W:?}/source" "${W:?}/hooks" "${W:?}/post" "${W:?}/py" "${W:?}/site" || exit $?
# mkdir, not install: a macOS Recovery has no /usr/bin/install (nor shasum, cmp, readlink: see recovery_tools below)
mkdir -p "$W/source" "$W/hooks" "$W/post" "$W/py" "$W/site" "$W/tmp" || exit $?
chmod 755 "$W/source" "$W/hooks" "$W/post" "$W/py" "$W/site" "$W/tmp" || exit $?
export TMPDIR="$W/tmp"
bootstrap() {
    (cd "$G/assembly-source" && tar -cf - .) | (cd "$W/source" && tar -xf -) || return $?
    (cd "$G/$PYDIR" && tar -cf - .) | (cd "$W/py" && tar -xf -) || return $?
    # Nothing this Python runs may write into the kit (the USB installer is checked against its manifest; a resumed
    # install must still pass): hooks start it with -I, which ignores PYTHONDONTWRITEBYTECODE, so the copy itself is
    # told, through the sitecustomize every start of it loads (-I keeps site-packages; only -S would skip it).
    for sp in "$W"/py/lib/python3*/site-packages; do
        if [ -d "$sp" ]; then printf 'import sys\nsys.dont_write_bytecode = True\n' > "$sp/sitecustomize.py" || return $?; fi
    done
    (cd "$G/site" && tar -cf - .) | (cd "$W/site" && tar -xf -) || return $?
    "$W/py/bin/python3.12" -I -B "$W/source/copy-hooks.py" "$G/hooks" "$W/hooks" --owner "$OWNER" || return $?
    # post recipes as hooks: an input a recipe declares read-only (read-from-share.txt) is read where it is in the kit
    "$W/py/bin/python3.12" -I -B "$W/source/copy-hooks.py" "$G/post" "$W/post" --owner "$OWNER" || return $?
    chown -R "$OWNER" "$W/source" "$W/py" "$W/site" || return $?
}
bootstrap || exit $?
export PYTHONDONTWRITEBYTECODE=1    # and for any other Python a hook starts without -I
# The tools the assembly and its hooks call that this Mac lacks (a Recovery): wrappers that run
# source/recovery-tools.py with the kit's CPython, first on PATH; the kit's CPython is last on PATH (python3 where the Mac has none).
recovery_tools() {
    mkdir -p "$W/recovery-bin" || return $?
    for t in install shasum cmp readlink; do
        command -v "$t" >/dev/null 2>&1 && continue
        printf '#!/bin/sh\nexec "%s" -I -B "%s" %s "$@"\n' "$W/py/bin/python3.12" "$W/source/recovery-tools.py" "$t" \
            > "$W/recovery-bin/$t" && chmod 755 "$W/recovery-bin/$t" || return $?
        echo "golden-assemble.sh: no $t on this Mac: m27's recovery-tools stands in for it"
    done
}
recovery_tools || exit $?
export PATH="$W/recovery-bin:$PATH:$W/py/bin"
echo "Assembly storage after local source/hook copies:"
df -k "$W"
set --
[ -z "$DISK" ] || set -- --disk "$DISK" --confirm-disk "$CONFIRM" $EXTERNAL ${IDENTITY:+--disk-identity "$IDENTITY"}
[ -z "$NOCACHE" ] || set -- "$@" $NOCACHE
"$W/py/bin/python3.12" -I -B "$W/source/kit.py" render "$G" "$W" --out "$W/config.json" "$@" || exit $?
set --
[ -z "$RESUME" ] || set -- --resume "$RESUME"
PYTHONPATH="$W/site" "$W/py/bin/python3.12" -I -B "$W/source/golden-image.py" "$W/config.json" "$@"
