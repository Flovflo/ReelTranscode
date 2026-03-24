#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <app_bundle> <codesign_identity> [--timestamp]" >&2
  exit 1
fi

APP_BUNDLE="$1"
CODESIGN_IDENTITY="$2"
TIMESTAMP_FLAG="${3:-}"

if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "App bundle not found: $APP_BUNDLE" >&2
  exit 1
fi

SIGN_ARGS=(--force --options runtime --sign "$CODESIGN_IDENTITY")
if [[ "$TIMESTAMP_FLAG" == "--timestamp" ]]; then
  SIGN_ARGS+=(--timestamp)
fi

find "$APP_BUNDLE/Contents" -type f \( -path "*/bin/*" -o -name "ReelTranscodeCore" -o -name "*.dylib" \) -print0 |
  while IFS= read -r -d '' file; do
    codesign "${SIGN_ARGS[@]}" "$file"
  done

codesign --deep "${SIGN_ARGS[@]}" "$APP_BUNDLE"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"
