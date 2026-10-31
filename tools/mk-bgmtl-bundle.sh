#!/bin/bash
# Build the real Metal plugin bundle: SweetBGMTL.bundle, whose principal class is Chlorophyll's BGMTLDevice
# (a _MTLDevice subclass that opens our kernel GPU through libbgair). This is what `MetalPluginName` should
# point at once the in-process test (bgmtl-adddev) works.
#
#   tools/mk-bgmtl-bundle.sh [OUTDIR]      default: ~/ios-userland/SweetBGMTL.bundle
#
# The bundle executable depends on /usr/local/lib/libbgair.dylib and /usr/local/lib/libdrm.2.dylib by
# absolute path: dyld's @rpath expansion is refused by AMFI on this image ("security policy does not allow
# @ path expansion"), which is why those dylibs live in /usr/local/lib.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SDK=${IOS_SDK:-$HOME/theos/sdks/iPhoneOS16.5.sdk}
CHL=${CHLOROPHYLL:-$HOME/ref/Chlorophyll}
OUT=${1:-$HOME/ios-userland/SweetBGMTL.bundle}
BG=${BGAIR:-$HOME/mesa-ios-build/mesa/build-ios/src/gallium/drivers/freedreno/libbgair.dylib}
# Copies with the install names they have on the device (/usr/local/lib): linking the bundle against
# these records absolute dependencies, and libbgair itself must not ask for @rpath/libdrm either.
LIB=$HOME/ios-userland/usr-local-lib
CC="clang --target=arm64-apple-ios16.0 -isysroot $SDK -O1 -fno-objc-arc -Wall -Wno-deprecated-declarations -fuse-ld=lld -L $SDK/usr/lib -lSystem"

[ -f "$BG" ] || { echo "no libbgair.dylib at $BG (run tools/build-bgair-ios.sh)" >&2; exit 1; }

mkdir -p "$LIB"
cp -f "$BG" "$LIB/libbgair.dylib"
cp -f $HOME/ios-userland/libdrm.2.dylib "$LIB/libdrm.2.dylib"
llvm-install-name-tool -id /usr/local/lib/libbgair.dylib \
    -change @rpath/libdrm.2.dylib /usr/local/lib/libdrm.2.dylib "$LIB/libbgair.dylib"
llvm-install-name-tool -id /usr/local/lib/libdrm.2.dylib "$LIB/libdrm.2.dylib"

rm -rf "$OUT"
mkdir -p "$OUT"

cat > "$OUT/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleIdentifier</key>
	<string>com.sweet.SweetBGMTL</string>
	<key>CFBundleName</key>
	<string>SweetBGMTL</string>
	<key>CFBundleExecutable</key>
	<string>SweetBGMTL</string>
	<key>CFBundlePackageType</key>
	<string>BNDL</string>
	<key>CFBundleVersion</key>
	<string>1</string>
	<key>CFBundleShortVersionString</key>
	<string>1.0</string>
	<key>NSPrincipalClass</key>
	<string>BGMTLDevice</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>CFBundleSignature</key>
	<string>????</string>
	<key>CFBundleSupportedPlatforms</key>
	<array>
		<string>iPhoneOS</string>
	</array>
	<key>DTPlatformName</key>
	<string>iphoneos</string>
	<key>MinimumOSVersion</key>
	<string>16.0</string>
	<key>UIDeviceFamily</key>
	<array>
		<integer>1</integer>
		<integer>2</integer>
	</array>
	<key>UIRequiredDeviceCapabilities</key>
	<array>
		<string>arm64</string>
	</array>
</dict>
</plist>
PLIST

# bgmtl.m from Chlorophyll plus our fixes (tools/mesa-ios/bgmtl-*.patch), built from a copy
SRC=$(mktemp -d)
trap 'rm -rf "$SRC"' EXIT
cp "$CHL/src/bgmtl.m" "$SRC/bgmtl.m"
for p in "$HERE"/mesa-ios/bgmtl-*.patch; do patch -s -p2 -d "$SRC" < "$p"; done

$CC -I"$CHL/air" -dynamiclib \
    -install_name /System/Library/Extensions/SweetBGMTL.bundle/SweetBGMTL \
    "$SRC/bgmtl.m" \
    "$LIB/libbgair.dylib" "$LIB/libdrm.2.dylib" \
    -framework Foundation -framework Metal -framework IOSurface \
    -o "$OUT/SweetBGMTL"

chmod 0755 "$OUT/SweetBGMTL"
file "$OUT/SweetBGMTL"
echo
echo "===== dependencies (must not be @rpath) ====="
llvm-objdump --macho --dylibs-used "$OUT/SweetBGMTL" "$LIB/libbgair.dylib" 2>/dev/null
! llvm-otool -L "$OUT/SweetBGMTL" "$LIB/libbgair.dylib" | grep -q '@rpath' || { echo "an @rpath dependency is left" >&2; exit 1; }
ls -la "$OUT"
