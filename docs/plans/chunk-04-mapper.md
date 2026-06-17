# Chunk 04 — `mapper.py` (port of `mapMaterial.js`) + golden parity

**Status:** `done`
**Depends on:** the `NormalizedMaterial` *shape* (defined by `blenderParse.js`) — known, no need for chunk 2
**Parent:** [`MASTER.md`](MASTER.md)

## Objective
Faithfully port the Studio's mapper (`electron-helpers/materialInjector/mapMaterial.js`) to pure Python, and
**prove** it does not drift via a golden parity test.

## Files touched
- `minervha_material_exporter/mapper.py` — `map_material(norm)` → `{entry, textures, report}` or `None`.
  1:1 port: `clamp01`, `channel_for_slot`, `CHANNEL_TO_FIELD`, emission folding, tiling/offset, defaults.
- `tests/fixtures/normalized_materials.json` — 10 normalized materials covering the edge cases.
- `tests/_gen_golden.cjs` — regenerates the golden from the **real** `mapMaterial.js` (Node).
- `tests/golden/expected.json` — expected output (committed source of truth).
- `tests/test_mapper.py` — asserts `entry` + `textures` == golden (floats within tolerance).

## Edge cases covered (parity 10/10)
base color present / absent (texture-driven) · normal strength · `Alpha` slot → `type:Masked` (alpha texture
ignored) · `UNKNOWN` slot ignored + counted · emission strength>0 folded+clamped (with / without Emission
Color) · `Mapping None/Default` → tiling `{1,1,1}` · `<packed>` → unresolved (no texture) · real multi-slot
(1 texture → diffuse+roughness, insertion order) · `skipped` → `None`.

## Watch-outs
- **Insertion order** of channels (`channel_tex`) must follow slot order — Python dict preserves it, like
  `Object.keys` in JS.
- `_is_num` excludes `bool` (JSON `true/false`) to match `typeof x === 'number'`.
- Parity compared on `entry` + `textures` (the data that lands in the save); `report.notes` not compared
  (float formatting is not significant).
- Regenerate the golden: `node tests/_gen_golden.cjs` (requires the Studio repo as a sibling).

## Verification
```
node tests/_gen_golden.cjs   # wrote golden for 10 fixtures
python tests/test_mapper.py  # PARITY OK — 10 fixtures match the Studio mapMaterial.js golden
```
