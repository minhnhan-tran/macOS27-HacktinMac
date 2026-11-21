# dev-session (dev images only): the test account comes up logged in, with no lock screen, no password prompts and the
# GUI helper already granted, built into the image instead of set over SSH after each push (owner, 2026-11-18).
# Release profiles refuse dev recipes (S18).
install -d -o root -g wheel -m 755 "$DATA/usr/local" "$DATA/usr/local/libexec" "$DATA/Library/LaunchDaemons" "$DATA/private/var/db"
install -d -o root -g wheel -m 700 "$DATA/private/var/db/m27-dev"
install -o root -g wheel -m 600 "$P/inputs/dev_session_config" "$DATA/private/var/db/m27-dev/session.conf"
install -o root -g wheel -m 755 "$P/src/dev-session.zsh" "$DATA/usr/local/libexec/dev-session"
install -o root -g wheel -m 644 "$P/src/dev.m27.session.plist" "$DATA/Library/LaunchDaemons/dev.m27.session.plist"
plutil -lint "$DATA/Library/LaunchDaemons/dev.m27.session.plist"
# the dev image skips Setup Assistant: the script makes the account (an image built to test Setup leaves this recipe out)
: > "$DATA/private/var/db/.AppleSetupDone"
chown root:wheel "$DATA/private/var/db/.AppleSetupDone"; chmod 644 "$DATA/private/var/db/.AppleSetupDone"
