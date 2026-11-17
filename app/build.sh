#!/bin/sh
# builds Cosmo.app next to this script
set -e
cd "$(dirname "$0")"
swift build -c release
APP=Cosmo.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp icon/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cp .build/release/Cosmo "$APP/Contents/MacOS/Cosmo"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Cosmo</string>
  <key>CFBundleDisplayName</key><string>Cosmo</string>
  <key>CFBundleIdentifier</key><string>com.notsg.cosmo.app</string>
  <key>CFBundleExecutable</key><string>Cosmo</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Cosmo listens for “Hey Cosmo” and your requests. Audio stays on this Mac.</string>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
PLIST
codesign --force --sign - "$APP"
echo "built $(pwd)/$APP"
if [ "$1" = "--install" ]; then
  rm -rf /Applications/Cosmo.app
  ditto "$APP" /Applications/Cosmo.app
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f /Applications/Cosmo.app
  echo "installed /Applications/Cosmo.app"
fi
