# dev-keepawake: a dev image never sleeps, from its first boot on (before and during Setup Assistant, at the login
# window, in every session): two system LaunchDaemons on the Data volume. dev.m27.keepawake holds caffeinate -dimsu
# for good; dev.m27.power sets the power settings at every boot (pmset sleep/displaysleep/disksleep/standby/
# autopoweroff/powernap 0, womp 1, disablesleep 1) and the login window's screensaver idle time 0. Dev sets only.
install -d -o root -g wheel -m 755 "$DATA/Library/LaunchDaemons"
install -o root -g wheel -m 644 "$P/src/dev.m27.keepawake.plist" "$DATA/Library/LaunchDaemons/dev.m27.keepawake.plist"
install -o root -g wheel -m 644 "$P/src/dev.m27.power.plist" "$DATA/Library/LaunchDaemons/dev.m27.power.plist"
plutil -lint "$DATA/Library/LaunchDaemons/dev.m27.keepawake.plist" "$DATA/Library/LaunchDaemons/dev.m27.power.plist"
