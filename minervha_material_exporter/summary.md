# summary — `minervha_material_exporter/`

Source of the Blender 4.2+ extension (this folder = build root, zipped for install).

| File | Role | Dependencies | Plans |
|---|---|---|---|
| `blender_manifest.toml` | Extension manifest (id, version, min 4.2.0, `files` permission, GPL license) | — | chunk-01 |
| `__init__.py` | `register`/`unregister` + **placeholder** "Minervha" N-panel | `bpy` | chunk-01 (real UI → chunk-06) |
| `skeleton.json` | Bundled collection skeleton (real header, emptied arrays) — base of the `.wlsave` | — | chunk-01 (consumed by `wlsave_export.py`, chunk-05) |
| `mapper.py` | `NormalizedMaterial` → `customMaterials` entry (faithful port of `mapMaterial.js`) | — (pure, no `bpy`) | chunk-04 (golden parity in `tests/`) |
| `bsdf_trace.py` | Shared node-tracing helpers (port of the script) — used by txt_export + introspect | `bpy` | chunk-02 |
| `introspect.py` | Scene → `NormalizedMaterial[]` (blenderParse.js shape), scope-aware | `bpy`, `bsdf_trace` | chunk-02 (validated live, Blender 5.1) |
| `wlsave_export.py` | `NormalizedMaterial[]` + name → portable `.wlsave` ZIP (textures bundled, skeleton filled) | `mapper`, `bpy` (re-export only) | chunk-05 (validated live, both texture paths) |

Upcoming: `txt_export.py` (chunk-03), `ui.py` (chunk-06).

Tests (`../tests/`): `test_mapper.py` (Python↔JS parity), fixtures + golden regenerable via `_gen_golden.cjs`.
