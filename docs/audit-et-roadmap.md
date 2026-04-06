# ReelTranscode: audit technique et roadmap produit

## 1. Ce que le projet est aujourd'hui

ReelTranscode est deja un bon moteur de preparation media "Apple-first".
Le produit actuel n'est pas encore une application de gestion de bibliotheque films/series au sens Plex, Infuse ou Jellyfin, mais plutot:

- un pipeline d'analyse et de transcodage/remux robuste
- un orchestrateur de watch folders pour traiter une librairie existante ou de nouveaux imports
- une app macOS SwiftUI qui pilote la configuration, le lancement, les logs et le statut runtime
- un systeme de validation post-traitement pour eviter les faux "succes" Dolby Vision / HDR / MP4

En clair: la base technique pour "optimiser" une bibliotheque existe deja. La couche "bibliotheque parfaite" au sens catalogue, ergonomie, metadata, reprise sur incident, UX de masse et gouvernance zero perte reste a construire.

## 2. Architecture actuelle

### Backend Python

Le coeur du projet est dans `reeltranscode/`.

- `analyzer.py`
  Analyse `ffprobe`, enrichissement `mediainfo`, detection Dolby Vision / HDR10, empreintes de flux et metadata.
- `decision_engine.py`
  Classification compatibilite Apple et choix de strategie (`no_op`, `remux_only`, `audio_only`, `video_only`, `full_pipeline`).
- `planner.py`
  Construction des commandes `ffmpeg`, de la voie DV-safe `DoViMuxer`, des workspaces temporaires et des conversions OCR sous-titres.
- `pipeline.py`
  Orchestration complete: probe, decision, plan, execution, validation, publication, cleanup, reporting.
- `validator.py`
  Garde-fous de sortie: container, codecs, duree, synchro, sous-titres, DV/HDR, tag HEVC.
- `watcher.py`
  Watch folders avec file d'attente, workers, pause/reprise et seed initial.
- `state_store.py`
  Persistance SQLite: fichiers deja vus, jobs, etat runtime.
- `reporter.py`
  Rapports JSON par job et resume CSV.

### App macOS SwiftUI

Le frontend macOS dans `macos/ReelTranscodeApp/` sert aujourd'hui surtout d'interface de pilotage.

- `AppViewModel.swift`
  Pont principal entre UI, backend embarque, config, logs et service `launchd`.
- `BackendRunner.swift`
  Execution du backend packagé et resolution du runtime embarque.
- `LaunchdService.swift`
  Installation et pilotage du watch mode en background.
- `ConfigurationView.swift`, `DashboardView.swift`, `JobsView.swift`, `IngestView.swift`, `LogsView.swift`
  Vue de configuration, supervision et journalisation.

### Flux critique source -> sortie

1. detection d'un media via batch ou watch
2. probe `ffprobe` et enrichissement optionnel `mediainfo`
3. decision de compatibilite Apple
4. construction d'un plan d'execution
5. generation dans un fichier temporaire / workspace
6. validation post-traitement
7. publication du fichier final
8. gestion de la source selon la politique (`keep_original`, `archive_original`, `replace_original`)
9. rapport JSON + CSV + etat SQLite

## 3. Points tres solides deja presents

- validation post-traitement explicite au lieu d'un simple "ffmpeg a retourne 0"
- decision engine separant compatibilite, fallback DV et planification
- protection forte autour de Dolby Vision et HDR10
- generation en temporaire avant publication finale
- protection contre les overlaps watch/output/temp dans la config
- OCR sous-titres image integre
- packaging runtime autonome cote app macOS
- persistance de jobs et statut runtime exploitable par l'UI

## 4. Correctifs de fiabilite appliques pendant cet audit

Deux angles morts ont ete corriges pendant la revue:

- le dedupe par etat ne skippe plus un fichier source si la sortie optimisee attendue a disparu
- un fichier volatil/disparu au debut du traitement remonte maintenant comme job en echec visible dans l'historique au lieu d'une exception silencieuse hors pipeline

Ces deux correctifs renforcent directement la promesse "ne pas perdre la trace d'un media ni rater une reconstruction necessaire".

## 5. Risques et limites actuels

