# Matrice de compatibilite media ReelTranscode

Ce document decrit ce que ReelTranscode gere **reellement aujourd'hui dans le code** pour les containers, codecs video, codecs audio, sous-titres, HDR et Dolby Vision.

Il est volontairement strict:
- il distingue ce qui est **nativement compatible**
- ce qui est **converti / remuxe / adapte**
- ce qui est **detecte mais non garanti**
- ce qui est **configure mais pas encore completement cable**

Source de verite principale:
- `reeltranscode/analyzer.py`
- `reeltranscode/decision_engine.py`
- `reeltranscode/planner.py`
- `reeltranscode/pipeline.py`
- `reeltranscode/validator.py`
- `reeltranscode/subtitle_ocr.py`

## Resume executif

| Domaine | Support natif | Support avec adaptation | Non garanti / limite actuelle |
| --- | --- | --- | --- |
| Detection de fichiers en mode watch | `.mkv`, `.mp4`, `.mov`, `.m4v`, `.ts`, `.m2ts` | - | les autres extensions ne sont pas prises automatiquement par le watcher |
| Containers deja consideres Apple-compatibles | `mp4`, `mov`, `m4v` | remux vers `mp4` surtout | ReelTranscode est tres clairement optimise pour `mp4` |
| Video Apple-compatible | `hevc`, `h264` sous conditions de pixel format / cadence / progressif | transcode via `hevc_videotoolbox` ou `h264_videotoolbox` | codecs comme `av1` ne passent pas en natif |
| Audio Apple-compatible | `eac3`, `ac3`, `aac` | transcode auto des pistes non compatibles | la logique actuelle ne s'appuie pas sur tous les champs audio de config |
| Sous-titres texte | `mov_text` en sortie MP4 | `subrip`, `srt`, `ass`, `ssa`, `webvtt` convertis en `mov_text` | pas de vraie externalisation sidecar aujourd'hui |
| Sous-titres image | detectes: `hdmv_pgs_subtitle`, `dvd_subtitle`, `xsub` | OCR vers `mov_text` si active | OCR explicitement pensee pour PGS/SUP; `dvd_subtitle` et `xsub` sont detectes mais pas garantis comme flux OCR robustes |
| Dolby Vision | conservation stricte si voie safe disponible | voie `DoViMuxer` + `MP4Box` + `mediainfo` + `mp4muxer` | si la preservation DV ne peut pas etre prouvee, ReelTranscode saute ou bascule selon la politique |
| HDR10 | conserve si la decision l'impose | transcode HEVC Main10 / `p010le` avec signalisation BT.2020/PQ | peut etre force en SDR si la politique DV/HDR l'exige |

## 1. Perimetre reel du support

Il y a trois niveaux de "support" dans ReelTranscode:

| Niveau | Signification concrete |
| --- | --- |
| Support natif | la source est deja consideree compatible Apple, ou peut etre recopiee telle quelle dans la sortie cible |
| Support avec adaptation | ReelTranscode modifie le container, transcode une piste, retague le HEVC, convertit les sous-titres, ou lance une voie DV-safe |
| Detecte mais non garanti | le type de flux est reconnu par le modele interne, mais la pipeline n'offre pas une promesse robuste sur tous les cas |

Important:
- ReelTranscode est un pipeline **Apple-first**.
- Le chemin produit le plus abouti est: **source diverse -> sortie MP4 Apple-native**.
- La quasi-totalite des regles avancees de sous-titres et de validation sont pensees pour `mp4`.

## 2. Containers et formats de fichier

### 2.1 Extensions surveillees en mode `watch`

Le watcher et le scanner automatiques ne prennent en compte que ces extensions:

| Extension | Etat |
| --- | --- |
| `.mkv` | supportee en entree watch |
| `.mp4` | supportee en entree watch |
| `.mov` | supportee en entree watch |
| `.m4v` | supportee en entree watch |
| `.ts` | supportee en entree watch |
| `.m2ts` | supportee en entree watch |

Note importante:
- En inference du code, les commandes `analyze` / `process` peuvent recevoir **n'importe quel chemin** si `ffprobe` sait l'ouvrir.
- En revanche, **l'ingestion automatique par watch** est limitee a la liste ci-dessus.

### 2.2 Containers consideres Apple-compatibles

ReelTranscode considere comme deja Apple-compatibles les containers suivants:

