# summary — `minervha_texture_exporter/`

Source of a Blender 4.2+ extension (this folder = build root, zipped for install).
**Independent of `minervha_material_exporter/`** — both can be installed at once, so Scene
properties are prefixed `minervha_texset_*` and classes `MINERVHATEX_*`; nothing is shared
between the two packages.

Writes, for every material of the selected mesh(es), a flat folder of PBR maps named after
the material, with a max-resolution cap: `<Material>_D.png` (Base Color), `<Material>_N.png`
(Normal), and the AO/Roughness/Metallic trio in one of **two layouts** (`orm_mode`) —
**packed** `<Material>_ORM.png` (R=AO, G=Rough, B=Metal) or **split** `<Material>_AO.png` /
`_R.png` / `_M.png`. The layouts differ only in how missing data is handled: packed always
emits one complete file (white AO, neutral fallbacks), split only ever emits maps carrying
real data (no packed source ⇒ no `_AO`; an unbakeable procedural channel ⇒ no map).

| File | Role | Dependencies | Plans |
|---|---|---|---|
| `blender_manifest.toml` | Extension manifest (id `minervha_texture_exporter`, min 4.2.0, `files` permission, GPL) | — | [texture-set-export](../docs/plans/features/texture-set-export/plan.md) |
| `__init__.py` | `register`/`unregister` — delegates to `ui` | `ui` | texture-set-export |
| `sources.py` | Material → `{channel: source}` by **backward**-tracing each Principled input (the mirror of the sibling add-on's forward trace) to `image` (+ pixel `channel`, + `packed`) / `const` / `procedural`. Passthrough set = Reroute, Normal Map, Separate Color/RGB; groups walked structurally; anything else transforms the data ⇒ procedural. `packed` (the value came out of a Separate node) is the ONLY accepted proof that a texture is a multi-channel pack. Also `principled_of` (active-output anchored, descends groups, returns the owning tree), `max_source_dim`, `materials_of`. **No `bpy` import** → unit-testable | — (pure) | texture-set-export |
| `images.py` | Pixel plumbing. `fit` (the cap — only ever shrinks), `write_direct` (re-export a source map, downscaled), `read_channel` (one channel resampled to a target size), `pack_orm_arrays` (pure — the R/G/B layout), `write_orm`, `write_gray` (one channel as its own map, split layout), `write_flat_color`, `flat_channel`, `channel_of`. **Colorspace contract, measured not assumed:** `Image.pixels` is RAW stored data in both directions (no transfer function on read or `save()`), so data channels pass through byte-exact — but a flat Base Color must be sRGB-encoded here (`_linear_to_srgb`) or it exports far too dark. **`_working_copy`:** `Image.copy()` re-generates a GENERATED image (Blender's "New Image" — a painted map never saved) into flat black, so those are rebuilt from the source buffer by hand | `numpy`, `bpy` (optional — pure helpers tested without it) | texture-set-export |
| `bake.py` | Cycles fallback for a channel driven by a node graph. Isolated throwaway scene (a bake syncs the whole active scene, so never the user's), full-UV placeholder plane (never a scene mesh → one texture correct for every mesh sharing the material), EMIT rewire for inputs with no dedicated pass (Metallic). Returns the bake **image** — the caller saves it (`diffuse`/`normal`) or reads its channel straight into the ORM. The target is tagged BEFORE the bake: Cycles encodes into the target's colorspace as it fills the buffer | `bpy` | texture-set-export |
| `export.py` | Policy + orchestration. Pure: `sanitize_name`, `unique_name` (two materials sanitizing alike must not overwrite each other's set), `reference_size` (`next_pow2` of the largest source texture, capped, 1024 with no texture — the size for flat/baked maps), **`infer_ao`** (AO index *derived* from the channels roughness/metallic occupy on a shared packed image: rough=G+metal=B → AO=R; metal=R+rough=G → AO=B; rough=G alone → AO=R; only R occupied → refuse, since R is AO's own slot in both conventions; no Separate node ⇒ no packed source ⇒ white AO). Orchestration: `export_material` (D → trio → N) dispatching on `orm_mode` to `_export_orm_packed` (composes one file, filling white/neutral for what is missing — the file needs a value per channel) or `_export_orm_split` + `_export_scalar_map` (one map per channel, each at its own natural size; a whole plugged texture is copied verbatim rather than rebuilt from a channel; nothing written for a channel with no real data), `export_texture_sets` (opens the bake environment only if some material needs it), per-material failure isolation, `format_report` | `sources`, `images`, `bake` | texture-set-export |
| `ui.py` | "Texture Set" panel in the "Minervha" N-panel tab: output folder (`//`-relative allowed once the .blend is saved), max-resolution cap, **AO/Rough/Metal layout** (Combined `_ORM` / Separate `_AO`,`_R`,`_M`), bake toggle, a preview of the sets that will be written, and the `minervha.export_texture_set` operator + a "Last run" sub-panel. The report popup is gated on `not bpy.app.background` — `popup_menu` headless is an uncatchable access violation | `bpy`, `export`, `sources` | texture-set-export |

**Channel policy.** Direct texture → copied and downscaled to the cap; constant → flat fill;
node graph → Cycles bake (toggle, on by default). A material with no normal input gets no
`_N` file. A procedural channel with baking off falls back to a reported neutral (roughness
0.5 / metallic 0.0) in the **packed** layout so its single file is always complete — the
**split** layout writes no map instead, since a flat neutral in its own file would be a
plausible-looking lie.

**Validated on Blender 5.1.2**: 16 pure tests (`../tests/test_texture_set.py`) + 25 live
assertions (`../tests/verify_texture_set_live.py`, run headless) covering packed-ORM channel
values, the split layout's per-channel values and its refusals (no `_AO` without a packed
source, no `_R` for an unbakeable graph), the cap, the sRGB encoding of both flat and baked
Base Color, and a baked procedural roughness carrying real detail.
