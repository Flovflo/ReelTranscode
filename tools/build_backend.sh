#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYINSTALLER_BIN="${PYINSTALLER_BIN:-}"
if [[ -z "$PYINSTALLER_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/pyinstaller" ]]; then
    PYINSTALLER_BIN="$ROOT_DIR/.venv/bin/pyinstaller"
  elif command -v pyinstaller >/dev/null 2>&1; then
    PYINSTALLER_BIN="$(command -v pyinstaller)"
  else
    echo "PyInstaller not found. Install with: ./.venv/bin/pip install pyinstaller" >&2
    exit 1
  fi
fi

rm -rf "$ROOT_DIR/build" "$ROOT_DIR/dist/ReelTranscodeCore"

"$PYINSTALLER_BIN" \
  --noconfirm \
  --clean \
  --onedir \
  --name ReelTranscodeCore \
  reeltranscode/cli.py

if [[ ! -x "$ROOT_DIR/dist/ReelTranscodeCore/ReelTranscodeCore" ]]; then
  echo "Expected executable not found at dist/ReelTranscodeCore/ReelTranscodeCore" >&2
  exit 1
fi

echo "Backend build complete: $ROOT_DIR/dist/ReelTranscodeCore"
