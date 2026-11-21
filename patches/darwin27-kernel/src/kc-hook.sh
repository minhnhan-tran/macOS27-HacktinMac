#!/bin/sh
# darwin27-kernel (kc-hook, first entry of every golden set): link the Boot and System kernel collections around the
# Darwin 27 x86_64 kernel with the image's Intel driver tree (Tahoe Extensions, KDK kexts, OCLP's hardware kexts).
# The base layer (golden-pristine.sh finalize) already installed the kernel file, because OCLP's hardware overlay
# reads its version; this recipe only checks it is the same file as inputs/darwin27_kernel.
# Tahoe kmutil is an explicit build input, independent of the target OS.
# Preserve the collection path: boot repairs are bound to the resulting bytes.
#   kc-hook.sh SYSTEM_MOUNT     env OCLP_PYTHON PBU GRP
set -eu
SYS=$1; H=$(cd "$(dirname "$0")" && pwd); K=$H/inputs/darwin27_kernel
cmp -s "$K" "$SYS/System/Library/Kernels/kernel" ||
    { echo "darwin27-kernel: System/Library/Kernels/kernel is not inputs/darwin27_kernel" >&2; exit 1; }
# Paths inside the target must differ from the live builder's paths for kmutil.
KC=System/Library/KernelCollections; B=/.darwin27-kc-build   # the build path is linked into the BootKC: changing it changes the bytes the repairs are bound to
W=
if [ -f "$H/prelinked/prelinked.json" ]; then
    # A kit for an install that cannot link here (the stick's Recovery: its kmutil links a kernel-only collection, it
    # reads no kext of the target volume): the three files a builder VM linked from the same tree (golden-run,
    # pipeline/prelinked_kc.py), each checked against prelinked.json first.
    "$OCLP_PYTHON" -I - "$H/prelinked" "$K" <<'PRELINKED'
import hashlib, json, pathlib, sys
d, kernel = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
data = json.loads((d / 'prelinked.json').read_text())
names = {'BootKernelExtensions.kc', 'BootKernelExtensions.kc.elides', 'SystemKernelExtensions.kc'}
if set(data['files']) != names:
    sys.exit('darwin27-kernel: prelinked.json lists %s, not exactly %s' % (sorted(data['files']), sorted(names)))
got = hashlib.sha256(kernel.read_bytes()).hexdigest()
if data.get('kernel_sha256') != got:
    sys.exit('darwin27-kernel: the prelinked collections were linked around kernel %s, this is %s'
             % (data.get('kernel_sha256'), got))
for name, want in sorted(data['files'].items()):
    got = hashlib.sha256((d / name).read_bytes()).hexdigest()
    if got != want:
        sys.exit('darwin27-kernel: prelinked %s is %s, the kit records %s' % (name, got, want))
PRELINKED
    rm -rf "$SYS$B"; mkdir "$SYS$B"   # a failed earlier run (--resume) may have left it
    for f in BootKernelExtensions.kc BootKernelExtensions.kc.elides SystemKernelExtensions.kc; do
        cp "$H/prelinked/$f" "$SYS$B/$f"
    done
    HOW="installed the prelinked collections (key $(plutil -extract key raw -o - "$H/prelinked/prelinked.json"))"
else
    W=$(mktemp -d)
    cp "$H/inputs/tahoe_kmutil" "$W/kmutil"; chmod 755 "$W/kmutil"
    if [ "$(uname -m)" = arm64 ] && ! arch -x86_64 /usr/bin/true 2>/dev/null; then
        # The arm64 builder VM has no Rosetta (a fresh base does not): the thin x86_64 kmutil then dies with "Bad CPU type in
        # executable". Install it from Apple (needs the guest's network; about 30 s).
        softwareupdate --install-rosetta --agree-to-license >&2 || :
        arch -x86_64 /usr/bin/true || { echo "kc-hook: no Rosetta in the builder; cannot run the x86_64 kmutil" >&2; exit 32; }
    fi
    # Every host (an Intel m27 image, Golden Gate's Recovery, the arm64 builder VM on 27.2.1): kmutil links in the
    # running system's libKernelCollectionBuilder, Golden Gate's, which refuses x86_64 ("unsupported arch: x86_64",
    # "Unexpected __HIB segment"). Load Tahoe's instead; the thin x86_64 kmutil runs under Rosetta on arm64.
    install -m 644 "$H/inputs/tahoe_kc_builder" "$W/kcbuilder.dylib"; codesign -s - -f "$W/kcbuilder.dylib"
    "$OCLP_PYTHON" -I "$H/kmutil_builder.py" "$W/kmutil" @executable_path/kcbuilder.dylib
    codesign -s - -f "$W/kmutil"
    rm -rf "$SYS$B"; mkdir "$SYS$B"
    set -- -a x86_64 --allow-missing-kdk -R "$SYS" -k "$SYS/System/Library/Kernels/kernel" -V release -B "$B/BootKernelExtensions.kc"
    "$W/kmutil" create -n boot "$@"
    "$OCLP_PYTHON" -I "$H/members.py" "$SYS$B/BootKernelExtensions.kc" > "$W/members"
    grep -vx com.apple.kernel "$W/members" > "$SYS$B/BootKernelExtensions.kc.elides" ||
        { echo "darwin27-kernel: kmutil linked a BootKC with no kext (it read none under $SYS)" >&2; exit 1; }
    "$W/kmutil" create -n sys "$@" -S "$B/SystemKernelExtensions.kc" $(sed 's/^/--elide-identifier /' "$SYS$B/BootKernelExtensions.kc.elides")
    HOW=built
fi
install -d -m 755 "$SYS/$KC"
for f in BootKernelExtensions.kc BootKernelExtensions.kc.elides SystemKernelExtensions.kc; do
    install -o root -g wheel -m 644 "$SYS$B/$f" "$SYS/$KC/$f"
done
info() { diskutil info -plist "$1" | plutil -extract "$2" raw -o - - 2>/dev/null; }
PBD=$(info "${PBU:?PBU}" DeviceIdentifier); GROUP=${GRP:?GRP}; OURS=
if [ -z "$(info "$PBD" MountPoint)" ]; then
    OURS=$(mktemp -d)
    diskutil mount -mountOptions nobrowse -mountPoint "$OURS" "$PBD" >/dev/null
fi
diskutil enableOwnership "$PBD" >/dev/null
PREBOOT=$(info "$PBD" MountPoint)/$GROUP/boot/$KC
install -d -m 755 "$PREBOOT"
for f in BootKernelExtensions.kc BootKernelExtensions.kc.elides; do
    install -o root -g wheel -m 644 "$SYS$B/$f" "$PREBOOT/$f"
done
if [ -n "$OURS" ]; then diskutil unmount "$PBD" >/dev/null; rmdir "$OURS"; fi
rm -rf "$SYS$B"; [ -z "$W" ] || rm -rf "$W"
echo "Linked the Darwin 27 kernel ($HOW): Boot $(shasum -a 256 "$SYS/$KC/BootKernelExtensions.kc" | cut -c1-16) and System collections"
