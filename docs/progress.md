# Status and progress

What works on the development Mac (MacBookPro9,2: Ivy Bridge, Intel HD 4000), what doesn't yet, and what changed.
Back to the [README](../README.md).

Tested with macOS 27.2.1 (26B131) on the MacBookPro9,2, 2026-11-14 to 2026-11-15.
[The 26B131 repair review](repair-review-26B131.md) records earlier evidence.

**Working**
- Startup: an Apple logo only; the screen comes on about 46 s after the kernel starts (about 50 s on the first start-up); Setup
  Assistant from Language to account creation, and the automatic login after it.
- Desktop: menu bar, Dock, Finder, Liquid Glass, Control Center, login window and lock screen. Finder stays up
  through sidebar navigation, views, Quick Look and Get Info; Spotlight indexing keeps running.
- Graphics: accelerated Metal on the HD 4000, including argument buffers and ASTC textures; hardware video decoding
  (YouTube 1080p H.264, no dropped frames).
- Connectivity: Wi-Fi joins from Setup Assistant and System Settings, and remembered networks at startup; Ethernet connects; Bluetooth pairs devices; AirDrop finds nearby devices; AirPlay
  displays are discovered, and sound plays on AirPlay speakers.
- Audio and input: the built-in speakers, camera and microphone, brightness keys, keyboard backlight, keyboard and
  trackpad; an external USB keyboard and mouse.
- Power and ports: sleep and wake, including the lid; battery level and charging; USB 3 ports at USB 3 speed; the SD
  card slot.
- Safari: web pages, YouTube, WebGPU compute and rendering.
- Maps: the satellite 3D globe, street-level satellite and 3D buildings.
- Weather: conditions, hourly and 10-day forecasts; the temperature, air-quality and wind maps.
- SceneKit: 3D scenes with physically based materials, shadows and reflections; Quick Look and Preview open them.
- Photos opens and shows the library; Music, TV and Podcasts play.
- Calendar, Preview, App Store, Books, News, Stocks, Terminal and System Settings open and stay running.
- macOS 27's ARM-only programs run on the Intel CPU through hao-runtime.
- iMessage; App Store sign-in and installs, including apps built only for Apple silicon, which run on the Intel CPU
  through hao-runtime.

