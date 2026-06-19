# summary — `minervha_material_exporter/`

Source of the Blender 4.2+ extension (this folder = build root, zipped for install).

| File | Role | Dependencies | Plans |
|---|---|---|---|
| `blender_manifest.toml` | Extension manifest (id, version, min 4.2.0, `files` permission, GPL license) | — | chunk-01 |
| `__init__.py` | `register`/`unregister` — delegates to `ui` | `bpy`, `ui` | chunk-01 / chunk-06 |
| `skeleton.json` | Bundled collection skeleton (real header, emptied arrays) — base of the `.wlsave`. **Save format v18** (`version:18`, `luaVersion:15`) | — | chunk-01; v18 bump in [full-material-export](../docs/plans/features/full-material-export/plan.md) |
| `mapper.py` | `NormalizedMaterial` → `customMaterials` entry. Emits the **complete v18 struct (24 fields, game key order)**. Single source of truth (the Studio `mapMaterial.js` it was ported from was removed). `textureTiling` = **reciprocal** of Blender's Mapping Scale (`_inv_scale`). **`type` decision table** (first-match-wins: Glass/Refraction/Transmission/Transparent BSDF → Transparent; linked-Alpha/Mix-Fac/Alpha-tex → Masked; constant α<0.99 → Transparent) + group-resolved `refraction` (clamp `[1,3]`, 1.45 fallback) | — (pure, no `bpy`) | chunk-04; full-material-export; [tiling-reciprocal](../docs/plans/features/tiling-reciprocal/plan.md); [material-type-fidelity](../docs/plans/features/material-type-fidelity/plan.md) |
| `prop_mapper.py` | `NormalizedObject` → Wild Life `props[]` entry (`UserMesh`/`Group`). Pure; deterministic guids (`make_guid`), `blender_to_wl_transform` (delegates to `wl_transform`), per-slot `CustomMaterial{i+OFFSET}` material linkage. Schema certified in [`../docs/wl-prop-schema.md`](../docs/wl-prop-schema.md) | `wl_transform` (pure, no `bpy`) | [scene-export](../docs/plans/features/scene-export/plan.md) chunk-02; [coordinate-transform](../docs/plans/features/coordinate-transform/plan.md) |
| `wl_transform.py` | **Single locus** of the Blender→WL coordinate convention (`WL_BASIS`): one change of basis `B` drives position (`B·p`), rotation (`B·R·Bᵀ`, extracted in the game rotator convention) and scale (axis permutation); geometry matrix `B_geom = C_objᵀ·B` (derived) + `geom_is_mirrored` (= `det(B)<0`). Reproduces Blender's euler convention exactly. THEORY SEED — calibrated in-game via the rig | — (pure, no `bpy`/numpy) | [coordinate-transform](../docs/plans/features/coordinate-transform/plan.md) chunk-01 |
| `scene_introspect.py` | Scene → `NormalizedObject[]` (scope-aware; mesh→UserMesh, empty→Group; local transforms via matrix decompose; mesh-datablock dedup keys; hierarchy; validation: UV/procedural/risky-scale). `build_mesh_object_map` for obj_export. **Needs live validation** | `bpy` | scene-export chunk-01 |
| `obj_export.py` | Export one mesh datablock to OBJ in **local space** (resets `matrix_world` during export — `wm.obj_export` bakes world transform). `make_obj_exporter` adapts it to `build_scene_wlsave`'s injected seam. **Needs live validation**. Axes co-calibrated with `prop_mapper` (calibration #2) | `bpy`, `wlsave_export` | scene-export chunk-03 |
| `bsdf_trace.py` | Node-tracing helpers (port of the script) — used by `introspect`. Also traces **height** textures (Bump/Displacement → Material Output). Adds the **active-output-anchored surface-shader walk** (`find_active_output`, `trace_surface_shaders`, `_resolve_input`) that classifies material `type`/`refraction` through Reroute/Mix/Add/groups | `bpy` | chunk-02; full-material-export; [material-type-fidelity](../docs/plans/features/material-type-fidelity/plan.md) |
| `introspect.py` | Scene → `NormalizedMaterial[]`, scope-aware. Reads specular, IOR, transmission, alpha, two-sided, alpha-cutoff + height textures for full v18 export. Adds **type-signal fields** (`shaderTypes`, `alphaLinked`, `transmissionLinked`/`StaticValue`, `refractiveIor`, `maskedFacMix`, `surfaceRenderMethod`, `useRaytraceRefraction`) + Glass/Refraction `Color`/`Roughness` fallback | `bpy`, `bsdf_trace` | chunk-02; full-material-export; material-type-fidelity (validated live, Blender 5.1.2) |
| `wlsave_export.py` | `NormalizedMaterial[]` (+ `NormalizedObject[]` for scene) + name → portable `.wlsave` ZIP. `build_wlsave` (materials-only) / `build_scene_wlsave` (materials + props + `Models/` OBJs, OBJ export injected; **`level=""` param** = collection vs fixed-map save — sole `level` locus). **Sanitizes every written name**; **namespaces** material names `<Collection>/<Mat>` (both modes). Shared core `_build_material_entries`. **Texture pre-pass `_process_textures`** (`tex_opts={prefer_jpg, jpg_quality, max_res}`): pure `_plan_texture` decides JPG-vs-PNG (alpha-bearing → always PNG, via `_image_facts` reading `Image.depth`) + downscale; `_export_image` re-encodes on a throwaway copy (**materializes the source pixel buffer first** — a never-displayed file image like a `.tga` loads lazily, and changing `file_format` before the buffer exists made `save()` throw, silently copying the raw game-unreadable `.tga`). `tex_opts=None` ⇒ legacy 'packed/generated → PNG, copy on-disk as-is' | `mapper`, `prop_mapper`, `bpy` (re-export only) | chunk-05; scene-export chunk-03; [filename-sanitization](../docs/plans/features/filename-sanitization/plan.md); [map-export-target](../docs/plans/features/map-export-target/plan.md); [texture-options](../docs/plans/features/texture-options/plan.md) |
| `bake.py` | **Phase 2 (net-new).** Flatten arbitrary node graphs (procedural / multi-texture / divergent-UV) to per-channel PBR PNGs via **Cycles** bake — rung **B** of the ladder, triggered by mapper's `bakeCandidates`. Non-destructive: `bake_environment` snapshots/restores engine+samples+selection; the EMIT-rewire (`metallic` & any pass-less input) is restored in `finally`. `ensure_uv` Smart-UV-Projects a no-UV mesh (never re-unwraps one with UVs). Colorspace set at **save** time (sRGB color / Non-Color data — the bake buffer is linear). `extract_orm_channel` splits a packed ORM by pixel-copy (no render). **Validated live, Blender 5.1.2** | `bpy` | [shading-compatibility](../docs/plans/features/shading-compatibility/plan.md) chunk-05 (live) |
| `ui.py` | "Minervha" N-panel: **Materials/Scene mode toggle** + scope dropdown + **Scene-only Target dropdown** (Collection / Showroom / NewWildLifeMap / OldWildLifeMap → save `level`) + **Textures box** (Prefer JPG / JPG Quality / Max resolution → `tex_opts`) + Export .wlsave operator + report (sanitized/renamed + scene counters: objects/meshes/no-UV/procedural + collection-vs-map line) + scene-limits hint. **Defaults: Scene / Selected Objects / Showroom** | `bpy`, `introspect`, `wlsave_export`, `scene_introspect`, `obj_export` | chunk-06 (validated live); scene-export chunk-04; filename-sanitization; map-export-target; [texture-options](../docs/plans/features/texture-options/plan.md) |

