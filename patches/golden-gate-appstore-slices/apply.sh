# golden-gate-appstore-slices: after each App Store install or update, log every Mach-O of the app that lacks an x86_64
# slice when the app's main executable is universal (owner, 2026-11-14: "verify apps come with both slices").
# Data volume: launchd runs the guard when /Applications changes and every 10 minutes (in-place updates).
install -d -o root -g wheel -m 755 "$DATA/usr/local/libexec" "$DATA/Library/LaunchDaemons"
install -o root -g wheel -m 755 "$P/inputs/appstore_slice_guard/m27-slice-guard" "$DATA/usr/local/libexec/m27-slice-guard"
install -o root -g wheel -m 644 "$P/src/org.m27.slice-guard.plist" "$DATA/Library/LaunchDaemons/org.m27.slice-guard.plist"
shasum -a 256 "$DATA/usr/local/libexec/m27-slice-guard" "$DATA/Library/LaunchDaemons/org.m27.slice-guard.plist"
