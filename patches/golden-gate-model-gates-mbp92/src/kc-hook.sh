#!/bin/sh
# golden-gate-model-gates-mbp92 (oclp-hook, pack mbp92): nothing to do here. golden-gate-userspace's cryptex build
# already renamed the MacBookPro9,2 in the three unsupported-model lists, because this target's platform_data has
# model_gates (src/model_gates.py there reads M27_TARGET_DATA). This recipe declares those edits (edits/cache-01.json,
# when_target model_gates), so the OS layer names no Mac.
exit 0
