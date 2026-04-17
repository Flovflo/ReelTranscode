# Rapport Malcolm S1 Native Player

Date: 2026-04-17

## Constat

Source analysee:

- `/Volumes/Giant_Boy_Plex/Series-opti/Malcom new/S1/Malcolm.in.the.Middle.Lifes.Still.Unfair.S01E01.MULTi.HDR.DV.2160p.WEB.H265-SUPPLY.mp4`

Symptome observe:

- lecture hachee dans le player natif avec des coupures regulieres

Diagnostic retenu:

- le flux video HEVC 2160p Main 10 reste decodable
- le probleme vient du mux MP4, pas du debit ni du disque
- le conteneur source porte la signature `mkvmerge v98.0 ('Chonks') 64-bit`
- un remux `ffmpeg` propre corrige fortement le comportement de lecture natif

## Benchmarks

Mesure comparee sur le meme extrait de 20s en decode hardware `videotoolbox`:

- source originale: `1.92x`
- sortie finale `S01E01.apple-clean.mp4`: `11.5x`

Conclusion:

- la regression vient bien du conteneur source
- un copy remux propre suffit, sans reencodage video

## Remux applique

Commande retenue:

```bash
ffmpeg -y -hide_banner -nostdin -i "<source>.mp4" \
  -map 0:v -map '0:a?' -map '0:s?' \
  -map_chapters 0 -map_metadata 0 \
  -c copy -tag:v hvc1 -strict unofficial \
  -movflags +write_colr+faststart \
  "<source>.apple-clean.mp4"
```

Ce remux:

- conserve le tag video `hvc1`
- conserve la signalisation Dolby Vision (`DOVI configuration record`)
- deplace le `moov` en tete
- laisse les originaux intacts

Sorties creees:

- `/Volumes/Giant_Boy_Plex/Series-opti/Malcom new/S1/Malcolm.in.the.Middle.Lifes.Still.Unfair.S01E01.MULTi.HDR.DV.2160p.WEB.H265-SUPPLY.apple-clean.mp4`
- `/Volumes/Giant_Boy_Plex/Series-opti/Malcom new/S1/Malcolm.in.the.Middle.Lifes.Still.Unfair.S01E02.MULTi.HDR.DV.2160p.WEB.H265-SUPPLY.apple-clean.mp4`
- `/Volumes/Giant_Boy_Plex/Series-opti/Malcom new/S1/Malcolm.in.the.Middle.Lifes.Still.Unfair.S01E03.MULTi.HDR.DV.2160p.WEB.H265-SUPPLY.apple-clean.mp4`
- `/Volumes/Giant_Boy_Plex/Series-opti/Malcom new/S1/Malcolm.in.the.Middle.Lifes.Still.Unfair.S01E04.FiNAL.MULTi.HDR.DV.2160p.WEB.H265-SUPPLY.apple-clean.mp4`

Verification finale:

- les 4 sorties ont bien `hvc1`
- les 4 sorties exposent un `DOVI configuration record`

Log batch:

- `/tmp/reeltranscode_malcolm_s1_clean.log`

## Code corrige

Changements principaux:

- `reeltranscode/analyzer.py`
  - detection des MP4 muxes par `mkvmerge` pour les marquer comme a normaliser
- `reeltranscode/decision_engine.py`
  - les MP4 deja compatibles mais muxes "sale" partent maintenant en `remux_only`
  - un remux MP4 vers MP4 n'est plus traite comme un changement de conteneur fragile pour Dolby Vision
- `reeltranscode/planner.py`
  - ajout d'un helper MP4 centralise pour injecter `-strict unofficial` quand une source Dolby Vision est remuxee via `ffmpeg`
  - le cleanup MP4 final re-utilise ce chemin pour conserver la signalisation DV
- `reeltranscode/pipeline.py`
  - le cleanup MP4 final ajoute precedemment reste actif pour normaliser les sorties remux-only

Tests verifies:

- `tests/unit/test_decision_engine.py`
- `tests/integration/test_command_planner.py`
- `tests/unit/test_pipeline_hevc_timestamp_fallback.py`
- `tests/unit/test_pipeline_dv_guard.py`
- `tests/unit/test_pipeline_skip_requires_existing_target.py`
- `tests/unit/test_pipeline_reporter_failure_visible.py`

Resultat:

- `49 passed`
