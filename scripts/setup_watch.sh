#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_CONFIG="$ROOT_DIR/config/reeltranscode.giant_boy.yaml"
DEFAULT_WATCH_DIR="/Volumes/Giant_Boy_Plex/Films/test"

prompt_default() {
  local message="$1"
  local default_value="$2"
  local value
  read -r -p "$message [$default_value]: " value
  if [[ -z "$value" ]]; then
    echo "$default_value"
  else
    echo "$value"
  fi
}

prompt_yes_no() {
  local message="$1"
  local default_value="$2"
  local value
  read -r -p "$message ($default_value) [y/n]: " value
  value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
  if [[ -z "$value" ]]; then
    value="$default_value"
  fi
  value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
  [[ "$value" == "y" ]]
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Commande manquante: $cmd"
    exit 1
  fi
}

echo "ReelTranscode setup interactif"
echo

require_cmd uv
require_cmd launchctl

if [[ ! -x /opt/homebrew/bin/ffmpeg ]]; then
  echo "ffmpeg introuvable: /opt/homebrew/bin/ffmpeg"
  exit 1
fi
if [[ ! -x /opt/homebrew/bin/ffprobe ]]; then
  echo "ffprobe introuvable: /opt/homebrew/bin/ffprobe"
  exit 1
fi

watch_dir="$(prompt_default "Dossier a surveiller" "$DEFAULT_WATCH_DIR")"
output_dir="$(prompt_default "Dossier de sortie optimisee" "${watch_dir%/}_optimized")"
archive_dir="$(prompt_default "Dossier archive (si mode archive)" "${watch_dir%/}_archive")"
config_path="$(prompt_default "Chemin du fichier de config" "$DEFAULT_CONFIG")"
workers="$(prompt_default "Nombre de workers" "1")"
poll_interval="$(prompt_default "Poll interval (secondes)" "15")"
stable_wait="$(prompt_default "Attente max fichier stable (secondes)" "7200")"

if prompt_yes_no "Supprimer le fichier source APRES succes + validation?" "y"; then
  delete_original="true"
else
  delete_original="false"
fi

if prompt_yes_no "Remplacer les fichiers de sortie existants?" "n"; then
  overwrite="true"
else
  overwrite="false"
fi

mkdir -p "$(dirname "$config_path")"

cat >"$config_path" <<EOF
dry_run: false

watch:
  folders:
    - $watch_dir
  recursive: true
  allowed_extensions: [.mkv, .mp4, .mov, .m4v, .ts, .m2ts]
  stable_wait_seconds: $stable_wait
  stable_checks: 2
  poll_interval_seconds: $poll_interval

remux:
  preferred_container: mp4
  faststart: true
  keep_chapters: true
  keep_attachments: false

audio:
  preferred_codec_multichannel: eac3
  preferred_codec_stereo: aac
  fallback_codec: ac3
  max_channels: 8
  preferred_languages: [fra, eng]
  keep_original_compatible_tracks: true

subtitles:
  mode: convert_or_externalize
  convert_text_to_mov_text: true
  external_subtitle_format: srt
  preserve_forced_only_when_needed: false
  ocr_image_subtitles: false

dolby_vision:
  preserve_when_safe: true
  safe_profiles: ["8.1"]
  remux_dv_from_mkv_to_mp4_is_safe: false
  fragile_fallback: preserve_hdr10

video:
  preferred_codec: hevc
  fallback_codec: h264
  force_cfr: false
  keyframe_interval_seconds: 2
  hevc_tag: hev1
  max_4k_fps: 60

output:
  mode: keep_original
  output_root: $output_dir
  archive_root: $archive_dir
  overwrite: $overwrite
  delete_original_after_success: $delete_original

concurrency:
  max_workers: $workers
  io_nice_sleep_seconds: 0.0

retry:
  max_attempts: 3
  backoff_initial_seconds: 5
  backoff_max_seconds: 90

paths:
  state_db: $ROOT_DIR/state/reeltranscode.giant_boy.db
  reports_dir: $ROOT_DIR/reports/giant_boy
  csv_summary: $ROOT_DIR/reports/giant_boy/summary.csv
  temp_dir: $ROOT_DIR/tmp

tooling:
  ffmpeg_bin: /opt/homebrew/bin/ffmpeg
  ffprobe_bin: /opt/homebrew/bin/ffprobe

validation:
  verify_duration_tolerance_seconds: 2.0
  verify_stream_count_delta_max: 4
  run_post_ffprobe: true

logging:
  level: INFO
  json_logs: false
EOF

mkdir -p "$ROOT_DIR/reports/giant_boy" "$ROOT_DIR/state" "$ROOT_DIR/tmp" "$output_dir"

echo
echo "Config ecrite: $config_path"
echo

if prompt_yes_no "Lancer un test rapide (pytest)?" "y"; then
  (cd "$ROOT_DIR" && uv run --extra test pytest -q)
fi

if prompt_yes_no "Lancer un batch maintenant?" "n"; then
  (cd "$ROOT_DIR" && uv run reeltranscode --config "$config_path" batch)
fi

if prompt_yes_no "Activer le monitoring automatique au demarrage (launchd)?" "y"; then
  label="$(prompt_default "Label launchd" "com.reelfin.reeltranscode.watch")"
  plist_path="$HOME/Library/LaunchAgents/${label}.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat >"$plist_path" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>$label</string>
    <key>ProgramArguments</key>
    <array>
      <string>/opt/homebrew/bin/uv</string>
      <string>run</string>
      <string>reeltranscode</string>
      <string>--config</string>
      <string>$config_path</string>
      <string>watch</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
      <key>SuccessfulExit</key>
      <false/>
    </dict>
    <key>WorkingDirectory</key>
    <string>$ROOT_DIR</string>
    <key>StandardOutPath</key>
    <string>$ROOT_DIR/reports/giant_boy/launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT_DIR/reports/giant_boy/launchd.stderr.log</string>
    <key>ProcessType</key>
    <string>Background</string>
  </dict>
</plist>
PLIST

  launchctl unload "$plist_path" >/dev/null 2>&1 || true
  launchctl load -w "$plist_path"
  echo "LaunchAgent active: $plist_path"
fi

echo
echo "Termine."
echo "Commande watch manuelle:"
echo "uv run reeltranscode --config $config_path watch"
