# Building the macOS 27 installer for an Intel Mac, on an Apple silicon Mac with Xcode 27

Written from a session on an **Intel** Mac (Xcode 26.5), where 13 of 16 made-on-Mac rows and 14 of the "ours" rows
were built. This file lists what is finished, what still needs Xcode 27, and the commands to run on the M Mac.

Nothing here has been run on the M Mac. Anything marked **(unverified)** was not run to completion.

---------------------------------------------------------------------------------------------------------------

## 0. The key point

The tracked input rows in `pipeline/inputs/` pin the sha256 of every compiled asset. Those pins were made by the
maintainers with **Xcode 27 on Apple silicon**. The Intel Mac (Xcode 26.5) built the same *source* (source hashes
matched) but different *bytes*, so every row failed its pin, and I worked around that with a local pins file.

**On the M Mac with Xcode 27 you should not need the local pins at all.** Builds there should reproduce the tracked
hashes. So:

- Do **not** copy `local-pins.json` or `store/artifacts/` from the Intel Mac, and do not set `MACOS27_LOCAL_PINS`
  at first. A build that matches its tracked pin proves the toolchain is right.
- Only if a row still differs (for example the Xcode 27 point release is not the pinned one) use local pins
  (section 6), and say so in your notes.

What does carry over safely: the downloaded Apple inputs (about 46 GB), because sha256 checks them. It is usually
quicker to download again on a fast line.

## 1. What you need

| Need | Detail |
|---|---|
| Mac | Apple silicon. The maintainer image build runs in an arm64 macOS VM, which an Intel Mac cannot run. |
| macOS | Stock install. The Intel Mac used here had SIP partly off and AMFI boot-args set; avoid that on the build Mac. |
| Xcode | **27.2** (the version `macOS27Tool doctor` names for byte-exact Metal rows) with its **Metal toolchain** (Xcode > Settings > Components, or `xcodebuild -downloadComponent MetalToolchain`). Check `xcrun metal --version` and `xcrun metallib --version`; AIR 2.9 must be accepted. |
| Python | **3.14** (python.org build, then run its "Install Certificates" command). `python3.14 --version` must work. |
| Disk | 160 GiB workspace and 80 GiB image, both sparse; plus about 46 GB of downloads. |
| Network | Apple CDN (`updates.cdn-apple.com`, `swcdn.apple.com`) and GitHub. |
| Tools | Xcode command-line tools, `git`, `curl`. `qemu` for the boot check (`tools/qemu-vm`). |
| Builder VM | `tools/ssdvm` plus a VM base at `store/vm/base` made with `ssdvm install` (see `tools/ssdvm/README.md`). |
| VM helpers | `python3.14 -m venv store/cache/vmctl-venv && store/cache/vmctl-venv/bin/python3 -m pip install pillow cryptography` |

Check these first:

```sh
python3.14 --version
xcodebuild -version        # expect Xcode 27.x
xcrun metal --version
xcrun metallib --version
```

## 2. Get the source

```sh
mkdir -p ~/macos27 && cd ~/macos27
git clone https://github.com/minhnhan-tran/macOS27-HacktinMac.git
cd macOS27-HacktinMac
git log --oneline -5          # expect the lock.py local-pins commits (217c493, 235346a) on top
mkdir -p ../store && ln -s ../store store     # git-ignored store, kept outside the checkout
```

This is your own repo, not `lythka/hao-patcher`. `lythka/hao-assets` and `lythka/hao-patcher` had **no releases or
tags** when checked, and the `hao-runtime` v0.0.1 download returned 404. So there is no `release.json` to download;
everything is made or fetched from the input rows.

The CLI is now `tools/macOS27Tool/macOS27Tool` (renamed from `tools/hao/hao` in commit `743814d`). The installed
command on the target stays `hao`, with `/usr/libexec/hao` and the `HAO_*` variables.

Run the checks that were already passing:

```sh
python3.14 -I -B -m unittest discover -s pipeline/tests          # 598 tests; 7 failed before any change (below)
python3.14 -I pipeline/patches.py check
```

These 7 failed on the Intel Mac before and after my work, so my changes did not cause them:
`test_overlay_plan_replaces_those_hooks_at_their_turn`,
`test_update_preboot_mount_displacement_is_repaired_before_metadata_write`,
`test_image_target_issues_the_recorded_commands` (3 times), `test_manifest_is_current`,
`test_records_list_only_proven_or_resolved_swaps`. They may pass on the M Mac; note any that still fail.

## 3. Download the inputs (about 46 GB)

