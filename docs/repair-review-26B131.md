# Userspace repair review: 26B131

This review separates observed binary compatibility from hardware and application
acceptance. Matching code patterns establish that a recipe applies; they do not
by themselves prove that a repaired path is exercised on the MacBookPro9,2.

| Repair | Current evidence | Decision |
|---|---|---|
| EndpointSecurity selectors | All 31 selector operands retain the old client ABI; the selected Tahoe provider retains its different ABI. | Keep the remapping. |
| Trackpad | All six sites retain the packet/variant behavior that excludes the Tahoe report path. | Keep; verify gestures on hardware. |
| CoreRoutine console lookup | The measured caller and guard still match; the repair initializes the UID when no console user is available. | Keep pending a bounded pre-login comparison; pattern matching alone is insufficient to remove it. |
| CoreSpotlight | Native CoreSpotlight still lacks the getter used by SpotlightDaemon; the supporting ivar remains. | Keep the getter adapter. |
| Display state | Native encoding still omits the presence byte consumed by the client. | Keep. |
| WindowServer | The incompatible legacy display-vtable path remains; inversion slot/flag moved in this release. | Keep the updated layout. Desktop inversion/Siri behavior still needs hardware validation. |
| Wi-Fi | The selected OCLP wrapper still lacks Apple80211Get20MhzChannelIndexBitmap and requires the code/data export distinctions. | Keep the adapter. |
| MPSMatrix | The legacy GPU family needs the fallback initializer; the current cache matches the recipe. | Keep pending graphics acceptance. |
| Audio | The no-console branch still reaches the server-died path. | Keep; a pre-login comparison could establish whether current system behavior makes it redundant. |
| Metal libraries/runtime/framebuffer | The legacy HD 4000 compiler and driver still serve newer framework clients. | Keep. Investigate pruning selector coverage used only by debug/replay paths. |
| Code identity | Translated programs gain a native executable slice, and re-signed native programs lose Apple's signature, while TCC, BTM, airportd and authd check `anchor apple`. | Replaced (2026-11-11) by one carrier in Security.framework (golden-gate-userspace r46) with macOS27Tool's stub-identities and golden-gate-native-identities; boot success alone cannot establish correct attribution. |
| Hello shaders | Only needed when retaining the Hello animation on the legacy GPU. | Optional feature. Blur-cache and Continue-button warmup are separate performance candidates. |
| hybridsearchd suppression | Historical non-Apple-silicon exit loop; current target behavior has not been compared. | Open removal candidate, requiring a bounded target run. |

No runtime repair has been removed based solely on this review. Targeted negative
controls are still needed for the candidates above.

Eight Tahoe framework roots were removed from extraction after positive evidence
that Golden Gate supplies their Intel code. The resulting selected native and
extension manifests match the prior selection. The minimal extraction also omits
the unused Haswell DriverKit cache. The archived donor payload fell from
1,942,322,740 to 1,476,002,843 bytes (about 24%). These are extraction savings,
not removal of an active compatibility repair.