| Container `ffprobe` | Etat |
| --- | --- |
| `mp4` | compatible Apple |
| `mov` | compatible Apple |
| `m4v` | compatible Apple |

Si le container source n'est pas dans cette famille, ReelTranscode peut planifier un remux ou une conversion.

### 2.3 Containers de sortie acceptes par la config

La config valide actuellement ces containers cibles:

| Valeur `remux.preferred_container` | Etat reel |
| --- | --- |
| `mp4` | chemin principal et le mieux supporte |
| `mov` | accepte par la config, mais moins de logique specialisee que `mp4` |
| `m4v` | accepte par la config, meme remarque que `mov` |
| `mkv` | accepte par la config, mais le coeur du produit n'est pas optimise pour cette cible |

Conclusion pratique:
- si tu veux le support le plus complet et le plus teste: **vise `mp4`**

## 3. Video

### 3.1 Codecs video reconnus comme Apple-compatibles

| Codec video | Etat natif | Conditions supplementaires |
| --- | --- | --- |
| `hevc` | compatible | pixel format limite, tag MP4 surveille, contraintes de cadence 4K |
| `h264` | compatible | doit rester en `yuv420p` |

Tout autre codec video est traite comme non compatible et pousse vers un transcode video.

Exemple explicitement couvert par les tests:
- `av1` -> non compatible Apple dans la logique ReelTranscode -> transcode video requis

### 3.2 Pixel formats acceptes

| Codec | Pixel formats acceptes comme Apple-compatibles |
| --- | --- |
| `hevc` | `yuv420p`, `yuv420p10le`, `p010le` |
| `h264` | `yuv420p` |

Consequences:
- `hevc` 10 bits HDR / DV peut rester compatible si le pixel format reste dans cette famille
- `h264` 10 bits ou format exotique est considere non compatible

### 3.3 Cadence et entrelacement

| Regle | Comportement |
| --- | --- |
| video entrelacee (`field_order` autre que `progressive` ou `unknown`) | consideree incompatible, transcode recommande |
| video 4K avec `width >= 3840` et FPS > `video.max_4k_fps` | consideree hors politique compatible |
| valeur par defaut de `video.max_4k_fps` | `60` |

### 3.4 Traitement video en sortie

| Cas | Action ReelTranscode |
| --- | --- |
| video deja compatible + pas de transcode requis | `-c:v copy` |
| source HEVC copiee vers MP4 | retag `-tag:v hvc1` par defaut |
| video incompatible + cible HEVC | transcode `hevc_videotoolbox` |
| video incompatible + cible H.264 | transcode `h264_videotoolbox` |

### 3.5 Reglages video appliques pendant un transcode

#### Sortie HEVC

| Situation | Reglage applique |
| --- | --- |
| pipeline HEVC standard | `-c:v hevc_videotoolbox -tag:v hvc1` |
| source 10 bits ou pipeline HDR/DV | `-profile:v main10 -pix_fmt p010le` |
| source SDR standard | `-profile:v main -pix_fmt yuv420p` |

#### Sortie H.264

| Situation | Reglage applique |
| --- | --- |
| pipeline H.264 | `-c:v h264_videotoolbox -profile:v high -pix_fmt yuv420p` |

### 3.6 Signalisation couleur preservee

| Cas | Signalisation forcee |
| --- | --- |
| preservation HDR10 / DV | `bt2020` + `smpte2084` + `bt2020nc` |
| fallback SDR force | `bt709` + `bt709` + `bt709` |
| autre cas | recopie des metadonnees couleur source quand disponibles |

### 3.7 Tags HEVC surveilles

| Situation | Tag accepte / force |
| --- | --- |
| sortie MP4 HEVC standard | `hvc1` par defaut |
| config validee | `hvc1` ou `hev1` uniquement |
| sortie Dolby Vision preservee | `hvc1`, `dvh1` ou `dvhe` acceptes par la validation |

Point cle:
- un MP4 HEVC tagge `hev1` peut etre considere comme necessitant un remux si la politique cible `hvc1`

## 4. Audio

### 4.1 Codecs audio consideres Apple-compatibles

| Codec audio | Etat natif |
| --- | --- |
| `eac3` | compatible |
| `ac3` | compatible |
| `aac` | compatible |

Condition minimale:
- il faut **au moins une piste audio** dans le fichier
- il faut **au moins une piste audio Apple-compatible**