Validated live on Blender 5.1.2. (Mode A / `.txt` export was dropped by request — `.wlsave` only.)
Full v18 material schema: [`../docs/wl-customMaterial-schema.md`](../docs/wl-customMaterial-schema.md).

**Shading compatibility — Phase 1 (pure-data, validated live Blender 5.1.2).** Stops silent fidelity loss
and infers what the flat schema can still hold, without baking: `mapper.py` emits per-channel loss **notes +
structured `bakeCandidates`** (multi-texture / divergent-UV / dropped rotation / packed ORM / UDIM·packed)
and `bIsTriplanar` from `projectionMapped`/`consumedByNoUvObject`; `bsdf_trace.py` adds
`texture_is_projection_mapped`, `first_bump_strength`, `_resolve_color`; `introspect.py` reads linked-static
PBR scalars (Value/RGB-driven Base Color/Metallic/Roughness/Emission), threads Mapping **rotation**, and sets
`projectionMapped`/`consumedByNoUvObject`; `scene_introspect.py` drops the dead `TEX_MUSGRAVE`. The
`bakeCandidates` are the trigger Phase 2 (opt-in baking) will consume. Plan + chunks:
[`../docs/plans/features/shading-compatibility/plan.md`](../docs/plans/features/shading-compatibility/plan.md).

