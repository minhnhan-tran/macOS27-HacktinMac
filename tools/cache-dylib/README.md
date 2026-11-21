# cache-dylib

Turns one image of an x86_64 dyld shared cache back into a standalone dylib that dyld can load from disk. On this
Mac it supplies Tahoe libraries that exist only inside a Tahoe cache, such as CoreWiFi for
m27-patcher's `tools/tahoe-donors` (`cache_images`). Apple's extractor (`dsc_extractor`) and
`ipsw dyld extract` write debugger files dyld refuses (no fixups; see m27-patcher's tahoe-donors). The output is an Apple binary: it goes under
`store/` with an input row and is never committed.

## How it works

OCLP's cache-cut libraries (moraea's `dsce`, for example `WiFiPeerToPeerOld.dylib`) use the same method. This is an
independent implementation. Where dsce leaves a cache address behind, this tool stops instead.

- **Layout.** Every segment keeps its cache address, rounded to whole pages. Code's RIP-relative references to its
  own data therefore stay valid without being rewritten. Neighbouring images' bytes on shared pages are zeroed.
- **Pointers.** The cache's slide info (version 2, the x86_64 format) lists every pointer.
  - A pointer into the image becomes a rebase.
  - A pointer to another image becomes a bind. The tool finds that image's exported symbol (an addend of up to
    0x40 bytes is allowed) and reaches it through one of the image's own dependencies, preferring the library that
    defines it over one that re-exports it.
  - A pointer to another image's copy of a string becomes a pointer to the image's own copy.
  - Anything else is an error.
- **Uniqued GOT.** The cache builder moved every image's GOT into one shared region at the start of the data
  mapping. Candidate slots are every 32-bit RIP-relative or relative value in the image that lands on a slot of that
  region. Each candidate that resolves is bound or rebased in a private `__CACHE_GOT` segment, kept at the same
  address, with one `__got` section.
