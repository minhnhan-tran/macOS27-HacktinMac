# Security policy

## Reporting a vulnerability

Report privately through GitHub: **Security** tab > **Report a vulnerability** ([form](https://github.com/lythka/hao-patcher/security/advisories/new)).
Do not open a public issue for a vulnerability. We acknowledge a report within 7 days and tell you what we will do.

## Supported versions

Only the latest release. hao is a developer preview (0.y.z); fixes ship in the next release.

## Scope

In scope: the `hao` command, `macOS27Tool create-installer`, `macOS27Tool install` and the installer image; the recipes and the changes they make to Apple files beyond those listed in docs/security.md; checking of downloads and release files (sha256, `release.json`).

Out of scope:
- The protections hao turns off on purpose; [docs/security.md](https://github.com/lythka/hao-patcher/blob/main/docs/security.md) lists them.
- macOS itself: report to [Apple](https://security.apple.com).
- OpenCore, OpenCore Legacy Patcher and the kexts they ship: report to their projects.