Si aucune piste n'est compatible, ReelTranscode passe en strategie `audio_only` ou `full_pipeline`.

Exemple explicitement couvert:
- `dts` -> non compatible dans la logique actuelle -> transcode audio requis

### 4.2 Transcode audio applique

Quand ReelTranscode doit convertir une piste audio non compatible:

| Type de piste source | Codec cible par defaut | Debit applique |
| --- | --- | --- |
| > 2 canaux | `eac3` | `640k` jusqu'a 6 canaux, `768k` au-dela |
| <= 2 canaux | `aac` | `192k` |

Important:
- la pipeline part d'un `-c:a copy` global puis remplace piste par piste seulement quand c'est necessaire
- les metadonnees de langue et de titre sont re-ecrites sur chaque piste audio de sortie
- la disposition `default` est preservee piste par piste

### 4.3 Fallback AAC stereo ajoute automatiquement

Pour une sortie `mp4`, ReelTranscode peut ajouter une piste de secours stereo:

| Condition | Action |
| --- | --- |
| pas de piste `aac` stereo deja presente | ajout d'une piste `AAC Stereo Fallback` |
| parametres de cette piste | `aac`, `2` canaux, `192k` |
| source utilisee | la piste audio `default`, sinon la premiere piste audio |

Limite importante:
- sur la voie **DoViMuxer DV-safe**, ce fallback AAC stereo est **volontairement saute**

## 5. Sous-titres

### 5.1 Sous-titres texte reconnus

Les codecs suivants sont modeles comme sous-titres texte:

| Codec sous-titre source | Etat |
| --- | --- |
| `subrip` | reconnu |
| `srt` | reconnu |
| `ass` | reconnu |
| `ssa` | reconnu |
| `webvtt` | reconnu |
| `mov_text` | reconnu |

### 5.2 Sous-titres texte en sortie MP4

Pour une cible `mp4`, ReelTranscode normalise les sous-titres texte vers:

| Format de sortie | Etat |
| --- | --- |
| `mov_text` | format de sortie reel pour les sous-titres texte en MP4 |

Informations preservees autant que possible:
- langue
- titre
- drapeau `default`
- drapeau `forced`
- marqueurs `hearing_impaired` / `captions`

Validation de sortie:
- la validation MP4 attend des pistes sous-titres `mov_text`
- cote MediaInfo, `tx3g` est interprete comme equivalent `mov_text`

### 5.3 Sous-titres image detectes

Les codecs suivants sont modeles comme sous-titres image:

| Codec sous-titre image | Etat de detection | Etat de traitement |
| --- | --- | --- |
| `hdmv_pgs_subtitle` | detecte explicitement | pris en charge pour drop ou OCR |
| `dvd_subtitle` | detecte explicitement | detecte comme incompatible en sortie MP4 Apple-native |
| `xsub` | detecte explicitement | detecte comme incompatible en sortie MP4 Apple-native |

### 5.4 Comportement sur les sous-titres image en cible MP4

| Configuration | Comportement |
| --- | --- |
| `ocr_image_subtitles: false` et `drop_incompatible_image_subtitles: true` | les pistes image incompatibles sont supprimees du MP4 final |
| `ocr_image_subtitles: true` et `drop_incompatible_image_subtitles: false` | ReelTranscode tente un OCR puis reinjecte le resultat en `mov_text` |
| `ocr_image_subtitles: false` et `drop_incompatible_image_subtitles: false` | erreur explicite; ReelTranscode refuse d'externaliser "magiquement" |

### 5.5 OCR sous-titres: ce qui est vraiment garanti

La chaine OCR actuelle fait:

1. extraction de la piste image depuis la source via `ffmpeg`
2. production d'un fichier intermediaire `.sup`
3. OCR via `pgsrip`
4. moteur OCR `tesseract`
5. generation d'un `.srt`
6. remux du resultat dans le MP4 final en `mov_text`

Conclusion pratique:

| Type de piste image | Niveau de confiance reel |
| --- | --- |
| `hdmv_pgs_subtitle` / PGS | support explicite et coherent avec la chaine `.sup` + `pgsrip` |
| `dvd_subtitle` | detecte comme image, mais pas documente / teste comme OCR robuste de bout en bout |
| `xsub` | detecte comme image, mais pas documente / teste comme OCR robuste de bout en bout |

