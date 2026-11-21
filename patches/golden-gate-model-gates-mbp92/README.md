# golden-gate-model-gates-mbp92

Takes the MacBookPro9,2 off 27.x's unsupported-model lists for Sidecar, AirPlay to Mac and Continuity Camera (pack
`mbp92`). The OS layer names no Mac (S15), so the model and its new name are platform data: `pipeline/targets/mbp92.json`
`"platform_data": {"model_gates": {"renamed": "NacBookPro9,2"}}`.

## How it works

[golden-gate-userspace](../golden-gate-userspace/README.md)'s cryptex build makes the edit, model-neutral: its
`src/model_gates.py` reads the target's model (`M27_TARGET_MODEL`) and `model_gates` (`M27_TARGET_DATA`). In each
listed image it renames that model's one entry in place, then re-signs the changed cache pages. A target without
`model_gates`, such as the generic image, gets no edit.

This recipe holds the declaration. `edits/cache-01.json`, with `when_target: model_gates`, lists the three sites with
their bytes and reasons, keyed by the Apple original of `dyld_shared_cache_x86_64.01`. Its hook does nothing: the edit
is already made by the time it runs. The two recipes' declarations on that file are a ledger chain
(`pipeline/ledger-expected.json`).

## Verify

- `python3.14 -I -B -m unittest discover -s patches/golden-gate-model-gates-mbp92/tests`. With USERSPACE_X86_CACHE (a
  26B131 x86_64 cache folder), the declared sites equal what userspace's planner makes for this target.
- On the Mac: Sidecar, AirPlay to Mac and Continuity Camera offer this Mac, as before the split. A 9,2 image built
  after the split is byte-identical to one built before (builder's image compare).

## Validation

NEEDS VALIDATION: split from golden-gate-userspace r87 (the same edit, now declared here and driven by target data).
Needs builder's 9,2 image compare: byte-identical apart from the declared differences.

## Revisions
- r1 2026-11-17 New (GEN1): golden-gate-userspace's model-gates edit, declared for pack mbp92; the 9,2 and its new name moved to the mbp92 target's platform_data.
