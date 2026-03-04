#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_DIR="${1:-$ROOT_DIR/macos/ReelTranscodeApp}"
RUNTIME_SRC="${2:-$ROOT_DIR/dist/ReelTranscodeCore}"
FFMPEG_BIN="${FFMPEG_BIN:-/opt/homebrew/bin/ffmpeg}"
FFPROBE_BIN="${FFPROBE_BIN:-/opt/homebrew/bin/ffprobe}"

RUNTIME_DEST="$APP_DIR/Resources/runtime"
BIN_DEST="$APP_DIR/Resources/bin"

mkdir -p "$RUNTIME_DEST" "$BIN_DEST"
rm -rf "$RUNTIME_DEST/ReelTranscodeCore"
cp -R "$RUNTIME_SRC" "$RUNTIME_DEST/ReelTranscodeCore"
rm -f "$BIN_DEST/ffmpeg" "$BIN_DEST/ffprobe"
cp -f "$FFMPEG_BIN" "$BIN_DEST/ffmpeg"
cp -f "$FFPROBE_BIN" "$BIN_DEST/ffprobe"
chmod +x "$RUNTIME_DEST/ReelTranscodeCore/ReelTranscodeCore" "$BIN_DEST/ffmpeg" "$BIN_DEST/ffprobe"

echo "Runtime assets copied to $APP_DIR/Resources"