The "download" rows are listed in `pipeline/release-classes.json` (class `download`) with a URL. Each row file under
`pipeline/inputs/golden/26B131/`, `pipeline/inputs/25G342/` and `pipeline/inputs/common/` holds the sha256 and the
path it must have under `store/`.

There is no `release.json` with downloads, so `macOS27Tool fetch` has nothing to read. This script does the same job
(resumable, sha256-checked, retries dropped connections). Save it as `fetch-inputs.py` next to the checkout:

```python
#!/usr/bin/env python3.14
"""usage: fetch-inputs.py CHECKOUT [--small|--big|ROW ...]"""
import hashlib, json, os, subprocess, sys
top = os.path.abspath(sys.argv[1]); args = sys.argv[2:]
rows = json.load(open(top + '/pipeline/release-classes.json')); rows = rows.get('rows', rows)
BIG = {'ipsw_27_0', 'installer', 'kdk'}
dirs = ['pipeline/inputs/golden/26B131', 'pipeline/inputs/25G342', 'pipeline/inputs/common']
sel = []
for k, v in rows.items():
    if v.get('class') != 'download':
        continue
    for d in dirs:
        p = '%s/%s/%s' % (top, d, k)
        if os.path.isfile(p):
            line = [l.split('#')[0].split() for l in open(p) if l.split('#')[0].strip()][0]
            sel.append({'row': k, 'sha': line[0], 'path': line[1], 'url': v['url']})
            break
if args == ['--small']: sel = [s for s in sel if s['row'] not in BIG]
elif args == ['--big']: sel = [s for s in sel if s['row'] in BIG]
elif args: sel = [s for s in sel if s['row'] in args]
def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 24), b''): h.update(b)
    return h.hexdigest()
bad = 0
for s in sel:
    p = s['path'] if os.path.isabs(s['path']) else os.path.join(top, s['path'])
    if os.path.isfile(p) and sha(p) == s['sha']:
        print('ok     ', s['row']); continue
    os.makedirs(os.path.dirname(p), exist_ok=True)
    print('fetch  ', s['row'], s['url'], flush=True)
    r = subprocess.call(['curl', '-fL', '--retry', '200', '--retry-all-errors', '--retry-delay', '3',
                         '-C', '-', '-o', p, s['url']])
    if r or sha(p) != s['sha']:
        print('FAILED ', s['row'], 'curl rc=%s' % r); bad += 1
    else:
        print('ok     ', s['row'], flush=True)
sys.exit(1 if bad else 0)
```

Notes from the first run:

- Row `path` values begin with `store/` (relative to the checkout), so with the symlink above they land in `../store`.
  Check one row before relying on it: `grep -v '^#' pipeline/inputs/golden/26B131/ipsw_27_0`.
- Sizes: IPSW 27.2.1 (26B131) about 25.6 GB, Tahoe `InstallAssistant.pkg` (25G342) about 18.4 GB, the KDK, plus small
  files (OCLP and PatcherSupportPkg packages, OpenCore, `ipsw` 3.1.737, Python wheels).
- Run the big ones in the background. A plain `curl --retry 5` was reset (rc 56) twice, and the large retry count
  above fixed it.

```sh
python3.14 ../fetch-inputs.py . --small
python3.14 ../fetch-inputs.py . --big      # long; or: nohup python3.14 ../fetch-inputs.py . --big > fetch-big.log &
```

Every row must end `ok`. Any `FAILED` means a pin mismatch or a dead URL; do not continue past it.

## 4. Third-party source that must match a pinned commit

`oclp_source` is a deterministic archive rebuilt from `dortania/OpenCore-Legacy-Patcher` at commit
`e2107e05d9ce574e9ac3fd8b223e20a2edb7ba53` plus a repo-side patch. A shallow clone does not have that commit. On the
Intel Mac this rebuilt to exactly the pinned sha256, so this step is proven:

```sh
git clone https://github.com/dortania/OpenCore-Legacy-Patcher ../OpenCore-Legacy-Patcher
git -C ../OpenCore-Legacy-Patcher fetch origin e2107e05d9ce574e9ac3fd8b223e20a2edb7ba53
git -C ../OpenCore-Legacy-Patcher update-ref refs/heads/pinned-e2107e05 e2107e05d9ce574e9ac3fd8b223e20a2edb7ba53
python3.14 -I -B pipeline/unpack/build-tools oclp-source-rebuild /tmp/oclp-rebuilt \
    --upstream "$PWD/../OpenCore-Legacy-Patcher"
shasum -a 256 /tmp/oclp-rebuilt/oclp-source.tar
# must be ff509df01f179a2d4281372115d6e637a11fa76e2fb7cdb1c643b30f9fc91e08
```

