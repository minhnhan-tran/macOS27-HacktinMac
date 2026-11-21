#!/bin/zsh
# dev-session (dev images only): the test account comes up logged in, unlocked and drivable with no one at the keyboard.
# Runs at every boot as root (launchd), idempotent. The account name and the GUI helper's path and signing identifier
# come from /private/var/db/m27-dev/session.conf (a private input of the dev build):
# 1. the account (uid 501, admin) with a random password made on this Mac, kept root-only in /private/var/db/m27-dev
#    (read over root SSH); no password is in the image or the repository;
# 2. automatic login (kcpassword + autoLoginUser) and the per-user Setup panes marked seen;
# 3. screen lock off for the account (no password after sleep or the screen saver);
# 4. Screen Recording, Accessibility, PostEvent and ListenEvent for the GUI helper at its fixed path, granted by its
#    signing identifier so a rebuilt helper keeps them.
emulate sh
set -u
M=/private/var/db/m27-dev; L=/private/var/log/dev-session.log
exec >>"$L" 2>&1
echo "$(date '+%F %T') start"
. "$M/session.conf" || { echo "no $M/session.conf"; exit 1; }
U=$ACCOUNT
umask 077; mkdir -p "$M"; chmod 700 "$M"
created=0
if ! id "$U" >/dev/null 2>&1; then
    [ -s "$M/$U.pw" ] || LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20 >"$M/$U.pw"
    sysadminctl -addUser "$U" -fullName "$U" -UID 501 -password "$(cat "$M/$U.pw")" -admin && created=1
    createhomedir -c -u "$U" >/dev/null 2>&1
fi
id "$U" >/dev/null 2>&1 || { echo "no $U account; nothing else done"; exit 1; }
# kcpassword: the password XOR the loginwindow key, padded with NULs to a multiple of 12 (always at least one)
perl -e 'my @k=(0x7D,0x89,0x52,0x23,0xD2,0xBC,0xDD,0xEA,0xA3,0xB9,0x1F); local $/; my $p=<STDIN>; my $n=length $p;
  my $l=$n+12-($n%12); $p.="\0" x ($l-$n); print join("", map { chr(ord(substr($p,$_,1)) ^ $k[$_ % 11]) } 0..$l-1)' \
  <"$M/$U.pw" >/etc/kcpassword.new && chown root:wheel /etc/kcpassword.new && chmod 600 /etc/kcpassword.new &&
  mv /etc/kcpassword.new /etc/kcpassword
defaults write /Library/Preferences/com.apple.loginwindow autoLoginUser "$U"
H=$(dscl . -read "/Users/$U" NFSHomeDirectory | sed -n 's/^NFSHomeDirectory: //p'); P="$H/Library/Preferences/com.apple.SetupAssistant.plist"
B=$(sw_vers -buildVersion); V=$(sw_vers -productVersion)
sudo -u "$U" sh -c '
P=$1; B=$2; V=$3; [ -f "$P" ] || plutil -create xml1 "$P"
for k in DidSeeCloudSetup DidSeePrivacy DidSeeSiriSetup DidSeeScreenTime DidSeeAppearanceSetup DidSeeAccessibility \
  DidSeeApplePaySetup DidSeeTouchIDSetup DidSeeActivationLock DidSeeIntelligence DidSeeSyncSetup DidSeeSyncSetup2 \
  DidSeeTermsOfAddress DidSeeLockdownMode DidSeeiCloudLoginForStorageServices DidSeeAvatarSetup DidSeeWallpaper \
  DidSeeUpdateSettings DidSeeFileVault DidSeeAnalytics DidSeeAppStore DidSeeTrueTonePrivacy DidSeeUniversalControl \
  SkipFirstLoginOptimization; do plutil -replace "$k" -bool YES "$P"; done
# the per-user Setup Assistant counts as done only with these too; while it is pending, bookdatastored quits at every
# start and launchd restarts it every 10 s (laptop, 2026-11-18)
for k in Siri Intelligence Diagnostics iCloudStorageServices AgeRangeSelection GlassTintUpsell; do
  plutil -replace "LastSeen${k}ProductVersion" -string "$V" "$P"; done
plutil -replace InitialSetupBuildVersion -string "$B" "$P"; plutil -replace InitialSetupProductVersion -string "$V" "$P"
plutil -replace InitialAccountSetupDate -date "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$P"
plutil -replace LastSeenBuddyBuildVersion -string "$B" "$P"; plutil -replace LastSeenCloudProductVersion -string "$V" "$P"
plutil -replace LastPreLoginTasksPerformedBuild -string "$B" "$P"; plutil -replace LastPreLoginTasksPerformedVersion -string "$V" "$P"
plutil -replace LastPrivacyBundleVersion -integer 9999 "$P"; touch "$HOME/.skipbuddy"
# screen lock off: no password after sleep or the screen saver, and the screen saver never starts
defaults write com.apple.screensaver askForPassword -int 0
defaults write com.apple.screensaver askForPasswordDelay -int 0
defaults -currentHost write com.apple.screensaver idleTime -int 0
' sh "$P" "$B" "$V"
sudo -u "$U" sysadminctl -screenLock off -password "$(cat "$M/$U.pw")" 2>&1 | tail -1
# TCC grants for the GUI helper at its root-owned path, by signing identifier
G=$GUI_HELPER; D="/Library/Application Support/com.apple.TCC/TCC.db"; i=0
while [ ! -s "$D" ] && [ $i -lt 60 ]; do sleep 2; i=$((i+1)); done
if [ -s "$D" ] && csreq -r="identifier \"$GUI_HELPER_ID\"" -b "$M/gui.csreq" 2>/dev/null; then
    hex=$(xxd -p "$M/gui.csreq" | tr -d '\n')
    for s in kTCCServiceScreenCapture kTCCServiceAccessibility kTCCServicePostEvent kTCCServiceListenEvent; do
        sqlite3 "$D" "INSERT OR REPLACE INTO access (service, client, client_type, auth_value, auth_reason, auth_version, csreq, flags) VALUES ('$s', '$G', 1, 2, 4, 1, X'$hex', 0);"
    done
    killall tccd 2>/dev/null || true
else
    echo "TCC.db not there after 120 s (or csreq failed): GUI helper grants not written"
fi
# a just-created account logs in now, not at the next boot: the login window re-reads autoLoginUser when it restarts
if [ $created = 1 ] && [ "$(stat -f %Su /dev/console)" = root ]; then killall loginwindow 2>/dev/null || true; fi
echo "$(date '+%F %T') done (created=$created)"
