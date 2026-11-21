#!/bin/bash
# Workflow plan step 9a laptop run: the live backend of `macOS27Tool patch` on the MacBookPro9,2 test laptop, by the holder
# of the laptop lock, one step at a time (two reboots in between are the lock holder's own steps).
#
#   tools/macOS27Tool/laptop/run-9a.sh prepare        copy m27 + an x86_64 Python to the laptop, make the test overlay there
#   tools/macOS27Tool/laptop/run-9a.sh probe          macOS27Tool patch --probe (read-only)
#   tools/macOS27Tool/laptop/run-9a.sh dry-run        what the patch would write (read-only)
#   tools/macOS27Tool/laptop/run-9a.sh apply          write it, take the snapshots
#   (reboot 1: tools/macOS27Tool/laptop/run-9a.sh reboot-note)
#   tools/macOS27Tool/laptop/run-9a.sh verify-applied running the patch? did the old snapshots survive the boot?
#   tools/macOS27Tool/laptop/run-9a.sh revert         restore the files, tag the snapshot booted before
#   (reboot 2)
#   tools/macOS27Tool/laptop/run-9a.sh verify-reverted
#   tools/macOS27Tool/laptop/run-9a.sh collect        records, logs and snapshot lists into EVIDENCE
#
# The test overlay (made on the laptop from its own files, tools/macOS27Tool/laptop/m279a.py):
#   usr/share/m27/9a-probe.txt                           new, System volume (seen only after the reboot)
#   usr/share/misc/ascii                                 replaced (the ascii(7) table plus one comment line), System
#   Library/Application Support/m27-9a/data-probe.txt    new, Data volume (seen at once)
#
# Environment: LAPTOP_SSH (required: the laptop SSH helper, with its LAPTOP/LAPTOP_KEY), PY_TGZ (the
# x86_64 python-build-standalone archive, sha256 below), EVIDENCE (default
# $repo/store/runs/m27-9a-YYYYMMDD), LOCK (the laptop lock file; default $repo/store/tmp/laptop.lock).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
ssh_tool=${LAPTOP_SSH:-}
py_tgz=${PY_TGZ:-$repo/store/artifacts/python-x86_64-3.12.14-20261109-0d6a4a299908/cpython-3.12.14+20260924-x86_64-apple-darwin-install_only.tar.gz}
py_sha=0d6a4a299908123f00bc844df737603f047ff9eba14fda6cad83f3cf3cb3a2af
evidence=${EVIDENCE:-$repo/store/runs/m27-9a-$(date +%Y%m%d)}
lock=${LOCK:-$repo/store/tmp/laptop.lock}
W=/private/var/tmp/m27-9a                 # on the laptop
M27="$W/python/bin/python3 -I $W/m27/m27 patch"
OV=$W/overlay
die() { echo "run-9a: $*" >&2; exit 1; }
on() { "$ssh_tool" -t 900 -- "$@"; }
expect() {   # expect FILE PATTERN...: every pattern (an extended regex) must match a line of FILE
    f=$1; shift
    for p in "$@"; do grep -Eq -- "$p" "$f" || { echo "MISSING: $p" >&2; return 1; }; done
    echo "expected output present ($# lines checked)"
}
step=${1:-}
[ -n "$step" ] || { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 2; }
[ "$step" = reboot-note ] || [ -n "$ssh_tool" ] || die "set LAPTOP_SSH to the laptop SSH helper"
[ -e "$lock" ] || echo "run-9a: note: $lock does not exist; rebooting and patching the laptop belong to its holder" >&2
mkdir -p "$evidence"
log=$evidence/$step.txt
case $step in
prepare)
    [ -f "$py_tgz" ] || die "no $py_tgz (PY_TGZ)"
    [ "$(shasum -a 256 "$py_tgz" | cut -d' ' -f1)" = "$py_sha" ] || die "$py_tgz: sha256 is not $py_sha"
    t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
    tar -C "$repo/tools" -cf "$t/m27.tar" --exclude __pycache__ --exclude tests m27
    on "rm -rf $W && mkdir -p $W && chmod 700 $W"
    "$ssh_tool" --put "$t/m27.tar" "$W/m27.tar"
    "$ssh_tool" --put "$py_tgz" "$W/python.tar.gz"
    on "cd $W && tar -xf m27.tar && tar -xzf python.tar.gz && [ \"\$(shasum -a 256 python.tar.gz | cut -d' ' -f1)\" = $py_sha ] \
        && $W/python/bin/python3 -I -c 'import ctypes, plistlib, sys; print(sys.version.split()[0], sys.platform)' \
        && $W/python/bin/python3 -I $W/m27/laptop/m279a.py overlay $OV && $W/python/bin/python3 -I $W/m27/laptop/m279a.py verify before $OV" | tee "$log"
    # 3.12.14 darwin; three overlay lines (ascii: before X after Y; the new files: before none);
    # PASS usr/share/m27/9a-probe.txt is absent, PASS usr/share/misc/ascii is X, PASS ... data-probe.txt is absent
    expect "$log" '^3\.12\.14 darwin$' '^PASS usr/share/m27/9a-probe.txt is absent' '^before: 0 failed$' ;;
