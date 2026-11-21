# macOS27-HacktinMac

macOS27-HacktinMac builds disk images of **macOS 27 (Golden Gate) for Intel x86_64 Macs**. The current build is
macOS 27.2.1 (26B131). The target of this checkout is a 2012 13-inch MacBook Pro (MacBookPro9,2), built from an
Apple silicon Mac; [Build progress](#build-progress) tracks how far that build has got.

macOS 27 does not support Intel Macs, so the image is built in layers: Golden Gate's own System and Data, the Intel
kernel extensions, drivers and boot files macOS 26 (Tahoe) still ships, legacy hardware support from OpenCore Legacy
Patcher, a set of compatibility fixes, and [macos27tool-runtime](tools/macos27tool-runtime/), which runs Golden
Gate's ARM-only programs on the Intel CPU.

![macOS 27.2.1 on a 2012 MacBook Pro: About This Mac, Control Center and widgets](docs/images/desktop-intel.jpg)

[Build progress](#build-progress) · [Gallery](docs/gallery.md) · [Progress](docs/progress.md) ·
[Building](docs/building.md) · [Build on an M Mac](BUILD-ON-M-MAC.md) ·
[Buy me a coffee](https://buymeacoffee.com/lythka)

### Accelerated Metal on the Intel HD 4000

Maps draws its satellite view and 3D buildings with Apple's shader libraries, repaired so the HD 4000's legacy Metal
compiler accepts them ([cache/metallib](cache/metallib)). Weather's maps need argument buffers and ASTC textures,
which that legacy stack lacks; macOS27-HacktinMac adds both to the shader compiler and the driver
([tools/metal-argbuf](tools/metal-argbuf)):

<p>
<img src="docs/images/gallery/maps-3d-buildings.jpg" alt="Maps satellite view with 3D buildings on the HD 4000" width="49%">
<img src="docs/images/gallery/weather-temperature-map.jpg" alt="Weather temperature map on the HD 4000" width="49%">
</p>

More in the [gallery](docs/gallery.md): Safari and YouTube, the wind and air-quality maps, WebGPU and SceneKit.

## Status

Developer preview. Developed and tested on a 2012 13-inch MacBook Pro (MacBookPro9,2: Ivy Bridge, Intel HD 4000); other
Intel Macs are untested. Tested with macOS 27.2.1 (26B131).

| Area | What works |
|---|---|
| Startup | Apple logo only; screen on about 46 s after the kernel starts (about 50 s on the first start-up); Setup Assistant, account creation, automatic login |
| Desktop | Menu bar, Dock, Finder, Liquid Glass, Control Center, Spotlight indexing, login window, lock screen |
| Graphics | Accelerated Metal on the HD 4000, argument buffers, ASTC textures, hardware video decoding (YouTube 1080p) |
| Connectivity | Wi-Fi (joins from Setup Assistant and System Settings, remembered networks at startup), Ethernet, Bluetooth, AirDrop, AirPlay display discovery, sound on AirPlay speakers |
| Audio and input | Speakers, camera and microphone, brightness keys, keyboard backlight, built-in keyboard and trackpad; external USB keyboard and mouse |
| Power and ports | Sleep and wake (including the lid), battery level and charging, USB 3 at USB 3 speed, SD card slot |
| Apps | Safari (YouTube, WebGPU), Maps (3D globe, satellite, 3D buildings), Weather (forecasts; temperature, air-quality and wind maps), Photos, Music, TV, Podcasts, Calendar, Preview, App Store, Books, News, Stocks, Terminal, System Settings |
| 3D | SceneKit scenes with materials, shadows and reflections; Quick Look and Preview open SceneKit scenes |
| Accounts | iMessage; App Store sign-in and installs |
| ARM-only programs | macOS 27's ARM-only programs and apps built only for Apple silicon run on the Intel CPU through macos27tool-runtime |

**Releases:** none yet. The next is v0.0.1; planned versions are the
[milestones](https://github.com/minhnhan-tran/macOS27-HacktinMac/milestones).

**Installing:** being validated; see [Install](#install).

[Progress](docs/progress.md) has the detailed lists, what's new and every fix.

## Build progress

Where this checkout stands on building the installer for the MacBookPro9,2. The numbers come from
`python3.14 -I -W ignore pipeline/release-cut --dry-run pipeline/assemblies/mbp92-26B131.json`, which classes every
input row the assembly needs; rerun it to refresh this section. Last read 2026-11-20, on macOS 27.3 with Xcode 27.2.

**Rows: 64 total — 44 ready, 10 absent, 10 built but unrecorded (no `build.json`).**

| Stage | State | Detail |
|---|---|---|
| Toolchain and checks | **done** | Xcode 27.2 with its Metal toolchain, python3.14; `patches.py check` passes, `pipeline/tests` runs 600 tests with 2 known failures |
| Downloaded inputs (21 rows) | **done** | About 46 GB fetched and sha256-checked: the Apple inputs (IPSW 26B131, Tahoe 25G342 installer, KDK) plus OpenCore, PatcherSupportPkg, the `ipsw` tool, Python wheels and two CPython builds. The two CPython rows carry no `build.json` |
| Third-party source | **done** | `oclp_source` (rebuilt from the pinned OCLP commit) matches its pinned sha256; it has no `build.json`, so it counts as unrecorded |
| Made-on-Mac rows (16) | **done** | `pipeline/make-on-mac` reports 16 of 16 rows check, including the three AIR 2.9 Metal rows that needed Xcode 27 |
| Our own assets (21 `ours` rows) | **partly** | 8 reproducible, 8 built but without a `build.json` (all match their pinned sha256), 5 absent (below) |
| Builder VM base | **blocked** | `tools/ssdvm` needs the Xcode 27.2 `.xip` and a python.org 3.14 `.pkg` to make `store/vm/base` |
| Kernel rows | **blocked** | `kernel_repair_efi`, `kernel_repair_binding` and `intel_commpage_abi` come from a `BootKernelExtensions.kc` built by the bootstrap assembly inside that VM |
| Runtime payload | **blocked** | `m27_runtime` pins a payload whose 1896-unit set is not published; the runtime source itself builds from `tools/macos27tool-runtime` |
| Harvested caches (5) | **blocked** | `font_registry`, `icon_caches`, `shader_caches`, `user_template` and `wallpaper_export` are harvested on a running MacBookPro9,2, so they need a machine already running the image |
| `release.json` | **partly** | Cut with `--allow-pending`, so it says `"state": "incomplete"`; a final cut needs every row above |
| Image build | **not started** | `macOS27Tool build-image` runs `golden-run` in the VM; it stops at the missing runtime payload |
| USB installer | **not started** | `macOS27Tool create-installer`; the writer is unproven ([docs/builder-path.md](docs/builder-path.md)) |

What each blocker needs:

- [ ] **Xcode 27.2 `.xip` and python.org 3.14 `.pkg`** — unblocks the VM base, and with it the three kernel rows.
- [ ] **The runtime payload's 1896-unit set**, or a published `m27-runtime-26B131-…-public.tar.gz` — unblocks
      `m27_runtime`.
- [ ] **A MacBookPro9,2 already running the image** — unblocks the five harvested cache rows.
- [ ] **`live_text_framework`** rebuilds to bytes that differ from its pin, and the cause is not yet known.
- [ ] **A published release** — nothing is on the [Releases](https://github.com/minhnhan-tran/macOS27-HacktinMac/releases)
      page yet, so `release.json` has to be generated
      ([Getting release.json](docs/building.md#getting-releasejson)).

Rows that differ from their pin are reported, not worked around; use local pins only as
[BUILD-ON-M-MAC.md](BUILD-ON-M-MAC.md) section 6 describes, and record it.

## Risks

**Reduced security.** macOS27Tool is a developer preview with reduced security: it is not as secure as macOS on a supported Mac. System Integrity Protection is partly off, Apple Mobile File Integrity (code-signature enforcement) is relaxed, and the sealed system volume and Secure Boot are off. Pointer authentication (PAC) is not enforced for the ARM code macOS27Tool translates. Some Apple programs and programs with special permissions can still have code inserted into them, and a program's Apple signature does not show that it was not changed while running. Install it only on a Mac that holds nothing you need to protect.

Use macOS27-HacktinMac only on a **dedicated test Mac** whose contents you can lose.

- Installing an image overwrites the whole target disk.
- The image weakens macOS security on purpose: AMFI is off, SIP is lowered and authenticated root is off
  (`csr-active-config` 0x803), Secure Boot is disabled, System programs are re-signed ad hoc, and OpenCore loads
  third-party kexts. [docs/security.md](docs/security.md) lists each change; report a vulnerability privately
  ([SECURITY.md](SECURITY.md)).
- macos27tool-runtime changes System files in place and has no uninstaller; see its
  [Read first](tools/macos27tool-runtime/README.md#read-first). Its binary translation is experimental and can
  crash programs or damage their data.
- To recover: hold Option at startup and boot another disk or Internet Recovery (Command-Option-R), reset NVRAM
  (Command-Option-P-R), then erase the disk and reinstall a supported macOS or restore a backup.

## Install

Developer preview, and this path is still being validated. Installing erases the MacBook Pro's internal disk.

1. Get macOS27Tool and the release's file list: `git clone https://github.com/minhnhan-tran/macOS27-HacktinMac`,
   `cd macOS27-HacktinMac/tools/macOS27Tool`, then download `release.json` from this repository's
   [Releases](https://github.com/minhnhan-tran/macOS27-HacktinMac/releases) page. If no release is published yet,
   make it yourself: [Getting release.json](docs/building.md#getting-releasejson).
2. Run `sudo ./macOS27Tool create-installer --link-on-target`, choose the USB drive and confirm (the drive is erased).
   Keep the USB installer with you between making it and installing: anyone who can change it can change what it
   installs. macOS27Tool install checks it against its manifest first, which catches damage and casual edits.
3. On the MacBook Pro, hold Option at startup, choose **EFI Boot** (a silent pause of several minutes follows)
   and run `/Volumes/M27/install` from the Recovery's Terminal.
4. After the first start-up, run `sudo /usr/libexec/m27 --aot` once.

[Install](docs/install.md) has the requirements, logs for a bug report and building from source.

## Building

For developers: [Building an image](docs/building.md) covers the inputs, what the build does and testing in QEMU;
[architecture](docs/architecture.md) explains how the pieces fit, and [CONTRIBUTING.md](CONTRIBUTING.md) the tests
and conventions.

| Path | Contents |
|---|---|
| `pipeline/` | Image assembly, input rows, targets and patch sets |
| `patches/` | Installation recipes, one folder per fix ([catalog](docs/PATCHES.md)) |
| `tools/` | Fix builders, input preparation, image and QEMU tools |
| `cache/` | Metal library and shader generators |
| `docs/` | Status, architecture, inputs and build guide |

## Credits

Built on [OpenCore Legacy Patcher](https://github.com/dortania/OpenCore-Legacy-Patcher),
[OpenCore](https://github.com/acidanthera/OpenCorePkg),
[Lilu](https://github.com/acidanthera/Lilu),
[MacKernelSDK](https://github.com/acidanthera/MacKernelSDK),
[MetallibSupportPkg](https://github.com/dortania/MetallibSupportPkg),
[Apple's XNU sources](https://github.com/apple-oss-distributions/xnu) and
[QEMU](https://www.qemu.org/). Each tool's NOTICE.md lists the third-party source it carries.

This product includes software developed by Dortania and the OpenCore Legacy
Patcher contributors.

## License

Original code is available under the [MIT license](LICENSE). Third-party code and
Apple components keep their own terms; see [NOTICE.md](NOTICE.md).
