# ReelTranscode

[![CI](https://github.com/Flovflo/ReelTranscode/actions/workflows/ci.yml/badge.svg)](https://github.com/Flovflo/ReelTranscode/actions/workflows/ci.yml)
[![Release](https://github.com/Flovflo/ReelTranscode/actions/workflows/release.yml/badge.svg)](https://github.com/Flovflo/ReelTranscode/actions/workflows/release.yml)
[![Latest Release](https://img.shields.io/github/v/release/Flovflo/ReelTranscode)](https://github.com/Flovflo/ReelTranscode/releases)

ReelTranscode est une pipeline macOS orientee Apple pour convertir ou remuxer des medias afin d'obtenir un vrai Direct Play dans QuickTime, Infuse, TV.app, Plex et l'ecosysteme iOS/macOS, sans degrader silencieusement HDR10 ou Dolby Vision.

Le projet combine:
- une app macOS native SwiftUI
- un backend Python robuste pour l'analyse et l'orchestration media
- une voie Dolby Vision safe via `DoViMuxer`
- une conversion OCR des sous-titres image vers `mov_text`
- un packaging DMG et une CI/CD GitHub Actions prets pour les releases

## Ce que ReelTranscode fait bien

- privilegie `copy` ou `remux` avant tout transcode
- cible un MP4 Apple-native lisible sans transcode cote lecture
- preserve Dolby Vision quand la voie DV-safe est disponible
- preserve HDR10 quand la politique de validation l'impose
- convertit les sous-titres texte incompatibles en `mov_text`
- convertit les sous-titres image `PGS` via OCR puis les reinjecte dans le MP4 final
- isole tous les artefacts temporaires dans des workspaces jetables
- publie un seul film final dans le dossier de sortie optimise

## Architecture

- `reeltranscode/`
  Backend Python: analyse `ffprobe`, moteur de decision, planner, pipeline, watcher, validation, OCR sous-titres.
- `macos/ReelTranscodeApp/`
  App macOS SwiftUI: onboarding, configuration, dashboard, logs, integration du runtime backend.
- `tools/`
  Build backend, collecte runtime, packaging DMG local et release signee/non signee.
- `tests/`
  Tests backend et tests macOS.
- `.github/workflows/`
  CI et release GitHub Actions.

## Points forts techniques

- sortie MP4 HEVC taggee `hvc1` par defaut
- `movflags +write_colr+faststart`
- fallback AAC stereo si necessaire pour la lecture Apple
- voie `DoViMuxer + MP4Box + mediainfo + mp4muxer` pour un remux DV-safe
- reapplication du signalement Dolby Vision apres le merge OCR des sous-titres pour ne pas perdre la preuve DV sur le MP4 final
- validation post-traitement sur:
  - DV/HDR
  - codec tag HEVC
  - durees container/video/audio
  - framerate
  - synchro audio/video via `start_time`

## Demarrage rapide

### Backend dev

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

### Commandes utiles

```bash
reeltranscode --config config/reeltranscode.yaml analyze "/path/to/movie.mkv"
reeltranscode --config config/reeltranscode.yaml process "/path/to/movie.mkv"
reeltranscode --config config/reeltranscode.yaml watch
reeltranscode --config config/reeltranscode.yaml status --json --limit 50
reeltranscode --config config/reeltranscode.yaml config-validate --json
```

### App macOS

```bash
xcodegen generate --spec macos/ReelTranscodeApp/project.yml
xcodebuild -project macos/ReelTranscodeApp/ReelTranscodeApp.xcodeproj -scheme ReelTranscodeApp -destination 'platform=macOS' test
```

### Build DMG local

```bash
tools/release_macos_local.sh
```

Artefacts:
- `build/macos-local/ReelTranscodeApp.app`
- `build/macos-local/ReelTranscode-macOS26-local.dmg`

## Flux media

### Texte incompatible MP4

- `subrip`, `ass`, `ssa`, `webvtt` sont convertis en `mov_text`

### Sous-titres image

Flux OCR:
1. extraction de la piste image en `.sup`
2. OCR via `pgsrip` + `tesseract`
3. generation d'un `.srt`
4. injection dans le MP4 final en `mov_text`

### Dolby Vision

Flux DV-safe:
1. analyse source et decision engine
2. remux DV-safe via `DoViMuxer`
3. merge des sous-titres OCR si necessaire
4. reapplication du signalement DV sur le fichier final
5. validation stricte avant publication

Si la preservation DV ne peut pas etre garantie, ReelTranscode skippe ou echoue explicitement au lieu de produire un faux MP4 "compatible".

## Stockage temporaire

`paths.temp_dir` est critique pour les gros MKV 4K DV/HDR.

ReelTranscode y stocke:
- les workspaces DV-safe
- les `.sup` et `.srt` temporaires
- les MP4 intermediaires

Bonnes pratiques:
- utiliser un volume avec beaucoup d'espace libre
- ne jamais pointer `temp_dir` vers un dossier media surveille
- si `temp_dir` est trop petit, ReelTranscode peut automatiquement basculer vers un cache temporaire sur le volume de sortie optimise

## CI/CD GitHub

Deux workflows sont fournis:

- [`ci.yml`](./.github/workflows/ci.yml)
  - lance les tests backend
  - regenere le projet Xcode depuis `project.yml`
  - lance les tests macOS
  - construit un DMG non signe en artifact

- [`release.yml`](./.github/workflows/release.yml)
  - se declenche sur tags `v*`
  - peut aussi etre lance manuellement
  - construit un DMG
  - publie la release GitHub avec checksum SHA-256
  - signe/notarise si les secrets Apple sont configures

### Secrets optionnels pour une release signee

- `MACOS_CERTIFICATE_P12_BASE64`
- `MACOS_CERTIFICATE_PASSWORD`
- `APPLE_TEAM_ID`
- `APPLE_ID`
- `APPLE_APP_PASSWORD`

Sans ces secrets, la release reste fonctionnelle mais non signee.

## Tooling embarque

Le runtime macOS embarque:
- `ffmpeg`
- `ffprobe`
- `DoViMuxer`
- `MP4Box`
- `mediainfo`
- `mp4muxer`

Le script `tools/collect_runtime_assets.sh` sait recuperer ou rebundler ces binaires pour produire une app autonome.

## Troubleshooting

- `PackageNotFoundError` ou `ModuleNotFoundError` dans la voie OCR:
  verifier qu'on lance bien la derniere app packagée.
- `No space left on device`:
  verifier l'espace libre du volume utilise pour `paths.temp_dir`.
- OCR lent sur plusieurs pistes PGS:
  comportement normal, chaque piste est traitee separement.
- fichier DV valide puis echec en validation:
  verifier qu'on utilise bien une build contenant le patch de reapplication du signalement DV apres merge sous-titres.

## Release locale actuelle

Le build local genere sur cette machine est disponible dans:
- `build/macos-local/ReelTranscode-macOS26-local.dmg`
