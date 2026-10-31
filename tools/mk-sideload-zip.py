#!/usr/bin/env python3
"""Build a recovery-flashable zip that writes a raw image to the userdata partition.

    mk-sideload-zip.py IMAGE OUT.zip

The image is stored (not compressed) and starts at a 1 MiB boundary of the zip, so
update-binary copies it with dd and needs no unzip on the phone. Flash it with
tools/flash-userdata-sideload.sh (OrangeFox: Advanced > ADB Sideload).
"""
import hashlib, os, sys, zipfile

MIB = 1 << 20
SCRIPT = r'''#!/sbin/sh
# $2 = recovery's ui_print fd, $3 = the zip (a FUSE file when sideloaded)
ui() { echo "ui_print $*" >&$2; echo "ui_print" >&$2; }
die() { ui "ERROR: $*"; exit 1; }
ZIP=$3
DEV=$(readlink -f /dev/block/by-name/userdata)
[ -b "$DEV" ] || die "userdata block device not found"
grep -q '^PARTNAME=userdata$' /sys/class/block/${DEV##*/}/uevent || die "$DEV is not userdata"
BYTES=$(blockdev --getsize64 $DEV)
[ "$BYTES" -ge @SIZE@ ] || die "userdata ($BYTES B) is smaller than the image"
ui "Writing @SIZE@ bytes to $DEV"
i=0
while [ $i -lt @CHUNKS@ ]; do
  dd if="$ZIP" of=$DEV bs=1048576 skip=$((@OFFSET_MIB@ + i * 256)) seek=$((i * 256)) count=256 conv=notrunc 2>/dev/null || die "write failed at chunk $i"
  i=$((i + 1))
  ui "$((i * 100 / @CHUNKS@))%"
done
sync
ui "Verifying"
SUM=$(dd if=$DEV bs=1048576 count=@MIB@ 2>/dev/null | sha256sum | cut -d' ' -f1)
[ "$SUM" = "@SHA@" ] || die "checksum mismatch: $SUM"
# A stale backup GPT in the last block gets restored over the HFS bitmap (NOTES-FAJITA 2026-10-30).
LAST=$((BYTES / 4096 - 1))
if [ "$(dd if=$DEV bs=4096 skip=$LAST count=1 2>/dev/null | head -c 8)" = "EFI PART" ]; then
  dd if=/dev/zero of=$DEV bs=4096 seek=$LAST count=1 conv=notrunc 2>/dev/null
  ui "Cleared a stale backup GPT in the last block"
fi
ui "Done: userdata verified"
exit 0
'''

img, out = sys.argv[1:3]
size = os.path.getsize(img)
assert size % MIB == 0 and size % (256 * MIB) == 0, "image size must be a multiple of 256 MiB"
h = hashlib.sha256()
with open(img, 'rb') as f:
    while b := f.read(8 * MIB):
        h.update(b)

with zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED, allowZip64=True) as z:
    # Header of the image entry: 30 + name + 20 (zip64 extra). Pad the first entry so the data starts on 1 MiB.
    name = 'userdata.img'
    hdr2 = 30 + len(name) + 20
    hdr1 = 30 + len('pad')
    pad = (-(hdr1 + hdr2)) % MIB
    z.writestr(zipfile.ZipInfo('pad'), b'\0' * pad)
    update = (SCRIPT.replace('@SIZE@', str(size)).replace('@CHUNKS@', str(size // (256 * MIB)))
              .replace('@OFFSET_MIB@', str((hdr1 + pad + hdr2) // MIB)).replace('@MIB@', str(size // MIB))
              .replace('@SHA@', h.hexdigest()))
    with z.open(zipfile.ZipInfo(name), 'w', force_zip64=True) as d, open(img, 'rb') as f:
        while b := f.read(8 * MIB):
            d.write(b)
    z.writestr(zipfile.ZipInfo('META-INF/com/google/android/update-binary'), update)
    z.writestr('META-INF/com/google/android/updater-script', '# update-binary does the work\n')

# self-check: the image bytes must sit at the offset the script uses
off = (hdr1 + pad + hdr2)
assert off % MIB == 0
with open(out, 'rb') as zf, open(img, 'rb') as f:
    zf.seek(off); f.seek(0)
    assert zf.read(4096) == f.read(4096), "image not at the expected offset"
    zf.seek(off + size - 4096); f.seek(size - 4096)
    assert zf.read(4096) == f.read(4096), "image not at the expected offset"
print(f"{out}: {os.path.getsize(out)} bytes, image at {off // MIB} MiB, sha256 {h.hexdigest()}")
