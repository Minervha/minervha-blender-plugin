# Chunk 04 — `mapper.py` (port de `mapMaterial.js`) + parité golden

**Statut :** `done`
**Dépend de :** la *forme* `NormalizedMaterial` (définie par `blenderParse.js`) — connue, pas besoin de chunk 2
**Parent :** [`MASTER.md`](MASTER.md)

## Objectif
Porter fidèlement le mapper du Studio (`electron-helpers/materialInjector/mapMaterial.js`) en Python pur,
et **prouver** qu'il ne diverge pas via un test de parité golden.

## Fichiers touchés
- `minervha_material_exporter/mapper.py` — `map_material(norm)` → `{entry, textures, report}` ou `None`.
  Port 1:1 : `clamp01`, `channel_for_slot`, `CHANNEL_TO_FIELD`, emission folding, tiling/offset, défauts.
- `tests/fixtures/normalized_materials.json` — 10 matières normalisées couvrant les cas limites.
- `tests/_gen_golden.cjs` — régénère le golden depuis le **vrai** `mapMaterial.js` (Node).
- `tests/golden/expected.json` — sortie attendue (source de vérité commitée).
- `tests/test_mapper.py` — assert `entry` + `textures` == golden (floats à tolérance).

## Cas limites couverts (parité 10/10)
base color présente / absente (texture-driven) · normal strength · slot `Alpha` → `type:Masked` (texture
alpha ignorée) · slot `UNKNOWN` ignoré + compté · emission strength>0 folded+clampé (avec / sans Emission
Color) · `Mapping None/Default` → tiling `{1,1,1}` · `<packed>` → non résolu (pas de texture) · multi-slot
réel (1 texture → diffuse+roughness, ordre d'insertion) · `skipped` → `None`.

## Watch-outs
- **Ordre d'insertion** des canaux (`channel_tex`) doit suivre l'ordre des slots — dict Python le préserve,
  comme `Object.keys` en JS.
- `_is_num` exclut les `bool` (JSON `true/false`) pour matcher `typeof x === 'number'`.
- Parité comparée sur `entry` + `textures` (la donnée qui atterrit dans la save) ; `report.notes` non comparé
  (formatage de float non significatif).
- Régénérer le golden : `node tests/_gen_golden.cjs` (nécessite le repo Studio en frère).

## Vérif
```
node tests/_gen_golden.cjs   # wrote golden for 10 fixtures
python tests/test_mapper.py  # PARITY OK — 10 fixtures match the Studio mapMaterial.js golden
```
