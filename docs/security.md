# What macOS27Tool changes in macOS security

macOS27Tool runs macOS 27 on a Mac Apple does not support, and turns off or lowers several macOS protections to do it.

**Reduced security.** macOS27Tool is a developer preview with reduced security: it is not as secure as macOS on a supported Mac. System Integrity Protection is partly off, Apple Mobile File Integrity (code-signature enforcement) is relaxed, and the sealed system volume and Secure Boot are off. Pointer authentication (PAC) is not enforced for the ARM code macOS27Tool translates. Some Apple programs and programs with special permissions can still have code inserted into them, and a program's Apple signature does not show that it was not changed while running. Install it only on a Mac that holds nothing you need to protect.

| Protection | On a macOS27Tool install | What it means |
|---|---|---|
| System Integrity Protection | Lowered (`csr-active-config` 0x803: unsigned kexts, writable system files, unauthenticated root) | Software with administrator rights can change the system and load kernel extensions |
| Authenticated root volume | Off | The system volume is not verified at startup |
| Apple Mobile File Integrity (AMFI) | Off (`amfi_get_out_of_my_way=1`) | Code signatures are not enforced |
| Task port protections | Relaxed (`ipc_control_port_options=0`) | macOS's hardening of task control ports is off |
| Secure Boot | Off | Startup does not check that the boot files are Apple's |
| System programs | Some re-signed ad hoc after macOS27Tool edits them | Their signatures are not Apple's |
| Kernel extensions | OpenCore loads third-party kexts (Lilu and its plug-ins, macOS27Tool's own) | Kernel code that Apple did not ship runs at startup |
| ARM programs | hao-runtime translates them to Intel code as they run, and keeps caches of translated code | Pointer authentication (PAC) is not enforced for translated code; a translator fault can crash a program or change its results |

What macOS27Tool keeps: every file and download is checked against its sha256 in `release.json` before it is used, and every
change to an Apple file is declared in [PATCHES.md](PATCHES.md) and can be checked on the Mac with `macOS27Tool verify`.

To report a vulnerability in macOS27Tool itself, see [SECURITY.md](../SECURITY.md).