**Shading compatibility — Phase 2 (baking, opt-in, validated live Blender 5.1.2).** `introspect.py` flags
**`dynamicChannels`** (a channel driven by a non-static node graph — Noise/Math/Mix — with no exportable
image, the procedural trigger); `mapper.py` turns those into `bakeCandidate{reason:"procedural"}` and resets
`textureTiling` to identity when a channel's texture is `baked`. `bake.py` (net-new) flattens flagged
channels to PNGs via Cycles. `wlsave_export.build_scene_wlsave` gains a `material_baker` hook (run before
mapping; **Scene mode only** — baking needs an object+UV); `ui.py` adds the **Bake** opt-in toggle + Bake
resolution (Scene mode), builds the baker over the in-scope objects (only meshes that *already* have UVs, to
stay non-destructive), and reports baked-channel counts. End-to-end live: a Noise→Base Color/Roughness
material bakes 2 PNGs into the `.wlsave`, channel paths filled, tiling identity, render state restored.

**Shading compatibility — Phase 3 (polish, validated live Blender 5.1.2).** `bFlipGreenChannel` is now an
export toggle: `ui.py` adds **Flip normal green (DirectX)** (default True), stamps `flipGreen` onto every norm
via `_annotate_flip`, and `mapper.py` reads `norm.get("flipGreen", True)`. `introspect.py` flags
**`lossyFeatures`** (anisotropy / coat / sheen — Principled inputs the flat struct cannot carry) which
`mapper.py` surfaces as "no WL equivalent, dropped" notes. **Deferred:** UV-bounds tiling inference (a fragile
niche heuristic — see plan chunk-08). Docs (chunk-09): MASTER addendum, schema note and this summary updated
(owner-approved 2026-06-18).

**Missing-texture detail.** `wlsave_export._build_material_entries` emits `report["missingDetail"]` — per
texture (grouped), the **material(s) + consumer mesh(es) + channel(s) + reason** (`packed`/`generated`/`udim`/
`missing path`/`file not found`) for everything that did NOT make it into the bundle. `ui.py` shows the first
10 in the export popup and prints the full list to the system console (`_print_missing_detail`). Answers the
user ask "the plugin says N missing — *which* texture on *which* mesh?". Additive report field (existing
consumers/tests untouched); the material→object link reuses `introspect`'s `norm["objects"]`
(`bsdf_trace.objects_using_material`).

Tests (`../tests/`): `test_mapper.py` (regression snapshot of `mapper.py` + `run_semantic()` asserting the
Phase-1 shading-compat signals — triplanar / loss notes / `bakeCandidates` — across 7 new fixtures), fixtures
+ golden regenerable via `_gen_golden.py`; `test_sanitize.py` (filename sanitization — units + end-to-end `build_wlsave`, pure Python);
`test_tiling.py` (`textureTiling` = reciprocal of Blender Mapping Scale);
`test_prop_mapper.py` + `test_transform.py` (snapshot of `prop_mapper.py` — `normalized_objects.json` fixtures,
golden `expected_props.json` regenerable via `_gen_golden_props.py`);
`test_scene_build.py` (`build_scene_wlsave` end-to-end with OBJ export injected — Models/props/cross-ref);
`test_texture_collision.py` (dedup by srcPath, collision rename);
`test_texture_options.py` (pure `_plan_texture` JPG/PNG/downscale decision + `tex_opts` end-to-end);
`test_missing_report.py` (per-texture `missingDetail`: packed → material+meshes+channel+reason; on-disk
`file not found` tie-back; resolved texture → no entry);
`verify_introspect_live.py` is a Blender-side probe (run in the Python console);
`verify_tga_reexport_live.py` is a headless Blender probe (`blender --background --python …`) asserting a
`.tga` re-encodes to a valid PNG/JPG in the bundle (the lazy-buffer fix) across default / prefer-JPG /
max-res paths.
