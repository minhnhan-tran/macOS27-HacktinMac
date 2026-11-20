# Versioning

macOS27Tool versions are `x.y.z`. While `x` is 0, macOS27Tool is in initial development and anything may change. Like OpenCore Legacy
Patcher and OpenCore, macOS27Tool started at 0.0.1 and counts up.

| Part | Changes when |
|---|---|
| `x` | updating needs a reinstall |
| `y` | a new macOS build, Mac model, feature or requirement |
| `z` | fixes only |

- **0.1.0** is the first version a user can build and install end to end, proven on hardware.
- **1.0.0** is the first version an end user installs with no developer tools.
- **Pre-release labels** are optional and rare: `-alpha.N` installs end to end on one model with known gaps, `-beta.N` has
  everything planned in, `-rc.N` has no known blocker.
- Development builds of `main` are not tagged.
- hao-patcher and macos27tool-runtime share one version. hao-assets is tagged by kit (`27.2.1-26B131.N`) and names the macOS27Tool version
  it belongs to.
- Each planned version has a [milestone](https://github.com/minhnhan-tran/macOS27-HacktinMac/milestones).
