# dev-capture: what every dev run collects (owner 2026-11-18, "Always capture"). com.m27's log messages keep their
# private data and their info lines persist, so a refusal or crash reason reads in full from any boot's log. The folder
# /private/var/db/m27-capture is where the metadata recorder writes. Dev sets only.
install -d -o root -g wheel -m 755 "$DATA/Library/Preferences/Logging/Subsystems"
install -o root -g wheel -m 644 "$P/src/com.m27.plist" "$DATA/Library/Preferences/Logging/Subsystems/com.m27.plist"
plutil -lint "$DATA/Library/Preferences/Logging/Subsystems/com.m27.plist"
install -d -o root -g wheel -m 755 "$DATA/private/var/db/m27-capture"
install -d -o root -g wheel -m 755 "$DATA/usr/local/libexec/m27-capture" "$DATA/Library/LaunchDaemons"
install -o root -g wheel -m 755 "$P/inputs/m27_capture_job" "$DATA/usr/local/libexec/m27-capture/m27-capture"
install -o root -g wheel -m 755 "$P/inputs/m27_capture_inventory" "$DATA/usr/local/libexec/m27-capture/inventory.sh"
install -o root -g wheel -m 644 "$P/inputs/m27_capture_plist" "$DATA/Library/LaunchDaemons/local.m27.capture.plist"
plutil -lint "$DATA/Library/LaunchDaemons/local.m27.capture.plist"
