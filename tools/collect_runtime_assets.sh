#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_DIR="${1:-$ROOT_DIR/macos/ReelTranscodeApp}"
RUNTIME_SRC="${2:-$ROOT_DIR/dist/ReelTranscodeCore}"
CACHE_DIR="${FFMPEG_CACHE_DIR:-$ROOT_DIR/.cache/evermeet-ffmpeg}"
FFMPEG_BIN="${FFMPEG_BIN:-}"
FFPROBE_BIN="${FFPROBE_BIN:-}"
DOVI_MUXER_BIN="${DOVI_MUXER_BIN:-}"
MP4BOX_BIN="${MP4BOX_BIN:-}"
MEDIAINFO_BIN="${MEDIAINFO_BIN:-}"
MP4MUXER_BIN="${MP4MUXER_BIN:-}"

RUNTIME_DEST="$APP_DIR/Resources/runtime"
BIN_DEST="$APP_DIR/Resources/bin"
LIB_DEST="$APP_DIR/Resources/lib"
BUNDLE_MACHO_SCRIPT="$ROOT_DIR/tools/bundle_macos_binary.py"
BUILD_MP4MUXER_SCRIPT="$ROOT_DIR/tools/build_mp4muxer.sh"
FFMPEG_DOVI_COMPAT_SCRIPT="$ROOT_DIR/tools/ffmpeg_dovi_compat.sh"

extract_evermeet_binary() {
  local tool="$1"
  local target="$2"
  local zip_path="$CACHE_DIR/${tool}.zip"
  mkdir -p "$CACHE_DIR"
  if [[ ! -x "$target" ]]; then
    curl -L --fail --silent --show-error \
      -o "$zip_path" \
      "https://evermeet.cx/ffmpeg/getrelease/${tool}/zip"
    unzip -o "$zip_path" -d "$CACHE_DIR" >/dev/null
    chmod +x "$target"
  fi
}

download_dovimuxer_binary() {
  local target="$1"
  local zip_path="$CACHE_DIR/DoViMuxer_osx-x64.zip"
  mkdir -p "$CACHE_DIR"
  if [[ ! -x "$target" ]]; then
    curl -L --fail --silent --show-error \
      -o "$zip_path" \
      "https://github.com/nilaoda/DoViMuxer/releases/download/v1.1.1/DoViMuxer_v1.1.1_osx-x64.zip"
    unzip -o "$zip_path" -d "$CACHE_DIR" >/dev/null
    chmod +x "$target"
  fi
}

copy_optional_binary() {
  local source_path="$1"
  local target_name="$2"
  if [[ -z "$source_path" ]]; then
    return 0
  fi
  if [[ ! -x "$source_path" ]]; then
    echo "optional binary not executable, skipping: $source_path" >&2
    return 0
  fi
  cp -f "$source_path" "$BIN_DEST/$target_name"
  chmod +x "$BIN_DEST/$target_name"
}

bundle_macho_binary() {
  local target_name="$1"
  local formula_name="$2"
  local bottle_relpath="$3"
  local source_path="$4"

  if [[ -n "$source_path" ]]; then
    if [[ ! -x "$source_path" ]]; then
      echo "optional binary not executable, skipping: $source_path" >&2
      return 0
    fi
    "$BUNDLE_MACHO_SCRIPT" \
      --source-binary "$source_path" \
      --target-name "$target_name" \
      --bin-dir "$BIN_DEST" \
      --lib-dir "$LIB_DEST"
    return 0
  fi

  "$BUNDLE_MACHO_SCRIPT" \
    --formula "$formula_name" \
    --binary-relpath "$bottle_relpath" \
    --target-name "$target_name" \
    --bin-dir "$BIN_DEST" \
    --lib-dir "$LIB_DEST"
}

if [[ -z "$FFMPEG_BIN" || -z "$FFPROBE_BIN" ]]; then
  extract_evermeet_binary "ffmpeg" "$CACHE_DIR/ffmpeg"
  extract_evermeet_binary "ffprobe" "$CACHE_DIR/ffprobe"
  FFMPEG_BIN="${FFMPEG_BIN:-$CACHE_DIR/ffmpeg}"
  FFPROBE_BIN="${FFPROBE_BIN:-$CACHE_DIR/ffprobe}"
fi

if [[ ! -x "$FFMPEG_BIN" ]]; then
  echo "ffmpeg binary not found or not executable: $FFMPEG_BIN" >&2
  exit 1
fi
if [[ ! -x "$FFPROBE_BIN" ]]; then
  echo "ffprobe binary not found or not executable: $FFPROBE_BIN" >&2
  exit 1
fi

mkdir -p "$RUNTIME_DEST" "$BIN_DEST" "$LIB_DEST"
rm -rf "$RUNTIME_DEST/ReelTranscodeCore"
cp -R "$RUNTIME_SRC" "$RUNTIME_DEST/ReelTranscodeCore"
rm -f "$BIN_DEST/ffmpeg" "$BIN_DEST/ffprobe" "$BIN_DEST/ffmpeg_dovi_compat" "$BIN_DEST/DoViMuxer" "$BIN_DEST/MP4Box" "$BIN_DEST/mediainfo" "$BIN_DEST/mp4muxer"
rm -rf "$LIB_DEST"
mkdir -p "$LIB_DEST"
cp -f "$FFMPEG_BIN" "$BIN_DEST/ffmpeg"
cp -f "$FFPROBE_BIN" "$BIN_DEST/ffprobe"
cp -f "$FFMPEG_DOVI_COMPAT_SCRIPT" "$BIN_DEST/ffmpeg_dovi_compat"
chmod +x "$RUNTIME_DEST/ReelTranscodeCore/ReelTranscodeCore" "$BIN_DEST/ffmpeg" "$BIN_DEST/ffprobe" "$BIN_DEST/ffmpeg_dovi_compat"

if [[ -z "$DOVI_MUXER_BIN" ]]; then
  download_dovimuxer_binary "$CACHE_DIR/DoViMuxer"
  DOVI_MUXER_BIN="$CACHE_DIR/DoViMuxer"
fi
copy_optional_binary "$DOVI_MUXER_BIN" "DoViMuxer"

bundle_macho_binary "MP4Box" "gpac" "bin/MP4Box" "${MP4BOX_BIN:-}"
bundle_macho_binary "mediainfo" "media-info" "bin/mediainfo" "${MEDIAINFO_BIN:-}"

if [[ -z "$MP4MUXER_BIN" ]]; then
  MP4MUXER_BIN="$($BUILD_MP4MUXER_SCRIPT "$ROOT_DIR/.cache/mp4muxer/mp4muxer" | tail -n 1)"
fi
bundle_macho_binary "mp4muxer" "" "" "$MP4MUXER_BIN"

find "$BIN_DEST" -type f -maxdepth 1 -exec chmod +x {} +
find "$LIB_DEST" -type f -name '*.dylib' -exec chmod +x {} +

echo "Runtime assets copied to $APP_DIR/Resources"
missing_tools=()
for tool in DoViMuxer MP4Box mediainfo mp4muxer; do
  if [[ ! -x "$BIN_DEST/$tool" ]]; then
    missing_tools+=("$tool")
  fi
done

if [[ ${#missing_tools[@]} -eq 0 ]]; then
  echo "Dolby Vision safe remux toolchain: enabled"
else
  echo "Dolby Vision safe remux toolchain: incomplete (missing: ${missing_tools[*]})"
fi
