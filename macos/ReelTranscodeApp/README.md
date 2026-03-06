# ReelTranscodeApp (macOS 26)

Native SwiftUI control app for ReelTranscode.

## Generate project

```bash
xcodegen generate --spec macos/ReelTranscodeApp/project.yml
```

## Build and test

```bash
xcodebuild -project macos/ReelTranscodeApp/ReelTranscodeApp.xcodeproj -scheme ReelTranscodeApp -configuration Debug build
xcodebuild -project macos/ReelTranscodeApp/ReelTranscodeApp.xcodeproj -scheme ReelTranscodeApp -destination 'platform=macOS' test
```

## Runtime assets

```bash
tools/build_backend.sh
tools/collect_runtime_assets.sh
```

`tools/collect_runtime_assets.sh` embarque maintenant tout le stack DV-safe:

- `ffmpeg`
- `ffprobe`
- `DoViMuxer`
- `MP4Box`
- `mediainfo`
- `mp4muxer`

`MP4Box` et `mediainfo` sont relinkes avec leurs `.dylib` dans `Resources/lib`, pour supprimer les dependances Homebrew au runtime.