probe)
    on "$M27 --probe" | tee "$log" || true
    on "$M27 --probe --log json" > "$evidence/probe.json" || true
    # every step "ok" except "revert: bless --last-sealed-snapshot" (an image m27 built has no sealed snapshot)
    expect "$log" '^machine x86_64, euid 0, csr 0x[0-9a-f]+, authenticated root (disabled|unknown)' \
        '^booted: [^ ]+ \(' '^ok   find the System and Data volumes' \
        '^ok   SIP allows' '^ok   mount the System volume read-write' '^ok   name a snapshot first' \
        '^ok   revert: root-to the booted snapshot' '^NO   revert: bless --last-sealed-snapshot: no sealed snapshot' \
        '^live patch would run$' ;;
dry-run)
    on "$M27 $OV --live --dry-run" | tee "$log"
    expect "$log" '^3 to write, 0 to set attributes on, 0 already current$' \
        '^  new    m27-9a +Library/Application Support/m27-9a/data-probe.txt$' \
        '^  new    m27-9a +usr/share/m27/9a-probe.txt$' '^  write  m27-9a +usr/share/misc/' ;;
apply)
    on "$M27 $OV --live --i-have-a-backup --show" | tee "$log"
    on "$M27 --list --live; diskutil apfs listSnapshots /; ls -lO /usr/share/m27 '/Library/Application Support/m27-9a' 2>&1" | tee -a "$log"
    # live: System diskNsM, Data ..., booted com.apple.bless.<A> (sealed: No)
    # 3 to write ...; mounted /dev/diskNsM read-write at /private/var/run/m27-live-system
    # named snapshot m27-pre-m27-<ID> taken before writing; new/new/write lines; next boot: new snapshot <B>
    # applied m27-<ID>: 3 written, 0 attributes set, 0 unchanged, snapshot com.apple.bless.<B>
    # The running system does not have usr/share/m27 yet (System files arrive with the reboot); the Data file is there.
    expect "$log" '^live: System disk[0-9]+s[0-9]+, Data disk[0-9]+s[0-9]+, booted com\.apple\.bless\.' \
        '^mounted /dev/disk[0-9]+s[0-9]+ read-write at /private/var/run/m27-live-system$' \
        '^(named snapshot m27-pre-m27-[0-9-]+ taken before writing|warning: no named snapshot before writing)' '^next boot: new snapshot com\.apple\.bless\.' \
        '^applied m27-[0-9-]+: 3 written, 0 attributes set, 0 unchanged, snapshot com\.apple\.bless\.' \
        '^m27-[0-9-]+ +applied +3 files' 'No such file or directory' 'data-probe.txt' ;;
reboot-note)
    cat <<EOF
Reboot (the lock holder's step): the laptop's default boot is the NetBoot Recovery, so
  1. ${ssh_tool:-\$LAPTOP_SSH} -- shutdown -r now                    # back to the Recovery
  2. the NetBoot tool's one-time boot of the internal disk (no push), and stay there
Then: $0 verify-applied (after apply) or $0 verify-reverted (after revert).
EOF
    exit 0 ;;
verify-applied)
    on "$W/python/bin/python3 -I $W/m27/laptop/m279a.py verify applied $OV; $M27 --probe" | tee "$log" || true
    # PASS for the three files (after sha256) and their mode/owner/flags, the record applied, running its snapshot.
    # NOTE lines answer the open question: did snapshot_booted and snapshot_pre SURVIVE a boot?
    expect "$log" '^applied: 0 failed$' '^NOTE snapshot_booted .* (SURVIVED|is GONE after) this boot$' \
        '^ok   no macOS27Tool patch or revert since this boot' '^live patch would run$' ;;
revert)
    on "$M27 --revert --live --i-have-a-backup --show" | tee "$log"
    on "ls '/Library/Application Support/m27-9a' 2>&1; diskutil apfs listSnapshots /" | tee -a "$log"
    # mounted ...; remove/remove/restore lines; next boot: snapshot com.apple.bless.<A>;
    # reverted m27-<ID>: 3 files; reboot to run snapshot com.apple.bless.<A>   (A = the snapshot booted before apply)
    # 26B131 laptop: apfs_systemsnapshot -r is refused (no entitlement), so revert blesses a new snapshot of the
    # restored volume instead of rooting to the one booted before
    expect "$log" '^reverted m27-[0-9-]+: 3 files; reboot to run snapshot ' \
        '^  restore usr/share/misc/' 'No such file or directory' ;;
verify-reverted)
    on "$W/python/bin/python3 -I $W/m27/laptop/m279a.py verify reverted $OV; $M27 --probe" | tee "$log" || true
    expect "$log" '^PASS usr/share/misc/ascii is ' '^PASS record m27-[0-9-]+ is reverted$' \
        '^live patch would run$' ;;
collect)
    on "cd /private/var/db && tar -cf - m27" > "$evidence/state-m27.tar"
    on "diskutil info -plist /; diskutil apfs listSnapshots -plist /; mount" > "$evidence/disks.txt"
    echo "evidence in $evidence"; ls -l "$evidence" ;;
*) die "unknown step $step" ;;
esac