Copy the output into the row's folder (the path in the `oclp_source` row; find it with
`find pipeline/inputs -name oclp_source`) together with `LICENSE.txt`, then check it with
`python3.14 -I -B pipeline/lock.py path <lock folder> oclp_source`.

`codeid_carrier` needs the exact pinned `hao-runtime` commit:

```sh
git clone https://github.com/lythka/hao-runtime ../hao-runtime
git -C ../hao-runtime fetch origin 33a0a0404d3f5c3008c79e295026ea99bee563eb
git -C ../hao-runtime update-ref refs/heads/pinned-33a0a04 33a0a0404d3f5c3008c79e295026ea99bee563eb
git -C ../hao-runtime worktree add --detach ../hr-33a0a04 33a0a0404d3f5c3008c79e295026ea99bee563eb
```

## 5. Build the "ours" assets

All of these were built on the Intel Mac (Xcode 26.5) but were **not byte-identical to the tracked pin**. Build them
again on Xcode 27 and expect a match.

`asset-build ROW OUT` builds one row; `asset-build list` names them: `display_state_shim io80211_compat
wifip2p_compat corewifi_compat windowserver_filter_shim corespotlight_compat_shim codeid_carrier store_service
appstore_slice_guard fl ripatch oclp_source hd4000_planes libhelloblur metalold_selectors metal_framebuffer
metal_argbuf metal_compiler_compat live_text_framework`.

```sh
python3.14 -I -B tools/asset-build/asset-build list
OUT=$(mktemp -d)
for r in display_state_shim io80211_compat wifip2p_compat corewifi_compat windowserver_filter_shim \
         corespotlight_compat_shim appstore_slice_guard fl ripatch hd4000_planes libhelloblur \
         metalold_selectors metal_framebuffer; do
  echo "== $r"; python3.14 -I -B tools/asset-build/asset-build $r "$OUT/$r"
done
python3.14 -I -B tools/asset-build/asset-build codeid_carrier "$OUT/codeid_carrier" --hao-runtime ../hr-33a0a04
```

`asset-build` prints `ROW is <sha>, the row says <sha>` when they differ. **A match is the success signal on the
M Mac.** A mismatch on Xcode 27 is something to report, not to paper over.

Rows I did **not** build, with the entry point I could not confirm:

- `metal_argbuf`: `tools/metal-argbuf/make` printed usage on a bare call; its README has the arguments.
- `live_text_framework` and `metal_compiler_compat`: `tools/live-text` (read its README); `asset-build` lists both.
- `store_service`: `tools/appstore-policy/prepare.py`.
- `kernel_repair_efi`, `kernel_repair_binding`, `intel_commpage_abi`: `tools/kernel-foundation` and
  `tools/kernel-fixes`; no command found yet.

Place each output at the path its row names, then verify. The helper does one row; the row path is the second
field of the row's only non-comment line:

```sh
place() {  # place LOCKDIR ROW SRC     (SRC is a file or a folder from asset-build)
  dest=$(grep -vE '^\s*(#|$)' "$1/$2" | head -1 | awk '{print $2}')
  mkdir -p "$(dirname "$dest")" && cp -R "$3" "$dest" && python3.14 -I -B pipeline/lock.py path "$1" "$2"
}
place pipeline/inputs/25G342 hd4000_planes "$OUT/hd4000_planes"      # example; find the lock folder per row
```

The lock folder differs per row (`pipeline/inputs/golden/26B131`, `.../25G342` or `.../common`); find it with
`find pipeline/inputs -name ROW`. A folder row needs its folder copied whole, a file row only the file (for example
`io80211_compat.dylib`, `wifip2p_compat.dylib`, `corewifi_compat.dylib`, `fl`). `lock.py path` exits 0 only when it
matches the tracked pin.

## 6. If a build still does not match its pin (local pins)

This is what the Intel Mac used. It is already in the repo (`pipeline/lock.py`, commits `217c493` and `235346a`):

```sh
export MACOS27_LOCAL_PINS="$PWD/../local-pins.json"        # a git-ignored JSON file outside the checkout
python3.14 -I -B pipeline/lock.py pin-local pipeline/inputs/25G342 hd4000_planes   # records what the row holds now
```

It never edits tracked rows. Unchecked (`-`) rows are refused. Payload rows (`hao_runtime*`) also record
`#manifest_sha256`. Without the variable, the tracked pins apply again.

## 7. Make the made-on-Mac rows

Run these only after sections 3 to 5. Make the release file first. It was written with `--allow-pending` because
`hao_runtime` and others were not built:

