#!/bin/sh
# Stage the m27 command for the post: tools/macOS27Tool (not its tests) and NOTICE.md under OUT/m27, and release.json when the
# release has one, so the installed Mac runs the same m27 as the USB installer. Repository source. In a checkout the
# tracked files; in a release archive (no .git) the files on disk, without tests and __pycache__.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOP=$(cd "$HERE/../.." && pwd)
OUT=${1:?output}
rm -rf "$OUT/m27"
mkdir -p "$OUT/m27"
if [ -e "$TOP/.git" ]; then
    # the committed bytes, not the working tree (an uncommitted change is never staged into an image)
    git -C "$TOP" archive HEAD tools/macOS27Tool NOTICE.md | (cd "$OUT/m27" && tar -xf -)
    rm -rf "$OUT/m27/tools/macOS27Tool/tests"
    VERSION=$(git -C "$TOP" describe --always --abbrev=12)
else
    (cd "$TOP" && find tools/macOS27Tool NOTICE.md -type f ! -path 'tools/macOS27Tool/tests/*' ! -path '*/__pycache__/*' ! -name '*.pyc' \
        ! -name '.DS_Store' | tar -cf - -T -) | (cd "$OUT/m27" && tar -xf -)
    VERSION=$(cat "$TOP/pipeline/SOURCE_COMMIT" 2>/dev/null || true)
    case $VERSION in ''|*Format*) VERSION=unknown ;; esac
fi
# release.json is not source (the user downloads it) and is signed: it goes with its signature, which m27 checks
# whenever it reads it; a candidate build's candidate key (release-cut --candidate) goes too
for f in release.json release.json.sig SHA256SUMS data/candidate-keys.pub; do
    [ ! -f "$TOP/tools/macOS27Tool/$f" ] || cp "$TOP/tools/macOS27Tool/$f" "$OUT/m27/tools/macOS27Tool/$f"
done
# the commit staged, for m27's version where there is no checkout and no release.json (a dev image)
printf '%s\n' "$VERSION" > "$OUT/m27/tools/macOS27Tool/VERSION"
