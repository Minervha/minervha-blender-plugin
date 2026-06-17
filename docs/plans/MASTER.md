# MASTER — Plugin Blender « Minervha Material Exporter »

**Statut :** design **validé** (brainstorming terminé) — en attente de **2 artefacts** avant implémentation
**Date :** 2026-06-17
**Emplacement projet :** `F:\Minervha\Studio\Minervha Blender Plugin` (projet frère de « Minervha Studio » / « Minervha Site », **git propre**)
**Cible :** Extension **Blender 4.2+** (`blender_manifest.toml`), `.zip` installable

---

## Point de départ (état actuel)

- Un script Blender `MaterialPrint_hardened.py` exporte les matériaux (Principled BSDF + textures) d'une
  scène dans un `.txt` (`texture_usage.txt`). Il tourne **à la main** (éditeur de texte Blender), pas de
  packaging.
- Le **Studio** (Electron/JS) lit ce `.txt` et injecte les matières dans une save/collection :
  - `electron-helpers/materialInjector/blenderParse.js` — parser `.txt` → `MaterialNormalized[]`
  - `electron-helpers/materialInjector/mapMaterial.js` — normalisé → entrée `customMaterials` + textures
  - `electron-helpers/materialInjector/injectMaterials.js` — écrit dans la save + copie textures
- Format `.wlsave` (confirmé via `wlsaveOps.js::exportWlsave`) = **ZIP** :
  ```
  <Name>/<Name>.json          ← save JSON (collection ⇒ "level": "")
  <Name>/<Name>.png           ← vignette optionnelle
  <Name>/Textures/<tex>.png   ← textures référencées, bundlées
  ```
  et dans le JSON : `*TexturePath = "<Name>/Textures/<basename>"`.

## Objectif final

Une **Extension Blender 4.2+** avec un panneau (N-panel « Minervha ») offrant **DEUX exports** :
- **A. `.txt`** — format actuel, lu par le Studio (chemin existant inchangé).
- **B. `.wlsave`** — **bundle portable autonome** (collection) contenant le `.json` + les textures,
  installable via le flux d'install existant du Studio.

## Critères de succès

1. L'Extension s'installe dans Blender 4.2+ (drag-drop) et affiche le panneau « Minervha ».
2. **Mode A** : le `.txt` produit est **byte-compatible** avec `blenderParse.js` (parse sans régression).
3. **Mode B** : le `.wlsave` produit s'importe dans le Studio → la collection apparaît avec ses matières
   et leurs textures affichées en jeu.
4. Le `customMaterials[]` du mode B est **identique** à ce que produit `injectMaterials.js` sur la même
   scène (garanti par le golden file / test de parité).
5. Textures packed/generated/`.tga` (que le chemin `.txt`→Studio ne résout pas) sont **résolues** dans le
   `.wlsave` via ré-export PNG.

---

## Scope

**Dans le scope :**
- Introspection scène → `NormalizedMaterial[]` (même forme que la sortie de `blenderParse.js`).
- **Sélecteur de scope par export** : objets sélectionnés / Collection Blender / fichier entier.
- Serializer **A** : `.txt` byte-compatible avec `blenderParse.js`.
- Serializer **B** : mapper (**port fidèle** de `mapMaterial.js`) → `customMaterials[]` → squelette
  collection **bundlé** → ZIP `.wlsave`.
- **Textures** : copie PNG/JPG tels quels ; **ré-export PNG via Blender** des `packed`/`generated`/`.tga` ;
  dédup par basename ; ref `<Name>/Textures/<basename>` ; color space respecté (Non-Color pour
  normal/rough/metallic, sRGB pour base/emissive).
- Squelette collection **bundlé** (`skeleton.json`) dérivé d'une vraie save fournie.
- UI N-panel + **rapport** post-run (matières créées, textures bundlées/ré-exportées/ignorées).
- Packaging Extension 4.2+ (`blender_manifest.toml`) → `.zip` installable.
- Garde **anti-drift** : golden file (snapshot de la sortie `customMaterials` attendue) + test de parité
  Python↔JS sur la fixture `sampleExport.js`.

**Hors scope (explicitement) :**
- Écriture directe dans le dossier `Saved` du jeu (mode B = `.wlsave` portable **uniquement**).
- Application des matières à un prop (`MaterialOverride`).
- Vignette/icône de collection en **v1** (`bHasDedicatedIcon:false` ; ajout via OpenGL render plus tard).
- Round-trip inverse WL → Blender.
- Support Blender < 4.2.

