#!/usr/bin/env bash
set -euo pipefail

APP_PATH="${1:?Usage: smoke_test_packaged_backend.sh /path/to/ReelTranscodeApp.app}"
RESOURCES_DIR="$APP_PATH/Contents/Resources"
BACKEND_BIN="$RESOURCES_DIR/runtime/ReelTranscodeCore/ReelTranscodeCore"
BIN_DIR="$RESOURCES_DIR/bin"

if [[ ! -x "$BACKEND_BIN" ]]; then
  echo "Packaged backend not found or not executable: $BACKEND_BIN" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/reeltranscode-smoke.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$TMP_DIR/reports" "$TMP_DIR/tmp" "$TMP_DIR/state"
CONFIG_PATH="$TMP_DIR/reeltranscode.yaml"
STATUS_JSON="$TMP_DIR/status.json"

cat >"$CONFIG_PATH" <<EOF
watch:
  folders: []

tooling:
  ffmpeg_bin: $BIN_DIR/ffmpeg
  ffprobe_bin: $BIN_DIR/ffprobe
  dovi_muxer_bin: $BIN_DIR/DoViMuxer
  mp4box_bin: $BIN_DIR/MP4Box
  mediainfo_bin: $BIN_DIR/mediainfo
  mp4muxer_bin: $BIN_DIR/mp4muxer

paths:
  state_db: $TMP_DIR/state/reeltranscode.db
  reports_dir: $TMP_DIR/reports
  csv_summary: $TMP_DIR/reports/summary.csv
  temp_dir: $TMP_DIR/tmp
EOF

"$BACKEND_BIN" --config "$CONFIG_PATH" status --json --limit 1 >"$STATUS_JSON"

/usr/bin/python3 - "$STATUS_JSON" "$BIN_DIR" <<'PY'
import json
import sys
from pathlib import Path

status_path = Path(sys.argv[1])
bin_dir = Path(sys.argv[2])
payload = json.loads(status_path.read_text(encoding="utf-8"))
resolved = payload["capabilities"]["resolved"]
ffmpeg_bin = resolved["ffmpeg_bin"]
expected = str(bin_dir / "ffmpeg_dovi_compat")

if ffmpeg_bin != expected:
    raise SystemExit(f"Expected packaged backend to resolve {expected}, got {ffmpeg_bin}")
if payload["capabilities"]["dv_mp4_safe_mux"] is not True:
    raise SystemExit("Expected dv_mp4_safe_mux=true in packaged backend status output")

print(f"Packaged backend smoke test passed: ffmpeg_bin={ffmpeg_bin}")
PY
