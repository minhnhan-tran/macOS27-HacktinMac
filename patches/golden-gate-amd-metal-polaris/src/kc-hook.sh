#!/bin/sh
# golden-gate-amd-metal-polaris (kc-hook, macpro71 pack): Tahoe 25G342's Polaris Metal driver and AMD's shader
# compilers, cut from Tahoe's x86_64h dyld cache, into the Tahoe AMD bundles the extensions base layer installed
# without code. Runs after golden-gate-corespotlight-compat and before golden-gate-native-identities.
# The libraries use AVX2/BMI (a Haswell cache), so the recipe is in the macpro71 pack only. No CPU check here: the
# hook may run on a builder whose CPU is not the target's.
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
IN=$H/inputs/amd_metal_polaris/root/System/Library/Extensions
E=$S/System/Library/Extensions
for b in AMDMTLBronzeDriver.bundle AMDShared.bundle; do
    [ -f "$E/$b/Contents/Info.plist" ] || { echo "golden-gate-amd-metal-polaris: $E/$b missing (extensions layer)" >&2; exit 1; }
done
put() {   # put SOURCE DEST: root:wheel 0755, restricted, as patch.json declares (the Recovery has no install(1))
    mkdir -p "$(dirname "$2")"
    if [ -e "$2" ]; then chflags norestricted "$2"; fi
    cp "$1" "$2.new.$$"
    chown root:wheel "$2.new.$$"
    chmod 755 "$2.new.$$"
    mv -f "$2.new.$$" "$2"
    chflags restricted "$2"
}
put "$IN/AMDMTLBronzeDriver.bundle/Contents/MacOS/AMDMTLBronzeDriver" "$E/AMDMTLBronzeDriver.bundle/Contents/MacOS/AMDMTLBronzeDriver"
put "$IN/AMDShared.bundle/Contents/MacOS/AMDShared" "$E/AMDShared.bundle/Contents/MacOS/AMDShared"
for f in libSC.dylib libAMDIL902.dylib libGCNASM.dylib; do
    put "$IN/AMDShared.bundle/Contents/PlugIns/$f" "$E/AMDShared.bundle/Contents/PlugIns/$f"
done
echo "golden-gate-amd-metal-polaris: installed AMDMTLBronzeDriver, AMDShared and 3 compiler libraries"
