# golden-gate-deep-sleep-off: deep sleep (standby, autopoweroff and hibernation) is off by default in v0.0.1, because a
# resume from it is unproven on this image (diag#79: a wake panic, USB 2.0 controller EHC1 waiting at power state Off).
# Ordinary sleep and wake stay on. powerd keeps standby, autopoweroff and hibernatemode only in a file named by each
# Mac's hardware UUID, so the image cannot ship the setting itself: a launchd daemon runs pmset once on the Mac (until
# it succeeds), then leaves a marker so a user who turns deep sleep back on keeps that choice.
L="$DATA/Library/LaunchDaemons/local.m27.deep-sleep-off.plist"
install -d -o root -g wheel -m 755 "$DATA/Library/LaunchDaemons"
install -o root -g wheel -m 644 "$P/src/local.m27.deep-sleep-off.plist" "$L"
plutil -lint "$L"
