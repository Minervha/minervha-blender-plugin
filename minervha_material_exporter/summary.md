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
| `introspect.py` | Scene → `NormalizedMaterial[]`, scope-aware. Reads specular, IOR, transmission, alpha, two-sided, alpha-cutoff + height textures for full v18 export. Adds **type-signal fields** (`shaderTypes`, `alphaLinked`, `transmissionLinked`/`StaticValue`, `refractiveIor`, `maskedFacMix`, `surfaceRenderMethod`, `useRaytraceRefraction`) + Glass/Refraction `Color`/`Roughness` fallback + **`perMeshDependency`** (vertex-color / object-space / geometry → flags a bake as a per-mesh approximation) + **`multiTextureChannels`** (a Principled colour input fed by ≥2 images, counted backward via `bsdf_trace.images_feeding_input` → `multi-texture` bakeCandidate, so a blended albedo isn't shipped as one wrong texture) | `bpy`, `bsdf_trace` | chunk-02; full-material-export; material-type-fidelity (validated live, Blender 5.1.2); [bake-on-placeholder](../docs/plans/features/bake-on-placeholder/plan.md) |
| `wlsave_export.py` | `NormalizedMaterial[]` (+ `NormalizedObject[]` for scene) + name → portable `.wlsave` ZIP. `build_wlsave` (materials-only) / `build_scene_wlsave` (materials + props + `Models/` OBJs, OBJ export injected; **`level=""` param** = collection vs fixed-map save — sole `level` locus). **Sanitizes every written name**; **namespaces** material names `<Collection>/<Mat>` (both modes). Shared core `_build_material_entries`. **Texture pre-pass `_process_textures`** (`tex_opts={prefer_jpg, jpg_quality, max_res}`): pure `_plan_texture` decides JPG-vs-PNG (alpha-bearing → always PNG, via `_image_facts` reading `Image.depth`) + downscale; `_export_image` re-encodes on a throwaway copy (**materializes the source pixel buffer first** — a never-displayed file image like a `.tga` loads lazily, and changing `file_format` before the buffer exists made `save()` throw, silently copying the raw game-unreadable `.tga`). `tex_opts=None` ⇒ legacy 'packed/generated → PNG, copy on-disk as-is' | `mapper`, `prop_mapper`, `bpy` (re-export only) | chunk-05; scene-export chunk-03; [filename-sanitization](../docs/plans/features/filename-sanitization/plan.md); [map-export-target](../docs/plans/features/map-export-target/plan.md); [texture-options](../docs/plans/features/texture-options/plan.md) |
| `bake.py` | **Phase 2 (net-new).** Flatten arbitrary node graphs (procedural / multi-texture / divergent-UV) to per-channel PBR PNGs via **Cycles** bake — rung **B** of the ladder, triggered by mapper's `bakeCandidates`. **Bakes on a throwaway full-UV placeholder plane** (`placeholder_plane`), NEVER a scene mesh → captures the material over the whole [0,1]² domain = **one shared texture correct for every mesh** (a scene-mesh bake blacks out all but that mesh's atlas islands). Non-destructive: `bake_environment` restores engine+samples+selection; the plane+mesh and EMIT-rewire (`metallic` & pass-less inputs) are torn down in `finally`. Colorspace set at **save** (sRGB color / Non-Color data — buffer is linear). (`ensure_uv`/`extract_orm_channel`: now-unused legacy helpers, kept.) **Validated live, Blender 5.1.2** | `bpy` | [shading-compatibility](../docs/plans/features/shading-compatibility/plan.md) ch05; [bake-on-placeholder](../docs/plans/features/bake-on-placeholder/plan.md) |
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
resolution (Scene mode) and reports baked-channel counts. End-to-end live: a Noise→Base Color/Roughness
material bakes 2 PNGs into the `.wlsave`, channel paths filled, tiling identity, render state restored.
(**Superseded by bake-on-placeholder** below — the baker now bakes on a full-UV placeholder plane, never a
scene mesh, so the "needs an object+UV / only meshes with UVs" constraint no longer applies.)

**Shading compatibility — Phase 3 (polish, validated live Blender 5.1.2).** `bFlipGreenChannel` is now an
export toggle: `ui.py` adds **Flip normal green (DirectX)** (default True), stamps `flipGreen` onto every norm
via `_annotate_flip`, and `mapper.py` reads `norm.get("flipGreen", True)`. `introspect.py` flags
**`lossyFeatures`** (anisotropy / coat / sheen — Principled inputs the flat struct cannot carry) which
`mapper.py` surfaces as "no WL equivalent, dropped" notes. **Deferred:** UV-bounds tiling inference (a fragile
niche heuristic — see plan chunk-08). Docs (chunk-09): MASTER addendum, schema note and this summary updated
(owner-approved 2026-06-18).

