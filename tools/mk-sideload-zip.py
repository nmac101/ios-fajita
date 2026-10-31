#!/usr/bin/env python3
"""Build a recovery-flashable zip that writes the iOS root to userdata and, optionally,
a boot image to the boot partition of the current slot.

    mk-sideload-zip.py IMAGE BOOTIMG|- OUT.zip

Flash it with tools/flash-userdata-sideload.sh (OrangeFox: Advanced > ADB Sideload).

Layout: one stored entry, `payload.bin`, at a 1 MiB boundary of the zip. It holds the
image as independent gzip streams of 256 MiB each (each padded to whole MiB) followed by
the raw boot image, so update-binary needs only dd, gzip and sha256sum (toybox) and can
write, read back and check every chunk on its own, printing a line for each.
Compression matters: Android's sideload FUSE refuses files over 262144 blocks of 64 KiB
(16 GiB), the size of the image.
"""
import gzip, hashlib, os, sys, zipfile
from concurrent.futures import ThreadPoolExecutor

MIB = 1 << 20
CHUNK = 256 * MIB

# update-binary. Runs in the recovery's shell: $2 = ui fd, $3 = the zip (FUSE file when
# sideloaded). DEVDIR/SYSBLK/SIDELOAD_TEST exist only for the host self-test.
SCRIPT = r'''#!/sbin/sh
FD=$2
ZIP=$3
T0=$(date +%s)
DEVDIR=${DEVDIR:-/dev/block/by-name}
SYSBLK=${SYSBLK:-/sys/class/block}
ui() { echo "ui_print [fajita] $*" >&$FD; }
prog() { echo "set_progress $1" >&$FD; }
el() { echo $(( $(date +%s) - T0 )); }
mmss() { printf '%dm%02ds' $(($1 / 60)) $(($1 % 60)); }
die() { ui "!!! ERROR after $(mmss $(el)): $*"; ui "!!! Stopped here. See /tmp/recovery.log."; exit 1; }
sha_of() { dd if="$1" bs=$2 skip=$3 count=$4 2>/dev/null | sha256sum | cut -d' ' -f1; }
part() {   # part NAME -> block device path, checked against the kernel's PARTNAME
	p=$(readlink -f "$DEVDIR/$1")
	[ -n "$SIDELOAD_TEST" ] || [ -b "$p" ] || die "$1: no block device ($DEVDIR/$1)"
	grep -q "^PARTNAME=$1\$" "$SYSBLK/${p##*/}/uevent" || die "$p is not partition $1"
	echo "$p"
}

ui "================ ios-fajita sideload ================"
ui "built  : @BUILT@"
ui "image  : @IMG_LABEL@"
ui "         @SIZE@ bytes = @CHUNKS@ chunks of 256 MiB, sha256 @SHA@"
ui "kernel : @BOOT_LABEL@"
ui "zip    : $ZIP (payload at @PAYLOAD_MIB@ MiB)"
ui "-- pre-flight checks"
UD=$(part userdata) || exit 1
UDB=$(blockdev --getsize64 "$UD") || die "blockdev failed on $UD"
ui "userdata  : $UD, $UDB bytes ($((UDB / 1048576)) MiB)"
[ "$UDB" -ge @SIZE@ ] || die "userdata is smaller than the image"
if [ @BOOT_SIZE@ -gt 0 ]; then
	SLOT=$(getprop ro.boot.slot_suffix)
	[ -n "$SLOT" ] || die "no ro.boot.slot_suffix: cannot tell which boot partition is live"
	BD=$(part boot$SLOT) || exit 1
	BDB=$(blockdev --getsize64 "$BD") || die "blockdev failed on $BD"
	ui "boot$SLOT    : $BD, $BDB bytes ($((BDB / 1048576)) MiB)"
	[ "$BDB" -ge @BOOT_SIZE@ ] || die "boot$SLOT is smaller than the boot image (@BOOT_SIZE@ bytes)"
	OLD=$(sha_of "$BD" 4096 0 @BOOT_BLOCKS@)
	ui "boot$SLOT now: sha256 $OLD (first @BOOT_SIZE@ bytes)"
	[ "$OLD" = "@BOOT_SHA@" ] && ui "            = the new kernel already"
fi
ui "checks passed after $(mmss $(el))"

ui "-- writing userdata: each chunk is unpacked, written, read back and compared"
DONE=0
while read N ZO GZ SO LN SUM; do
	try=1
	while :; do
		t=$(date +%s)
		dd if="$ZIP" bs=1048576 skip=$ZO count=$GZ 2>/dev/null | gzip -dc | \
			dd of="$UD" bs=1048576 seek=$SO conv=notrunc 2>/dev/null
		t2=$(date +%s)
		GOT=$(sha_of "$UD" 1048576 $SO $LN)
		t3=$(date +%s)
		[ "$GOT" = "$SUM" ] && break
		ui "    chunk $N: read back $GOT, expected $SUM (attempt $try)"
		[ $try -lt 3 ] || die "chunk $N (MiB $SO..$((SO + LN - 1))) still wrong after 3 writes"
		try=$((try + 1))
	done
	DONE=$((DONE + 1))
	E=$(el)
	ETA=$((E * (@CHUNKS@ - DONE) / DONE))
	ui "[$(printf %2d $DONE)/@CHUNKS@] MiB $(printf %5d $SO)-$(printf %5d $((SO + LN - 1)))  gz $(printf %3d $GZ) MiB  write $(($t2 - $t))s  verify $(($t3 - $t2))s  ok   elapsed $(mmss $E)  eta $(mmss $ETA)"
	P=$((DONE * 90 / @CHUNKS@))
	prog "0.$(printf %02d $P)"
done <<'TABLE'
@TABLE@
TABLE
ui "userdata: all @CHUNKS@ chunks verified (sha256 of the image @SHA@)"

# A stale backup GPT in the last block gets restored over the HFS bitmap (NOTES-FAJITA 2026-10-30).
LAST=$((UDB / 4096 - 1))
if [ "$(dd if="$UD" bs=4096 skip=$LAST count=1 2>/dev/null | head -c 8)" = "EFI PART" ]; then
	dd if=/dev/zero of="$UD" bs=4096 seek=$LAST count=1 conv=notrunc 2>/dev/null
	ui "cleared a stale backup GPT header in the last block of userdata (block $LAST)"
else
	ui "last block of userdata holds no GPT header (good)"
fi

if [ @BOOT_SIZE@ -gt 0 ]; then
	ui "-- writing the kernel to boot$SLOT ($BD)"
	prog "0.92"
	dd if="$ZIP" of="$BD" bs=4096 skip=$((@BOOT_MIB_OFF@ * 256)) count=@BOOT_BLOCKS@ conv=notrunc 2>/dev/null
	sync
	GOT=$(sha_of "$BD" 4096 0 @BOOT_BLOCKS@)
	[ "$GOT" = "@BOOT_SHA@" ] || die "boot$SLOT read back $GOT, expected @BOOT_SHA@ - reflash boot$SLOT with fastboot before rebooting"
	ui "boot$SLOT verified: sha256 $GOT"
else
	ui "-- no kernel in this zip: boot partitions untouched"
fi
sync
prog "1.00"
ui "================ done in $(mmss $(el)) ================"
ui "Reboot to start iOS. The first boot after a fresh root is the iOS data"
ui "migration (10-15 min on the Apple logo/black screen)."
exit 0
'''


