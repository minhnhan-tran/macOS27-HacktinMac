# macos27tool-command: the m27 command on the installed Mac (owner 2026-11-16, Q5 "Bundle it."): the kit's x86_64 CPython under
# usr/local/lib/m27/python, tools/macOS27Tool under usr/local/lib/m27/m27, and usr/local/bin/m27, which starts one with the
# other. All on the Data volume, so a later m27 update can replace them without touching the sealed System.
L="$DATA/usr/local/lib/m27"
B="$DATA/usr/local/bin"
# the folders must be folders, not symlinks: root writes into them
for d in "$DATA/usr" "$DATA/usr/local" "$DATA/usr/local/lib" "$L" "$B"; do
    [ ! -L "$d" ] || { echo "macos27tool-command: $d is a symlink: refusing to install through it" >&2; exit 1; }
done
install -d -o root -g wheel -m 755 "$DATA/usr/local" "$DATA/usr/local/lib" "$L" "$B"
# every folder from usr down to bin is root's alone (owner checked, ACLs removed, before anything is unpacked, so no
# ACL planted earlier is inherited by the files): no one else can swap a name on the way or write what lands here
if [ "$(id -u)" = 0 ]; then
    for d in "$DATA/usr" "$DATA/usr/local" "$DATA/usr/local/lib" "$L" "$B"; do
        [ "$(stat -f %u "$d")" = 0 ] || { echo "macos27tool-command: $d is not root's: refusing to install there" >&2; exit 1; }
        chmod -N "$d" || { echo "macos27tool-command: the ACLs of $d cannot be removed" >&2; exit 1; }
        chmod go-w "$d"
    done
fi
# m27's log folder: m27 and its launchd jobs log here (m27.aot.plist's aot-auto.log; each user's processes under
# user/<uid>). Made root:wheel 755 here, because launchd creates a missing log folder 744 for the first job that names
# it, and 744 keeps every user's process from reaching its own m27 log.
V="$DATA/private/var/log/m27"
[ ! -L "$V" ] || { echo "macos27tool-command: $V is a symlink: refusing to install through it" >&2; exit 1; }
install -d -o root -g wheel -m 755 "$V"
chmod 755 "$V"
rm -rf "$L/python" "$L/m27"
tar -xzf "$P/inputs/python_x86_64" -C "$L"
[ -x "$L/python/bin/python3.12" ] || { echo "macos27tool-command: the CPython archive has no python/bin/python3.12" >&2; exit 1; }
(cd "$P/build" && tar -cf - m27) | (cd "$L" && tar -xf -)
[ -f "$L/m27/tools/macOS27Tool/macOS27Tool" ] || { echo "macos27tool-command: the staged tools/macOS27Tool is missing" >&2; exit 1; }
# the launcher: a new file renamed over the old name; a link at the name is removed first, a folder refused
if [ -d "$B/m27" ] && [ ! -L "$B/m27" ]; then
    echo "macos27tool-command: $B/m27 is a folder: refusing to replace it" >&2; exit 1
fi
[ ! -L "$B/m27" ] || rm -f "$B/m27"        # a link (to a file or a folder) is removed, never followed
# the new launcher is made by mktemp (exclusive) in the root-only bin, filled, and renamed over the name
N=$(mktemp "$B/.m27.new.XXXXXX")
printf '#!/bin/sh\nexec /usr/local/lib/m27/python/bin/python3.12 -I /usr/local/lib/m27/m27/tools/macOS27Tool/macOS27Tool "$@"\n' > "$N"
chown root:wheel "$N"
chmod 755 "$N"
mv -f "$N" "$B/m27"
chown -R root:wheel "$L"
chmod -R go-w "$L"
[ "$(id -u)" != 0 ] || chmod -R -N "$L"      # and no ACL on anything installed
echo "macos27tool-command: /usr/local/bin/m27, with CPython and tools/macOS27Tool under /usr/local/lib/m27"