```sh
python3.14 -I -W ignore pipeline/release-cut --write ../release.json pipeline/assemblies/mbp92-26B131.json \
  --release 27.2.1-26B131.0 --channel dev --version v0.0.1-alpha.1 --stage ../assets --allow-pending
```

`--stage` needs an empty or new folder (it refused a non-empty one). Then:

```sh
python3.14 -I -W ignore pipeline/make-on-mac ../release.json --dry-run     # 16 rows planned
python3.14 -I -W ignore pipeline/make-on-mac ../release.json               # long; run in the background
```

`make-on-mac` skips any row that is already in place and checks, so you can rerun it after each fix. On the Intel Mac
it made, in order: `golden_gate_userspace`, `darwin27_kernel`, `python_site`, `oclp_hardware`, the Tahoe donors, then
the Metal rows. It needs the `oclp_source` and the built "ours" rows from section 5 in `store/` first, or it stops
at the first missing tool (it stopped at `ripatch`).

On the Intel Mac **13 of 16 were made**. The three that failed need Xcode 27:

- `hello_metallib_golden_gate`
- `metallib_repairs_golden_gate`
- `metal_quad_runtime_golden_gate`

The failure was `air version set to 2.9.0 ..., but expecting 2.8 in textFragment` from `xcrun metallib`: the 27.2.1
shaders are AIR 2.9 and Xcode 26.5's Metal toolchain is AIR 2.8. The `HAO_METAL_LLVM` route does not work either: all
three rows still call `xcrun metal -c`, which has no bundled-LLVM equivalent (`toolchain.Unavailable`). With
Xcode 27 these should run with no environment variable.

Make sure `xcode-select -p` points at Xcode 27 and that `ipsw` 3.1.737 is available (the `ipsw_tool` row, or
`brew install blacktop/tap/ipsw`), because `golden_gate_userspace` stops without it.

## 8. Still not done at all

- **`hao_runtime`, `hao_runtime_fold`, `hao_runtime_fold_catalog`.** These are payloads from the `hao-runtime` repo.
  The rows point at `hao-runtime-26B131-...-public.tar.gz` assets that do not exist upstream (the release URL is a
  404). They must be built from `hao-runtime`: it has a `tools/hao-image-payload` script (needs an unpacked System
  root) and a `make-public-archive.py` for the public archive. **(unverified)** I ran neither. Read `hao-runtime`'s
  README first; its docs say Xcode 27 and Apple silicon.
- `kernel_repair_*`, `intel_commpage_abi`, `metal_argbuf`, `live_text_framework`, `metal_compiler_compat`,
  `store_service`: entry points to confirm (section 5).
- **The kit and the USB installer writer are not built.** `docs/builder-path.md` says the Recovery-on-USB launcher
  and the kit writer (`tools/macOS27Tool/src/installer.py`, `stick.py`) are unproven or missing, and
  `macOS27Tool install` has only had host tests. Making a real USB installer is a separate step after all rows
  check. Do not run any step that erases a disk unless that is what you want.

## 9. Build and test the image

Only when every row checks:

```sh
python3.14 -I -B -m unittest discover -s pipeline/tests
python3.14 -I pipeline/patches.py check
python3.14 -I pipeline/ledger.py pipeline/sets/golden-gate-26B131.txt pipeline/targets/mbp92.json 25G342
python3.14 -I pipeline/release-cut --dry-run pipeline/assemblies/mbp92-26B131.json
tools/ssdvm/build.sh store/vm/bin
python3.14 -I tools/macOS27Tool/macOS27Tool build-image ../image --model MacBookPro9,2 \
    --assembly pipeline/assemblies/mbp92-26B131.json
```

Boot-check the image in QEMU (`tools/qemu-vm`) before writing anything to a disk. Maintainers can also use
`python3.14 -I pipeline/golden-run pipeline/assemblies/mbp92-26B131.json --repo /path/to/image-repo`.

Installing on the target (MacBookPro9,2) erases its internal disk. After the first start, run `sudo /usr/libexec/hao --aot`.

## 10. Quick reference

| What | Where |
|---|---|
| Rebuild and pin one row on a mismatching toolchain | section 6 |
| All "ours" and "download" rows and who makes them | `pipeline/release-classes.json` |
| Row files and pinned hashes | `pipeline/inputs/{golden/26B131,25G342,common}/ROW` |
| Make-on-Mac producers | `pipeline/make-on-mac` (docstring lists the options) |
| Environment check | `python3.14 -I tools/macOS27Tool/macOS27Tool doctor` |
| Security | The image turns AMFI off, lowers SIP (`csr-active-config` 0x803) and disables Secure Boot. Dedicated test Mac only. |