---

## Architecture — un cœur, deux serializers

> Reproduit la stratification éprouvée du Studio (`parse → map → inject`) pour que les deux sorties ne
> puissent **jamais** diverger sur ce qu'elles lisent.

```
Introspection scène ──▶ NormalizedMaterial[]      (forme = sortie de blenderParse.js)
                              ├─▶ Serializer A : texture_usage.txt   (byte-compatible blenderParse.js)
                              └─▶ Serializer B : map → customMaterials[] → collection JSON → zip .wlsave
```

### Modules Python

| Fichier | Rôle |
|---|---|
| `blender_manifest.toml` + `__init__.py` | Manifeste Extension (min Blender 4.2.0) + register/unregister |
| `introspect.py` | Lit Principled BSDF + nœuds textures de chaque matériau → `NormalizedMaterial`. Scope-aware. **Port de la logique du script actuel** |
| `txt_export.py` | `NormalizedMaterial[]` → `.txt`, gardé **byte-identique** à ce que `blenderParse.js` parse |
| `mapper.py` | `NormalizedMaterial` → entrée `customMaterials` — **port fidèle de `mapMaterial.js`** (clamps, channel map, emission folding, tiling/offset) |
| `wlsave_export.py` | Textures (copie / ré-export / dédup) + remplit le squelette + écrit le ZIP au format `<Name>/<Name>.json` + `<Name>/Textures/…` |
| `skeleton.json` | Squelette collection minimal bundlé (dérivé d'une vraie save) |
| `ui.py` | N-panel « Minervha » (sidebar 3D) : scope, nom, 2 opérateurs, rapport |

### Flux `.wlsave` (mode B)
scope + nom (panneau) → introspect → map chaque matière → gather textures uniques (copie PNG/JPG tels
quels, ré-export packed/generated/`.tga` en PNG, dédup) → charge `skeleton.json`, set
`customMaterials`/`name`/`level:""` → écrit le ZIP → rapport. Résultat : `.wlsave` portable installable
via le Studio.

---

## Décisions verrouillées

1. **2 modes** : `.txt` (Studio) + `.wlsave` (bundle autonome).
2. **Scope dropdown** par export (objets sélectionnés / Collection Blender / fichier entier).
3. **Squelette bundlé** (`skeleton.json`), dérivé d'une vraie save.
4. **Textures** : copie réelle PNG/JPG + ré-export PNG des packed/generated/`.tga`, dédup par basename.
5. **Blender 4.2+ Extension** (`blender_manifest.toml`).
6. **Emplacement** : projet frère sous `Studio\`, parité via **golden file** commité.
7. **Pas de vignette** en v1.
8. **Mode B = `.wlsave` portable**, pas d'écriture directe dans le jeu.

---

## Artefacts requis avant implémentation

1. **`MaterialPrint_hardened.py`** (script actuel) — pour porter l'introspection + le format `.txt`
   fidèlement (pas de reverse-engineering).
2. **Une vraie collection `.wlsave` ou son `.json`** (petite/vide idéale) — pour dériver `skeleton.json`
   vérifié (`version`, `luaVersion`, `initialSaveVersion`, `lastUpgradeVersion`,
   `maxCustomEventsPerTick`, `cameraOverrideSettings`).

---

## Découpage en chunks (prévisionnel — Phase 3)

| # | Chunk | Dépend de |
|---|---|---|
| 1 | Scaffold Extension (`blender_manifest.toml`, `__init__`, register) + `skeleton.json` dérivé | artefact #2 |
| 2 | `introspect.py` — scène → `NormalizedMaterial[]` (port du script, scope-aware) | artefact #1 |
| 3 | `txt_export.py` — `NormalizedMaterial[]` → `.txt` byte-compatible + test parité parser | 2 |
| 4 | `mapper.py` — port fidèle de `mapMaterial.js` + golden parité | 2 |
| 5 | `wlsave_export.py` — textures (copie/ré-export/dédup) + build JSON + ZIP `.wlsave` | 1, 4 |
| 6 | `ui.py` — N-panel, scope, 2 opérateurs, rapport | 3, 5 |

> C2–C4 = pur traitement de données (testables hors Blender). C1/C5/C6 touchent l'API `bpy` / le packaging.
