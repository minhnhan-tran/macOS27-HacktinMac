# golden-gate-amd-metal-polaris

Gives the macpro71 PC's AMD Polaris GPU (RX 470-590, here an RX 580) a Metal driver on Golden Gate 26B131: Tahoe
25G342's `AMDMTLBronzeDriver` with `AMDShared` and AMD's shader compilers, cut out of Tahoe's x86_64h dyld cache.
Pack `macpro71` only (`requires.gpu` amd-polaris); never on an Ivy Bridge image (the code uses AVX2/BMI).

## Root cause

On the PC, native WindowServer aborts at launch in a loop (boot-0923: 25 reports, lit black screen, no pointer):
`CoreDisplay_CreateDisplayForCGXDisplayDevice` asserts `Failed to create MetalDevice` (`Display.cpp:3228`) while
`CGXDisplayDriverInitialize` sets up the first display. Metal finds no driver for the RX 580: 27's x86_64 cache
(Rosetta cryptex, 3945 images) holds no AMD or Intel GPU driver, and the extensions base layer's Tahoe AMD bundles
(`minimal-extensions` row) carry Info.plists only, because Tahoe keeps their code in its own shared cache.

## How it works

`make` cuts five images from row `tahoe_os_cache_x86_64h` with [tools/cache-dylib](../../tools/cache-dylib/README.md)
(r10: the first revision whose `AMDMTLBronzeDriver` llvm-objdump accepts), runs `check.py --cpu haswell` on each and
signs each ad hoc. Every symbol they bind from system libraries exists in 27's x86_64 cache (443 binds for the driver,
148 / 335 / 278 / 243 for the others, 0 missing); the only libraries 27 lacks are these five.

| File on System | What |
|---|---|
| `System/Library/Extensions/AMDMTLBronzeDriver.bundle/Contents/MacOS/AMDMTLBronzeDriver` | the Polaris Metal driver (the AMDRadeonX4000 accelerator's Metal plug-in) |
| `System/Library/Extensions/AMDShared.bundle/Contents/MacOS/AMDShared` | shared shader-compiler front end |
| `System/Library/Extensions/AMDShared.bundle/Contents/PlugIns/libSC.dylib` | shader compiler |
| `System/Library/Extensions/AMDShared.bundle/Contents/PlugIns/libAMDIL902.dylib` | IL compiler |
| `System/Library/Extensions/AMDShared.bundle/Contents/PlugIns/libGCNASM.dylib` | GCN assembler |

The Metal plug-in name is the kext's, not this recipe's: the PC's installed SystemKernelExtensions.kc holds
AMDRadeonX4000 with `AMDMTLBronzeDriver.bundle` among its bundles and the `AMDMTLBronzeDriver` / `BronzeMtlDevice`
names the accelerator publishes as `MetalPluginName` / `MetalPluginClassName`, so only the code was missing.

The kc-hook refuses a System without the two bundles, then installs the files root:wheel 0755,
restricted. It runs after golden-gate-corespotlight-compat and before golden-gate-native-identities.

## Build

    patches/golden-gate-amd-metal-polaris/make store/artifacts/amd-metal-polaris-25G342-r1

prints the row hash (without `build.json`); a remake from the same cache matches.

## Verify

On the PC after install: `/System/Library/Extensions/AMDMTLBronzeDriver.bundle/Contents/MacOS/AMDMTLBronzeDriver`
exists; WindowServer no longer leaves `Failed to create MetalDevice` reports; `ioreg -l | grep MetalPluginName`
shows the accelerator naming `AMDMTLBronzeDriver`.

## Validation

NEEDS VALIDATION: not booted. Symbol-level only: whether the Tahoe driver works under 27's Metal at run time, and
whether 27 maps the ad hoc signed driver into WindowServer, only a boot on the PC shows.

## Revisions
- r1 2026-11-27 Initial (product, not validated): Tahoe 25G342's AMDMTLBronzeDriver, AMDShared, libSC, libAMDIL902, libGCNASM from row amd_metal_polaris, macpro71 pack.
