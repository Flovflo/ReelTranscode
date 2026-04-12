#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
APP_NAME="ReelTranscodeApp"
BUNDLE_ID="com.reelfin.ReelTranscodeApp"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_PATH="$ROOT_DIR/macos/ReelTranscodeApp/ReelTranscodeApp.xcodeproj"
PROJECT_SPEC="$ROOT_DIR/macos/ReelTranscodeApp/project.yml"
BUILD_ROOT="$ROOT_DIR/build/codex-run"
APP_BUNDLE="$BUILD_ROOT/Debug/$APP_NAME.app"

select_developer_dir() {
  local current_dir=""
  current_dir="$(xcode-select -p 2>/dev/null || true)"
  if [[ -n "$current_dir" && "$current_dir" != "/Library/Developer/CommandLineTools" && -x "$current_dir/usr/bin/xcodebuild" ]]; then
    printf '%s\n' "$current_dir"
    return 0
  fi

  local candidate=""
  while IFS= read -r app; do
    local developer_dir="$app/Contents/Developer"
    local version=""
    version="$(defaults read "$app/Contents/Info" CFBundleShortVersionString 2>/dev/null || true)"
    if [[ "$version" == 26* && -x "$developer_dir/usr/bin/xcodebuild" ]]; then
      printf '%s\n' "$developer_dir"
      return 0
    fi
    if [[ -z "$candidate" && -x "$developer_dir/usr/bin/xcodebuild" ]]; then
      candidate="$developer_dir"
    fi
  done < <(find /Applications -maxdepth 1 -type d -name 'Xcode*.app' | sort -Vr)

  if [[ -n "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  return 1
}

DEVELOPER_DIR="${DEVELOPER_DIR:-}"
if [[ -z "$DEVELOPER_DIR" ]]; then
  if ! DEVELOPER_DIR="$(select_developer_dir)"; then
    echo "Full Xcode is required to build $APP_NAME. Install Xcode 26 and rerun ./script/build_and_run.sh." >&2
    exit 1
  fi
fi
export DEVELOPER_DIR

XCODEBUILD_BIN="$DEVELOPER_DIR/usr/bin/xcodebuild"
if [[ ! -x "$XCODEBUILD_BIN" ]]; then
  echo "xcodebuild not found in DEVELOPER_DIR=$DEVELOPER_DIR" >&2
  exit 1
fi

pkill -x "$APP_NAME" >/dev/null 2>&1 || true

if ! command -v xcodegen >/dev/null 2>&1; then
  echo "xcodegen is required to regenerate $PROJECT_PATH before each launch." >&2
  exit 1
fi

mkdir -p "$BUILD_ROOT"
xcodegen generate --spec "$PROJECT_SPEC" >/dev/null

"$XCODEBUILD_BIN" \
  -project "$PROJECT_PATH" \
  -scheme "$APP_NAME" \
  -configuration Debug \
  -destination "generic/platform=macOS" \
  SYMROOT="$BUILD_ROOT" \
  OBJROOT="$BUILD_ROOT/Intermediates.noindex" \
  build

if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "Built app bundle not found at $APP_BUNDLE" >&2
  exit 1
fi

open_app() {
  /usr/bin/open -n "$APP_BUNDLE"
}

case "$MODE" in
  run)
    open_app
    ;;
  --debug|debug)
    lldb -- "$APP_BUNDLE/Contents/MacOS/$APP_NAME"
    ;;
  --logs|logs)
    open_app
    /usr/bin/log stream --info --style compact --predicate "process == \"$APP_NAME\""
    ;;
  --telemetry|telemetry)
    open_app
    /usr/bin/log stream --info --style compact --predicate "subsystem == \"$BUNDLE_ID\""
    ;;
  --verify|verify)
    open_app
    sleep 2
    pgrep -x "$APP_NAME" >/dev/null
    ;;
  *)
    echo "usage: $0 [run|--debug|--logs|--telemetry|--verify]" >&2
    exit 2
    ;;
esac