- **Objective-C.**
  - Selector references point back at the image's own `__objc_methname` strings.
  - Relative method lists lose the cache's direct selector offsets: each name points to the image's selector
    reference again. Types copied to another image are pointed back at the image's own copies.
  - Method lists lose their uniqued and sorted flags.
  - Protocol references and protocol lists point at the image's own protocol definitions, and the canonical and
    fixed-up flags are cleared.
  - A class list that the cache builder turned into a relative list of lists (the class's own list plus other
    images' categories, preattached) points at the class's own list again.
  - Class method caches are bound to `__objc_empty_cache`, without preoptimized caches.
  - The image info no longer says the image was optimized by dyld, nor that dyld attached its categories (with that
    bit left set libobjc skips the image's categories when it loads at launch; a dlopen still attaches them).
- **Linkedit.** New rebase and bind opcodes (`LC_DYLD_INFO_ONLY`; binds to weakly linked libraries are weak
  imports), an export trie rebuilt from the cache's trie, the original function starts and data-in-code, a symbol
  table of the exports, and a new UUID. `MH_DYLIB_IN_CACHE` is cleared. The result is unsigned; sign it ad hoc.
- **Header room.** The header stays where the cache put it, with only the room the image's own load commands took.
  When the new ones (plus codesign's 16 bytes) do not fit, the headers of sections nothing looks up at run time are
  dropped until they do: `__TEXT,__const`, `__TEXT,__cstring`, then `__DATA_CONST,__const`. Their bytes stay in the
  segment. The report's stats name them (`informational_section_headers_dropped`).

`check.py` verifies the result with the host's tools:
- No instruction needs a CPU newer than Ivy Bridge: AVX2, BMI1/2, FMA, MOVBE, LZCNT or TZCNT. A cache built for
  x86_64h may use them, and Ivy Bridge would fault on most of them and silently run LZCNT/TZCNT as BSR/BSF.
- Every RIP-relative operand in the disassembly lands inside the dylib.
- The Objective-C image info claims no dyld optimization (bits 0, 3, 7).
- Every operand that lands in `__CACHE_GOT` uses a slot the dylib binds or rebases (`dyld_info -fixups`).
- A target outside the dylib fails if it lies in the source cache's uniqued GOT region. Other such targets are listed
  as decoding artifacts: data inside `__text` decoded as instructions, with absurd displacements.
- The Swift metadata the runtime scans (conformance records, type descriptors, field descriptors and symbolic
  references in mangled names) resolves inside the dylib, through bound slots where the reference is indirect.

## Usage

```sh
python3.14 -I tools/cache-dylib/cache_dylib.py extract CACHE_DIR x86_64h \
  /System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A/CoreWiFi OUT.dylib \
  --install-name /System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A/CoreWiFiTahoe.dylib --report report.json
codesign -f -s - OUT.dylib
python3.14 -I tools/cache-dylib/check.py OUT.dylib report.json
```

The report lists every library the dylib binds and the symbols it binds there, so a caller can check them against
another OS build. m27-patcher's golden-gate-userspace `src/corewifi/make` checks them against 27.x.

## Limits

- Only x86_64 and x86_64h caches with slide info v2 and an Objective-C optimization header of version 1 (macOS 13),
  2 (macOS 26) or 4 (macOS 27). The tool refuses anything else.
- macOS 13 (header v1): the selector base comes from libobjc's `objc_opt_t` (`__TEXT,__objc_opt_ro`, version 16,
  int64 at byte 40); strings the builder coalesced into libobjc's sectionless `__OBJC_RO` segment are pointed back at
  the image's own copies; images there keep their own `__DATA,__got` (no uniqued GOT, so no `__CACHE_GOT` segment).
- macOS 27 (header v4): the builder empties every image's ObjC string sections into libobjc's `__OBJC_RO`. The
  strings an image uses are copied into a new read-only `__OBJC_STRS` segment right after `__TEXT` (so 32-bit relative
  offsets reach), the empty sections are dropped, and method lists flagged 0x20000000 (types as selector-base offsets)
  get image-relative types. The selector base must start with dyld's marker selector, or the tool refuses.
- Not handled: thread-local variables, `__init_offsets`, stub-and-resolver exports, and chained-fixup output.
- Not proven for every image. A bind that resolves through an addend is a heuristic and is counted in the report.
- Other cache images' categories on the extracted classes are dropped from the class's list. libobjc attaches the
  target OS's own categories at run time.

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/cache-dylib/tests
# with Tahoe 25G342's x86_64h cache (row tahoe_os_cache_x86_64h): extract, verify and load CoreWiFi under Rosetta
CACHE_DYLIB_TAHOE_CACHE=store/inputs/25G342/os-cryptex-x86_64h python3.14 -I -B -m unittest discover -s tools/cache-dylib/tests
```

## Revisions
- r9 2026-11-15 Ships in m27-patcher as `tools/cache-dylib`, so a macOS27Tool checkout runs it without a second checkout
  (`tools/tahoe-donors` and `pipeline/unpack/kc-builder` find it here unless `CACHE_DYLIB` names another copy).
  `cache_dylib.py`, `check.py` and `dsc.py` are byte for byte r8's; no code change.
- r8 2026-11-13 Null uniqued-GOT candidates, for a GPUCompiler cut-out (27.2.1 26B131 x86_64 GPUCompiler 32023).
  A candidate slot holding 0 is no longer bound: libLLVM.dylib failed with "0x7ffb400000d0 is outside the new image"
  because the null fixup wrote before the private GOT segment existed (2 such slots). check.py now lists a
  misaligned hit in `__CACHE_GOT` as a decoding artifact, not an unbound slot (libGPUCompilerImpl: `callq
  *0x29fffffe(%rip)` between `<unknown>` bytes inside `__text`). Outputs that cut before are unchanged: the null path
  only ran when it raised.
- r7 2026-11-13 Header room: informational section headers are dropped when the load commands would not fit, for
  m27-patcher's darwin27-kernel on an Intel host (Golden Gate's own kernel-collection builder refuses x86_64 there).
  Tahoe 25G342's `/usr/lib/libKernelCollectionBuilder.dylib` needed 2104 bytes of 2016; without `__TEXT,__const` and
  `__TEXT,__cstring` it cuts, check.py 0 errors, and Tahoe's kmutil links a BootKC with it on the MBP9,2 running a macOS27Tool
  image. CoreWiFi's output is unchanged (it fits as before).
- r6 2026-11-12 macOS 27 caches (ObjC header v4), from m27-patcher's patch (made for a laptop test of 27's own Metal):
  27.2.1's x86_64 Metal cuts reproducibly (f86874ac..., signed), check.py 0 errors (49545 RIP operands, 593 GOT slots),
  and loads under Rosetta. Also fixed: the linkedit string table is now 8-aligned (dyld_info refused m27-patcher's v4
  output); this changes the bytes of outputs whose indirect-symbol count is odd: Tahoe CoreWiFi 6c35426d... ->
  64e08e42..., 13.2.1 MetalOld (signed) dd1ad676... -> 46fba581.... Those r5 outputs are still reproducible at
  84f3e8c; they loaded and ran as they were.
- r5 2026-11-12 macOS 13 caches (ObjC optimization header v1), for m27-patcher's own `MetalOld` (plan, legacy Metal
  base; `docs/metal-legacy-base.md`): from the 13.2.1 (22D68) UniversalMac IPSW's x86_64 cache, Metal cuts to a
  dylib that check.py passes (0 errors, 35602 RIP operands, 33919 fixups), loads under Rosetta, and has OCLP's
  `MetalOld` instruction stream, dependencies and exports exactly (apart from OCLP's one edit). Reproducible: two
  runs give the same bytes. macOS 26 behaviour unchanged.
- r4 2026-11-11 The development copy brought to m27-patcher's r3 (it had r1); links name m27-patcher instead of relative paths.
- r3 2026-11-11 Also clear objc_image_info bit 0 (dyld categories optimized), and check.py refuses it: with it set, Tahoe CoreWiFi's categories (e.g. -[NSString redactedForWiFi]) were not attached when airportd loaded it at launch, and airportd threw an unrecognized selector at start-up on the laptop.
- r2 2026-11-11 Used by tools/tahoe-donors (`native --cache`) instead of a recipe of its own; no code change.
- r1 2026-11-11 First version: extracts Tahoe 25G342's CoreWiFi from the x86_64h cache. Verified by check.py: no
  Haswell-only instruction; 720 GOT slots used by code, all bound; 3453 Swift relative pointers resolved. Under Rosetta on 27.2 its classes
  register and secure coding round-trips.
