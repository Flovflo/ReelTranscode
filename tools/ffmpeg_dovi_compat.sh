#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL_FFMPEG="${REAL_FFMPEG:-$SCRIPT_DIR/ffmpeg}"

if [[ ! -x "$REAL_FFMPEG" ]]; then
  echo "ffmpeg_dovi_compat: real ffmpeg not found at $REAL_FFMPEG" >&2
  exit 1
fi

translated_args=()
while (($#)); do
  case "$1" in
    -vbsf)
      shift
      if (($# == 0)); then
        echo "ffmpeg_dovi_compat: missing value after -vbsf" >&2
        exit 2
      fi
      translated_args+=("-bsf:v" "$1")
      ;;
    -absf)
      shift
      if (($# == 0)); then
        echo "ffmpeg_dovi_compat: missing value after -absf" >&2
        exit 2
      fi
      translated_args+=("-bsf:a" "$1")
      ;;
    *)
      translated_args+=("$1")
      ;;
  esac
  shift || true
done

exec "$REAL_FFMPEG" "${translated_args[@]}"