### P0. Risques critiques pour un objectif "zero perte"

#### 5.1 Publication cross-volume non atomique

`utils.atomic_replace()` fait bien un `os.replace()` quand source et destination sont sur le meme volume.
En revanche, en cross-device, le fallback passe par `shutil.move()`, donc par une copie non atomique.

Consequence:

- la validation a lieu sur le fichier temporaire, pas sur le fichier final apres copie cross-volume
- un incident I/O, une coupure ou un disque instable peut produire une sortie finale partielle ou incomplete
- l'operation n'a pas de transaction de publication "all or nothing" inter-volume

Pour une promesse zero perte, il faut viser:

- un staging systematiquement sur le volume de destination finale
- une verification d'integrite apres publication
- une publication finale atomique locale au volume cible

#### 5.2 Absence de manifest d'actifs publies

Le projet suit bien les jobs, mais pas encore la notion de "bundle logique de media".

Exemples non couverts de facon explicite:

- film principal + sous-titres externes
- episode + artwork + NFO + sidecars
- film + extras + bande-annonce + edition director's cut
- variantes multiples d'un meme titre

Sans manifest unifie, on sait qu'un job est passe, mais pas encore "quels actifs appartiennent a cette oeuvre" ni "ce qui manque pour une librairie coherente".

#### 5.3 Pas encore de mode "repair / rebuild"

Le moteur optimise tres bien un media donne, mais il manque encore un vrai mode d'exploitation pour une grosse bibliotheque:

- revalider une librairie deja traitee
- retrouver les outputs manquants
- reconstruire seulement les items incomplets
- verifier que le fichier final existe toujours et reste lisible
- signaler les orphelins, doublons ou degradations

Pour un usage longue duree, ce mode est indispensable.

### P1. Risques importants mais pas bloquants

#### 5.4 L'application n'est pas encore une vraie bibliotheque films + series

Le modele actuel travaille au niveau fichier.
Il ne connait pas encore:

- titre de l'oeuvre
- annee
- saga / collection
- saison / episode
- version / edition
- langue dominante
- artwork / resume / genres
- statut de sante de la fiche media

Si l'objectif est "mettre toute ma librairie de films et de series dans une belle app", il faut ajouter une vraie couche catalogue au-dessus du pipeline.

#### 5.5 Config orientee pipeline, pas encore orientee usage

L'UI expose surtout des dossiers, chemins et profils de perf.
Il manque des politiques de haut niveau, par exemple:

- "prioriser la preservation absolue"
- "autoriser OCR lent mais complet"
- "ne jamais toucher aux episodes deja valides"
- "reencoder seulement les fichiers qui bloquent Direct Play"
- "garder les bonus/extras a part"

#### 5.6 Resume CSV non protege contre l'ecriture concurrente

Le backend ecrit un CSV de synthese shared depuis plusieurs workers, sans verrou dedie dans `reporter.py`.
Ce n'est pas un risque media direct, mais c'est un point de fragilite pour l'observabilite.

### P2. Qualite produit / UX / performance

#### 5.7 Rafraichissement UI agressif

`RootView.swift` relance toutes les 2 secondes:

- `refreshStatus()`
- `refreshLaunchdStatus()`
- `refreshLogs()`

Le polling est simple et fonctionnel, mais il peut lancer des taches qui se recouvrent, augmenter le bruit runtime et faire plus d'I/O que necessaire.

#### 5.8 YAML genere a la main cote Swift

`ConfigDocument.toYAML()` assemble le YAML par interpolation de chaines.
Ca reste lisible, mais c'est fragile pour des chemins contenant des caracteres speciaux, et ca rend l'evolution du schema plus delicate.

#### 5.9 Fonctionnalites config non pleinement exploitees

Certaines options de sous-titres existent dans la config (`mode`, `external_subtitle_format`) mais ne sont pas encore portees en vraie logique produit complete.
Ca suggere un schema plus avance que l'implementation effective.

## 6. Cas d'usage a couvrir pour une "application parfaite"

### Films