**Bake on placeholder plane (validated live Blender 5.1.2).** The Phase-2 baker baked each channel on one
*representative scene mesh*, so `bpy.ops.object.bake()` filled only that mesh's UV islands — a material shared
across many meshes (an atlas) came out mostly **black** for all but the representative, and it crashed when
that mesh sat under a render-disabled collection. `bake.bake_channel(mat, …)` now bakes on a throwaway full-UV
**placeholder plane** (`bake.placeholder_plane`), capturing the flattened `uv→value` function over the whole
[0,1]² tile → **one shared texture, correct for every mesh** (each samples it through its own UVs).
`introspect._per_mesh_dependency` flags materials whose look depends on **per-mesh data** (vertex colors /
object-space coords / geometry); `build_scene_wlsave` reports those baked as **`materialsApproximated`** (baked
to a representative state — one texture can't be faithful, the per-mesh **fork** is deferred). A Principled **colour channel fed by
≥2 image textures** (real albedo × tiled detail × object-colour — the `nita` model pattern) is detected by a
backward image count (`introspect.multiTextureChannels` → `bsdf_trace.images_feeding_input`, node-keyed) and
baked: the forward slot-trace had kept only ONE texture (often the wrong tiled detail) and dropped the real
albedo to "UNKNOWN" — **257→~0** such silent albedo drops in the test scene. Removed: the
`_force_render_enabled` patch (moot — no scene mesh is baked). `_make_material_baker` dropped its representative-
mesh selection (`mat_obj`/`objs`). Plan + chunks:
[`../docs/plans/features/bake-on-placeholder/plan.md`](../docs/plans/features/bake-on-placeholder/plan.md).

**Export optimizations — chunk-01 (bake size, validated live Blender 5.1.2).** Baked channels shipped as a
fixed-2048² **PNG even with *Prefer JPG* on**: `bake.bake_channel` removed its target image right after
saving, so the texture pre-pass could no longer re-encode it (no bpy image → assumed alpha → kept PNG) — a
526-material scene ballooned **245 MB → 1.1 GB**. Fix: `bake_channel` writes the **final format directly**
(`image_format`/`jpg_quality`; bake targets are `alpha=False`, so JPEG is always valid); `ui._make_material_baker`
picks JPG/PNG from the user's `tex_opts` and `wlsave_export._process_textures` **skips `baked` textures**
(already final); the bake runs at an **adaptive resolution** (`ui._adaptive_bake_res` = the material's largest
source texture, rounded to a power of two, capped by the Bake-resolution dropdown — **now a ceiling** —, floor
512, procedural-only 1024). Live on `Scene_ResortMadness`: `concreteceiling001a` (512² sources) **6.0 MB
PNG@2048 → 114 KB JPEG@512**; **1100/1146** materials bake below the old fixed 2048². Commit `506d970`. Plan:
[`../docs/plans/features/export-optimizations/plan.md`](../docs/plans/features/export-optimizations/plan.md).

**Export optimizations — chunk-02 (unused materials, validated live Blender 5.1.2).** A material sitting in a
slot but used by **no polygon** — and, in Whole-File scope, an **orphan** (0 users) — was still exported and
dragged its textures into the bundle. `introspect._materials_for_scope` now keeps only face-used materials:
usage is read from the **unmodified base mesh**'s `material_index` in bulk (`_used_indices`, `foreach_get`) —
exact for an unmodified mesh (= what `wm.obj_export` writes) and fast (1.16 s over a 15k-object / 1166-material
scene; a depsgraph-eval pass timed out). **Modified** meshes (Geometry Nodes / Solidify / Boolean may add or
shift materials) and **non-mesh** objects keep ALL their slot materials, so the filter never false-drops a used
one. Scene mode stays safe (`material_slots` order untouched, a dropped slot's `CustomMaterial{i}` resolves to
`""`). `ui` reports `materialsUnused`. Live: 193/1166 dropped. Commit `ed42f4c`.

**Export optimizations — chunk-03 (only direct textures ship, validated live Blender 5.1.2).** A texture that
reaches a Principled slot only through a **transforming node** (Mix / Math / ColorRamp / ...) is a procedural
*input*, not the channel's map — but the forward trace attributed it to that channel, so the first-wins pick
could ship a wrong texture (a noise mask as the albedo). `bsdf_trace.direct_slots_from_texture` returns the slots
reachable through **transparent nodes only** (Reroute / Normal Map / Separate* / group structure); `introspect`
threads `tex["directSlots"]`; `mapper` fills a channel only from a **direct (or baked)** texture and **drops the
guess** on a multi-texture / procedural channel that was not baked, recording it in `report["needsBake"]` (Bake
off → empty + reported, never a wrong texture); orm-packed / divergent-uv / rotation keep their valid primary.
`wlsave._process_textures` skips textures with no direct channel slot (no wasted re-encode). `mapper` stays pure
(directSlots absent → all slots direct, golden unchanged but for `MultiTexBaseColor`); 2 fixtures added, golden
regenerated, GOLDEN+SEMANTIC (13)+66 green. Live: a Mix-blended albedo is correctly marked transformed. Commit
`51f82f0`.

**Missing-texture detail.** `wlsave_export._build_material_entries` emits `report["missingDetail"]` — per

**Missing-texture detail.** `wlsave_export._build_material_entries` emits `report["missingDetail"]` — per
texture (grouped), the **material(s) + consumer mesh(es) + channel(s) + reason** (`packed`/`generated`/`udim`/
`missing path`/`file not found`) for everything that did NOT make it into the bundle. `ui.py` shows the first
10 in the export popup and prints the full list to the system console (`_print_missing_detail`). Answers the
user ask "the plugin says N missing — *which* texture on *which* mesh?". Additive report field (existing
consumers/tests untouched); the material→object link reuses `introspect`'s `norm["objects"]`
(`bsdf_trace.objects_using_material`).

**Export options — master group / thumbnail / collisions (validated live Blender 5.1.2).** Three opt-in
export options, all certified against the real game corpus (`%LOCALAPPDATA%/WildLifeC/Saved/SandboxSaveGames`,
135 saves) and the Studio builder (`Minervha Studio/electron-helpers/wlsaveOps.js`). **(1) Master group**
(Scene mode): `prop_mapper.master_group(name)` emits one synthetic root `Group` (identity transform — keeps
children's parent-relative placement) and `build_scene_wlsave` re-parents every otherwise-root prop onto it,
so the imported scene hangs off a single node. Its guid is reserved-key-derived (never collides with an
object's). **(2) Thumbnail** (both modes): a chosen image is bundled as `<Name>/<Name>.png` written **before**
any `Textures/` entry — the Studio reader (`extractFirstPngFromZip`) takes the FIRST `.png` in archive order —
and `bHasDedicatedIcon` is set. `wlsave_export._prepare_icon` re-encodes any Blender-readable image to PNG
capped at 512 px (live: 1024×512 → 512×256); `ui.MINERVHA_OT_capture_thumbnail` renders the 3D viewport to a
temp PNG (viewport aspect, 512 px long side — live 512×222) as an alternative source (an explicit `.png`
render filepath is written verbatim; `frame_path()` must NOT be used — it suffixes a frame number). **(3)
Collisions** (Scene mode): one scene-wide toggle drives every UserMesh's `boolSettings.EnableCollision`
(threaded through `prop_mapper.map_object`); default **off** (~90% of real saves: 16465 false / 1743 true).
`ui.py` adds the three props (+ a "Scene options" box and a Thumbnail row with a capture button) and reports
each in the popup. Plan: [`../docs/plans/features/export-options/plan.md`](../docs/plans/features/export-options/plan.md).

Tests (`../tests/`): `test_mapper.py` (regression snapshot of `mapper.py` + `run_semantic()` asserting the
Phase-1 shading-compat signals — triplanar / loss notes / `bakeCandidates` — across 7 new fixtures), fixtures
+ golden regenerable via `_gen_golden.py`; `test_sanitize.py` (filename sanitization — units + end-to-end `build_wlsave`, pure Python);
`test_tiling.py` (`textureTiling` = reciprocal of Blender Mapping Scale);
`test_prop_mapper.py` + `test_transform.py` (snapshot of `prop_mapper.py` — `normalized_objects.json` fixtures,
golden `expected_props.json` regenerable via `_gen_golden_props.py`; + `master_group` shape and the
`enable_collision` toggle);
`test_scene_build.py` (`build_scene_wlsave` end-to-end with OBJ export injected — Models/props/cross-ref; +
master-group wrapping, collision propagation, thumbnail-as-first-PNG);
`test_thumbnail.py` (`_prepare_icon` pure path + `_write_zip` writes the icon before any texture + `bHasDedicatedIcon`);
`test_texture_collision.py` (dedup by srcPath, collision rename);
`test_texture_options.py` (pure `_plan_texture` JPG/PNG/downscale decision + `tex_opts` end-to-end);
`test_missing_report.py` (per-texture `missingDetail`: packed → material+meshes+channel+reason; on-disk
`file not found` tie-back; resolved texture → no entry);
`verify_introspect_live.py` is a Blender-side probe (run in the Python console);
`verify_tga_reexport_live.py` is a headless Blender probe (`blender --background --python …`) asserting a
`.tga` re-encodes to a valid PNG/JPG in the bundle (the lazy-buffer fix) across default / prefer-JPG /
max-res paths.
