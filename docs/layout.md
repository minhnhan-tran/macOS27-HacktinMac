# Recipe layout: OS layer, platform packs, dev

Every recipe belongs to one pack, every pack to one layer, and an image's patch list is generated from one build
order and a profile. A new Mac or GPU adds a pack and its recipes; no hand-made list is edited.

## Layers

| Layer | Holds | Selected by |
|---|---|---|
| **os** | what any Intel Mac running this macOS build needs: the Darwin 27 kernel and its repairs, the macOS27Tool runtime and the `hao` command, the userspace cryptex, dyld, launchd, identities, the per-build caches and seeds (pack `base`; opt-in pack `os-extras`; the installer's Recovery: pack `recovery`) | always for that build (`base`), or by name in a profile (opt-in packs) |
| **platform** | one hardware family each, with a `kind`: GPU (`intel-hd4000`, `metal-3802`), Wi-Fi (`wifi-broadcom-modern`), audio (`audio-modern`), CPU, model (`mbp92`: OpenCore config, board, early kexts), the model's Recovery drivers (`recovery-mbp92`) | the pack's `detect` (OCLP patch sets, board id) on the target, as `macOS27Tool detect` reads it |
| **dev** | test-image-only recipes (SSH, measurement, no sleep) | by name only |

## Declarations

- `patches/NAME/patch.json` has `"pack": PACK`, exactly one. A recipe's machine scope is its pack's detection;
  `requires` ([requires.md](requires.md)) states the builds, GPU, CPU features and firmware it is valid for, and
  `patches.py plan` stops on an unmet one (it replaces `applies`).
- `packs/PACK.json` has `"layer"` (`os`, `platform`, `dev`); a platform pack also `"kind"` and `"platform"`
  (`apple-intel`). A pack lists no recipes: `pipeline/patches.py` collects them from the recipes' `pack`.
- A variant recipe has `"variant": NAME` and may have `"replaces": RECIPE` of the same pack (e.g. `macos27tool-runtime-fold`
  replaces `macos27tool-runtime` in variant `cache-fold`).
- `patches/` stays flat: the layer is declared, not encoded in the path.

## Target platform data

Data that belongs to one machine, not to the OS layer (a model name to rename in a list, a board-specific value),
goes in the target, `pipeline/targets/NAME.json`, as `"platform_data": {KEY: VALUE, ...}`: each KEY lower-case
letters, digits and `_`, each VALUE any JSON. An OS-layer recipe stays model-neutral and reads it:

- Every hook and post (`src/hook.sh`, `src/kc-hook.sh`, `apply.sh`; in the builder VM and wherever a kit is assembled,
  `macOS27Tool build-disk` and `macOS27Tool install`) gets `HAO_TARGET_MODEL` (the target's `model`) and `HAO_TARGET_DATA` (a JSON
  file holding its `platform_data`, `{}` when it has none). golden-run writes the file into the kit
  (`target-data.json`).
- An edit spec with `"when_target": KEY` applies only on a target whose `platform_data` has KEY and is skipped
  elsewhere ([tools/edit-engine](../tools/edit-engine/README.md)).

Keys in use: `model_gates` (golden-gate-userspace), `opencore_reasons` and `kext_provenance` (the reasons and
provenance of the model's own OpenCore elements, merged by macos27tool-opencore's `config_reasons.py`), `opencore_generic`
(the generic image's config may carry the generic-only elements).

`patches.py check` refuses a `platform_data` that is not an object or has a key of another form.

## Target ESP base

The target's `"efi_build"` says what the image's base ESP is built from (`pipeline/stages/build-efi.py`, with the
assembly's pinned OpenCore release; the set's post recipes then complete it). golden-run refuses a target without
one, or with another `base`, before staging anything.

- `"base": "oclp"` (the default when `base` is absent; `mbp92`): OCLP's OpenCore build for the target's `model`
  from the pinned OCLP source, with `"settings"` (OCLP build settings).
- `"base": "qemu_reference"` (`generic`): the row `qemu_reference_efi` (OpenCore tested on OVMF:
  `EFI/BOOT/BOOTx64.efi`, `EFI/OC` with its drivers, kexts and `config.plist`), copied as
  `tools/kernel-foundation/vm_esp.py` copies it, with the release's binaries. No OCLP configuration; no `settings`.

## Sets are generated

- `pipeline/order.txt` lists every image recipe once, in build order, each with the reason for its position.
- `pipeline/profiles/NAME.json` says what an image is: `target` (a `pipeline/targets/*.json`), opt-in `packs` and
  single opt-in `recipes` by name, and `variants`.
- A profile marked `"release": true` is one that ships to people. `release-cut` cuts only those, and `patches.py
  check` (so `pipeline/ci`) refuses one that holds a dev-layer pack's recipe or a recipe with status `dev` or
  `diagnostic` (`release_problems`). `release-cut` and `patches.py release-gate [PROFILE...]` also refuse a recipe
  that is `"validated": false` or has status `experiment`, unless the owner waived it: `pipeline/release-waivers.json`
  = `{"waivers": [{"profile", "recipe", "revision", "reason", "decided"}]}`, the reason saying `owner DATE: ...`
  with `decided` that date. A waiver covers only that recipe at that revision in that profile; a new revision needs a
  new waiver, and `check` refuses one that names an unknown or non-release profile, a recipe not in it, a revision
  other than the current one, or a recipe that no longer needs it.
- The set = `order.txt` filtered to the packs the target detects, the profile's named packs and recipes, and its
  variants (each replacing the recipe it names). `pipeline/patches.py set PROFILE` prints it. golden-run,
  `macOS27Tool build-image`, release-cut and `patches.py plan` take a set name and read its profile when there is one; the
  hand sets that remain (`pipeline/sets/`: the legacy runner sets, the bootstrap and 26A428 sets) are read as files.
- Each profile generates exactly its former set file (`pipeline/tests/test_layout.py`).

## Checks

`pipeline/patches.py check` refuses a recipe without a pack, a pack without a layer (a platform pack without its kind
and platform), a pack that lists recipes, an image recipe missing from `order.txt` or listed twice, a variant that
replaces a recipe of another pack, a profile that does not generate, and a target whose `platform_data` is malformed. `patches/TRACKER.md` and `docs/PATCHES.md`
are regenerated from the declarations.

## Profiles of a release

A release carries one or more profiles, each its own image: `alpha-v0.1.0` (target `mbp92`: the OS layer plus the
9,2's GPU, Wi-Fi, audio and model packs) and `generic` (target `generic`: the same OS layer plus the model pack
`generic`, software graphics). The generic target has no board id and an OCLP hardware profile whose
`patchset_order` is an explicit empty list, so detection selects no platform pack; its model pack is opt-in and named
by the profile, so it never reaches another target's image. `macOS27Tool build-image --profile NAME`, `macOS27Tool create-installer`
(`--profile generic`, or its question) and `release-cut` (one assembly per profile, `release.json` `profiles`) choose
between them; see [building](building.md#choosing-a-profile).

## What a new Mac or GPU adds

- **Another Intel Mac with a supported GPU**: `pipeline/targets/NAME.json`, its OCLP hardware profile
  (`tools/oclp-hardware/profiles/NAME.json`) and a model pack (`packs/NAME.json`, kind `model`: board id, its OpenCore
  config recipe, early kexts). The OS layer comes unchanged; GPU, Wi-Fi and audio packs come from detection. What
  an OS recipe needs to know about the model goes in the target's `platform_data`.
- **Another GPU**: a platform pack of kind `gpu` with its `detect` and its recipes, each with `"pack"` and a line in
  `order.txt`.
- **Another macOS build**: rows under `pipeline/inputs/golden/BUILD`, `applies.builds` widened on the recipes that
  carry over, and the per-build data (caches, seeds, metallib repairs) remade as rows.
