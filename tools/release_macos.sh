#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ROOT="$ROOT_DIR/macos/ReelTranscodeApp"
PROJECT_PATH="$APP_ROOT/ReelTranscodeApp.xcodeproj"
SCHEME="ReelTranscodeApp"
CONFIGURATION="Release"
BUILD_DIR="$ROOT_DIR/build/macos"
ARCHIVE_PATH="$BUILD_DIR/ReelTranscodeApp.xcarchive"
EXPORT_DIR="$BUILD_DIR/export"
DMG_PATH="$BUILD_DIR/ReelTranscode-macOS26-universal.dmg"
APP_BUNDLE="$EXPORT_DIR/ReelTranscodeApp.app"

: "${APPLE_TEAM_ID:?Set APPLE_TEAM_ID}"
: "${APPLE_SIGN_IDENTITY:?Set APPLE_SIGN_IDENTITY (Developer ID Application: ...)}"
: "${APPLE_ID:?Set APPLE_ID}"
: "${APPLE_APP_PASSWORD:?Set APPLE_APP_PASSWORD (app-specific)}"

cd "$ROOT_DIR"

"$ROOT_DIR/tools/build_backend.sh"
"$ROOT_DIR/tools/collect_runtime_assets.sh"

if ! command -v xcodegen >/dev/null 2>&1; then
  echo "xcodegen required" >&2
  exit 1
fi

xcodegen generate --spec "$APP_ROOT/project.yml"

rm -rf "$BUILD_DIR"
mkdir -p "$EXPORT_DIR"

xcodebuild \
  -project "$PROJECT_PATH" \
  -scheme "$SCHEME" \
  -configuration "$CONFIGURATION" \
  -archivePath "$ARCHIVE_PATH" \
  DEVELOPMENT_TEAM="$APPLE_TEAM_ID" \
  CODE_SIGN_STYLE=Manual \
  CODE_SIGN_IDENTITY="$APPLE_SIGN_IDENTITY" \
  ENABLE_HARDENED_RUNTIME=YES \
  archive

cp -R "$ARCHIVE_PATH/Products/Applications/ReelTranscodeApp.app" "$APP_BUNDLE"

# Sign embedded executables first, then app.
find "$APP_BUNDLE/Contents/Resources" -type f \( -path "*/bin/*" -o -name "ReelTranscodeCore" -o -name "*.dylib" \) -print0 | while IFS= read -r -d '' file; do
  codesign --force --options runtime --timestamp --sign "$APPLE_SIGN_IDENTITY" "$file"
done
codesign --force --deep --options runtime --timestamp --sign "$APPLE_SIGN_IDENTITY" "$APP_BUNDLE"

hdiutil create -volname "ReelTranscode" -srcfolder "$APP_BUNDLE" -ov -format UDZO "$DMG_PATH"

xcrun notarytool submit "$DMG_PATH" \
  --apple-id "$APPLE_ID" \
  --password "$APPLE_APP_PASSWORD" \
  --team-id "$APPLE_TEAM_ID" \
  --wait

xcrun stapler staple "$APP_BUNDLE"
xcrun stapler staple "$DMG_PATH"

echo "Release artifact: $DMG_PATH"