Autrement dit:
- **PGS est le vrai cas OCR supporte**
- `dvd_subtitle` et `xsub` sont bien detectes comme sous-titres image incompatibles pour MP4 Apple-native
- mais le code ne donne pas aujourd'hui une promesse produit aussi solide pour leur OCR que pour le PGS

### 5.6 Externalisation sidecar: etat reel

Le modele de config expose encore des options comme:
- `subtitles.mode`
- `subtitles.external_subtitle_format`
- `subtitles.preserve_forced_only_when_needed`

Mais dans l'etat actuel du planner:
- il n'y a **pas de vraie filiere sidecar externe complete** produite par defaut
- les exports de sous-titres externes ne sont pas aujourd'hui le chemin produit principal
- la logique effective est surtout: **convertir vers `mov_text`, OCRer, ou dropper**

Donc:
- ne pas lire `convert_or_externalize` comme "externalisation sidecar pleinement implementee"

## 6. Dolby Vision et HDR

### 6.1 Detection Dolby Vision

ReelTranscode cherche la preuve Dolby Vision via:

| Source d'analyse | Usage |
| --- | --- |
| `ffprobe` | side data DOVI, champs DV de stream |
| `mediainfo` | confirmation DV sur containers Apple / MP4 |

### 6.2 Profils Dolby Vision

| Regle | Valeur actuelle |
| --- | --- |
| profils DV consideres "safe" par defaut | `8.1` |
| option de config | `dolby_vision.safe_profiles` |

### 6.3 Quand la voie DoViMuxer est utilisee

La voie DV-safe peut etre selectionnee si:

| Condition | Necessaire |
| --- | --- |
| cible `mp4` | oui |
| toolchain complete disponible | oui |
| pas de transcode video requis | oui |
| pas de transcode audio requis | oui |
| sous-titres compatibles avec cette voie | oui |

Toolchain requise:

| Binaire | Role |
| --- | --- |
| `DoViMuxer` | remux DV-safe principal |
| `MP4Box` | patch / reapplique la signalisation DV et certains ajustements |
| `mediainfo` | verification / evidence DV |
| `mp4muxer` | mux MP4 dans la voie DV-safe |
| `ffmpeg` | support de la chaine globale |

### 6.4 Comportement si la preservation DV n'est pas sure

| Situation | Reaction ReelTranscode |
| --- | --- |
| DV preservee et prouvable | sortie acceptee |
| DV fragile mais voie DoViMuxer disponible | ReelTranscode passe par DoViMuxer |
| DV fragile et voie safe indisponible, fallback `preserve_hdr10` | ReelTranscode preserve HDR10 si possible |
| DV/HDR trop fragile, fallback `force_sdr` | transcode SDR explicite |
| validation DV requise mais preuve DV absente en sortie | echec explicite |

Point important:
- ReelTranscode prefere **echouer ou skipper proprement** plutot que produire un faux MP4 "compatible" qui perdrait silencieusement le Dolby Vision

## 7. Chapitres, attachments, cover art, metadata

| Element | Comportement par defaut |
| --- | --- |
| chapitres | conserves si `remux.keep_chapters: true` |
| attachments | non conserves par defaut |
| image attachee / cover art (`attached_pic`) | exclue du flux video principal |
| metadata globale | remappee avec `-map_metadata 0` ou equivalent selon la voie |

Point de vigilance:
- les attachments ne font pas partie du coeur "Apple-native MP4" du projet

## 8. Validation post-traitement

La sortie n'est pas consideree bonne juste parce que `ffmpeg` a termine.

ReelTranscode valide notamment:

| Controle | Verifie |
| --- | --- |
| container | compatibilite Apple si la cible est `mp4` |
| video | codec, pixel format, cadence, progressif |
| audio | presence d'au moins une piste compatible |
| tag HEVC | `hvc1` par defaut, ou `dvh1` / `dvhe` si DV preservee |
| Dolby Vision | preuve explicite maintenue si requise |
| HDR10 | signalisation preservee si la decision l'impose |
| sous-titres MP4 | pistes `mov_text`, compte, langue, titre, flags `default` / `forced` / SDH |
| duree | tolerance configurable |
| frame rate | derivee de la source |
| synchro audio/video | comparaison des `start_time` et offsets |

## 9. Champs de config a ne pas surestimer

Quelques champs existent dans la config mais ne pilotent pas encore une logique produit aussi complete que leur nom pourrait le suggerer.