- MKV HEVC 4K HDR10 deja compatible sauf container
- MKV Dolby Vision profile 8.1 a remuxer sans perte
- film avec DTS-HD MA + fallback AAC necessaire
- film avec plusieurs langues et sous-titres forced + SDH
- film avec sous-titres PGS a OCRiser
- film deja optimisé mais output final manquant
- film en doublon sur deux volumes differents
- film avec edition alternative et director's cut

### Series

- detection saison / episode a partir du chemin et du nom de fichier
- imports partiels de saison
- remplacements d'episodes deja presents
- episodes speciaux / hors-saison
- anime avec multi-audio, multi-subs et naming heterogene
- serie avec centaines d'episodes a traiter par vagues

### Exploitation / fiabilite

- copie NAS lente avec fichier encore en cours d'ecriture
- source deplacee ou supprimee pendant le traitement
- manque d'espace temporaire
- crash au milieu d'un remux DV-safe
- output final supprime manuellement apres succes
- disque de destination hors ligne
- relecture d'une bibliotheque deja optimisee pour detecter les trous

## 7. Blueprint de la version ideale

### 7.1 Un moteur media "transactionnel"

Le pipeline doit devenir explicitemment transactionnel:

- creation d'un manifest de job
- staging sur le volume de destination
- validation avant et apres publication
- ecriture d'un receipt final
- cleanup seulement une fois la publication attestee
- mode reprise / repair / reconcile

### 7.2 Une vraie couche catalogue

Ajouter une base "library" separee de la base runtime:

- `library_items`
- `library_versions`
- `library_assets`
- `library_health_events`
- `library_artwork`

Chaque item represente une oeuvre ou un episode, pas seulement un chemin de fichier.

### 7.3 Une UX orientee bibliotheque

Pour une belle app macOS, il faut viser:

- vue Films
- vue Series
- vue Saisons / Episodes
- vue Erreurs / Quarantaines / Repairs
- detail d'un item avec pistes, HDR/DV, langues, sous-titres, historique de jobs
- indicateurs visuels de sante et de completude

### 7.4 Une exploitation "zero perte" assumee

Le mode "preservation absolue" devrait imposer:

- jamais de suppression source sans receipt final
- aucune publication cross-volume non reverifiee
- quarantaine explicite des sorties douteuses
- checksums facultatifs ou obligatoires selon profil
- audit periodique de la bibliotheque optimisee

## 8. Roadmap recommande

### Phase 1: durcissement fiabilite

- forcer le staging sur le volume de destination finale
- ajouter une validation post-publication quand le commit n'est pas atomique
- introduire un manifest/receipt de job
- ajouter une commande `repair` ou `reconcile`
- verrouiller l'ecriture du CSV et clarifier la journalisation structurée

### Phase 2: modele de bibliotheque

- introduire un index films / series / saisons / episodes
- detecter les doublons et les variantes
- suivre les sidecars et assets annexes
- exposer une sante de bibliotheque par item

### Phase 3: UX macOS premium

- remplacer le simple tableau de jobs par une navigation catalogue
- filtrage par type, codec, HDR, langue, erreurs, volume
- detail d'un media avec historique et outils de reprise
- tableaux de bord "espace disque", "outputs manquants", "quarantine", "OCR failures"

## 9. Criteres d'acceptation pour la cible ideale

Une version "bibliotheque parfaite" devrait pouvoir affirmer:

- aucun output ne peut etre considere sain sans receipt final et verification
- la disparition d'un fichier optimise est detectee et reparable
- chaque film ou episode a une representation metier stable, independante du chemin brut
- les incidents I/O, OCR, DV et manque d'espace produisent des etats explicites, actionnables
- l'UI permet de piloter une grande bibliotheque sans ouvrir les logs bruts

## 10. Priorites nettes

Si l'objectif principal reste "ne perdre aucun fichier", l'ordre a suivre est:

1. transaction de publication et repair mode
2. manifest de bibliotheque et verification de completude
3. UX catalogue films / series
4. raffinement performance / ergonomie

Le projet est deja une tres bonne base de pipeline media. Pour devenir une vraie application de bibliotheque haut de gamme, il faut maintenant monter d'un cran: passer du "job par fichier" au "systeme catalogue + fiabilite transactionnelle + operations de maintenance".
