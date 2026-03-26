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

SIGN_ARGS=(--force --sign "$CODESIGN_IDENTITY")
if [[ "$CODESIGN_IDENTITY" != "-" ]]; then
  SIGN_ARGS+=(--options runtime)
  if [[ "$TIMESTAMP_FLAG" == "--timestamp" ]]; then
    SIGN_ARGS+=(--timestamp)
  fi
fi

find "$APP_BUNDLE/Contents" -type f \
  \( -path "*/bin/*" -o -name "ReelTranscodeCore" -o -name "Python" -o -name "*.dylib" -o -name "*.so" \) \
  -print0 |
  while IFS= read -r -d '' file; do
    codesign "${SIGN_ARGS[@]}" "$file"
  done

while IFS= read -r bundle; do
  codesign "${SIGN_ARGS[@]}" "$bundle"
done < <(
  find "$APP_BUNDLE/Contents" -type d \
    \( -name "*.framework" -o -name "*.bundle" -o -name "*.app" -o -name "*.xpc" -o -name "*.appex" \) \
    ! -path "$APP_BUNDLE" |
    python3 -c '
import os
import sys

items = [line.rstrip("\n") for line in sys.stdin if line.rstrip("\n")]
for item in sorted(items, key=lambda value: value.count(os.sep), reverse=True):
    print(item)
'
)

codesign --deep "${SIGN_ARGS[@]}" "$APP_BUNDLE"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"
