# summary — `minervha_material_exporter/`

Source of the Blender 4.2+ extension (this folder = build root, zipped for install).

| File | Role | Dependencies | Plans |
|---|---|---|---|
| `blender_manifest.toml` | Extension manifest (id, version, min 4.2.0, `files` permission, GPL license) | — | chunk-01 |
| `__init__.py` | `register`/`unregister` — delegates to `ui` | `bpy`, `ui` | chunk-01 / chunk-06 |
| `skeleton.json` | Bundled collection skeleton (real header, emptied arrays) — base of the `.wlsave`. **Save format v18** (`version:18`, `luaVersion:15`) | — | chunk-01; v18 bump in [full-material-export](../docs/plans/features/full-material-export/plan.md) |
| `mapper.py` | `NormalizedMaterial` → `customMaterials` entry. Emits the **complete v18 struct (24 fields, game key order)**. Single source of truth (the Studio `mapMaterial.js` it was ported from was removed). `textureTiling` = **reciprocal** of Blender's Mapping Scale (`_inv_scale`) | — (pure, no `bpy`) | chunk-04; full-material-export; [tiling-reciprocal](../docs/plans/features/tiling-reciprocal/plan.md) |
| `prop_mapper.py` | `NormalizedObject` → Wild Life `props[]` entry (`UserMesh`/`Group`). Pure; deterministic guids (`make_guid`), `blender_to_wl_transform` (the single axis/unit-convention locus, calibration #2), per-slot `CustomMaterial{i+OFFSET}` material linkage. Schema certified in [`../docs/wl-prop-schema.md`](../docs/wl-prop-schema.md) | — (pure, no `bpy`) | [scene-export](../docs/plans/features/scene-export/plan.md) chunk-02 |
| `scene_introspect.py` | Scene → `NormalizedObject[]` (scope-aware; mesh→UserMesh, empty→Group; local transforms via matrix decompose; mesh-datablock dedup keys; hierarchy; validation: UV/procedural/risky-scale). `build_mesh_object_map` for obj_export. **Needs live validation** | `bpy` | scene-export chunk-01 |
| `obj_export.py` | Export one mesh datablock to OBJ in **local space** (resets `matrix_world` during export — `wm.obj_export` bakes world transform). `make_obj_exporter` adapts it to `build_scene_wlsave`'s injected seam. **Needs live validation**. Axes co-calibrated with `prop_mapper` (calibration #2) | `bpy`, `wlsave_export` | scene-export chunk-03 |
| `bsdf_trace.py` | Node-tracing helpers (port of the script) — used by `introspect`. Also traces **height** textures (Bump/Displacement → Material Output) | `bpy` | chunk-02; full-material-export |
| `introspect.py` | Scene → `NormalizedMaterial[]`, scope-aware. Reads specular, IOR, transmission, alpha, two-sided, alpha-cutoff + height textures for full v18 export | `bpy`, `bsdf_trace` | chunk-02; full-material-export (validated live, Blender 5.1.2) |
| `wlsave_export.py` | `NormalizedMaterial[]` (+ `NormalizedObject[]` for scene) + name → portable `.wlsave` ZIP. `build_wlsave` (materials-only) / `build_scene_wlsave` (materials + props + `Models/` OBJs, OBJ export injected). **Sanitizes every written name**; **namespaces** material names `<Collection>/<Mat>` (both modes). Shared core `_build_material_entries` | `mapper`, `prop_mapper`, `bpy` (re-export only) | chunk-05; scene-export chunk-03; [filename-sanitization](../docs/plans/features/filename-sanitization/plan.md) |
| `ui.py` | "Minervha" N-panel: **Materials/Scene mode toggle** + scope dropdown + Export .wlsave operator + report (sanitized/renamed + scene counters: objects/meshes/no-UV/procedural) + scene-limits hint | `bpy`, `introspect`, `wlsave_export`, `scene_introspect`, `obj_export` | chunk-06 (validated live); scene-export chunk-04; filename-sanitization |

Validated live on Blender 5.1.2. (Mode A / `.txt` export was dropped by request — `.wlsave` only.)
Full v18 material schema: [`../docs/wl-customMaterial-schema.md`](../docs/wl-customMaterial-schema.md).

Tests (`../tests/`): `test_mapper.py` (regression snapshot of `mapper.py`), fixtures + golden regenerable via
`_gen_golden.py`; `test_sanitize.py` (filename sanitization — units + end-to-end `build_wlsave`, pure Python);
`test_tiling.py` (`textureTiling` = reciprocal of Blender Mapping Scale);
`test_prop_mapper.py` + `test_transform.py` (snapshot of `prop_mapper.py` — `normalized_objects.json` fixtures,
golden `expected_props.json` regenerable via `_gen_golden_props.py`);
`test_scene_build.py` (`build_scene_wlsave` end-to-end with OBJ export injected — Models/props/cross-ref);
`verify_introspect_live.py` is a Blender-side probe (run in the Python console).