**In development**
- Weather's precipitation map: the rain colour.
- Spotlight: local app results. The next image lets macOS download its search data (only dyld is treated as
  an internal install, so the asset service uses Apple's public servers).
- Live Text: the analysis service runs text recognition on the HD 4000; results in Photos
  and Preview are being finished.
- Maps pin shadows: the VectorKit shader repair is built and installed.
- Performance of translated code: general code runs at about 1/2 to 1/3 of native speed;
  vector and floating-point work is profiled, with hao-runtime optimizations lined up.
- Safari WebGL and canvases: WebGPU computes and renders correctly; WebGL drawing and showing WebGL and WebGPU
  canvases on screen are being worked on.
- Freeform: opens; opening a board is being fixed in hao-runtime.
- Maps links to a coordinate (`maps://?ll=`): fixed in hao-runtime, in the next payload.
- RealityKit: needs placement heaps, which the HD 4000's Metal driver lacks.
- Software updates, external displays, the headphone jack and Thunderbolt: not tested yet.

## What's new

- **2026-11-15:** in the next image: Swift's prespecialized metadata stays on in the Intel shared cache
  (`golden-gate-userspace` r75-r78); macOS 27's model lists no longer switch off Sidecar, AirPlay to Mac and
  Continuity Camera on this Mac (r77); App Store apps are checked for missing Intel code after each install or
  update (`golden-gate-appstore-slices`).
- **2026-11-14:** in the next image: Photos' zoom to the full-size view reads the HD 4000's image planes with
  their own row pitch (`golden-gate-hd4000-planes`); Weather's precipitation map no longer aborts and Safari's
  WebGPU uniforms compile (`golden-gate-metal-argbuf` r4, r5); hao-runtime keeps Finder and Spotlight's indexer
  up and makes Control Center's first open after a restart faster. The USB installer uses only the Recovery's
  own commands, keeps the Mac awake, and can resume a failed install.
- **Weather and WebGPU in the image:** the Metal argument-buffer support now installs with the image
  (`golden-gate-metal-argbuf`).
- **Faster first login:** new accounts start from a preloaded account template (app and widget containers,
  widget descriptors, Reminders stores), so the first login doesn't build them.
- **Only dyld is treated as an internal install:** the `ic_csr_internal` boot argument is gone, so the rest of
  macOS behaves as on a customer Mac and can download its Spotlight, Siri and language assets.
- **mDNSResponder:** no longer crashes when AirDrop registers its service (Wi-Fi peer-to-peer compatibility).
- **Background services:** the Kerberos daemon (`gssd`) crash loop is fixed (hao-runtime 397b267).
- **Clean startup:** an Apple logo only, with the OpenCore RELEASE build; developer access and diagnostics are a
  separate opt-in pack.
- **`hao` command:** hardware detection, downloads, verification and image builds from one command
  ([tools/macOS27Tool](../tools/macOS27Tool)).
- **Earlier (2026-11-11 to 11-12):** native Metal argument buffers and ASTC textures on the HD 4000; SceneKit's
  shader library repaired; Wi-Fi joining from Setup and automatic joining; Setup's automatic login; Spotlight and
  the `rapportd` and Control Center crash loops fixed; preloaded shader, icon and font caches; recipes run once in
  set-file order.

In development: Control Center and widgets, Live Text, Spotlight's local results, Maps shadows,
faster translated vector and floating-point code.

## Fixes

Each fix has an installation recipe in `patches/` and its source and builder in `tools/`,
`cache/` or the patch's own `build/` folder.

| Fix | Recipe | Source |
|---|---|---|
| Intel kernel, boot and platform repairs | `darwin27-kernel`, `hao-opencore` | [tools/kernel-fixes](../tools/kernel-fixes), [tools/kernel-foundation](../tools/kernel-foundation) |
| Metal libraries and framebuffer | `golden-gate-metal-libraries`, `golden-gate-metal-framebuffer` | [tools/metal-fixes](../tools/metal-fixes), [cache/metallib](../cache/metallib) |
| First-boot hello and Welcome animation | `golden-gate-hello` | [patches/golden-gate-hello](../patches/golden-gate-hello), [cache/metallib](../cache/metallib) |
| Legacy Metal selectors | `metalold-selectors` | [patches/metalold-selectors/build](../patches/metalold-selectors/build) |
| CoreSpotlight API adapter | `golden-gate-corespotlight-compat` | [patches/golden-gate-corespotlight-compat/build](../patches/golden-gate-corespotlight-compat/build) |
| Display and WindowServer adapters | `golden-gate-windowserver-filter` | [tools/display-fixes](../tools/display-fixes), [tools/display-state-shim](../tools/display-state-shim) |
| Userspace, Wi-Fi, Wi-Fi peer-to-peer and pre-login audio adapters | `golden-gate-userspace` | [patches/golden-gate-userspace](../patches/golden-gate-userspace), [tools/audiomxd-fix](../tools/audiomxd-fix) |
| Tahoe CoreWiFi for Tahoe's airportd | native Tahoe donor + `golden-gate-userspace` (compat library) | [tools/tahoe-donors](../tools/tahoe-donors), [patches/golden-gate-userspace/src/corewifi](../patches/golden-gate-userspace/src/corewifi) |
| ARM-only programs | `hao-runtime`, `hao-opencore` | [hao-runtime](https://github.com/lythka/hao-runtime) payload |
| Apple code identity of macOS27Tool and re-signed programs | `golden-gate-userspace` (carrier in Security), `golden-gate-native-identities` | [patches/golden-gate-userspace/src/codeid](../patches/golden-gate-userspace/src/codeid), hao-runtime `libhao_codeid` |
| Boot time: no failing Aux KC build; kernel-manager helper and, before Setup, Setup's system service started early | `hao-opencore` (`auxKCAtBoot=false`), `golden-gate-launchd-warm` | [patches/golden-gate-launchd-warm](../patches/golden-gate-launchd-warm) |
| Live Text (Vision text recognition) | `metal-compiler-compat`, `golden-gate-live-text-shared`, `golden-gate-live-text-service` | [tools/live-text](../tools/live-text), [cache/metallib](../cache/metallib) |
| Metal argument buffers and ASTC textures (HD 4000) | `golden-gate-metal-argbuf` | [tools/metal-argbuf](../tools/metal-argbuf) |
| On-disk Metal libraries without the internal-build flag | `golden-gate-dyld-internal`, `hao-opencore` | [patches/golden-gate-dyld-internal](../patches/golden-gate-dyld-internal) |
| First login: account template and per-account caches | `golden-gate-user-template`, `golden-gate-user-seed` | [cache/seeds](../cache/seeds) |
| macOS 27's own Metal on the HD 4000 (adapter in place of MetalOld) | laptop test only | [tools/metal-adapter](../tools/metal-adapter) |

[patches/README.md](../patches/README.md) describes the recipe format, and
[patches/TRACKER.md](../patches/TRACKER.md) lists every recipe with its stage and
validation state.
