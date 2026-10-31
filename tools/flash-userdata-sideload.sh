#!/bin/sh
# Flash the iOS root image to userdata through OrangeFox's ADB sideload (no fastboot).
#
#   fastboot boot OrangeFox.img     # or boot recovery any other way
#   (OrangeFox: Advanced > ADB Sideload > swipe to start)
#   tools/flash-userdata-sideload.sh [IMAGE]    (default ~/ipsw-x/ios16-root-20H392.img)
#
# Overwrites the first IMAGE-size bytes of userdata, verifies a SHA-256 of what was written
# and clears a stale backup GPT in the last block. Nothing else on the phone is touched.
set -eu
img=${1:-$HOME/ipsw-x/ios16-root-20H392.img}
zip=${img%.img}-sideload.zip
here=$(dirname "$0")
[ -s "$zip" ] && [ "$zip" -nt "$img" ] || python3 -I "$here/mk-sideload-zip.py" "$img" "$zip"
adb devices | grep -q sideload || { echo "phone is not in ADB sideload mode" >&2; exit 1; }
exec adb sideload "$zip"
