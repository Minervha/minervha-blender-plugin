# summary — `minervha_material_exporter/`

Source of the Blender 4.2+ extension (this folder = build root, zipped for install).

| File | Role | Dependencies | Plans |
|---|---|---|---|
| `blender_manifest.toml` | Extension manifest (id, version, min 4.2.0, `files` permission, GPL license) | — | chunk-01 |
| `__init__.py` | `register`/`unregister` — delegates to `ui` | `bpy`, `ui` | chunk-01 / chunk-06 |
| `skeleton.json` | Bundled collection skeleton (real header, emptied arrays) — base of the `.wlsave`. **Save format v18** (`version:18`, `luaVersion:15`) | — | chunk-01; v18 bump in [full-material-export](../docs/plans/features/full-material-export/plan.md) |
| `mapper.py` | `NormalizedMaterial` → `customMaterials` entry. Emits the **complete v18 struct (24 fields, game key order)**. Single source of truth (the Studio `mapMaterial.js` it was ported from was removed) | — (pure, no `bpy`) | chunk-04; full-material-export |
| `bsdf_trace.py` | Node-tracing helpers (port of the script) — used by `introspect`. Also traces **height** textures (Bump/Displacement → Material Output) | `bpy` | chunk-02; full-material-export |
| `introspect.py` | Scene → `NormalizedMaterial[]`, scope-aware. Reads specular, IOR, transmission, alpha, two-sided, alpha-cutoff + height textures for full v18 export | `bpy`, `bsdf_trace` | chunk-02; full-material-export (validated live, Blender 5.1.2) |
| `wlsave_export.py` | `NormalizedMaterial[]` + name → portable `.wlsave` ZIP (textures bundled incl. height, skeleton filled) | `mapper`, `bpy` (re-export only) | chunk-05; full-material-export |
| `ui.py` | "Minervha" N-panel + scope dropdown + Export .wlsave operator + report | `bpy`, `introspect`, `wlsave_export` | chunk-06 (validated live) |

Validated live on Blender 5.1.2. (Mode A / `.txt` export was dropped by request — `.wlsave` only.)
Full v18 material schema: [`../docs/wl-customMaterial-schema.md`](../docs/wl-customMaterial-schema.md).

Tests (`../tests/`): `test_mapper.py` (regression snapshot of `mapper.py`), fixtures + golden regenerable via
`_gen_golden.py`; `verify_introspect_live.py` is a Blender-side probe (run in the Python console).
