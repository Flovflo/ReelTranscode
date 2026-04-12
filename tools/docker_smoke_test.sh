#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE_TAG="reeltranscode:smoke-test"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/reeltranscode-docker-smoke.XXXXXX")"
trap 'rm -rf "$TMP_DIR"; docker image rm -f "$IMAGE_TAG" >/dev/null 2>&1 || true' EXIT

CONFIG_PATH="$TMP_DIR/reeltranscode.yaml"
STATUS_PATH="$TMP_DIR/status.json"

cat >"$CONFIG_PATH" <<'EOF'
watch:
  folders: []

tooling:
  ffmpeg_bin: /usr/bin/ffmpeg
  ffprobe_bin: /usr/bin/ffprobe
  mediainfo_bin: /usr/bin/mediainfo

paths:
  state_db: /workspace/state/reeltranscode.db
  reports_dir: /workspace/reports
  csv_summary: /workspace/reports/summary.csv
  temp_dir: /workspace/tmp
EOF

docker build -t "$IMAGE_TAG" .

docker run --rm \
  -v "$TMP_DIR:/config" \
  "$IMAGE_TAG" \
  --config /config/reeltranscode.yaml status --json --limit 1 >"$STATUS_PATH"

/usr/bin/python3 - "$STATUS_PATH" <<'PY'
import json
import sys
from pathlib import Path

status_path = Path(sys.argv[1])
payload = json.loads(status_path.read_text(encoding="utf-8"))

runtime = payload.get("runtime") or {}
capabilities = payload.get("capabilities") or {}
resolved = capabilities.get("resolved") or {}

if payload.get("api_version") != 1:
    raise SystemExit(f"Expected api_version=1, got {payload!r}")
if runtime.get("watch_running") is not False:
    raise SystemExit(f"Expected watch_running=false, got {runtime!r}")
ffmpeg_bin = resolved.get("ffmpeg_bin")
if not isinstance(ffmpeg_bin, str) or not ffmpeg_bin.endswith("ffmpeg"):
    raise SystemExit(f"Expected resolved ffmpeg path, got {resolved!r}")
if "missing_tools" not in capabilities:
    raise SystemExit(f"Expected capabilities.missing_tools in payload, got {capabilities!r}")

print(f"Docker smoke test passed: ffmpeg_bin={ffmpeg_bin}")
PY
