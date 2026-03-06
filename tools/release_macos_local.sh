#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ROOT="$ROOT_DIR/macos/ReelTranscodeApp"
PROJECT_PATH="$APP_ROOT/ReelTranscodeApp.xcodeproj"
SCHEME="ReelTranscodeApp"
CONFIGURATION="Release"
BUILD_DIR="$ROOT_DIR/build/macos-local"
APP_PATH="$BUILD_DIR/ReelTranscodeApp.app"
DMG_PATH="$BUILD_DIR/ReelTranscode-macOS26-local.dmg"
DERIVED_DATA_PATH="/tmp/ReelTranscodeApp-DerivedData"

cd "$ROOT_DIR"

"$ROOT_DIR/tools/build_backend.sh"
"$ROOT_DIR/tools/collect_runtime_assets.sh"

xcodegen generate --spec "$APP_ROOT/project.yml"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
rm -rf "$DERIVED_DATA_PATH"

xcodebuild \
  -project "$PROJECT_PATH" \
  -scheme "$SCHEME" \
  -configuration "$CONFIGURATION" \
  -destination 'platform=macOS' \
  -derivedDataPath "$DERIVED_DATA_PATH" \
  build

cp -R "$DERIVED_DATA_PATH/Build/Products/Release/ReelTranscodeApp.app" "$APP_PATH"

"$ROOT_DIR/tools/smoke_test_packaged_backend.sh" "$APP_PATH"

hdiutil create \
  -volname "ReelTranscode" \
  -srcfolder "$APP_PATH" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Local app bundle: $APP_PATH"
echo "Local DMG: $DMG_PATH"
