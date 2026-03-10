#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYINSTALLER_WORKDIR="$ROOT_DIR/build/pyinstaller/work"
PYINSTALLER_SPECDIR="$ROOT_DIR/build/pyinstaller/spec"
PYINSTALLER_DISTDIR="$ROOT_DIR/dist"

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

rm -rf "$PYINSTALLER_WORKDIR" "$PYINSTALLER_SPECDIR" "$PYINSTALLER_DISTDIR/ReelTranscodeCore"

"$PYINSTALLER_BIN" \
  --noconfirm \
  --clean \
  --onedir \
  --copy-metadata pgsrip \
  --copy-metadata cleanit \
  --copy-metadata babelfish \
  --copy-metadata trakit \
  --collect-submodules babelfish \
  --collect-submodules cleanit \
  --collect-submodules trakit \
  --collect-data babelfish \
  --collect-data cleanit \
  --collect-data trakit \
  --workpath "$PYINSTALLER_WORKDIR" \
  --specpath "$PYINSTALLER_SPECDIR" \
  --distpath "$PYINSTALLER_DISTDIR" \
  --name ReelTranscodeCore \
  reeltranscode/cli.py

if [[ ! -x "$ROOT_DIR/dist/ReelTranscodeCore/ReelTranscodeCore" ]]; then
  echo "Expected executable not found at dist/ReelTranscodeCore/ReelTranscodeCore" >&2
  exit 1
fi

echo "Backend build complete: $ROOT_DIR/dist/ReelTranscodeCore"
