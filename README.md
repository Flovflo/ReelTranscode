# ReelTranscode

ReelTranscode est une pipeline orientee Apple (Plex/Infuse/TV app) qui optimise les medias pour le Direct Play, avec une app macOS native SwiftUI et un backend Python embarque.

## Points forts

- App macOS 26 native (SwiftUI) avec onboarding, dashboard, jobs, config, logs.
- Moteur de traitement Python conserve pour la robustesse et la compatibilite.
- Watch mode persistant via `launchd` + fallback in-app.
- API JSON stable (`api_version: 1`) pour integrer proprement Swift <-> backend.
- Packaging autonome local en `.dmg` (runtime backend + ffmpeg/ffprobe embarques).
- Priorite qualite Apple video:
  - copie/remux avant transcode
  - HEVC Main10 quand pipeline HDR/DV necessite un transcode
  - tag MP4 HEVC par defaut: `hvc1`
  - `movflags +write_colr+faststart` pour meilleure preservation metadata couleur.
  - fallback audio AAC stereo ajoute automatiquement si absent en sortie MP4.

## Structure

- `reeltranscode/`: core backend (analyze, decision, planner, pipeline, watcher, CLI).
- `macos/ReelTranscodeApp/`: app SwiftUI macOS.
- `tools/`: scripts build backend, collecte runtime, release DMG local.
- `tests/`: unit + integration (backend) et tests Swift.

## Prerequis

- macOS (Apple Silicon recommande)
- Python 3.12+
- Xcode 17+ (pour l'app native)
- `xcodegen`
- ffmpeg/ffprobe disponibles (ou embarques via l'app)

## Backend CLI (developpement)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[test]
pytest -q
```

### Commandes principales

- `reeltranscode --config <path> batch`
- `reeltranscode --config <path> watch`
- `reeltranscode --config <path> analyze <file>`
- `reeltranscode --config <path> process <file>`
- `reeltranscode --config <path> status --json --limit 50`
- `reeltranscode --config <path> config-export --json`
- `reeltranscode --config <path> config-validate --json`

## Setup rapide

Assistant interactif:

```bash
./scripts/setup_watch.sh
```

Config de reference:

```bash
config/reeltranscode.yaml
```

## App macOS native

Generer et tester:

```bash
xcodegen generate --spec macos/ReelTranscodeApp/project.yml
xcodebuild -project macos/ReelTranscodeApp/ReelTranscodeApp.xcodeproj -scheme ReelTranscodeApp -destination 'platform=macOS' test
```

## Build runtime + DMG local

```bash
tools/build_backend.sh
tools/collect_runtime_assets.sh
tools/release_macos_local.sh
```

Sorties:

- App: `build/macos-local/ReelTranscodeApp.app`
- DMG local: `build/macos-local/ReelTranscode-macOS26-local.dmg`

## Notes Dolby Vision / HDR

- ReelTranscode tente d'eviter le transcode video des qu'un remux/copy suffit.
- Les chemins Dolby Vision fragiles restent traites de facon conservative selon la policy.
- Si un transcode video est necessaire sur media HDR/DV, la pipeline force HEVC Main10 (`p010le`) et conserve les tags couleur source quand possible.
- Le tag HEVC MP4 cible est configurable via `video.hevc_tag` (`hvc1` par defaut, `hev1` optionnel).

## Troubleshooting rapide

- `dyld ... ffprobe`: verifier `tooling.ffprobe_bin` + executable bit.
- Service `launchd` en erreur: verifier droits dossier `~/Library/LaunchAgents` et logs app support.
- Aucun job visible: ouvrir `Logs` dans l'app et lancer `status --json` sur la meme config.
