#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${MP4MUXER_CACHE_DIR:-$ROOT_DIR/.cache/mp4muxer-src}"
REPO_URL="${MP4MUXER_REPO_URL:-https://github.com/DolbyLaboratories/dlb_mp4base.git}"
REPO_REF="${MP4MUXER_REPO_REF:-8da6d4a8fc095a88349fbdac33e7e68fb3b93649}"
OUTPUT_PATH="${1:-$ROOT_DIR/.cache/mp4muxer/mp4muxer}"
SRC_DIR="$CACHE_DIR/dlb_mp4base"
BUILD_DIR="$SRC_DIR/make/mp4muxer/macos"
BUILD_OUTPUT="$BUILD_DIR/mp4muxer_release"

mkdir -p "$CACHE_DIR" "$(dirname "$OUTPUT_PATH")"

if [[ ! -d "$SRC_DIR/.git" ]]; then
  rm -rf "$SRC_DIR"
  git clone --filter=blob:none "$REPO_URL" "$SRC_DIR" >&2
fi

git -C "$SRC_DIR" fetch --all --tags --prune >&2
CURRENT_REF="$(git -C "$SRC_DIR" rev-parse HEAD)"
if [[ "$CURRENT_REF" != "$REPO_REF" ]]; then
  git -C "$SRC_DIR" checkout --force "$REPO_REF" >&2
fi

make -C "$BUILD_DIR" clean >/dev/null 2>&1 || true
make -C "$BUILD_DIR" mp4muxer_release >&2

if [[ ! -x "$BUILD_OUTPUT" ]]; then
  echo "mp4muxer build output missing: $BUILD_OUTPUT" >&2
  exit 1
fi

cp -f "$BUILD_OUTPUT" "$OUTPUT_PATH"
chmod +x "$OUTPUT_PATH"
codesign --force --sign - "$OUTPUT_PATH" >/dev/null 2>&1 || true

echo "$OUTPUT_PATH"
