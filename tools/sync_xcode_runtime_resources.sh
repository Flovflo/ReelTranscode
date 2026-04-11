#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/macos/ReelTranscodeApp"
DIST_BACKEND="$ROOT_DIR/dist/ReelTranscodeCore/ReelTranscodeCore"
RESOURCE_BACKEND="$APP_DIR/Resources/runtime/ReelTranscodeCore/ReelTranscodeCore"

backend_sources_changed() {
  if [[ ! -x "$DIST_BACKEND" ]]; then
    return 0
  fi

  if find \
    "$ROOT_DIR/reeltranscode" \
    "$ROOT_DIR/tests" \
    -type f \
    \( -name '*.py' -o -name '*.yaml' -o -name '*.yml' \) \
    -newer "$DIST_BACKEND" \
    -print -quit | grep -q .; then
    return 0
  fi

  [[ "$ROOT_DIR/pyproject.toml" -nt "$DIST_BACKEND" ]] && return 0
  [[ "$ROOT_DIR/tools/build_backend.sh" -nt "$DIST_BACKEND" ]] && return 0
  return 1
}

resource_runtime_changed() {
  if [[ ! -x "$RESOURCE_BACKEND" ]]; then
    return 0
  fi

  local dist_hash resource_hash
  dist_hash="$(shasum -a 256 "$DIST_BACKEND" | awk '{print $1}')"
  resource_hash="$(shasum -a 256 "$RESOURCE_BACKEND" | awk '{print $1}')"
  [[ "$dist_hash" != "$resource_hash" ]] && return 0

  for required in \
    "$APP_DIR/Resources/bin/ffmpeg" \
    "$APP_DIR/Resources/bin/ffprobe" \
    "$APP_DIR/Resources/bin/ffmpeg_dovi_compat"; do
    [[ ! -x "$required" ]] && return 0
  done

  return 1
}

if backend_sources_changed; then
  "$ROOT_DIR/tools/build_backend.sh"
fi

if resource_runtime_changed; then
  "$ROOT_DIR/tools/collect_runtime_assets.sh" "$APP_DIR"
fi
