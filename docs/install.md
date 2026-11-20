# Install

> [!WARNING]
> Developer preview, and this path is still being validated. Installing erases the MacBook Pro's internal disk.

Making the USB installer yourself is in progress: parts made on another macOS version may differ in signature; the
graphics parts need Xcode's Metal toolchain.

macOS27Tool installs macOS 27 on an Intel Mac. You make a USB installer on any Mac, then install from it on the
MacBook Pro.

## Requirements

| Part | Requirement |
|---|---|
| Target | MacBook Pro (13-inch, Mid 2012), the first supported model. Its internal disk is erased. |
| Build Mac | Any Mac, Intel or Apple silicon, on its normal macOS, with no SIP or AMFI changes. It needs Xcode with its Metal toolchain and Python 3.14 (python.org's build: run its Install Certificates once). `sudo ./macOS27Tool` runs only that Python, and only while it is root's alone; `./macOS27Tool doctor` checks this and prints the fix. |
| USB drive | 64 GB or larger. It is erased. |
| Free disk | About 26 GB on the build Mac at the peak. macOS27Tool downloads about 3.6 GB and reads about 35 GB of Apple's IPSW and installer in parts. |
| Time | On the build Mac about 25-30 minutes when Apple's servers are fast (over an hour when they are slow); the install about 1-2 hours, most of it one quiet step. |
| Network | Internet on the build Mac (the downloads come from Apple and this project's releases). |

## Install

**Reduced security.** macOS27Tool is a developer preview with reduced security: it is not as secure as macOS on a supported Mac. System Integrity Protection is partly off, Apple Mobile File Integrity (code-signature enforcement) is relaxed, and the sealed system volume and Secure Boot are off. Pointer authentication (PAC) is not enforced for the ARM code macOS27Tool translates. Some Apple programs and programs with special permissions can still have code inserted into them, and a program's Apple signature does not show that it was not changed while running. Install it only on a Mac that holds nothing you need to protect.

1. Get macOS27Tool and the release's file list:

   ```sh
   git clone https://github.com/minhnhan-tran/macOS27-HacktinMac && cd macOS27-HacktinMac/tools/macOS27Tool
   curl -LO https://github.com/lythka/hao-assets/releases/download/27.2.1-26B131.0/release.json
   ```

2. Plug in the USB drive and run:

   ```sh
   sudo ./macOS27Tool create-installer --link-on-target
   ```

   Choose the USB drive and confirm. This erases the drive. macOS27Tool then downloads what the release lists, makes the parts
   that must be made on this Mac, and writes the USB installer.

   Keep the USB installer with you between making it and installing: anyone who can change it can change what it installs. macOS27Tool install checks it against its manifest first, which catches damage and casual edits.
3. On the MacBook Pro, hold Option at startup and choose **EFI Boot**. Expect a silent pause of several minutes
   before the Apple logo. In the Recovery, open Utilities > Terminal and run `/Volumes/HAO/install`.
4. After the first start-up, run once:

   ```sh
   sudo /usr/libexec/hao --aot
   ```

   Until then, apps take longer to open the first time.

## Logs

If something doesn't work, save a report and attach it to a
[GitHub issue](https://github.com/minhnhan-tran/macOS27-HacktinMac/issues/new/choose):

```sh
./macOS27Tool report --output report.txt
```

The report leaves out the serial number, user names and Wi-Fi names.

## Build from source

To build macOS27Tool and its kit yourself, follow [the developer guide](building.md):

```sh
git clone https://github.com/minhnhan-tran/macOS27-HacktinMac && cd macOS27-HacktinMac
sudo tools/macOS27Tool/macOS27Tool create-installer --link-on-target
```