### 9.1 Champs presents mais peu ou pas exploites dans la pipeline actuelle

| Champ | Observation |
| --- | --- |
| `audio.fallback_codec` | charge en config, mais la logique audio actuelle choisit surtout `preferred_codec_multichannel` / `preferred_codec_stereo` |
| `audio.max_channels` | valide en config, mais pas applique directement dans le planner actuel |
| `audio.preferred_languages` | charge, mais pas reellement utilise pour une selection intelligente des pistes |
| `audio.keep_original_compatible_tracks` | charge, mais pas moteur principal de la selection actuelle |
| `video.fallback_codec` | charge, mais le planner suit surtout `video.preferred_codec` |
| `subtitles.convert_text_to_mov_text` | charge, mais en pratique la conversion texte -> `mov_text` est deja le comportement reel pour une cible MP4 |
| `subtitles.external_subtitle_format` | charge, mais pas au coeur d'une vraie filiere sidecar finalisee |
| `subtitles.preserve_forced_only_when_needed` | charge, mais pas encore porte par une logique produit complete |
| `subtitles.mode` | son nom suggere plus que ce que le planner execute reellement aujourd'hui |

## 10. Matrice finale "oui / non / conditionnel"

### 10.1 Video

| Entree | ReelTranscode peut la gerer ? | Comment |
| --- | --- | --- |
| HEVC 8 bits / 10 bits compatible | oui | copy ou remux, souvent vers MP4 `hvc1` |
| H.264 `yuv420p` | oui | copy ou remux |
| AV1 | oui, avec adaptation | transcode video requis |
| video entrelacee | oui, avec adaptation | transcode recommande / requis par la logique de compatibilite |
| 4K > 60 fps par defaut | oui, avec adaptation | hors politique Apple -> transcode / decision specifique |

### 10.2 Audio

| Entree | ReelTranscode peut la gerer ? | Comment |
| --- | --- | --- |
| E-AC-3 / `eac3` | oui | conserve si possible |
| AC-3 / `ac3` | oui | conserve si possible |
| AAC / `aac` | oui | conserve si possible |
| DTS | oui, avec adaptation | transcode audio requis |
| audio multicanal non compatible | oui, avec adaptation | conversion par defaut vers `eac3` |
| audio stereo non compatible | oui, avec adaptation | conversion par defaut vers `aac` |

### 10.3 Sous-titres

| Entree | ReelTranscode peut la gerer ? | Comment |
| --- | --- | --- |
| `subrip` / `srt` | oui | conversion vers `mov_text` pour MP4 |
| `ass` / `ssa` | oui | conversion vers `mov_text` pour MP4 |
| `webvtt` | oui | conversion vers `mov_text` pour MP4 |
| `mov_text` | oui | conserve / remuxe |
| PGS / `hdmv_pgs_subtitle` | oui, conditionnel | OCR vers `mov_text` si active, sinon drop possible |
| `dvd_subtitle` | conditionnel | detecte comme image incompatible; OCR non garanti de bout en bout |
| `xsub` | conditionnel | detecte comme image incompatible; OCR non garanti de bout en bout |

### 10.4 HDR / DV

| Entree | ReelTranscode peut la gerer ? | Comment |
| --- | --- | --- |
| HDR10 | oui | preserve si la decision l'exige |
| Dolby Vision profile 8.1 | oui, conditionnel | prefere une voie DV-safe avec DoViMuxer |
| Dolby Vision fragile sans voie safe | oui, mais avec garde-fou | preserve HDR10 si possible, sinon force SDR, sinon skip / fail |

## 11. Recommandation pratique

Si tu veux rester dans le chemin le plus propre et le plus fiable de ReelTranscode aujourd'hui:

| Element | Recommandation |
| --- | --- |
| container cible | `mp4` |
| video ideale | `hevc` tagge `hvc1` |
| audio ideal | `eac3` + eventuel fallback `aac` stereo |
| sous-titres ideaux | texte convertibles vers `mov_text` |
| sous-titres image | PGS si OCR necessaire |
| Dolby Vision | profile `8.1` avec toolchain DV-safe complete |

En une phrase:
- **ReelTranscode gere tres bien le pipeline "MKV/TS divers -> MP4 Apple-native HEVC + audio Apple-compatible + sous-titres `mov_text`, avec un chemin Dolby Vision prudent et strict".**
