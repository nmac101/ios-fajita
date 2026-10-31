#!/usr/bin/env python3
"""Build a recovery-flashable zip that writes a raw image to the userdata partition.

    mk-sideload-zip.py IMAGE OUT.zip

The image goes in as a gzip stream, stored in the zip at a 1 MiB boundary, so update-binary
runs `dd | gzip -dc | dd` and needs no unzip on the phone. Compression matters: Android's
sideload FUSE refuses files over 262144 blocks of 64 KiB (16 GiB), the size of the image. Flash it with
tools/flash-userdata-sideload.sh (OrangeFox: Advanced > ADB Sideload).
"""
import gzip, hashlib, os, shutil, sys, zipfile

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
ui "Writing @SIZE@ bytes to $DEV (about 10 minutes, no progress shown)"
dd if="$ZIP" bs=1048576 skip=@OFFSET_MIB@ count=@GZMIB@ 2>/dev/null | gzip -dc 2>/dev/null | dd of=$DEV bs=1048576 conv=notrunc 2>/dev/null
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
assert size % MIB == 0, "image size must be a multiple of 1 MiB"
h = hashlib.sha256()
with open(img, 'rb') as f:
    while b := f.read(8 * MIB):
        h.update(b)

gz = out + '.gz.tmp'
with open(img, 'rb') as f, open(gz, 'wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=3, mtime=0) as g:
    shutil.copyfileobj(f, g, 8 * MIB)
gzsize = -(-os.path.getsize(gz) // MIB) * MIB   # padded with zeros to whole MiB (gzip ignores trailing zeros)
with open(gz, 'ab') as raw:
    raw.truncate(gzsize)

with zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED, allowZip64=True) as z:
    name = 'userdata.img.gz'
    hdr2 = 30 + len(name) + 20       # local header with the zip64 extra
    hdr1 = 30 + len('pad')
    pad = (-(hdr1 + hdr2)) % MIB
    z.writestr(zipfile.ZipInfo('pad'), b'\0' * pad)
    off = hdr1 + pad + hdr2
    update = (SCRIPT.replace('@SIZE@', str(size)).replace('@MIB@', str(size // MIB))
              .replace('@OFFSET_MIB@', str(off // MIB)).replace('@GZMIB@', str(gzsize // MIB))
              .replace('@SHA@', h.hexdigest()))
    with z.open(zipfile.ZipInfo(name), 'w', force_zip64=True) as d, open(gz, 'rb') as f:
        shutil.copyfileobj(f, d, 8 * MIB)
    z.writestr(zipfile.ZipInfo('META-INF/com/google/android/update-binary'), update)
    z.writestr('META-INF/com/google/android/updater-script', '# update-binary does the work\n')

# self-check: the gzip bytes sit at the offset the script uses, and the stream decompresses to the image
assert off % MIB == 0
with open(out, 'rb') as zf, open(gz, 'rb') as f:
    zf.seek(off)
    assert zf.read(4096) == f.read(4096), "gzip data not at the expected offset"
os.remove(gz)
assert os.path.getsize(out) < 16 * 1024 * MIB, "zip over the 16 GiB sideload limit"
print(f"{out}: {os.path.getsize(out)} bytes, gzip at {off // MIB} MiB ({gzsize // MIB} MiB), sha256 {h.hexdigest()}")