def main():
    img, boot, out = sys.argv[1:4]
    boot = None if boot == '-' else boot
    size = os.path.getsize(img)
    assert size % MIB == 0, "image size must be a multiple of 1 MiB"
    nchunks = -(-size // CHUNK)

    def pack(i):
        with open(img, 'rb') as f:
            f.seek(i * CHUNK)
            raw = f.read(CHUNK)
        gz = gzip.compress(raw, compresslevel=3, mtime=0)
        return hashlib.sha256(raw).hexdigest(), raw, gz

    tmp = out + '.payload.tmp'
    table, whole = [], hashlib.sha256()
    with open(tmp, 'wb') as p, ThreadPoolExecutor(8) as ex:
        for i, (sha, raw, gz) in enumerate(ex.map(pack, range(nchunks))):
            whole.update(raw)
            gz_mib = -(-len(gz) // MIB)
            table.append((i + 1, p.tell() // MIB, gz_mib, i * CHUNK // MIB, len(raw) // MIB, sha))
            p.write(gz + b'\0' * (gz_mib * MIB - len(gz)))   # gzip -d ignores trailing zeros
            print(f"\rchunk {i + 1}/{nchunks}", end='', file=sys.stderr)
        print(file=sys.stderr)
        boot_off = p.tell() // MIB
        boot_size, boot_sha, boot_label = 0, '-', 'none (boot partitions are not touched)'
        if boot:
            b = open(boot, 'rb').read()
            assert len(b) % 4096 == 0, "boot image size must be a multiple of 4096"
            boot_size, boot_sha = len(b), hashlib.sha256(b).hexdigest()
            boot_label = f"{os.path.basename(boot)}, {boot_size} bytes, sha256 {boot_sha}"
            p.write(b + b'\0' * (-len(b) % MIB))

    name = 'payload.bin'
    hdr1 = 30 + len('pad')
    hdr2 = 30 + len(name) + 20          # local header with the zip64 extra
    pad = (-(hdr1 + hdr2)) % MIB
    off_mib = (hdr1 + pad + hdr2) // MIB
    rows = '\n'.join(f"{n} {zo + off_mib} {gz} {so} {ln} {sha}" for n, zo, gz, so, ln, sha in table)
    subst = {
        '@BUILT@': f"{os.uname().nodename} " + __import__('time').strftime('%Y-%m-%d %H:%M UTC', __import__('time').gmtime()),
        '@IMG_LABEL@': os.path.basename(img), '@SIZE@': str(size), '@CHUNKS@': str(nchunks),
        '@SHA@': whole.hexdigest(), '@BOOT_LABEL@': boot_label, '@BOOT_SIZE@': str(boot_size),
        '@BOOT_SHA@': boot_sha, '@BOOT_BLOCKS@': str(boot_size // 4096),
        '@BOOT_MIB_OFF@': str(boot_off + off_mib), '@PAYLOAD_MIB@': str(off_mib), '@TABLE@': rows,
    }
    script = SCRIPT
    for k, v in subst.items():
        script = script.replace(k, v)
    assert '@' not in script.replace('ui_print', ''), "unreplaced placeholder"

    with zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED, allowZip64=True) as z:
        z.writestr(zipfile.ZipInfo('pad'), b'\0' * pad)
        with z.open(zipfile.ZipInfo(name), 'w', force_zip64=True) as d, open(tmp, 'rb') as f:
            while b := f.read(8 * MIB):
                d.write(b)
        z.writestr(zipfile.ZipInfo('META-INF/com/google/android/update-binary'), script)
        z.writestr('META-INF/com/google/android/updater-script', '# update-binary does the work\n')

    with open(out, 'rb') as zf, open(tmp, 'rb') as f:   # self-check: payload where the script reads it
        zf.seek(off_mib * MIB)
        assert zf.read(MIB) == f.read(MIB), "payload not at the expected offset"
    os.remove(tmp)
    assert os.path.getsize(out) < 16 * 1024 * MIB, "zip over the 16 GiB sideload limit"
    print(f"{out}: {os.path.getsize(out)} bytes, {nchunks} chunks, image sha256 {whole.hexdigest()}, kernel: {boot_label}")


if __name__ == '__main__':
    main()
