# summary — `minervha_material_exporter/`

Source de l'Extension Blender 4.2+ (ce dossier = racine de build, zippé pour l'install).

| Fichier | Rôle | Dépendances | Plans |
|---|---|---|---|
| `blender_manifest.toml` | Manifeste Extension (id, version, min 4.2.0, permission `files`, licence GPL) | — | chunk-01 |
| `__init__.py` | `register`/`unregister` + panneau **placeholder** N-panel « Minervha » | `bpy` | chunk-01 (UI réelle → chunk-06) |
| `skeleton.json` | Squelette collection bundlé (en-tête réel, tableaux vidés) — base du `.wlsave` | — | chunk-01 (consommé par `wlsave_export.py`, chunk-05) |

À venir : `introspect.py` (chunk-02), `txt_export.py` (chunk-03), `mapper.py` (chunk-04),
`wlsave_export.py` (chunk-05), `ui.py` (chunk-06).
