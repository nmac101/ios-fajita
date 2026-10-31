#!/bin/bash
# Flash the iOS root to userdata, and a kernel to boot of the current slot, through
# OrangeFox's ADB sideload (no fastboot).
#
#   tools/flash-userdata-sideload.sh IMAGE BOOTIMG|-
#     e.g. tools/flash-userdata-sideload.sh ~/ipsw-x/ios16-root-gpu.img shim/out/shim-boot-0178-gpu.img
#     ("-" leaves the boot partitions alone)
#
# Start with the phone in OrangeFox (normal mode): the script backs up the live boot
# partition to ~/ios-fajita-backups/, then asks you to start Advanced > ADB Sideload.
# The phone prints one line per 256 MiB chunk (written, read back, compared); afterwards
# this script prints the phone's log of the run.
set -euo pipefail
img=${1:?usage: flash-userdata-sideload.sh IMAGE BOOTIMG|-}
boot=${2:?usage: flash-userdata-sideload.sh IMAGE BOOTIMG|-}
here=$(cd "$(dirname "$0")" && pwd)
zip=${img%.img}-sideload.zip
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

[ -f "$img" ] || { echo "no image: $img" >&2; exit 1; }
[ "$boot" = - ] || [ -f "$boot" ] || { echo "no boot image: $boot" >&2; exit 1; }
inputs="$(realpath "$img") $(stat -c %s.%Y "$img") $([ "$boot" = - ] && echo - || echo "$(realpath "$boot") $(stat -c %s.%Y "$boot")") $(stat -c %Y "$here/mk-sideload-zip.py")"
if [ -s "$zip" ] && [ "$(cat "$zip.inputs" 2>/dev/null)" = "$inputs" ]; then
	log "reusing $zip (same image, kernel and builder)"
else
	log "building $zip (about a minute)"
	python3 -I "$here/mk-sideload-zip.py" "$img" "$boot" "$zip"
	echo "$inputs" > "$zip.inputs"
fi
log "zip: $(stat -c %s "$zip") bytes"
log "image : $img"
log "kernel: $boot"

state=$(adb get-state 2>/dev/null || true)
log "phone state: ${state:-not connected}"
if [ "$state" = recovery ] && [ "$boot" != - ]; then
	slot=$(adb shell getprop ro.boot.slot_suffix | tr -d '\r')
	mkdir -p ~/ios-fajita-backups
	bk=~/ios-fajita-backups/boot${slot}-$(date +%Y%m%d-%H%M%S).img
	log "backing up boot$slot to $bk"
	adb exec-out "dd if=/dev/block/by-name/boot$slot bs=1048576 2>/dev/null" > "$bk"
	log "backup: $(stat -c %s "$bk") bytes, sha256 $(sha256sum "$bk" | cut -c1-16)..."
	[ -s "$bk" ] || { echo "boot backup is empty; stopping" >&2; exit 1; }
elif [ "$boot" != - ]; then
	log "WARNING: phone not in normal recovery mode, so the current boot partition is NOT backed up"
fi

if [ "$state" != sideload ]; then
	log ">>> On the phone: OrangeFox > Advanced > ADB Sideload > swipe. Waiting..."
	adb wait-for-sideload
fi
log "sideloading; the phone shows one line per chunk (OrangeFox screen)"
t0=$(date +%s)
adb sideload "$zip" || log "adb sideload returned $? (it often does at the end; checking the phone's log)"
log "transfer ended after $(( $(date +%s) - t0 )) s; waiting for the recovery to come back"
timeout 120 adb wait-for-recovery || { log "recovery did not come back over adb within 2 min"; exit 1; }
log "phone's log of this run:"
adb shell 'grep -E "\[fajita\]|update-binary|Install took|E:" /tmp/recovery.log' | tr -d '\r' | \
	awk '/================ ios-fajita sideload/{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}'
adb shell 'grep -q "\[fajita\] ================ done" /tmp/recovery.log' && log "RESULT: flash verified" || { log "RESULT: NOT verified - read the log above"; exit 1; }
