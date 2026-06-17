# Chunk 01 — Scaffold Extension + `skeleton.json`

**Statut :** `done`
**Dépend de :** artefact #2 (vraie collection) — **fourni**
**Parent :** [`MASTER.md`](MASTER.md)

## Objectif
Une Extension Blender 4.2+ **installable** (manifest + register) + le `skeleton.json` vérifié, dérivé
d'une vraie collection du jeu.

## Fichiers touchés
- `minervha_material_exporter/blender_manifest.toml` — manifeste Extension (id, version, min 4.2.0,
  permission `files`, licence GPL).
- `minervha_material_exporter/__init__.py` — `register`/`unregister` + **panneau placeholder** N-panel
  « Minervha » (vérifie l'install ; remplacé par `ui.py` au chunk 6).
- `minervha_material_exporter/skeleton.json` — squelette collection (en-tête réel + tableaux vidés).
- `minervha_material_exporter/summary.md` — doc-hygiène du dossier.

## Source du squelette
Dérivé de `…\SandboxSaveGames\Collections\Clothes\Minervha Maya Top.json` (vraie save, `version:14`,
`luaVersion:11`, bloc `cameraOverrideSettings` complet). Tableaux `props/characters/sexScenes/poses/
customMaterials` **vidés**, `level:""`, `bHasDedicatedIcon:false`, `bOverrideCameraSettings:false`.

## Watch-outs
- Valeurs d'en-tête **numériques** (`14`, pas `"14"`) — comme les vraies saves.
- Manifeste : `id` en `^[a-z][a-z0-9_]*$`, `tagline` ≤ 64 sans point final, licence GPL (bpy ⇒ dérivé GPL),
  déclarer `[permissions] files` (on lit des textures + écrit `.txt`/`.wlsave` hors du dossier extension).
- `__init__.py` **sans `bl_info`** (le manifeste le remplace en Extension 4.2+).

## Vérif
- `blender --command extension validate minervha_material_exporter` (ou install drag-drop) sans erreur.
- L'onglet « Minervha » apparaît dans la sidebar du viewport 3D.
